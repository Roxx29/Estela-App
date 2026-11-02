import json
import unittest
import urllib.error
import urllib.request

from PySide6.QtCore import QCoreApplication

from brillomusical import bridge


class TestBridge(unittest.TestCase):
    def test_clean(self):
        st = bridge.clean({"now": {"title": "A" * 999, "artist": 3},
                           "queue": [{"title": "x", "artist": "y", "thumb": "javascript:alert(1)", "selected": "true"},
                                     "basura", {"title": "z", "thumb": "https://i.ytimg.com/a.jpg", "selected": True}]})
        self.assertEqual(len(st["now"]["title"]), bridge.MAX_TEXT)
        self.assertEqual(st["now"]["artist"], "")
        self.assertEqual(len(st["queue"]), 2)
        self.assertEqual(st["queue"][0]["thumb"], "")  # solo https
        self.assertFalse(st["queue"][0]["selected"])  # solo True de verdad
        self.assertTrue(st["queue"][1]["selected"])
        self.assertIsNone(bridge.clean([1, 2]))
        self.assertEqual(bridge.clean({"lyrics": 5})["lyrics"], "")
        self.assertEqual(len(bridge.clean({"lyrics": "x" * 99999})["lyrics"]), bridge.MAX_LYRICS)
        self.assertEqual(bridge.clean({"volume": 42})["volume"], 42)
        for bad in (101, -1, True, "50", None):
            self.assertIsNone(bridge.clean({"volume": bad})["volume"])

    def test_servidor(self):
        app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
        b = bridge.Bridge()
        if b.server is None:
            self.skipTest("puerto ocupado")
        got = []
        b.state.connect(got.append)
        b.command("queue", 3)
        body = json.dumps({"now": {"title": "t", "artist": "a"}, "queue": []}).encode()

        def post(origin):
            req = urllib.request.Request(f"http://127.0.0.1:{bridge.PORT}/state", body, method="POST",
                                         headers={"Content-Type": "application/json", "Origin": origin})
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read())
        for origin in ("https://evil.example", "chrome-extension://otra"):  # ni webs ni otras extensiones
            with self.assertRaises(urllib.error.HTTPError):
                post(origin)
        self.assertEqual(post(bridge.EXTENSION)["commands"], [{"name": "queue", "index": 3, "title": ""}])
        self.assertEqual(post(bridge.EXTENSION)["commands"], [])  # la orden se entrega una vez
        b.stop()


if __name__ == "__main__":
    unittest.main()
