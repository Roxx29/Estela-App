import math
import unittest

from brillomusical import layout


def lrc(*items):
    return list(items)


class TestStanzas(unittest.TestCase):
    def test_corta_en_linea_vacia(self):
        lines = lrc((0, "a b"), (2, "c d"), (4, ""), (5, "e f"))
        self.assertEqual(layout.stanzas(lines), [[0, 1], [3]])

    def test_corta_en_hueco_largo(self):
        lines = lrc((0, "a"), (2, "b"), (20, "c"))
        self.assertEqual(layout.stanzas(lines), [[0, 1], [2]])

    def test_maximo_4_lineas_repartidas(self):
        lines = [(i * 2.0, "x y") for i in range(6)]
        self.assertEqual(layout.stanzas(lines), [[0, 1, 2], [3, 4, 5]])

    def test_sin_letra_y_una_linea(self):
        self.assertEqual(layout.stanzas([]), [])
        self.assertEqual(layout.stanzas([(1, "hola")]), [[0]])

    def test_stanza_of(self):
        st = [[0, 1], [3]]
        self.assertEqual(layout.stanza_of(st, 1), 0)
        self.assertEqual(layout.stanza_of(st, 2), 0)  # línea vacía: sigue la estrofa anterior
        self.assertEqual(layout.stanza_of(st, 3), 1)
        self.assertIsNone(layout.stanza_of(st, -1))


class TestPlace(unittest.TestCase):
    def test_determinista_y_distinta_con_otra_semilla(self):
        self.assertEqual(layout.place(20, 7), layout.place(20, 7))
        self.assertNotEqual(layout.place(20, 7), layout.place(20, 8))

    def test_dentro_de_margenes(self):
        for x, y in layout.place(30, 3):
            self.assertTrue(layout.MARGIN <= x <= 1 - layout.MARGIN)
            self.assertTrue(layout.MARGIN <= y <= 1 - layout.MARGIN)

    def test_sin_solapes(self):
        pts = layout.place(24, 11)
        a = layout.ASPECT
        for i, (x1, y1) in enumerate(pts):
            for x2, y2 in pts[:i]:
                self.assertGreaterEqual(math.hypot((x1 - x2) * a, y1 - y2), layout.MIN_DIST - 1e-9)

    def test_evita_la_zona_reservada(self):
        avoid = (0.0, 0.7, 0.4, 1.0)  # esquina inferior izquierda (tarjeta)
        for seed in range(200):
            pts = layout.place(24, seed, avoid)
            self.assertEqual(len(pts), 24)
            for x, y in pts:
                self.assertFalse(avoid[0] <= x <= avoid[2] and avoid[1] <= y <= avoid[3], (seed, x, y))
        self.assertEqual(layout.place(10, 3, avoid), layout.place(10, 3, avoid))

    def test_visible_tope(self):
        self.assertEqual(layout.visible(range(40), 30), list(range(7, 31)))
        self.assertEqual(layout.visible(range(5), 2), [0, 1, 2])


class TestSongShape(unittest.TestCase):
    lines = [(0, "verso uno aqui"), (3, "sigue el verso"), (6, ""),
             (8, "este es el coro"), (10, "canta el coro"), (12, ""),
             (14, "otro verso distinto"), (17, ""),
             (19, "este es el coro"), (21, "canta el coro")]

    def test_estribillos(self):
        groups = layout.stanzas(self.lines)
        self.assertEqual(layout.chorus(self.lines, groups), {1, 3})

    def test_huecos_instrumentales(self):
        lines = [(20.0, "empieza tarde"), (23.0, "otra"), (45.0, "tras el solo"), (47.0, "")]
        gaps = layout.gaps(lines, 12)
        self.assertEqual(len(gaps), 2)
        self.assertAlmostEqual(gaps[0][0], 2.0)  # intro
        self.assertAlmostEqual(gaps[0][1], 18.5)
        self.assertTrue(23 < gaps[1][0] < 30 and abs(gaps[1][1] - 43.5) < 1e-9)
        self.assertEqual(layout.gaps([(1, "a"), (3, "b")], 12), [])


def line(t, end, agent=None, words=None):
    return {"t": t, "end": end, "agent": agent, "words": words or [(t, end, "x", False)]}


class TestModelo(unittest.TestCase):
    def test_huecos_con_fin_real_y_duo_solapado(self):
        model = [line(0, 10, "v1"), line(2, 4, "v2"), line(17, 18), line(30, 31)]
        # 10 → 17 = 7 s (no llega); el v2 acaba antes que el v1: cuenta el fin más tardío
        self.assertEqual(layout.breaks(model), [(18, 30)])
        self.assertEqual(layout.breaks([line(9, 10)]), [(0.0, 9)])  # intro ≥ 8 s
        self.assertEqual(layout.gaps(model, 12), [(20.0, 28.5)])

    def test_cuenta_atras(self):
        brk = [(18, 30)]
        self.assertEqual([layout.countdown(brk, p) for p in (26.9, 27.0, 28.5, 29.5, 30.0)], [0, 3, 2, 1, 0])

    def test_palabra_actual_solo_avanza(self):
        self.assertEqual(layout.advance(5, 10.0, 4, 9.9), 5)  # tirón hacia atrás pequeño
        self.assertEqual(layout.advance(5, 10.0, 2, 8.5), 2)  # salto > 1 s
        self.assertEqual(layout.advance(5, 10.0, 6, 10.1), 6)
        self.assertEqual(layout.advance(None, 0.0, 3, 5.0), 3)

    def test_visibles_con_coros(self):
        bg = [i % 2 == 1 for i in range(40)]  # impares = coros
        vis = layout.visible(range(40), 39, bg)
        self.assertEqual(len(vis), 24)
        # 6 coros como mucho; aun así sobran 2 → fuera los 2 coros más viejos (no palabras principales)
        self.assertEqual([i for i in vis if bg[i]], [33, 35, 37, 39])
        self.assertEqual(sum(not bg[i] for i in vis), 20)
        vis = layout.visible(range(20), 19, [i % 2 == 1 for i in range(20)])
        self.assertEqual([i for i in vis if i % 2], [9, 11, 13, 15, 17, 19])  # cabe todo: los 6 más nuevos
        bg = [i >= 20 for i in range(26)]  # 20 principales + 6 coros = 26 > 24: fuera los 2 coros más viejos
        self.assertEqual(layout.visible(range(26), 25, bg), list(range(20)) + [22, 23, 24, 25])

    def test_duo_cada_voz_en_su_franja_y_su_cadena(self):
        words = [("v1", False), ("v2", False), ("v1", False), ("v1", True), ("v2", False), ("v1000", False)] * 3
        pts, links = layout.arrange(words, 5, duet=True)
        for (voice, bg), p, ln in zip(words, pts, links):
            if not bg:
                lo, hi = layout.VOICE_X[voice]
                self.assertTrue(lo <= p[0] <= hi, (voice, p))
            if ln is not None:
                self.assertEqual(words[ln][0], voice)  # cada cantante su cadena
                self.assertFalse(words[ln][1])  # se une a una palabra principal
        self.assertIsNone(links[0])
        self.assertEqual(links[3], 2)  # el coro, satélite de la palabra anterior de su voz
        self.assertLess(math.hypot((pts[3][0] - pts[2][0]) * layout.ASPECT, pts[3][1] - pts[2][1]), 0.08)

    def test_franja_reservada(self):
        self.assertEqual(layout.squeeze(0.9, 0.0, 0.0), 0.9)
        self.assertAlmostEqual(layout.squeeze(1.0, 0.0, 0.25), 0.75)
        self.assertEqual(layout.squeeze(0.2, 0.3, 0.25), 0.2)  # a la izquierda de la tarjeta no se mueve
        self.assertAlmostEqual(layout.squeeze(1.0, 0.3, 0.25), 0.75)

    def test_api_anterior_a_modelo(self):
        model = layout.to_model([(1.0, "hola mundo"), (5.0, "")])
        self.assertEqual(model[0]["agent"], None)
        self.assertEqual([w[2] for w in model[0]["words"]], ["hola", "mundo"])
        self.assertEqual(model[1]["words"], [])
        same = [line(0, 1)]
        self.assertEqual(layout.to_model(same), same)


if __name__ == "__main__":
    unittest.main()
