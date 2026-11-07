import unittest

from brillomusical import ttml

DOC = """<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">
<head><metadata><ttm:agent type="person" xml:id="v1"/><ttm:agent type="person" xml:id="v2"/><ttm:agent type="group" xml:id="v1000"/></metadata></head>
<body dur="3:00.000">
<div begin="1.000" end="9.000">
<p begin="1.000" end="3.000" ttm:agent="v1"><span begin="1.000" end="1.400">Hel</span><span begin="1.400" end="1.800">lo</span> <span begin="2.000" end="2.500">world</span><span ttm:role="x-bg"><span begin="2.500" end="2.800">(oh</span> <span begin="2.800" end="3.000">yeah)</span></span></p>
<p begin="4.000" end="6.000" ttm:agent="v2"><span begin="4.000" end="4.500">Tu</span> <span begin="4.500" end="5.000">(eco</span> <span begin="5.000" end="5.500">eco)</span> <span begin="5.500" end="6.000">fin</span></p>
</div>
<div begin="10.000" end="12.000">
<p begin="00:10.000" end="00:12.000" ttm:agent="v1000">Todos a la vez</p>
</div>
</body></tt>"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.lines = ttml.parse(DOC)

    def test_silabas_unidas_y_coro_x_bg(self):
        first = self.lines[0]
        self.assertEqual((first["t"], first["end"], first["agent"]), (1.0, 3.0, "v1"))
        self.assertEqual(first["words"], [(1.0, 1.8, "Hello", False), (2.0, 2.5, "world", False),
                                          (2.5, 2.8, "oh", True), (2.8, 3.0, "yeah", True)])

    def test_coro_entre_parentesis(self):
        second = self.lines[1]
        self.assertEqual(second["agent"], "v2")
        self.assertEqual([(w[2], w[3]) for w in second["words"]],
                         [("Tu", False), ("eco", True), ("eco", True), ("fin", False)])

    def test_fin_de_estrofa_y_linea_sin_spans(self):
        self.assertEqual(self.lines[2]["words"], [])  # fin del primer div
        last = self.lines[3]
        self.assertEqual(last["agent"], "v1000")
        self.assertEqual([w[2] for w in last["words"]], ["Todos", "a", "la", "vez"])
        self.assertEqual(last["words"][0][0], 10.0)
        self.assertTrue(all(10.0 <= w[0] < 12.0 for w in last["words"]))
        self.assertEqual(len(self.lines), 4)  # sin línea vacía al final

    def test_agentes_con_otro_nombre(self):
        doc = DOC.replace('"v1"', '"a"').replace('"v2"', '"b"').replace('"v1000"', '"c"')
        self.assertEqual([line["agent"] for line in ttml.parse(doc) if line["words"]], ["v1", "v2", "v1000"])

    def test_tiempos(self):
        self.assertEqual(ttml.seconds("1:02.5"), 62.5)
        self.assertEqual(ttml.seconds("01:00:01.5"), 3601.5)
        self.assertEqual(ttml.seconds("12.5s"), 12.5)
        self.assertEqual(ttml.seconds("800ms"), 0.8)
        self.assertIsNone(ttml.seconds("x"))


class TestSeguridad(unittest.TestCase):
    def test_rechaza_doctype_y_entity(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><tt><body><div><p begin="1" end="2">&lol;</p></div></body></tt>'
        self.assertEqual(ttml.parse(bomb), [])
        self.assertEqual(ttml.parse(DOC.replace("<tt ", "<!ENTITY x 'y'><tt ", 1)), [])

    def test_limite_de_tamano(self):
        self.assertEqual(ttml.parse(DOC + " " * ttml.MAX_CHARS), [])

    def test_nunca_lanza(self):
        for bad in ("", None, "<tt>", "no es xml", "<tt><body><div><p begin='x'>a</p></div></body></tt>", 42):
            self.assertEqual(ttml.parse(bad), [])


if __name__ == "__main__":
    unittest.main()
