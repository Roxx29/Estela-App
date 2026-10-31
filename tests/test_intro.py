import math
import unittest

from brillomusical import intro


class TestIntro(unittest.TestCase):
    def test_estela_continua_y_acaba_arriba(self):
        prev = intro.head(0.0)
        self.assertEqual(prev, intro.START)
        for i in range(1, 1001):
            pt = intro.head(i / 1000)
            self.assertLess(math.dist(pt, prev), 0.15)  # sin saltos entre fases
            prev = pt
        self.assertAlmostEqual(intro.head(intro.ENTER)[1], -1.0)  # entra por la punta de arriba
        self.assertLess(math.dist(intro.along(1.0), intro.along(0.0)), 1e-9)  # el trazo cierra la estrella

    def test_fases(self):
        a, z = intro.state(0.0), intro.state(1.0)
        self.assertEqual((a["traced"], a["ring"], a["fill"], a["out"]), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual((z["traced"], z["ring"], z["fill"], z["out"]), (1.0, 1.0, 1.0, 1.0))
        self.assertEqual(intro.state(intro.RING)["flash"], 1.0)  # el tac
        self.assertEqual(intro.state(intro.TAC)["out"], 0.0)


if __name__ == "__main__":
    unittest.main()
