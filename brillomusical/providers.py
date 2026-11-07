"""Proveedores de letra con tiempos por palabra. Cada uno: fn(title, artist, album, duration) → (líneas, nivel).

nivel 2 = tiempos por palabra/sílaba, 1 = solo por línea. Fallo de red → OSError; "no hay" → ([], 0).
"""
import html
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import ttml

TIMEOUT = 5.0  # ponytail: por petición (LrcRed encadena 3); el tope real es lyrics.TOTAL_S = 8 s
MAX_BYTES = 2_000_000
TOLERANCE = 3.0  # segundos de diferencia de duración admitidos (otra versión = letra desfasada)
UA = "Estela/1.0 (+https://github.com/Roxx29)"
_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
YOULY = ("https://lyricsplus.prjktla.my.id", "https://lyricsplus.binimum.org")
NONE = ([], 0)


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Sigue redirecciones solo a https (una a http se queda en error 3xx → OSError)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnly)


def get(url, params=None, timeout=TIMEOUT):
    """Cuerpo como texto; None si 404 o demasiado grande. Otros fallos HTTP/red → OSError."""
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    if not url.startswith("https://"):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except http.client.HTTPException as e:  # p. ej. IncompleteRead: es un fallo de red, no "sin letra"
        raise OSError(f"respuesta cortada: {e!r}") from e
    if len(body) > MAX_BYTES:
        return None
    return body.decode("utf-8", "replace")


def get_json(url, params=None):
    body = get(url, params)
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def fits(lines, duration, meta=None):
    """La letra encaja con la canción: duración declarada ±3 s y no acaba después del final."""
    if not duration:
        return True
    if isinstance(meta, (int, float)) and meta > 0 and abs(meta - duration) > TOLERANCE:
        return False
    return not lines or lines[-1]["end"] <= duration + TOLERANCE


def _level(text):
    return 2 if re.search(r"<span[^>]*\bbegin=", text) else 1


def _from_ttml(text, duration, meta=None):
    if not text:
        return NONE
    if "&lt;tt" in text:
        text = html.unescape(text)
    lines = ttml.parse(text)
    return (lines, _level(text)) if lines and fits(lines, duration, meta) else NONE


def _find_ttml(data):
    """Primer texto con un documento TTML dentro de un JSON cualquiera."""
    if isinstance(data, str):
        return data if "<tt" in data or "&lt;tt" in data else None
    items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else ()
    for v in items:
        found = _find_ttml(v)
        if found:
            return found
    return None


def better_lyrics(title, artist, album, duration):
    data = get_json("https://lyrics-api.boidu.dev/getLyrics",
                    {"s": title, "a": artist, "al": album, "d": round(duration) or None})
    return _from_ttml(_find_ttml(data), duration)


def bini_lyrics(title, artist, album, duration):
    data = get_json("https://lyrics-api.binimum.org/",
                    {"track": title, "artist": artist, "album": album, "duration": round(duration) or None})
    hits = (data or {}).get("results") if isinstance(data, dict) else None
    if not hits or not isinstance(hits[0], dict) or not hits[0].get("lyricsUrl"):
        return NONE
    return _from_ttml(get(hits[0]["lyricsUrl"]), duration, hits[0].get("duration"))


def deezer_isrc(title, artist, duration):
    data = get_json("https://api.deezer.com/search", {"q": f'artist:"{artist}" track:"{title}"'})
    hits = [h for h in (data or {}).get("data") or [] if isinstance(h, dict) and h.get("id")]
    if duration:
        hits = sorted((h for h in hits if abs((h.get("duration") or 0) - duration) <= TOLERANCE),
                      key=lambda h: abs((h.get("duration") or 0) - duration))
    if not hits:
        return None
    isrc = str((get_json(f"https://api.deezer.com/track/{int(hits[0]['id'])}") or {}).get("isrc") or "").upper()
    return isrc if _ISRC.match(isrc) else None


def lrc_red(title, artist, album, duration):
    isrc = deezer_isrc(title, artist, duration)
    return _from_ttml(get(f"https://lrc.red/s/{isrc}.ttml"), duration) if isrc else NONE


def youly_line(item):
    """Línea de LyricsPlus v2 → modelo (las sílabas se unen en palabras como en TTML)."""
    t = (item.get("time") or 0) / 1000
    end = t + (item.get("duration") or 0) / 1000
    syl = [s for s in item.get("syllabus") or [] if isinstance(s, dict) and s.get("text")]
    if syl:
        pieces = [[(s.get("time") or 0) / 1000, ((s.get("time") or 0) + (s.get("duration") or 0)) / 1000,
                   s["text"], bool(s.get("isBackground")), False] for s in syl]
        words = ttml._words(pieces)
    else:
        words = ttml.spread(t, end if end > t else t + 6, (item.get("text") or "").split())
    singer = (item.get("element") or {}).get("singer")
    start = words[0][0] if words else t
    if item.get("time") is not None:
        start = min(start, t)
    return {"t": start, "end": max([end] + [w[1] for w in words]),
            "agent": singer if singer in ttml.AGENTS else None, "words": words}


def youly_plus(title, artist, album, duration):
    params = {"title": title, "artist": artist, "album": album, "duration": round(duration) or None}
    for server in YOULY:  # espejos: el primero que responda
        try:
            data = get_json(server + "/v2/lyrics/get", params)
            break
        except OSError:
            if server == YOULY[-1]:
                raise
    items = (data or {}).get("lyrics") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return NONE
    lines = sorted((youly_line(i) for i in items if isinstance(i, dict)), key=lambda line: line["t"])
    lines = [line for line in lines if line["words"]]
    level = 2 if any(i.get("syllabus") for i in items if isinstance(i, dict)) else 1
    return (lines, level) if lines and fits(lines, duration) else NONE


# orden = prioridad en caso de empate de nivel
PROVIDERS = [("BetterLyrics", better_lyrics), ("BiniLyrics", bini_lyrics), ("LrcRed", lrc_red),
             ("YouLy+", youly_plus)]
