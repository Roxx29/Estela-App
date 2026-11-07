import colorsys
import unittest

from PIL import Image

from brillomusical import color


def hsv(rgb):
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))


class TestColor(unittest.TestCase):
    def test_gris_da_ambar(self):
        self.assertEqual(color.dominant(Image.new("RGB", (50, 50), (128, 128, 128))), color.FALLBACK)

    def test_negro_con_rojo_elige_rojo(self):
        img = Image.new("RGB", (100, 100), (5, 5, 5))
        img.paste((200, 30, 30), (0, 0, 100, 30))
        r, g, b = color.dominant(img)
        self.assertTrue(r > g and r > b)

    def test_resultado_saturado_y_luminoso(self):
        h, s, v = hsv(color.dominant(Image.new("RGB", (10, 10), (60, 80, 50))))
        self.assertGreaterEqual(s, 0.54)
        self.assertGreaterEqual(v, 0.74)

    def test_acepta_bytes(self):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (0, 0, 200)).save(buf, "PNG")
        r, g, b = color.from_bytes(buf.getvalue())[0][0]
        self.assertTrue(b > r and b > g)

    def test_backdrop_textura_difuminada(self):
        import io
        img = Image.new("RGB", (300, 200), (0, 0, 0))
        img.paste((255, 255, 255), (0, 0, 150, 200))  # mitad blanca, mitad negra: el borde se suaviza
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data, lum = color.backdrop(buf.getvalue())
        self.assertEqual(len(data), color.BACKDROP * color.BACKDROP * 3)
        self.assertTrue(0.3 < lum < 0.7)
        mid = Image.frombytes("RGB", (color.BACKDROP, color.BACKDROP), data).getpixel((color.BACKDROP // 2, 64))
        self.assertTrue(30 < mid[0] < 225)  # gris en la frontera: está difuminado

    def test_caratula_cuadrada_ampliada(self):
        import io
        buf = io.BytesIO()
        img = Image.new("RGB", (160, 120), (0, 0, 255))
        img.paste((255, 0, 0), (0, 0, 20, 120))  # franja que el recorte centrado debe quitar
        img.save(buf, "PNG")
        data, side = color.cover(buf.getvalue())
        self.assertEqual(side, 256)
        self.assertEqual(len(data), side * side * 3)
        px = Image.frombytes("RGB", (side, side), data).getpixel((2, 128))
        self.assertGreater(px[2], px[0])  # el borde izquierdo ya es azul: recortado

    def test_caratula_grande_se_limita_a_1024(self):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (1400, 1400), (10, 200, 10)).save(buf, "PNG")
        self.assertEqual(color.cover(buf.getvalue())[1], 1024)

    def test_calido(self):
        self.assertTrue(color.is_warm((255, 120, 40)))
        self.assertTrue(color.is_warm((255, 40, 120)))  # tono > 330°
        self.assertFalse(color.is_warm((60, 120, 255)))

    def test_paleta_gris_da_ambar(self):
        self.assertEqual(color.palette(Image.new("RGB", (20, 20), (128, 128, 128))), [color.FALLBACK])

    def test_paleta_dos_colores(self):
        img = Image.new("RGB", (100, 100), (200, 30, 30))
        img.paste((30, 30, 200), (0, 0, 100, 50))
        pal = color.palette(img)
        self.assertEqual(len(pal), 2)

    def test_paleta_maximo_3_sin_grises(self):
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        for i, c in enumerate([(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]):
            img.paste(c, (0, i * 20, 100, i * 20 + 20))
        pal = color.palette(img)
        self.assertEqual(len(pal), 3)
        for rgb in pal:
            self.assertGreaterEqual(hsv(rgb)[1], 0.54)

    def test_from_bytes_aspecto(self):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (160, 90), (0, 0, 200)).save(buf, "PNG")
        self.assertAlmostEqual(color.from_bytes(buf.getvalue())[1], 160 / 90)


class TestDuet(unittest.TestCase):
    dark = (20, 10, 10)

    def hue_deg(self, rgb):
        return hsv(rgb)[0] * 360

    def test_segundo_de_la_paleta_si_es_distinto(self):
        v2 = color.duet([(230, 40, 40), (40, 90, 240)], self.dark)
        self.assertAlmostEqual(self.hue_deg(v2), self.hue_deg((40, 90, 240)), delta=3)
        self.assertGreaterEqual(color.contrast(v2, self.dark), 4.5)

    def test_sin_segundo_color_gira_150(self):
        v2 = color.duet([(230, 40, 40)], self.dark)
        self.assertAlmostEqual(self.hue_deg(v2), 150, delta=4)
        self.assertGreaterEqual(color.contrast(v2, self.dark), 4.5)

    def test_parecido_no_vale_y_se_aclara_hasta_el_contraste(self):
        dark = (30, 30, 120)  # fondo azul: un azul oscuro no se leería
        v2 = color.duet([(230, 40, 40), (240, 80, 40)], dark)  # 2.º color a <60°: se gira
        self.assertGreaterEqual(color.contrast(v2, dark), 4.5)
        self.assertEqual(color.duet([(255, 255, 0)], (250, 250, 250)), (255, 255, 255))  # imposible: blanco

    def test_contraste_wcag(self):
        self.assertAlmostEqual(color.contrast((255, 255, 255), (0, 0, 0)), 21, places=3)
        self.assertEqual(color.mix((0, 0, 0), (255, 255, 255), 0.5), (128, 128, 128))


if __name__ == "__main__":
    unittest.main()
