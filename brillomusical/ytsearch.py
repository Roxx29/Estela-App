"""Busca una canción en YouTube Music (InnerTube, cliente WEB_REMIX) para abrirla en el invitado."""
import json
import re
import urllib.request

URL = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20241023.01.00", "hl": "es"}
SONGS = "EgWKAQIIAWoKEAkQBRAKEAMQBA=="  # filtro "Canciones"
TIMEOUT = 8.0
MAX_BYTES = 3_000_000
TOLERANCE = 5.0
_DUR = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_VID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def valid_id(v):
    return isinstance(v, str) and bool(_VID.match(v))


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _vid(item):
    for d in _walk(item):
        v = d.get("videoId")
        if isinstance(v, str) and _VID.match(v):
            return v
    return None


def parse(data):
    """[(videoId, título, artista, duración s)] en orden de la búsqueda."""
    out = []
    for d in _walk(data):
        item = d.get("musicResponsiveListItemRenderer")
        if not isinstance(item, dict):
            continue
        cols = []
        for c in item.get("flexColumns") or []:
            runs = (((c or {}).get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}).get("runs")
            cols.append([r.get("text", "") for r in runs or [] if isinstance(r, dict)])
        vid = _vid(item)
        if not vid or not cols or not cols[0]:
            continue
        parts = [p.strip() for col in cols[1:] for p in "".join(col).split("•") if p.strip()]
        dur = 0.0
        for p in parts:
            m = _DUR.match(p)
            if m:
                dur = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        artist = next((p for p in parts if p.lower() not in ("canción", "song", "vídeo", "video")), "")
        out.append((vid, "".join(cols[0]), artist, float(dur)))
    return out


def pick(results, duration, tol=TOLERANCE):
    """El primer resultado con la duración ±5 s (sin duración conocida, el primero)."""
    for r in results:
        if not duration or (r[3] and abs(r[3] - duration) <= tol):
            return r
    return None


def search(title, artist):
    body = json.dumps({"context": {"client": CLIENT}, "query": f"{title} {artist}".strip(),
                       "params": SONGS}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Origin": "https://music.youtube.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise OSError("Respuesta de YouTube Music demasiado grande")
    return parse(json.loads(raw))


def find(title, artist, duration):
    """(videoId, título, artista, duración) o None. Fallo de red → OSError."""
    return pick(search(title, artist), duration)
