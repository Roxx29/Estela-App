"""Letras sincronizadas: limpieza de títulos, proveedores en paralelo, caché y tiempos por palabra (sin Qt)."""
import bisect
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

from . import providers, ttml

API = "https://lrclib.net/api/"
CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BrilloMusical", "lyrics2")  # caché v2
TOTAL_S = 8.0  # espera máxima a todos los proveedores
MISS_TTL = 24 * 3600  # "sin letra" se vuelve a buscar al día siguiente

_JUNK = re.compile(
    r"\s*[\(\[][^\)\]]*\b(official|video|audio|lyrics?|letra|visuali[sz]er|mv|hd|4k|remaster\w*)\b[^\)\]]*[\)\]]",
    re.I,
)
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


def clean(title, artist):
    title = _JUNK.sub("", title).strip()
    artist = re.sub(r"\s*-\s*Topic$|VEVO$", "", artist.strip()).strip()
    if " - " in title and (not artist or title.lower().startswith(artist.lower() + " - ")):
        artist, title = (p.strip() for p in title.split(" - ", 1))
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
    data = providers.get_json(API + endpoint, params)  # timeout y límite de lectura de providers
    if data is None:
        raise urllib.error.HTTPError(API + endpoint, 404, "Not Found", None, None)
    return data


def _query(title, artist, album, duration):
    try:
        item = _get("get", {"track_name": title, "artist_name": artist, "album_name": album, "duration": round(duration)})
        if item.get("syncedLyrics"):
            log.debug("Letra de get, diferencia de duración %.1f s", (item.get("duration") or 0) - duration)
            return parse_lrc(item["syncedLyrics"])
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    try:
        found = [i for i in _get("search", {"track_name": title, "artist_name": main_artist(artist)})
                 if isinstance(i, dict) and i.get("syncedLyrics")]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        found = []
    if duration:  # la versión de duración más parecida (otra versión = letra desfasada)
        found = sorted((i for i in found if abs((i.get("duration") or 0) - duration) <= 3),
                       key=lambda i: abs((i.get("duration") or 0) - duration))
    if found:
        log.debug("Letra de search, diferencia de duración %.1f s", (found[0].get("duration") or 0) - duration)
        return parse_lrc(found[0]["syncedLyrics"])
    return []


def lrclib(title, artist, album, duration):
    pairs = _query(title, artist, album, duration)
    return from_lrc(pairs), (2 if any(_tagged(text) for _, text in pairs) else 1)


def search(title, artist, album, duration, on_update=None, jobs=None):
    """Todos los proveedores en paralelo (5 s cada uno, 8 s en total). Gana el nivel más alto; empate = orden.

    on_update(líneas, fuente) se llama desde otro hilo: con LRCLIB en cuanto llega y otra vez con cada letra
    más detallada (quien la muestra la aplica al empezar la siguiente estrofa).
    Devuelve (líneas, nivel, fuente, {proveedores con fallo de red o sin respuesta a tiempo}).
    """
    jobs = jobs or providers.PROVIDERS + [("LRCLIB", lrclib)]
    best, failed = None, set()  # best = (líneas, nivel, fuente, prioridad)
    pool = ThreadPoolExecutor(len(jobs))
    futures = {pool.submit(fn, title, artist, album, duration): (prio, name) for prio, (name, fn) in enumerate(jobs)}
    try:
        for f in as_completed(futures, timeout=TOTAL_S):
            prio, name = futures[f]
            try:
                lines, level = f.result()
            except OSError as e:
                log.debug("%s: fallo de red (%s)", name, e)
                failed.add(name)
                continue
            except Exception:  # noqa: BLE001 (un proveedor roto no tumba a los demás)
                log.warning("%s: respuesta no válida", name, exc_info=True)
                continue
            if lines and (best is None or (level, -prio) > (best[1], -best[3])):
                best = (lines, level, name, prio)
                log.debug("Letra de %s (nivel %d)", name, level)
                if on_update:
                    on_update(lines, name)
            if best and best[1] == 2 and all(g.done() for g, (p, _) in futures.items() if p < best[3]):
                break  # ningún pendiente puede ganar ya
    except FuturesTimeout:
        failed |= {name for f, (_, name) in futures.items() if not f.done()}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return (best[0], best[1], best[2], failed) if best else ([], 0, None, failed)


def fetch_lines(title, artist, album, duration, on_update=None):
    """Modelo de líneas; [] si no hay letra. Lanza OSError si LRCLIB no responde y nadie trae letra."""
    title, artist = clean(title, artist)
    key = hashlib.sha1(f"{artist}|{title}|{round(duration)}".lower().encode()).hexdigest()
    path = os.path.join(CACHE, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data["v"] == 2 and (data["lines"] or time.time() - os.path.getmtime(path) < MISS_TTL):
            lines = [dict(line, words=[tuple(w) for w in line["words"]]) for line in data["lines"]]
            if lines and on_update:
                on_update(lines, data.get("source"))
            return lines
    except (OSError, ValueError, KeyError, TypeError):
        pass
    lines, level, source, failed = search(title, artist, album, duration, on_update)
    if not lines and "LRCLIB" in failed:
        raise OSError("sin conexión")  # un fallo de red no se guarda como "sin letra"
    # sin duración puede ser otra versión; con fallos quizá faltó una letra mejor: no se guarda
    if not duration or (failed and level < 2):
        return lines
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"v": 2, "level": level, "source": source, "lines": lines}, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass  # sin caché no es grave
    return lines


def fetch(title, artist, album, duration):
    """API anterior: [(segundos, texto)]; los tiempos por palabra van en TimedText. OSError sin conexión."""
    return legacy(fetch_lines(title, artist, album, duration))
