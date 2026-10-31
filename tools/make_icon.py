"""Dibuja el icono de Estela (estrella de 4 puntas dentro de un aro azul estrella → cian) en assets/icon.ico."""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

SIZE = 256
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico")


def star(cx, cy, r_out, r_in, points=4):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / points - math.pi / 2
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def logo(size):
    """Dibujado a 4× y reducido: bordes suaves también a 16 px."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    width = max(4, big // (8 if size <= 24 else 12))  # a 16 px el aro necesita más grosor para verse
    for i in range(big):  # degradado de arriba a abajo: azul estrella #4DA3FF → cian #2A7FA8
        t = i / big
        d.line([(0, i), (big, i)], fill=(round(0x4D + (0x2A - 0x4D) * t), round(0xA3 + (0x7F - 0xA3) * t),
                                         round(0xFF + (0xA8 - 0xFF) * t), 255))
    mask = Image.new("L", (big, big), 0)
    m = ImageDraw.Draw(mask)
    pad = width // 2 + big // 32
    m.ellipse((pad, pad, big - pad, big - pad), outline=255, width=width)
    img.paste(ring, (0, 0), mask)
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(star(big / 2, big / 2, big * 0.40, big * 0.11), fill=(77, 163, 255, 160))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(big / 40)))
    ImageDraw.Draw(img).polygon(star(big / 2, big / 2, big * 0.36, big * 0.09), fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


SIZES = (16, 24, 32, 48, 64, 128, 256)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
images = [logo(s) for s in SIZES]
images[-1].save(OUT, sizes=[(s, s) for s in SIZES], append_images=images[:-1])
print("Icono:", OUT)
