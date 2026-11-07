"""Dibujo de la constelación sin pantalla (Qt offscreen): dúo, coros, karaoke, cuenta atrás y franja reservada."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from brillomusical import constellation  # noqa: E402


class Settings(dict):
    def value(self, key, default=None, type=None):  # noqa: A002 (misma firma que QSettings)
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self[key] = value


class Frames(QObject):
    tick = Signal()

    def want(self, name, on):
        pass


def words(t, texts, bg=False, step=0.4):
    return [(t + i * step, t + (i + 1) * step - 0.05, w, bg) for i, w in enumerate(texts)]


MODEL = [
    {"t": 0.0, "end": 2.0, "agent": "v1", "words": words(0.0, "yo te quiero tanto".split())
     + words(1.6, ["(oh)"], bg=True)},
    {"t": 1.0, "end": 3.0, "agent": "v2", "words": words(1.0, "y yo a ti".split())},
    {"t": 3.2, "end": 4.6, "agent": "v1000", "words": words(3.2, "los dos juntos".split())},
    {"t": 4.6, "end": 4.6, "agent": None, "words": []},
    {"t": 14.0, "end": 16.0, "agent": "v1", "words": words(14.0, "otra vez".split())},
]


def snap(pos):
    return {"title": "Canción", "artist": "Dúo", "album": "Disco", "duration": 200, "ad": False,
            "color": (230, 60, 60), "palette": [(230, 60, 60), (60, 110, 240)], "backdrop": None, "cover": None,
            "art_id": None, "playing": False, "pos": pos, "stamp": time.time(), "stamp_ok": True, "rate": 1.0}


class TestConstellationPaint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.win = constellation.LyricsWindow(Settings(help_shown=True), Frames())
        self.win.resize(1280, 720)
        self.win.set_lyrics("", MODEL)
        self.pm = QPixmap(1280, 720)

    def paint(self, pos):
        self.win.set_snapshot(snap(pos))
        self.win.render(self.pm)

    def test_duo_coros_karaoke_y_cuenta_atras(self):
        self.assertTrue(self.win.duet)
        for pos in (0.1, 0.5, 1.2, 1.7, 2.5, 3.5, 4.4, 8.0, 11.5, 12.5, 13.5, 14.2):
            self.paint(pos)
        v2 = self.win._voice("v2")
        self.assertNotEqual(v2, tuple(self.win.rgb))
        self.assertIs(v2, self.win._voice("v2"))  # una vez por canción
        self.assertEqual(self.win.breaks, [(4.6, 14.0)])

    def test_palabra_actual_no_retrocede_por_un_tiron(self):
        self.paint(1.25)
        cur = self.win.cur_state[1]
        self.paint(1.0)  # 0,25 s atrás (p. ej. el desfase): no retrocede
        self.assertEqual(self.win.cur_state[1], cur)

    def test_franja_reservada_sin_recolocar(self):
        self.paint(2.5)
        pts = self.win._stanza(0)[1]
        x0 = self.win._px(pts[1])[0]
        self.win.reserve_right(320)
        self.paint(2.5)
        self.assertIs(self.win._stanza(0)[1], pts)  # misma colocación
        self.assertLessEqual(self.win._px(pts[1])[0], max(x0, 1280 - 320))
        self.assertTrue(all(self.win._px(p)[0] <= 1280 - 320 for p in pts))

    def test_presupuesto_por_fotograma(self):
        for pos in (0.1, 0.5):  # calentar cachés (fondo, halos, etiquetas)
            self.paint(pos)
        n, t0 = 60, time.perf_counter()
        for i in range(n):
            self.paint(0.5 + 3.5 * i / n)
        ms = (time.perf_counter() - t0) / n * 1000
        print(f"\nconstelación: {ms:.2f} ms/fotograma (offscreen, 1280×720)")
        self.assertLess(ms, 16)  # holgado en CI; el objetivo en la app es < 4 ms


if __name__ == "__main__":
    unittest.main()
