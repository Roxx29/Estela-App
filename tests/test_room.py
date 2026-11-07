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


if __name__ == "__main__":
    unittest.main()
