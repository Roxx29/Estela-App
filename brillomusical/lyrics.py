"""Letras sincronizadas: limpieza de títulos, LRCLIB, caché y tiempos por palabra (sin Qt)."""
import bisect
import hashlib
import json
import logging
import os
import re
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
# vídeos de YouTube: "Canción | lo que sea" y "Canción - Video Oficial" / "Canción Official Music Video" sin paréntesis
_TAIL = re.compile(r"\s+[|｜].*$|\s+(?:[-–—]\s+)?(?:(?:official|oficial)(?:\s+(?:music|video|audio|lyrics?|visuali[sz]er))*"
                   r"|(?:music\s+)?(?:video|videoclip|lyrics?|letra)(?:\s+(?:oficial|official))?)$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")
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


def clean(title, artist):
    title = _TAIL.sub("", _JUNK.sub("", title)).strip()
    artist = re.sub(r"\s*-\s*Topic$|VEVO$|\s+(official|oficial)$", "", artist.strip(), flags=re.I).strip()
    parts = _DASH.split(title, 1)
    # "Artista - Canción": el canal suele ser el artista con otra forma (BadBunnyVEVO, "Bad Bunny Official")
    if len(parts) == 2 and (not artist or _squash(parts[0]) in _squash(artist) or _squash(artist) in _squash(parts[0])):
        artist, title = (p.strip() for p in parts)
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
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.load(r)


def _query(title, artist, album, duration):
    try:
        item = _get("get", {"track_name": title, "artist_name": artist, "album_name": album, "duration": round(duration)})
        if item.get("syncedLyrics"):
            log.debug("Letra de get, diferencia de duración %.1f s", (item.get("duration") or 0) - duration)
            return parse_lrc(item["syncedLyrics"])
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    tries = [{"track_name": title, "artist_name": main_artist(artist)}]
    if not album:  # un vídeo cualquiera: el canal puede no ser el artista y el título traer de todo
        song = _FEAT.sub("", title).strip()
        parts = _DASH.split(song, 1)
        if len(parts) == 2:  # "Artista - Canción" con un canal que no se parece al artista
            tries.append({"track_name": parts[1], "artist_name": main_artist(parts[0])})
        tries += [{"q": f"{main_artist(artist)} {song}".strip()}, {"q": song}]
    tol = VIDEO_TOL if not album else 3
    for params in tries:
        found = [i for i in _get("search", params) if i.get("syncedLyrics")]
        if duration:  # la versión de duración más parecida (otra versión = letra desfasada)
            found = sorted((i for i in found if abs((i.get("duration") or 0) - duration) <= tol),
                           key=lambda i: abs((i.get("duration") or 0) - duration))
        if found:
            log.debug("Letra de search %s, diferencia de duración %.1f s", list(params),
                      (found[0].get("duration") or 0) - duration)
            return parse_lrc(found[0]["syncedLyrics"])
    return []


def fetch(title, artist, album, duration):
    """Lista de (segundos, texto); [] si no hay letra sincronizada. Lanza OSError sin conexión."""
    title, artist = clean(title, artist)
    key = hashlib.sha1(f"{artist}|{title}|{round(duration)}".lower().encode()).hexdigest()
    path = os.path.join(CACHE, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            lines = json.load(f)
        if lines or time.time() - os.path.getmtime(path) < MISS_TTL:
            return [tuple(x) for x in lines]
    except (OSError, ValueError):
        pass
    lines = _query(title, artist, album, duration)
    if not duration:
        return lines  # sin duración puede ser otra versión: no se guarda en caché
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(lines, f, ensure_ascii=False)
    except OSError:
        pass  # sin caché no es grave
    return lines
