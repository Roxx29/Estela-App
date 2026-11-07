"""Escuchar juntos: cliente del servidor de salas (docs/protocolo.md) y lógica pura de sincronía.

El cliente vive en su propio hilo (websockets síncrono) y solo emite señales Qt.
"""
import json
import logging
import random
import re
import threading
import time
import unicodedata
import urllib.parse

from PySide6.QtCore import QObject, Signal
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from . import lyrics

MAX_SIZE = 64 * 1024
HEARTBEAT = 15.0  # latido del protocolo
PINGS = 5  # muestras de reloj; vale la de menor ida y vuelta
RESYNC = 300.0  # se vuelve a medir el reloj cada 5 min
JUMP = 2.0  # desfase a partir del cual se salta con corte
START, STOP = 0.35, 0.10  # histéresis de la corrección suave
RATE = 0.08  # ±8 %
SLOW_SEEK = 0.7  # sin cambio de velocidad en el navegador: se salta a partir de aquí
SAME_DUR = 5.0  # misma canción: duración ±5 s
CHAT_MAX = 300
UA = "Estela/1.0"

TXT = {
    "no-server": "Falta tu servidor. Configúralo en Escuchar juntos → Servidor.",
    "unreachable": "No llegó al servidor. Revisa la dirección.",
    "sala-no-existe": "No existe la sala {code}.",
    "sala-llena": "La sala está llena.",
    "pending": "Esperando a que el anfitrión te deje entrar…",
    "rechazado": "El anfitrión no aceptó tu entrada.",
    "left": "Has salido de la sala.",
    "not-found": "No encontré {song} en YouTube Music. Búscala tú y sigues sincronizado.",
    "other-version": "Puede ser otra versión.",
    "seek-failed": "Avanza tú hasta {mss} para ir a la par.",
    "closed": "El anfitrión cerró la sala.",
    "opening": "Abriendo {song} en YouTube Music…",
}
FATAL = ("sala-no-existe", "sala-llena", "token-invalido")
_BIDI = dict.fromkeys(map(ord, "؜‎‏‪‫‬‭‮⁦⁧⁨⁩"))
_URL = re.compile(r"(?:wss?|https?)://[^\s]+", re.I)
_CODE = re.compile(r"^[A-HJKMNP-Z2-9]{6}$")
_SALA = re.compile(r"\bsala:\s*([A-Za-z0-9]{6})\b", re.I)
log = logging.getLogger(__name__)


# --- funciones puras ---
def clean_text(s, limit=CHAT_MAX):
    """Texto ajeno seguro para mostrar: sin caracteres de control ni de dirección, recortado."""
    s = str(s or "").translate(_BIDI)
    s = "".join(c for c in s if unicodedata.category(c) != "Cc")
    return s.strip()[:limit]


def normalize_server(url):
    """https://x.workers.dev → wss://x.workers.dev. Solo wss (ws únicamente en localhost para pruebas)."""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/ws"):
        url = url[:-3]
    p = urllib.parse.urlsplit(url)
    scheme = {"https": "wss", "http": "ws"}.get(p.scheme.lower(), p.scheme.lower())
    if not p.hostname or p.query or p.fragment or p.path not in ("", "/"):
        return None
    if scheme != "wss" and not (scheme == "ws" and p.hostname in ("localhost", "127.0.0.1")):
        return None
    return f"{scheme}://{p.netloc}"


def normalize_code(code):
    code = (code or "").strip().upper()
    return code if _CODE.match(code) else None


def invite_text(server, code):
    return f"Escucha conmigo en Estela.\nServidor: {server}\nSala: {code}"


def parse_invite(text):
    """(servidor, código) de una invitación pegada; lo que falte, None."""
    text = text or ""
    m = _URL.search(text)
    server = normalize_server(m.group(0)) if m else None
    c = _SALA.search(text)
    code = normalize_code(c.group(1) if c else text)
    return server, code


def backoff(attempt, rnd=None):
    """Espera antes de reconectar: exponencial con tope de 30 s y aleatoriedad (×0,5–1,5)."""
    rnd = random.random() if rnd is None else rnd
    return min(30.0, 1.0 * 2 ** attempt) * (0.5 + rnd)


def clock_offset(samples):
    """Desfase servidor − local (ms) de la muestra (t0, ts, t1) con menor ida y vuelta."""
    t0, ts, t1 = min(samples, key=lambda s: s[2] - s[0])
    return ts - (t0 + t1) / 2


def host_position(state, server_ms):
    """Dónde va el anfitrión ahora (s), a partir del último `estado` y la hora del servidor."""
    pos = state["posicion"]
    if state["reproduciendo"]:
        pos += max(0.0, server_ms - state["ts"]) / 1000
    return min(pos, state["duracion"]) if state["duracion"] else pos


def sync_action(err, correcting, rate_ok=True):
    """err = invitado − anfitrión (s). → ("seek", None) o ("rate", velocidad) con histéresis."""
    a = abs(err)
    if a >= JUMP or (not rate_ok and a >= SLOW_SEEK):
        return "seek", None
    if rate_ok and (a > START or (correcting and a > STOP)):
        return "rate", 1 - RATE if err > 0 else 1 + RATE
    return "rate", 1.0


def _norm(s):
    s = unicodedata.normalize("NFKD", s.casefold())
    s = re.sub(r"[\(\[].*?[\)\]]|\s(feat|ft)\.?\s.*", "", s)
    return re.sub(r"[\W_]+", "", s)


def same_song(title_a, artist_a, dur_a, title_b, artist_b, dur_b):
    """Misma canción aunque cambien mayúsculas, tildes, «(Official Video)» o «feat.»; duración ±5 s."""
    ta, aa = lyrics.clean(title_a or "", artist_a or "")
    tb, ab = lyrics.clean(title_b or "", artist_b or "")
    ta, tb = _norm(ta), _norm(tb)
    aa, ab = _norm(lyrics.main_artist(aa)), _norm(lyrics.main_artist(ab))
    if not ta or not tb or (ta not in tb and tb not in ta):
        return False
    if aa and ab and aa not in ab and ab not in aa:
        return False
    return not (dur_a and dur_b and abs(dur_a - dur_b) > SAME_DUR)


def host_state(snap, pos, transition, seed):
    """Mensaje `estado` del anfitrión, dentro de los rangos del protocolo."""
    dur = max(0.0, min(86400.0, float(snap["duration"] or 0)))
    return {
        "tipo": "estado", "titulo": (snap["title"] or "")[:200], "artista": (snap["artist"] or "")[:200],
        "album": (snap["album"] or "")[:200], "duracion": dur,
        "posicion": max(0.0, min(dur or 86400.0, float(pos))), "reproduciendo": bool(snap["playing"]),
        "semilla": seed & 0x7FFFFFFF, "transicion": (transition or "")[:32],
    }


def fmt_mss(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


# --- cliente ---
class _Stop(Exception):
    pass


class RoomClient(QObject):
    received = Signal(dict)  # mensajes del servidor, ya en el hilo de Qt
    failed = Signal(str)  # texto para el usuario; la conexión ha terminado
    clock = Signal(float)  # desfase servidor − local en ms

    def __init__(self, server, first):
        """first: {"tipo": "crear", ...} o {"tipo": "unirse", "codigo": ..., ...}."""
        super().__init__()
        self.server, self.first = server, first
        self.code, self.token = first.get("codigo"), None
        self._ws = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="sala")
        self._thread.start()

    def send(self, msg):
        ws = self._ws
        if ws:
            try:
                ws.send(json.dumps(msg))
            except Exception:  # se cae ahora: el hilo reconecta
                log.debug("No se pudo enviar %s", msg.get("tipo"), exc_info=True)

    def leave(self):
        self.send({"tipo": "salir"})
        self._stop.set()
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def _run(self):
        attempt, ever_open = 0, False
        while not self._stop.is_set():
            url = self.server + "/ws" + (f"/{self.code}" if self.code else "")
            try:
                with connect(url, max_size=MAX_SIZE, open_timeout=10, close_timeout=2,
                             user_agent_header=UA) as ws:
                    ever_open, self._ws = True, ws
                    first = ({"tipo": "reconectar", "codigo": self.code, "token": self.token} if self.token
                             else self.first)
                    ws.send(json.dumps(first))
                    self._session(ws)
                    attempt = 0
            except _Stop as e:
                if str(e):
                    self.failed.emit(str(e))
                break
            except (OSError, WebSocketException) as e:
                log.info("Sala: conexión perdida (%s)", e)
            finally:
                self._ws = None
            if self._stop.is_set():
                break
            if not ever_open:
                self.failed.emit(TXT["unreachable"])
                break
            if self._stop.wait(backoff(attempt)):
                break
            attempt = min(attempt + 1, 6)

    def _session(self, ws):
        pings, next_sync, beat = [], 0.0, time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - beat >= HEARTBEAT:
                beat = now
                ws.send('{"tipo":"latido"}')
            if self.token and now >= next_sync:
                pings, next_sync = [], now + RESYNC
                ws.send(json.dumps({"tipo": "ping-reloj", "t0": time.time() * 1000}))
            try:
                msg = json.loads(ws.recv(timeout=1.0))
            except TimeoutError:
                continue
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            t = msg.get("tipo")
            if t == "pong-reloj":
                if isinstance(msg.get("t0"), (int, float)) and isinstance(msg.get("ts"), (int, float)):
                    pings.append((msg["t0"], msg["ts"], time.time() * 1000))
                    if len(pings) < PINGS:
                        ws.send(json.dumps({"tipo": "ping-reloj", "t0": time.time() * 1000}))
                    else:
                        self.clock.emit(clock_offset(pings))
                continue
            if t == "bienvenida":
                self.code, self.token, next_sync = msg.get("codigo") or self.code, msg.get("token"), 0.0
            elif t == "error" and msg.get("codigo") in FATAL:
                self._stop.set()
                c = msg["codigo"]
                # con token ya estábamos dentro: la sala ya no existe o nos quitaron
                raise _Stop(TXT["closed"] if self.token else TXT[c].format(code=self.code)
                            if c != "token-invalido" else TXT["left"])
            elif t in ("rechazado", "expulsado"):
                self._stop.set()
                raise _Stop(TXT["rechazado"] if t == "rechazado" else TXT["left"])
            self.received.emit(msg)
