import unittest

from brillomusical import room, ytsearch


class TestReloj(unittest.TestCase):
    def test_desfase_con_menor_ida_y_vuelta(self):
        # servidor 1000 ms por delante; la muestra lenta (ida y vuelta 400 ms, asimétrica) se descarta
        samples = [(0, 1300, 400), (500, 1520, 540), (900, 1950, 1000)]
        self.assertAlmostEqual(room.clock_offset(samples), 1000)

    def test_posicion_del_anfitrion(self):
        st = {"posicion": 10.0, "reproduciendo": True, "ts": 5000, "duracion": 200.0}
        self.assertAlmostEqual(room.host_position(st, 7500), 12.5)
        self.assertAlmostEqual(room.host_position({**st, "reproduciendo": False}, 7500), 10.0)
        self.assertAlmostEqual(room.host_position(st, 5000 + 999_000), 200.0)  # no pasa del final

    def test_histeresis(self):
        self.assertEqual(room.sync_action(0.2, False), ("rate", 1.0))  # dentro del margen
        self.assertEqual(room.sync_action(0.5, False), ("rate", 0.92))  # invitado adelantado: frena
        self.assertEqual(room.sync_action(-0.5, False), ("rate", 1.08))
        self.assertEqual(room.sync_action(0.2, True), ("rate", 0.92))  # sigue corrigiendo hasta 0,1
        self.assertEqual(room.sync_action(0.05, True), ("rate", 1.0))
        self.assertEqual(room.sync_action(2.5, True), ("seek", None))  # ≥2 s: corte
        self.assertEqual(room.sync_action(-1.0, False, rate_ok=False), ("seek", None))
        self.assertEqual(room.sync_action(0.5, False, rate_ok=False), ("rate", 1.0))


class TestCancion(unittest.TestCase):
    def test_misma_cancion(self):
        self.assertTrue(room.same_song("Canción (Official Video)", "Rosalía", 200, "cancion", "ROSALÍA, Bad Bunny", 203))
        self.assertTrue(room.same_song("Song feat. X", "A", 0, "Song", "A", 180))
        self.assertFalse(room.same_song("Song", "A", 200, "Song", "A", 260))  # otra versión
        self.assertFalse(room.same_song("Song", "A", 200, "Other", "A", 200))
        self.assertFalse(room.same_song("Song", "A", 200, "Song", "B", 200))

    def test_estado_del_anfitrion_en_rango(self):
        snap = {"title": "t" * 300, "artist": "a", "album": None, "duration": 100.0, "playing": True}
        m = room.host_state(snap, 150.0, "viaje", -5)
        self.assertEqual(len(m["titulo"]), 200)
        self.assertEqual(m["album"], "")
        self.assertEqual(m["posicion"], 100.0)
        self.assertTrue(0 <= m["semilla"] <= 2147483647)

    def test_busqueda_valida_duracion(self):
        def item(vid, title, meta):
            return {"musicResponsiveListItemRenderer": {
                "playlistItemData": {"videoId": vid},
                "flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": title}]}}},
                                {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": meta}]}}}]}}
        data = {"contents": [item("aaaaaaaaaaa", "Live", "Canción • Artista • Álbum • 5:10"),
                             item("bbbbbbbbbbb", "Studio", "Artista • Álbum • 3:21"),
                             {"musicResponsiveListItemRenderer": {"flexColumns": []}}]}
        res = ytsearch.parse(data)
        self.assertEqual([r[0] for r in res], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        self.assertEqual(res[0][2], "Artista")
        self.assertEqual(res[1][3], 201.0)
        self.assertEqual(ytsearch.pick(res, 200.0)[0], "bbbbbbbbbbb")  # la primera no encaja: la siguiente
        self.assertIsNone(ytsearch.pick(res, 100.0))
        self.assertEqual(ytsearch.pick(res, 0)[0], "aaaaaaaaaaa")


class TestTextos(unittest.TestCase):
    def test_invitacion_ida_y_vuelta(self):
        text = room.invite_text("wss://estela.x.workers.dev", "K7P2QX")
        self.assertEqual(room.parse_invite(text), ("wss://estela.x.workers.dev", "K7P2QX"))
        self.assertEqual(room.parse_invite("k7p2qx"), (None, "K7P2QX"))
        self.assertEqual(room.parse_invite("hola"), (None, None))

    def test_servidor(self):
        self.assertEqual(room.normalize_server("https://a.workers.dev/"), "wss://a.workers.dev")
        self.assertEqual(room.normalize_server("wss://a.workers.dev/ws"), "wss://a.workers.dev")
        self.assertIsNone(room.normalize_server("http://a.workers.dev"))  # sin TLS no
        self.assertIsNone(room.normalize_server("ftp://a"))
        self.assertEqual(room.normalize_server("ws://localhost:8787"), "ws://localhost:8787")

    def test_texto_limpio(self):
        self.assertEqual(room.clean_text("ho‮la\x00 ⁦x"), "hola x")
        self.assertEqual(len(room.clean_text("a" * 500)), 300)

    def test_espera_y_formato(self):
        self.assertEqual(room.backoff(0, 0.0), 0.5)
        self.assertEqual(room.backoff(10, 0.999), 30.0 * 1.499)
        self.assertEqual(room.fmt_mss(125.9), "2:05")


class TestRevision(unittest.TestCase):
    def test_errores_fatales(self):
        self.assertFalse(room.is_fatal({"codigo": "sala-llena", "ref": "aprobar"}, True))  # el anfitrión sigue
        self.assertTrue(room.is_fatal({"codigo": "sala-llena", "ref": "unirse"}, False))
        self.assertTrue(room.is_fatal({"codigo": "sala-no-existe", "ref": "reconectar"}, True))
        self.assertTrue(room.is_fatal({"codigo": "limite", "ref": None}, False))  # sin token: no reintentar
        self.assertFalse(room.is_fatal({"codigo": "limite", "ref": "chat"}, True))

    def test_estado_sin_bidi(self):
        snap = {"title": "‮سلام\x00", "artist": "⁧שלום", "album": "a‏b", "duration": 10.0,
                "playing": False}
        m = room.host_state(snap, 1.0, "travel\x07", 1)
        self.assertEqual((m["titulo"], m["artista"], m["album"], m["transicion"]), ("سلام", "שלום", "ab", "travel"))

    def test_estado_remoto_valido(self):
        ok = {"titulo": "t", "artista": "a", "duracion": 200, "posicion": 1.5, "reproduciendo": True, "ts": 1.7e12}
        self.assertTrue(room.valid_state(ok))
        for bad in ({"posicion": float("nan")}, {"duracion": float("inf")}, {"posicion": -1}, {"duracion": 90000},
                    {"ts": "1"}, {"reproduciendo": 1}, {"posicion": True}, {"titulo": None}):
            self.assertFalse(room.valid_state({**ok, **bad}), bad)
        self.assertFalse(room.valid_state(None))

    def test_semilla_y_video(self):
        self.assertTrue(room.valid_seed(0) and room.valid_seed(2147483647))
        for bad in (-1, 2147483648, 1.5, True, "1", None):
            self.assertFalse(room.valid_seed(bad), bad)
        self.assertTrue(ytsearch.valid_id("dQw4w9WgXcQ"))
        for bad in ("dQw4w9WgXc", "dQw4w9WgXcQ&x=1", "../../evil1", None, 5):
            self.assertFalse(ytsearch.valid_id(bad), bad)
        self.assertIsNone(room.normalize_code("../x"))


class TestPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from PySide6.QtCore import QObject, QSettings, Signal
        from PySide6.QtWidgets import QWidget
        from brillomusical import room_ui

        class Win(QWidget):
            trans_setting, snap = "auto", None
            calls = []

            def toast(self, *a):
                pass

            def set_transition(self, setting, persist=True):
                self.calls.append((setting, persist))
                self.trans_setting = setting

        class Watcher(QObject):
            command_failed = Signal(str, object)

            def command(self, *a):
                pass

        class Client:
            sent = []

            def send(self, m):
                self.sent.append(m)

        p = room_ui.RoomPanel(Win(), QSettings("EstelaTest", "unittest"), Watcher())
        p.client = Client()
        return p

    def test_anuncio_del_anfitrion_pausa_a_los_invitados(self):
        import time
        p = self._panel()
        p.me = p.host = "m1"
        snap = {"title": "Song", "artist": "A", "album": "X", "duration": 200.0, "playing": True, "ad": False,
                "pos": 10.0, "stamp": time.time(), "rate": 1.0}
        p.on_snapshot(snap)
        p.on_snapshot({**snap, "title": "Anuncio", "ad": True})
        p.on_snapshot({**snap, "title": "Anuncio", "ad": True})  # solo una vez
        sent = p.client.sent
        self.assertEqual(len(sent), 2)
        self.assertEqual((sent[1]["titulo"], sent[1]["reproduciendo"]), ("Song", False))

    def test_transicion_del_anfitrion_no_se_guarda(self):
        p = self._panel()
        p.me, p.host = "m2", "m1"
        p._on_state({"titulo": "t", "artista": "a", "duracion": 100, "posicion": 0, "reproduciendo": False,
                     "ts": 1.7e12, "transicion": "travel", "semilla": 7})
        self.assertEqual(p.window.calls[-1], ("travel", False))
        p._on_state({"titulo": "t", "artista": "a", "duracion": float("nan"), "posicion": 0,
                     "reproduciendo": False, "ts": 1.7e12})
        self.assertEqual(p.state["duracion"], 100)  # el estado roto se ignora


if __name__ == "__main__":
    unittest.main()
