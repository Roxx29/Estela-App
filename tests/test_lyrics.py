import unittest

from brillomusical import lyrics


class TestClean(unittest.TestCase):
    def test_quita_parentesis_de_video(self):
        self.assertEqual(lyrics.clean("Song (Official Video)", "Band"), ("Song", "Band"))

    def test_quita_corchetes_y_letra(self):
        self.assertEqual(lyrics.clean("Canción [Letra] (Remastered 2011)", "Grupo"), ("Canción", "Grupo"))

    def test_conserva_parentesis_utiles(self):
        self.assertEqual(lyrics.clean("Song (feat. X)", "Band"), ("Song (feat. X)", "Band"))

    def test_quita_topic_y_vevo(self):
        self.assertEqual(lyrics.clean("Song", "Band - Topic"), ("Song", "Band"))
        self.assertEqual(lyrics.clean("Song", "BandVEVO"), ("Song", "Band"))

    def test_separa_artista_guion_titulo(self):
        self.assertEqual(lyrics.clean("Band - Song (Lyrics)", ""), ("Song", "Band"))
        # vídeos de YouTube cualquiera
        self.assertEqual(lyrics.clean("Bad Bunny - DtMF | DeBÍ TiRAR MáS FOToS (Video Oficial)", "BadBunnyVEVO"),
                         ("DtMF", "Bad Bunny"))
        self.assertEqual(lyrics.clean("Karol G - Si Antes Te Hubiera Conocido Official Video", "Karol G Official"),
                         ("Si Antes Te Hubiera Conocido", "Karol G"))
        self.assertEqual(lyrics.clean("Canción (Videoclip) [Prod. X]", "Grupo"), ("Canción", "Grupo"))
        self.assertEqual(lyrics.clean("Lana Del Rey - Video Games", "LanaDelReyVEVO"), ("Video Games", "Lana Del Rey"))
        self.assertEqual(lyrics.clean("Canción - Video Oficial", "Grupo"), ("Canción", "Grupo"))
        self.assertEqual(lyrics.clean("TRUENO || RAIN III", "Trueno"), ("RAIN III", "TRUENO"))
        self.assertEqual(lyrics.clean("RAIN III | Trueno", "Trueno"), ("RAIN III", "Trueno"))
        self.assertEqual(lyrics.clean("TRUENO - RAIN III (Prod. Tatool)", "Trueno"), ("RAIN III", "TRUENO"))
        self.assertEqual(lyrics.clean("RAIN III", "Trueno"), ("RAIN III", "Trueno"))

    def test_artista_principal(self):
        self.assertEqual(lyrics.main_artist("A, B & C feat. D"), "A")
        self.assertEqual(lyrics.main_artist("A feat. D"), "A")


class TestParseLrc(unittest.TestCase):
    def test_parsea_y_ordena(self):
        text = "[ar:x]\n[00:12.50]Hola mundo\n[00:01.00]Primera\n[01:02.3]Tercera"
        self.assertEqual(lyrics.parse_lrc(text), [(1.0, "Primera"), (12.5, "Hola mundo"), (62.3, "Tercera")])

    def test_varias_marcas_en_una_linea(self):
        self.assertEqual(lyrics.parse_lrc("[00:01.00][00:05.00]Coro"), [(1.0, "Coro"), (5.0, "Coro")])

    def test_vacio(self):
        self.assertEqual(lyrics.parse_lrc(""), [])


class TestTiming(unittest.TestCase):
    lines = [(1.0, "uno dos"), (5.0, ""), (6.0, "tres")]

    def test_line_index(self):
        self.assertEqual(lyrics.line_index(self.lines, 0.5), -1)
        self.assertEqual(lyrics.line_index(self.lines, 1.0), 0)
        self.assertEqual(lyrics.line_index(self.lines, 5.5), 1)
        self.assertEqual(lyrics.line_index(self.lines, 100), 2)

    def test_word_times_reparte_por_longitud(self):
        starts = lyrics.word_times(self.lines, 0)
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0], 1.0)
        self.assertTrue(1.0 < starts[1] < 5.0)

    def test_word_times_ultima_linea_maximo_6s(self):
        starts = lyrics.word_times([(10.0, "a bbbbbbbb")], 0)
        self.assertTrue(10.0 < starts[1] < 16.0)


class TestWordTags(unittest.TestCase):
    def test_etiquetas_por_palabra(self):
        lines = [(1.0, "<00:01.00>a <00:01.80>bb <00:02.50>c")]
        self.assertEqual(lyrics.word_times(lines, 0), [1.0, 1.8, 2.5])
        self.assertEqual(lyrics.words(lines[0][1]), ["a", "bb", "c"])

    def test_etiquetas_con_espacios_y_cierre(self):
        lines = [(1.0, "<00:01.00> Hola <00:01.80> mundo <00:02.50>")]
        self.assertEqual(lyrics.words(lines[0][1]), ["Hola", "mundo"])
        self.assertEqual(lyrics.word_times(lines, 0), [1.0, 1.8])

    def test_nota_larga_no_retrasa_palabras(self):
        starts = lyrics.word_times([(0.0, "a b"), (20.0, "c")], 0)
        self.assertLess(starts[1], 1.5)  # duración estimada: 0,5 s por palabra + 0,5


class TestClock(unittest.TestCase):
    def test_salta_al_empezar_y_en_pausa(self):
        c = lyrics.Clock()
        self.assertEqual(c.get(10.0, True, 0.0, "a"), 10.0)
        self.assertEqual(c.get(42.0, False, 0.5, "a"), 42.0)

    def test_nunca_retrocede_con_errores_pequenos(self):
        c = lyrics.Clock()
        prev = c.get(10.0, True, 0.0, "a")
        for i in range(1, 60):
            now = i / 60
            v = c.get(10.0 + now - 0.8, True, now, "a")  # Windows dice 0,8 s menos
            self.assertGreaterEqual(v, prev)
            prev = v

    def test_corrige_error_pequeno_en_3s(self):
        c = lyrics.Clock()
        c.get(10.0, True, 0.0, "a")
        for i in range(1, 181):
            now = i / 60
            v = c.get(10.0 + now + 0.3, True, now, "a")
        self.assertLess(abs(v - (10.0 + 3.0 + 0.3)), 0.03)

    def test_salta_con_error_grande_y_cambio_de_cancion(self):
        c = lyrics.Clock()
        c.get(10.0, True, 0.0, "a")
        self.assertEqual(c.get(30.0, True, 0.1, "a"), 30.0)
        self.assertEqual(c.get(1.0, True, 0.2, "b"), 1.0)


class TestOffsets(unittest.TestCase):
    def test_limite_y_tope(self):
        o = lyrics.Offsets({})
        o.add("k", 7)
        self.assertEqual(o.get("k"), 5.0)
        o.add("k", -0.25)
        self.assertAlmostEqual(o.get("k"), 4.75)
        for i in range(305):
            o.add(f"s{i}", 0.25)
        self.assertLessEqual(len(o.data), lyrics.Offsets.MAX)
        self.assertEqual(o.get("s0"), 0.0)  # la más antigua se olvida

    def test_reiniciar(self):
        o = lyrics.Offsets({"k": 1.0})
        o.reset("k")
        self.assertEqual(o.get("k"), 0.0)


class TestQuery(unittest.TestCase):
    def test_elige_la_duracion_mas_parecida(self):
        import urllib.error
        from unittest import mock

        def fake(endpoint, params):
            if endpoint == "get":
                raise urllib.error.HTTPError("u", 404, "x", None, None)
            return [{"duration": 182.5, "syncedLyrics": "[00:01.00]A"},
                    {"duration": 180.4, "syncedLyrics": "[00:01.00]B"},
                    {"duration": 300, "syncedLyrics": "[00:01.00]C"}]

        with mock.patch.object(lyrics, "_get", fake):
            self.assertEqual(lyrics._query("t", "a", "", 180.0), [(1.0, "B")])

    def test_video_con_canal_que_no_es_el_artista(self):
        import urllib.error
        from unittest import mock
        calls = []

        def fake(endpoint, params):
            calls.append(params)
            if endpoint == "get":
                raise urllib.error.HTTPError("u", 404, "x", None, None)
            if params.get("artist_name") == "Artista":  # solo encuentra con artista y canción separados
                return [{"duration": 200.0, "syncedLyrics": "[00:01.00]V"}]
            return []

        with mock.patch.object(lyrics, "_get", fake):
            # vídeo de 212 s (con intro) en un canal de recopilaciones: tolerancia de vídeo
            self.assertEqual(lyrics._query("Artista - Canción (feat. Y)", "Canal Música", "", 212.0), [(1.0, "V")])
            self.assertEqual(lyrics._query("Canción", "Grupo", "Álbum", 212.0), [])  # con álbum, ±3 s
        self.assertEqual(calls[0].get("album_name"), "")  # /get también sin álbum: LRCLIB la busca fuera

    def test_get_lento_no_es_sin_conexion(self):
        from unittest import mock

        def fake(endpoint, params):
            if endpoint == "get":
                raise TimeoutError("timed out")
            return [{"duration": 200.0, "syncedLyrics": "[00:01.00]S"}]

        with mock.patch.object(lyrics, "_get", fake):
            self.assertEqual(lyrics._query("Canción", "Grupo", "Álbum", 201.0), [(1.0, "S")])

        def none(endpoint, params):
            if endpoint == "get":
                raise TimeoutError("timed out")
            return []

        with mock.patch.object(lyrics, "_get", none):
            self.assertIsNone(lyrics._query("Canción", "Grupo", "Álbum", 201.0))  # sin letra, sin guardarlo

        def down(endpoint, params):
            raise OSError("sin red")

        with mock.patch.object(lyrics, "_get", down):
            with self.assertRaises(OSError):
                lyrics._query("Canción", "Grupo", "Álbum", 201.0)

    def test_error_del_servidor_no_es_sin_conexion(self):
        import urllib.error
        from unittest import mock

        def fake(endpoint, params):
            if endpoint == "get" or "q" not in params:
                raise urllib.error.HTTPError("u", 400, "x", None, None)
            return [{"duration": 200.0, "syncedLyrics": "[00:01.00]Q"}]

        with mock.patch.object(lyrics, "_get", fake):
            self.assertEqual(lyrics._query("Canción", "Canal", "", 205.0), [(1.0, "Q")])
            self.assertEqual(lyrics._query("Canción", "Grupo", "Álbum", 205.0), [])  # sin letra, no excepción


class TestAd(unittest.TestCase):
    def test_anuncios(self):
        self.assertTrue(lyrics.is_ad("Song", "", "Album", 200))
        self.assertTrue(lyrics.is_ad("Anuncio", "X", "", 20))
        self.assertTrue(lyrics.is_ad("Promo", "X", "", 15))

    def test_cancion_normal(self):
        self.assertFalse(lyrics.is_ad("Song", "Band", "Album", 200))
        self.assertFalse(lyrics.is_ad("Intro", "Band", "Album", 20))

    def test_titulo_que_empieza_por_ad(self):
        self.assertFalse(lyrics.is_ad("Ad Astra", "Band", "Album", 200))
        self.assertFalse(lyrics.is_ad("Ad Infinitum", "Band", "", 200))


if __name__ == "__main__":
    unittest.main()
