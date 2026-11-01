"""Letras sincronizadas: limpieza de títulos, LRCLIB, caché y tiempos por palabra (sin Qt)."""
import bisect
import hashlib
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api/"
CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BrilloMusical", "lyrics")
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
    """Inicio de cada palabra: exacto si la letra trae <mm:ss.xx>, si no estimado por longitud."""
    start, text = lines[idx]
    tagged = _tagged(text)
    if tagged:
        return [t for t, _ in tagged]
    clean_words = text.split()
    end = lines[idx + 1][0] if idx + 1 < len(lines) else start + 6
    # una nota larga al final no debe retrasar las palabras: ~0,5 s por palabra como mucho
    span = min(end - start, 0.5 * len(clean_words) + 0.5, 6)
    weights = [len(w) + 2 for w in clean_words]
    total, acc, out = sum(weights), 0, []
    for w in weights:
        out.append(start + span * acc / total)
        acc += w
    return out


def _get(endpoint, params):
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Estela/1.0"})
    # /get puede buscar fuera de LRCLIB y tardar bastante; las búsquedas son rápidas
    with urllib.request.urlopen(req, timeout=12 if endpoint == "get" else 8) as r:
        return json.load(r)


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
    if isinstance(e, TimeoutError) or "timed out" in s:
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


def fetch(title, artist, album, duration):
    """Lista de (segundos, texto); [] si no hay letra sincronizada. Lanza OSError sin conexión."""
    title, artist = clean(title, artist)
    # "v2": los "sin letra" guardados por versiones que fallaban no cuentan
    key = hashlib.sha1(f"v2|{artist}|{title}|{round(duration)}".lower().encode()).hexdigest()
    path = os.path.join(CACHE, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            lines = json.load(f)
        if lines or time.time() - os.path.getmtime(path) < MISS_TTL:
            return [tuple(x) for x in lines]
    except (OSError, ValueError):
        pass
    lines = _query(title, artist, album, duration)
    if lines is None or isinstance(lines, Plain):
        return lines or []  # aproximada o con algún fallo: se muestra, pero no se guarda (la próxima vez se busca)
    if not duration:
        return lines  # sin duración puede ser otra versión: no se guarda en caché
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(lines, f, ensure_ascii=False)
    except OSError:
        pass  # sin caché no es grave
    return lines
