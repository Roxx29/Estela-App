"""Portada en alta resolución: iTunes (1000×1000) y, si no la tiene, Deezer (1000×1000). Con caché.

Windows solo da la portada a 120×120. Envía el título y el artista a Apple y a Deezer (como LRCLIB
con las letras); se puede desactivar en la bandeja. Si no hay coincidencia clara, no se inventa:
se usa la de Windows.
"""
import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request

from PIL import Image

from . import lyrics

API = "https://itunes.apple.com/search"
DEEZER = "https://api.deezer.com/search"
CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BrilloMusical", "art")
MISS_TTL = 24 * 3600
MAX_BYTES = 5_000_000  # lo que venga de la red, con tope
MAX_FILES = 500


def _norm(s):
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s).lower().strip()


def _artist(s):
    return re.split(r",|&| y | x | feat\.? | ft\.? ", s.lower())[0].strip()


def _matches(track, artist_name, title, artist):
    title, artist = lyrics.clean(title, artist)
    a = _artist(artist)
    return _norm(track) == _norm(title) and bool(a) and a in artist_name.lower()


def pick(results, title, artist):
    """URL 1000×1000 (iTunes) del resultado cuyo título y artista principal coinciden; None si ninguno."""
    for r in results:
        url = r.get("artworkUrl100") or ""
        if url.startswith("https://") and _matches(r.get("trackName", ""), r.get("artistName", ""), title, artist):
            return url.replace("100x100bb", "1000x1000bb")
    return None


def pick_deezer(data, title, artist, duration):
    """cover_xl (1000×1000) de Deezer si coinciden título, artista y duración (±3 s)."""
    for r in data:
        url = (r.get("album") or {}).get("cover_xl") or ""
        host = urllib.parse.urlparse(url).hostname or ""
        if not (url.startswith("https://") and host.endswith(".dzcdn.net")):
            continue
        if duration and abs((r.get("duration") or 0) - duration) > 3:
            continue  # otra versión de la canción
        if _matches(r.get("title", ""), (r.get("artist") or {}).get("name", ""), title, artist):
            return url
    return None


def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Estela/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_BYTES)


def _valid(data):
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _prune():
    """Borra las más antiguas si la caché pasa de MAX_FILES archivos."""
    try:
        files = sorted((os.path.join(CACHE, f) for f in os.listdir(CACHE)), key=os.path.getmtime)
        for f in files[:-MAX_FILES]:
            os.remove(f)
    except OSError:
        pass


def _search(title, artist):
    clean_title, clean_artist = lyrics.clean(title, artist)
    term = f"{_artist(clean_artist)} {clean_title}"
    return json.loads(_get(API + "?" + urllib.parse.urlencode({"term": term, "entity": "song", "limit": 10}))).get(
        "results", [])


def _itunes(title, artist):
    return pick(_search(title, artist), title, artist)


_genres = {}


def genre(title, artist):
    """Género según iTunes (sin país: tienda de EE. UU., nombres en inglés) o None. OSError sin red."""
    key = (title, artist)
    if key not in _genres:
        if len(_genres) > 200:
            _genres.clear()
        _genres[key] = next((r.get("primaryGenreName") for r in _search(title, artist)
                             if _matches(r.get("trackName", ""), r.get("artistName", ""), title, artist)), None)
    return _genres[key]


def _deezer(title, artist, duration):
    clean_title, clean_artist = lyrics.clean(title, artist)
    a = _artist(clean_artist)
    # primero la búsqueda exacta; a veces viene vacía y la libre sí encuentra
    for q in (f'artist:"{a}" track:"{clean_title}"', f"{a} {clean_title}"):
        data = json.loads(_get(DEEZER + "?" + urllib.parse.urlencode({"q": q, "limit": 10}))).get("data", [])
        url = pick_deezer(data, title, artist, duration)
        if url:
            return url
    return None


def fetch(title, artist, duration=0):
    """Bytes de la portada grande o None. Lanza OSError si no hay conexión con ninguna fuente."""
    # v2: con Deezer; las marcas "no encontrada" de antes (solo iTunes) no deben bloquearla
    key = hashlib.sha1(f"v2|{artist}|{title}".lower().encode()).hexdigest()
    path, miss = os.path.join(CACHE, key + ".img"), os.path.join(CACHE, key + ".none")
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data and _valid(data):
            return data
    except OSError:
        pass  # no está o no se puede leer: se vuelve a pedir
    if os.path.exists(miss) and time.time() - os.path.getmtime(miss) < MISS_TTL:
        return None
    data, errors = None, 0
    for source in (lambda: _itunes(title, artist), lambda: _deezer(title, artist, duration)):
        try:
            url = source()
            data = _get(url) if url else None
        except (OSError, ValueError):  # sin red, respuesta rara: se prueba la siguiente fuente
            errors += 1
            continue
        if data and _valid(data):
            break
        data = None
    if errors == 2:
        raise OSError("sin conexión con iTunes ni con Deezer")  # no se marca como "no encontrada"
    if not data and errors:
        return None  # una fuente falló por red: no se marca "no encontrada" (se reintenta otra vez)
    try:
        os.makedirs(CACHE, exist_ok=True)
        tmp = (path if data else miss) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data or b"")
        os.replace(tmp, path if data else miss)  # atómico: un cierre a medias no deja una portada rota
        _prune()
    except OSError:
        pass  # sin caché no es grave
    return data
