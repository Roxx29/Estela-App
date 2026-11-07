"""Colores de la portada, ajustados para que brillen (sin Qt)."""
import colorsys
import io

from PIL import Image, ImageEnhance, ImageFilter, ImageStat

FALLBACK = (255, 179, 71)  # ámbar cuando la portada es gris/negra/blanca
DISTINCT_HUE = 60 / 360  # dos colores de la paleta se distinguen si sus tonos difieren más de 60°


def _tune(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.55), min(max(v, 0.75), 1.0))
    return round(r * 255), round(g * 255), round(b * 255)


def palette(img):
    """Hasta 3 colores vivos y de tonos distintos, el dominante primero."""
    small = img.convert("RGB").resize((64, 64))
    cands = []
    for count, rgb in small.quantize(8).convert("RGB").getcolors(64 * 64):
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        if v < 0.15 or s < 0.2:  # casi negro o gris (incluye casi blanco)
            continue
        cands.append((count * s, h, s, v))
    chosen = []
    for _, h, s, v in sorted(cands, reverse=True):
        if all(min(abs(h - c[0]), 1 - abs(h - c[0])) > DISTINCT_HUE for c in chosen):
            chosen.append((h, s, v))
        if len(chosen) == 3:
            break
    return [_tune(*c) for c in chosen] or [FALLBACK]


def is_warm(rgb):
    """Rojos, naranjas, amarillos y rosas (tono < 50° o > 330°)."""
    h = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0] * 360
    return h < 50 or h > 330


def dominant(img):
    return palette(img)[0]


BACKDROP = 256  # lado de la textura de fondo (aprovecha las portadas HD)
BLUR, SATURATION = 5, 1.25  # difuminada pero reconocible
COVER, COVER_MAX = 256, 1024  # lado mínimo y máximo de la carátula nítida


def _square(data):
    img = Image.open(io.BytesIO(data))
    if max(img.size) > 4000:  # una portada no es tan grande: no descomprimir algo enorme
        raise ValueError(f"portada demasiado grande: {img.size}")
    img = img.convert("RGB")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    return img.crop((left, top, left + side, top + side))


def backdrop(data):
    """Portada para el fondo de las letras: cuadrada, 128×128, difuminada y algo más saturada.

    Devuelve (bytes RGB, luminancia media 0..1).
    """
    img = _square(data).resize((BACKDROP, BACKDROP), Image.LANCZOS)
    img = ImageEnhance.Color(img.filter(ImageFilter.GaussianBlur(BLUR))).enhance(SATURATION)
    return img.tobytes(), ImageStat.Stat(img.convert("L")).mean[0] / 255


def cover(data):
    """Carátula nítida y cuadrada; si es pequeña (Windows da 120 px) se amplía con Lanczos.

    Devuelve (bytes RGB, lado).
    """
    img = _square(data)
    if img.width < COVER or img.width > COVER_MAX:
        side = COVER if img.width < COVER else COVER_MAX
        img = img.resize((side, side), Image.LANCZOS)
    return img.tobytes(), img.width


def from_bytes(data):
    """Devuelve (paleta, ancho/alto) de una imagen en bytes."""
    img = Image.open(io.BytesIO(data))
    return palette(img), img.width / img.height


def mix(a, b, f):
    """a → b en proporción f (0 = a, 1 = b)."""
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


def _lum(rgb):
    lin = [(c / 255) / 12.92 if c <= 10 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    """Contraste WCAG entre dos colores (1..21)."""
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hue(rgb):
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]


def duet(pal, bg, min_contrast=4.5):
    """Color de la 2.ª voz (v2), una vez por canción: el 2.º de la paleta si su tono difiere ≥60° del 1.º,
    si no el 1.º girado +150°; se aclara hacia el blanco hasta tener contraste ≥ 4,5:1 sobre `bg`."""
    v1 = pal[0] if pal else FALLBACK
    h1 = _hue(v1)
    v2 = next((c for c in pal[1:2] if min(abs(_hue(c) - h1), 1 - abs(_hue(c) - h1)) >= DISTINCT_HUE), None)
    if v2 is None:
        _, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in v1))
        v2 = _tune((h1 + 150 / 360) % 1, s, v)
    for k in range(11):
        c = mix(v2, (255, 255, 255), k / 10)
        if contrast(c, bg) >= min_contrast:
            return c
    return 255, 255, 255  # ponytail: fondo demasiado claro, el blanco es lo máximo que se puede dar
