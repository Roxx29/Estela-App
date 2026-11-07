"""Constelación: agrupación en estrofas y colocación aleatoria sin solapes (sin Qt)."""
import math
import random

from . import lyrics

ASPECT = 16 / 9  # distancias medidas como si la ventana fuera 16:9
MARGIN = 0.08
MIN_DIST = 0.08
STEP = (0.12, 0.45)  # distancia de cada palabra a la anterior
TRIES = 30
MAX_VISIBLE = 24
MAX_LINES = 4
GAP = 5.0  # segundos de silencio que cortan la estrofa
BG_MAX = 6  # coros visibles a la vez (dentro de MAX_VISIBLE)
SAT = (0.045, 0.07)  # distancia del coro (estrella satélite) a su palabra
VOICE_X = {"v1": (0.08, 0.62), "v2": (0.38, 0.92), "v1000": (0.30, 0.70)}  # franjas de cada cantante en un dúo
BREAK = 8.0  # hueco = siguiente.t − fin ≥ 8 s
COUNTDOWN = 3  # puntos de la cuenta atrás (últimos 3 s del hueco)
JUMP = 1.0  # la palabra actual solo retrocede si la posición salta más de 1 s


def stanzas(lines):
    """Listas de índices de líneas no vacías: corte en línea vacía, hueco largo o más de 4 líneas."""
    groups, cur = [], []
    for i, (t, text) in enumerate(lines):
        if not text.strip():
            if cur:
                groups.append(cur)
            cur = []
            continue
        if cur:
            pt, ptext = lines[cur[-1]]
            if t - pt > GAP + 0.4 * len(ptext.split()):  # la línea anterior ya acabó hace rato
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        k = math.ceil(len(g) / MAX_LINES)
        out += [g[round(i * len(g) / k):round((i + 1) * len(g) / k)] for i in range(k)]
    return out


def stanza_of(groups, idx):
    """Estrofa que se ve en la línea `idx` (una línea vacía mantiene la anterior)."""
    found = None
    for n, g in enumerate(groups):
        if g[0] <= idx:
            found = n
    return found


def _dist(a, b):
    return math.hypot((a[0] - b[0]) * ASPECT, a[1] - b[1])


def arrange(words, seed, avoid=None, duet=False):
    """Coloca palabras [(voz, coro)] en orden de canto → (puntos (x, y) en 0..1, enlaces).

    Cada cantante tiene su cadena: cada palabra cerca de la anterior de su voz y lejos de todas.
    En un dúo v1 va a la izquierda, v2 a la derecha y v1000 al centro. Un coro es una estrella
    satélite de la última palabra principal de su voz. enlaces[i] = índice al que se une i (None = ninguno).
    avoid: rectángulo (x0, y0, x1, y1) normalizado donde no puede caer ninguno (p. ej. la tarjeta).
    """
    rnd = random.Random(seed)
    pts, links, last = [], [], {}
    full = (MARGIN, 1 - MARGIN)

    def inside(p, xr):
        if not (xr[0] <= p[0] <= xr[1] and MARGIN <= p[1] <= 1 - MARGIN):
            return False
        return not avoid or not (avoid[0] <= p[0] <= avoid[2] and avoid[1] <= p[1] <= avoid[3])

    def free(p):
        return min((_dist(p, q) for q in pts), default=1.0)

    def anywhere(xr):
        for _ in range(100):
            p = rnd.uniform(*xr), rnd.uniform(MARGIN, 1 - MARGIN)
            if inside(p, xr):
                return p
        return (xr[0] + xr[1]) / 2, MARGIN  # zona reservada absurda: arriba

    def near(q, step, xr, min_d):
        for _ in range(TRIES):
            a, r = rnd.uniform(0, 2 * math.pi), rnd.uniform(*step)
            p = (q[0] + r * math.cos(a) / ASPECT, q[1] + r * math.sin(a))
            if inside(p, xr) and free(p) >= min_d:
                return p
        return None

    for voice, bg in words:
        chain = voice if duet else None
        xr = VOICE_X.get(voice, VOICE_X["v1"]) if duet else full
        prev = last.get(chain)
        if bg and prev is not None:  # satélite: pegado a su palabra, puede estar algo más cerca de otras
            p = near(pts[prev], SAT, full, MIN_DIST / 2) or near(pts[prev], SAT, full, 0.0) or pts[prev]
            links.append(prev)
            pts.append(p)
            continue
        p = near(pts[prev], STEP, xr, MIN_DIST) if prev is not None else anywhere(xr)
        if p is None:  # sin sitio cerca: el punto libre más lejano de unos cuantos al azar
            p = max((anywhere(xr) for _ in range(TRIES)), key=free)
        links.append(prev)
        if not bg:
            last[chain] = len(pts)
        pts.append(p)
    return pts, links


def place(n, seed, avoid=None):
    """n puntos (x, y) en 0..1, cada uno cerca del anterior y lejos de todos los demás."""
    return arrange([(None, False)] * n, seed, avoid)[0]


def squeeze(x, anchor, frac):
    """x normalizada con una franja derecha reservada de `frac` del ancho (sin rehacer la colocación).

    Lo que está a la izquierda de `anchor` (p. ej. sobre la tarjeta) no se mueve; el resto se comprime.
    """
    if frac <= 0 or x <= anchor or anchor >= 1:
        return x
    k = max(0.2, (1 - frac - anchor) / (1 - anchor))
    return anchor + (x - anchor) * k


def _words(text):
    return {w.strip(".,;:!?¡¿\"'()").lower() for w in text.split()} - {""}


def chorus(lines, groups, similar=0.8):
    """Estrofas que se repiten en la canción (estribillos): parecido de palabras (Jaccard) ≥ 0,8."""
    sets = [set().union(*(_words(lines[i][1]) for i in g)) for g in groups]
    out = set()
    for a in range(len(sets)):
        for b in range(a + 1, len(sets)):
            if sets[a] and sets[b] and len(sets[a] & sets[b]) / len(sets[a] | sets[b]) >= similar:
                out |= {a, b}
    return out


def to_model(lines):
    """Acepta el modelo {t, end, agent, words} o la API anterior [(t, texto)] y devuelve el modelo."""
    if not lines or isinstance(lines[0], dict):
        return list(lines)
    model = lyrics.from_lrc(lines)
    for line, (_, text) in zip(model, lines):
        times = getattr(text, "times", ())
        if times and len(times) == len(text.split()):  # tiempos exactos de lyrics.legacy
            ends = list(times[1:]) + [min(line["end"], times[-1] + 0.6)]
            line["words"] = [(a, max(a, b), w, False) for a, b, w in zip(times, ends, text.split())]
        if line["words"]:  # fin = el de la última palabra (from_lrc pone el inicio de la siguiente línea)
            line["end"] = max(w[1] for w in line["words"])
    return model


def breaks(model, min_s=BREAK):
    """Huecos sin cantar: [(fin de lo cantado, inicio de la siguiente línea)] con siguiente.t − fin ≥ min_s."""
    sung = [line for line in to_model(model) if line["words"]]
    out = []
    if sung and sung[0]["t"] >= min_s:  # intro larga
        out.append((0.0, sung[0]["t"]))
    end = None
    for a, b in zip(sung, sung[1:]):
        end = a["end"] if end is None else max(end, a["end"])  # en un dúo las líneas se solapan
        if b["t"] - end >= min_s:
            out.append((end, b["t"]))
    return out


def gaps(model, min_s=12.0):
    """Instrumentales largos (≥ min_s): [(desde, hasta)]; 2 s tras lo cantado y hasta 1,5 s antes de la siguiente."""
    return [(a + 2.0, b - 1.5) for a, b in breaks(model, min_s)]


def countdown(brks, pos):
    """Puntos encendidos de la cuenta atrás (3, 2, 1) en los últimos segundos de un hueco; 0 si no toca."""
    for a, b in brks:
        if b - COUNTDOWN <= pos < b and pos >= a:
            return math.ceil(b - pos)
    return 0


def advance(prev, prev_pos, cur, pos):
    """La palabra actual solo avanza; retrocede únicamente si la posición salta más de JUMP segundos."""
    if prev is None or cur >= prev or abs(pos - prev_pos) > JUMP:
        return cur
    return prev


def visible(indices, cur, bg=None):
    """Palabras ya cantadas hasta `cur`, como mucho MAX_VISIBLE (y BG_MAX coros); sobran → fuera los coros más viejos."""
    upto = [i for i in indices if i <= cur]
    if bg:
        bgs = [i for i in upto if bg[i]]
        drop = bgs[:-BG_MAX] if len(bgs) > BG_MAX else []
        over = len(upto) - len(drop) - MAX_VISIBLE
        if over > 0:
            drop += bgs[len(drop):len(drop) + over]
        if drop:
            drop = set(drop)
            upto = [i for i in upto if i not in drop]
    return upto[-MAX_VISIBLE:]
