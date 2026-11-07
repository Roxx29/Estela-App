"""TTML de letras (Apple/Amazon) → modelo de línea {t, end, agent, words[(t, end, texto, bg)]}. Solo stdlib.

Nunca lanza: ante cualquier problema devuelve []. Rechaza DOCTYPE/ENTITY (bombas XML) y textos enormes.
"""
import re
import xml.etree.ElementTree as ET

MAX_CHARS = 2_000_000
AGENTS = ("v1", "v2", "v1000")
_UNSAFE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)
_CLOCK = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")
_OFFSET = re.compile(r"^(\d+(?:\.\d+)?)(h|m|s|ms)$")


def _local(name):
    return name.rsplit("}", 1)[-1]


def _attr(el, name):
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def seconds(value):
    """'1:02.5', '01:02:03.4', '12.5s', '800ms', '3.2' → segundos; None si no se entiende."""
    if not value:
        return None
    value = value.strip()
    m = _OFFSET.match(value)
    if m:
        return float(m[1]) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[m[2]]
    m = _CLOCK.match(value)
    if not m:
        return None
    a, b, s = m.groups()
    if b is not None:  # h:m:s
        return int(a) * 3600 + int(b) * 60 + float(s)
    return (int(a) * 60 if a is not None else 0) + float(s)


def spread(t, end, texts):
    """Tiempos estimados por longitud (letra sin tiempos por palabra); ~0,5 s por palabra como mucho."""
    span = max(0.0, min(end - t, 0.5 * len(texts) + 0.5, 6.0))
    weights = [len(w) + 2 for w in texts]
    total, acc, out = sum(weights) or 1, 0, []
    for text, w in zip(texts, weights):
        a = t + span * acc / total
        acc += w
        out.append((a, t + span * acc / total, text, False))
    return out


def _agents(root):
    """xml:id de los ttm:agent → v1/v2/v1000 (los ids raros se mapean por orden y tipo)."""
    out, persons = {}, 0
    for el in root.iter():
        if _local(el.tag) != "agent":
            continue
        aid = _attr(el, "id")
        if not aid:
            continue
        if aid in AGENTS:
            out[aid] = aid
        elif (_attr(el, "type") or "") == "group":
            out[aid] = "v1000"
        else:
            out[aid] = "v1" if persons == 0 else "v2"
            persons += 1
    return out


def _pieces(el, bg, out):
    """Recorre los span con tiempo: (t, end, texto, bg, espacio_detrás)."""
    for child in el:
        if _local(child.tag) != "span":
            continue
        is_bg = bg or _attr(child, "role") == "x-bg"
        t, end = seconds(_attr(child, "begin")), seconds(_attr(child, "end"))
        if t is not None and len(child) == 0:
            out.append([t, end if end is not None else t, child.text or "", is_bg, False])
        else:
            _pieces(child, is_bg, out)
        if out and child.tail and child.tail[:1].isspace():
            out[-1][4] = True


def _words(pieces):
    """Une sílabas sin espacio entre ellas en palabras; marca coros entre paréntesis."""
    words, cur = [], None
    for t, end, text, bg, space in pieces:
        if cur and (text[:1].isspace() or cur[3] != bg):  # espacio o cambio voz principal/coro
            words.append(cur)
            cur = None
        for i, tok in enumerate(text.split()):
            if i and cur:
                words.append(cur)
                cur = None
            if cur is None:
                cur = [t, end, tok, bg]
            else:
                cur[1], cur[2] = end, cur[2] + tok
        if (space or text[-1:].isspace()) and cur:
            words.append(cur)
            cur = None
    if cur:
        words.append(cur)
    depth, out = 0, []
    for t, end, text, bg in words:
        if text.startswith("("):
            depth += 1
        inside = bg or depth > 0
        if text.endswith(")") and depth:
            depth -= 1
        if inside:
            text = text.strip("()") or text
        out.append((t, max(t, end), text, inside))
    return out


def parse(text):
    """Lista de líneas ordenadas por inicio; [] si el TTML no vale."""
    try:
        if not text or len(text) > MAX_CHARS or _UNSAFE.search(text):
            return []
        root = ET.fromstring(text)
        agents = _agents(root)
        lines = []
        for div in root.iter():
            if _local(div.tag) != "div":
                continue
            div_agent = _attr(div, "agent")
            for p in div:
                if _local(p.tag) != "p":
                    continue
                t, end = seconds(_attr(p, "begin")), seconds(_attr(p, "end"))
                pieces = []
                _pieces(p, False, pieces)
                if pieces:
                    words = _words(pieces)
                else:
                    if t is None:
                        continue
                    words = spread(t, end if end is not None else t + 6, "".join(p.itertext()).split())
                if not words:
                    continue
                t = words[0][0] if t is None else min(t, words[0][0])
                end = max([w[1] for w in words] + ([end] if end is not None else []))
                agent = _attr(p, "agent") or div_agent
                lines.append({"t": t, "end": end, "agent": agents.get(agent, agent if agent in AGENTS else None),
                              "words": words})
            if lines and lines[-1]["words"]:  # fin de estrofa (div) = línea vacía, como en LRC
                lines.append({"t": lines[-1]["end"], "end": lines[-1]["end"], "agent": None, "words": []})
        while lines and not lines[-1]["words"]:
            lines.pop()
        return sorted(lines, key=lambda line: line["t"])
    except Exception:  # noqa: BLE001 (contrato: nunca lanza)
        return []
