"""Letras sincronizadas: limpieza de títulos, proveedores en paralelo, caché y tiempos por palabra (sin Qt)."""
import bisect
import hashlib
import json
import logging
import os
import re
import ssl
import queue
import threading
import time
import urllib.error

from . import providers, ttml

API = "https://lrclib.net/api/"
CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BrilloMusical", "lyrics")  # misma carpeta: claves v3 y se leen las de antes
TOTAL_S = 8.0  # espera máxima a todos los proveedores
MISS_TTL = 24 * 3600  # "sin letra" se vuelve a buscar al día siguiente

_JUNK = re.compile(
    r"\s*[\(\[][^\)\]]*\b(official|oficial|video|videoclip|clip|audio|lyrics?|letra|visuali[sz]er|mv|hd|4k|prod|"
    r"remaster\w*)\b[^\)\]]*[\)\]]",
    re.I,
)
# vídeos de YouTube: "Canción - Video Oficial" / "Canción Official Music Video" sin paréntesis
_TAIL = re.compile(r"\s+(?:[-–—]\s+)?(?:(?:official|oficial)(?:\s+(?:music|video|audio|lyrics?|visuali[sz]er))*"
                   r"|(?:music\s+)?(?:video|videoclip|lyrics?|letra)(?:\s+(?:oficial|official))?)$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")
_PIPE = re.compile(r"\s*(?:\|+|｜)\s*")
_FEAT = re.compile(r"\s*[\(\[]?\b(?:feat|ft|featuring)\b\.?[^\)\]]*[\)\]]?", re.I)
VIDEO_TOL = 20  # un vídeo (sin álbum) dura más que la canción por la intro o el final
_TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_WTAGS = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>\s*([^<]*)")  # LRC mejorado: <mm:ss.xx> palabra
_AD = re.compile(r"^(ad|anuncio|advertisement|publicidad)$|\b(anuncio|advertisement|publicidad)\b|\bad ·", re.I)
log = logging.getLogger(__name__)


class Clock:
    """Posición suavizada: avanza sola y se corrige poco a poco hacia Windows (sin saltos ni retrocesos)."""
    TAU = 1.0  # segundos para corregir un error pequeño
    JUMP = 1.0  # error a partir del cual se salta (el usuario movió la barra)

    def __init__(self):
        self.key, self.pos, self.t, self.playing = object(), None, 0.0, False

    def get(self, target, playing, now, key=None):
        if not playing or not self.playing or self.pos is None or key != self.key:
            self.pos = target
        else:
            dt = now - self.t
            pred = self.pos + dt
            err = target - pred
            self.pos = target if abs(err) > self.JUMP else max(self.pos, pred + err * min(1.0, dt / self.TAU))
        self.key, self.t, self.playing = key, now, playing
        return self.pos


class Offsets:
    """Desfase manual de la letra por canción (segundos, + = la letra va antes)."""
    MAX = 300
    LIMIT = 5.0

    def __init__(self, data):
        self.data = dict(data or {})

    def get(self, key):
        return self.data.get(key, 0.0)

    def add(self, key, delta):
        value = max(-self.LIMIT, min(self.LIMIT, self.get(key) + delta))
        self.data.pop(key, None)
        self.data[key] = round(value, 3)
        while len(self.data) > self.MAX:
            del self.data[next(iter(self.data))]  # la más antigua
        return value

    def reset(self, key):
        self.data.pop(key, None)


def _squash(s):
    return re.sub(r"\W", "", s.lower())


def _same(a, b):
    a, b = _squash(a), _squash(b)
    return bool(a and b) and (a in b or b in a)


def clean(title, artist):
    title = _TAIL.sub("", _JUNK.sub("", title)).strip()
    artist = re.sub(r"\s*-\s*Topic$|VEVO$|\s+(official|oficial)$", "", artist.strip(), flags=re.I).strip()
    segs = [p for p in _PIPE.split(title) if p]
    if len(segs) > 1:  # "Artista - Canción | Álbum": sobra lo de después; "TRUENO || RAIN III": es como un guion
        title = segs[0] if _DASH.search(segs[0]) else f"{segs[0]} - {segs[1]}"
    parts = [p.strip() for p in _DASH.split(title, 1)]
    # "Artista - Canción" (o "Canción - Artista"): el canal suele ser el artista con otra forma (BadBunnyVEVO)
    if len(parts) == 2 and (not artist or _same(parts[0], artist)):
        artist, title = parts
    elif len(parts) == 2 and _same(parts[1], artist):
        title = parts[0]
    return title, artist


def main_artist(artist):
    return re.split(r",|&| feat\.? | ft\.? ", artist, flags=re.I)[0].strip()


def is_ad(title, artist, album, duration):
    return not artist.strip() or bool(_AD.search(title)) or (0 < duration < 35 and not album)


def parse_lrc(text):
    out = []
    for raw in text.splitlines():
        tags = _TAG.findall(raw)
        if not tags:
            continue
        words = _TAG.sub("", raw).strip()
        out += [(int(m) * 60 + float(s), words) for m, s in tags]
    return sorted(out, key=lambda x: x[0])


def line_index(lines, pos):
    """Índice de la línea que suena en `pos` (-1 antes de la primera)."""
    return bisect.bisect_right([t for t, _ in lines], pos) - 1


def _tagged(text):
    """[(segundos, palabra)] de una línea con etiquetas <mm:ss.xx> (con o sin espacio); [] si no las trae."""
    out = []
    for m, s, chunk in _WTAGS.findall(text):
        out += [(int(m) * 60 + float(s), w) for w in chunk.split()]  # etiqueta de cierre: trozo vacío
    return out


def words(text):
    """Palabras de una línea sin las etiquetas <mm:ss.xx>."""
    tagged = _tagged(text)
    return [w for _, w in tagged] if tagged else text.split()


def word_times(lines, idx):
    """Inicio de cada palabra: exacto si la letra trae tiempos (<mm:ss.xx> o TimedText), si no estimado."""
    start, text = lines[idx]
    if getattr(text, "times", None):
        return list(text.times)
    tagged = _tagged(text)
    if tagged:
        return [t for t, _ in tagged]
    end = lines[idx + 1][0] if idx + 1 < len(lines) else start + 6
    return [w[0] for w in ttml.spread(start, end, text.split())]


# --- Modelo único de línea: {"t", "end", "agent", "words": [(t, end, texto, bg)]}; words=[] = fin de estrofa ---

class TimedText(str):
    """Texto de una línea con los tiempos exactos de sus palabras (capa compatible con (t, texto))."""
    times = ()


def legacy(lines):
    """Modelo → [(t, texto)] de la API anterior, sin perder los tiempos por palabra."""
    out = []
    for line in lines:
        text = TimedText(" ".join(w[2] for w in line["words"]))
        text.times = tuple(w[0] for w in line["words"])
        out.append((line["t"], text))
    return out


def from_lrc(pairs):
    """[(t, texto)] de parse_lrc → modelo. Las líneas vacías se conservan (marcan fin de estrofa)."""
    out = []
    for i, (t, text) in enumerate(pairs):
        end = pairs[i + 1][0] if i + 1 < len(pairs) else t + 6
        tagged = _tagged(text)
        if tagged:
            starts = [s for s, _ in tagged] + [end]
            words = [(s, max(s, starts[j + 1]), w, False) for j, (s, w) in enumerate(tagged)]
        else:
            words = ttml.spread(t, end, text.split())
        out.append({"t": t, "end": max(t, end), "agent": None, "words": words})
    return out


def _get(endpoint, params):
    # timeout, límite de lectura y solo https de providers; /get puede buscar fuera de LRCLIB y tardar
    body = providers.get(API + endpoint, params, timeout=12 if endpoint == "get" else 8)
    if body is None:
        raise urllib.error.HTTPError(API + endpoint, 404, "Not Found", None, None)
    return json.loads(body)  # ValueError si no es JSON: _query lo trata como respuesta rara


class Plain(list):
    """Letra sin tiempos repartida a lo largo de la canción: se muestra, pero no se guarda (otro día puede haber sincronizada)."""


def spread(text, duration):
    """Reparte las líneas de una letra sin tiempos entre el 8 % y el 95 % de la canción; las vacías son pausas."""
    rows = [r.strip() for r in text.splitlines()]
    weights = [len(r) + 8 for r in rows]
    total, acc, out = sum(weights) or 1, 0, Plain()
    for r, w in zip(rows, weights):
        if r:
            out.append((duration * (0.08 + 0.87 * acc / total), r))
        acc += w
    return out


def reason(e):
    """Texto para el usuario según el fallo: no todo es "sin conexión"."""
    s = str(e).lower()
    if isinstance(e, ssl.SSLError) or "certificate" in s or "ssl" in s:
        return "No se pudo comprobar el certificado del servidor de letras (antivirus o proxy)"
    if isinstance(e, TimeoutError) or "timed out" in s or "no responde" in s:
        return "El servidor de letras no responde · reintentando…"
    if isinstance(e, OSError):
        return "Sin conexión · reintentando…"
    return "Error al buscar la letra"


def _query(title, artist, album, duration):
    """Letra de LRCLIB; None si alguna consulta falló (no se guarda "sin letra"). Lanza OSError si ninguna respondió.

    Solo es "sin conexión" si no contesta nada: un error o un /get lento (LRCLIB lo busca fuera y tarda)
    no deben dejar la canción en "Sin conexión · reintentando…".
    """
    online, err, down = False, None, 0

    def ask(endpoint, params):
        nonlocal online, err, down
        if down >= 2:  # dos fallos de conexión seguidos: no se espera a las demás (se reintenta luego)
            return None
        try:
            out = _get(endpoint, params)
            online, down = True, 0
            return out
        except urllib.error.HTTPError as e:  # 404: no está; otro código: se prueba lo siguiente
            online = True
            if e.code >= 500:  # LRCLIB caído (502…): no es "sin letra", no se guarda y se reintenta
                err = err or e
            log.info("LRCLIB %s: %s", endpoint, e.code)
        except ValueError as e:  # respondió algo que no es JSON
            online = True
            err = err or e
            log.info("LRCLIB %s: respuesta rara", endpoint)
        except OSError as e:  # sin red, certificado o tiempo agotado
            err, down = e, down + 1
            log.info("LRCLIB %s: %s", endpoint, e)
        return None

    plain = None
    if duration:  # /get también sin álbum: si LRCLIB no la tiene, la busca fuera (así salían muchas de vídeos)
        item = ask("get", {"track_name": title, "artist_name": artist, "album_name": album,
                           "duration": round(duration)})
        if isinstance(item, dict) and item.get("syncedLyrics"):
            log.debug("Letra de get, diferencia de duración %.1f s", (item.get("duration") or 0) - duration)
            return parse_lrc(item["syncedLyrics"])
        plain = item.get("plainLyrics") if isinstance(item, dict) else None
    else:
        plain = None
    song = _FEAT.sub("", title).strip()
    tries = [{"track_name": title, "artist_name": main_artist(artist)}]
    parts = _DASH.split(song, 1)
    if len(parts) == 2:  # quedó un guion: "Artista - Canción" o "Canción - Artista" con un canal raro
        tries += [{"track_name": parts[1], "artist_name": main_artist(parts[0])},
                  {"track_name": parts[0], "artist_name": main_artist(parts[1])}]
    tries += [{"q": f"{main_artist(artist)} {song}".strip()}, {"q": song}]  # nombres que no cuadran del todo
    tol = VIDEO_TOL if not album else 3
    for params in tries:
        items = [i for i in ask("search", params) or [] if isinstance(i, dict)]
        if duration:  # la versión de duración más parecida (otra versión = letra desfasada)
            items = sorted((i for i in items if abs((i.get("duration") or 0) - duration) <= tol),
                           key=lambda i: abs((i.get("duration") or 0) - duration))
        found = [i for i in items if i.get("syncedLyrics")]
        plain = plain or next((i["plainLyrics"] for i in items if i.get("plainLyrics")), None)
        if found:
            log.debug("Letra de search %s, diferencia de duración %.1f s", list(params),
                      (found[0].get("duration") or 0) - duration)
            return parse_lrc(found[0]["syncedLyrics"])
    if plain and duration:  # sin sincronizada: la letra normal repartida en el tiempo, mejor que nada
        log.info("Letra sin tiempos (aproximada)")
        return spread(plain, duration)
    if not online:
        raise err if isinstance(err, OSError) else OSError("LRCLIB no responde")
    return None if err else []


def lrclib(title, artist, album, duration):
    """Proveedor LRCLIB para search(): nivel 1 sincronizada, 2 con tiempos por palabra, 0 aproximada (Plain)."""
    pairs = _query(title, artist, album, duration)
    if pairs is None:  # alguna consulta falló: no es "sin letra"
        raise OSError("LRCLIB no responde")
    if isinstance(pairs, Plain):
        return from_lrc(pairs), 0
    return from_lrc(pairs), (2 if any(_tagged(text) for _, text in pairs) else 1)


def search(title, artist, album, duration, on_update=None, jobs=None):
    """Todos los proveedores en paralelo (5 s cada uno, 8 s en total). Gana el nivel más alto; empate = orden.

    on_update(líneas, fuente) se llama desde otro hilo: con LRCLIB en cuanto llega y otra vez con cada letra
    más detallada (quien la muestra la aplica al empezar la siguiente estrofa).
    Devuelve (líneas, nivel, fuente, {proveedores con fallo de red o sin respuesta a tiempo}).
    """
    jobs = jobs or providers.PROVIDERS + [("LRCLIB", lrclib)]
    best, failed = None, set()  # best = (líneas, nivel, fuente, prioridad)
    results = queue.Queue()

    def run(prio, name, fn):
        try:
            results.put((prio, name, fn(title, artist, album, duration), None))
        except Exception as e:  # noqa: BLE001 (un proveedor roto no tumba a los demás)
            results.put((prio, name, None, e))

    # hilos daemon: uno colgado no impide cerrar el .exe
    for prio, (name, fn) in enumerate(jobs):
        threading.Thread(target=run, args=(prio, name, fn), daemon=True, name=f"letra-{name}").start()
    pending = set(range(len(jobs)))
    deadline = time.monotonic() + TOTAL_S
    while pending:
        try:
            prio, name, result, error = results.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            failed |= {jobs[p][0] for p in pending}
            break
        pending.discard(prio)
        if isinstance(error, OSError):
            log.debug("%s: fallo de red (%s)", name, error)
            failed.add(name)
            continue
        if error is not None:
            log.warning("%s: respuesta no válida", name, exc_info=error)
            continue
        lines, level = result
        if lines and (best is None or (level, -prio) > (best[1], -best[3])):
            best = (lines, level, name, prio)
            log.debug("Letra de %s (nivel %d)", name, level)
            if on_update:
                on_update(lines, name)
        if best and best[1] == 2 and not any(p < best[3] for p in pending):
            break  # ningún pendiente puede ganar ya
    return (best[0], best[1], best[2], failed) if best else ([], 0, None, failed)


def _legacy_cache(artist, title, duration):
    """Letras guardadas por versiones anteriores ([(t, texto)] con clave "v2|…" o sin prefijo): sirven si tienen letra."""
    base = f"{artist}|{title}|{round(duration)}".lower()
    for key in (f"v2|{base}", base):
        try:
            with open(os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest() + ".json"), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return from_lrc([tuple(x) for x in data])
        except (OSError, ValueError, TypeError):
            pass
    return None


def fetch_lines(title, artist, album, duration, on_update=None):
    """Modelo de líneas; [] si no hay letra. Lanza OSError si LRCLIB no responde y nadie trae letra."""
    title, artist = clean(title, artist)
    key = hashlib.sha1(f"v3|{artist}|{title}|{round(duration)}".lower().encode()).hexdigest()
    path = os.path.join(CACHE, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data["v"] == 3 and (data["lines"] or time.time() - os.path.getmtime(path) < MISS_TTL):
            lines = [dict(line, words=[tuple(w) for w in line["words"]]) for line in data["lines"]]
            if lines and on_update:
                on_update(lines, data.get("source"))
            return lines
    except (OSError, ValueError, KeyError, TypeError):
        pass
    lines, level, source, failed = search(title, artist, album, duration, on_update)
    if not lines and "LRCLIB" in failed:
        old = _legacy_cache(artist, title, duration)  # con LRCLIB caído, mejor la letra de antes
        if old:
            return old
        raise OSError("LRCLIB no responde")  # un fallo de red no se guarda como "sin letra"
    # sin duración puede ser otra versión; aproximada (nivel 0) o con fallos quizá faltó una mejor: no se guarda
    if not duration or level == 0 or (failed and level < 2):
        return lines
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"v": 3, "level": level, "source": source, "lines": lines}, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass  # sin caché no es grave
    return lines


def fetch(title, artist, album, duration):
    """API anterior: [(segundos, texto)]; los tiempos por palabra van en TimedText. OSError sin conexión."""
    return legacy(fetch_lines(title, artist, album, duration))
