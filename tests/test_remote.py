import os
import json
import unittest
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # una sola app para toda la suite: otros tests crean widgets

from brillomusical import remote


class TestRemote(unittest.TestCase):
    def test_parse_cmd(self):
        ok = remote.parse_cmd
        self.assertEqual(ok({"name": "control", "value": "play"}), ("control", "play"))
        self.assertEqual(ok({"name": "volume", "value": 55.7}), ("volume", 55))
        self.assertEqual(ok({"name": "seek", "value": 12}), ("seek", 12.0))
        self.assertEqual(ok({"name": "action", "value": "12.3"}), ("action", "12.3"))
        self.assertEqual(ok({"name": "queue", "value": 4}), ("queue", 4))
        for bad in ({"name": "control", "value": "rm"}, {"name": "volume", "value": 101}, {"name": "volume", "value": True},
                    {"name": "seek", "value": float("nan")}, {"name": "seek", "value": -1}, {"name": "action", "value": "1;x"},
                    {"name": "queue", "value": -1}, {"name": "exec", "value": 1}, [], "x"):
            self.assertIsNone(ok(bad), bad)

    def test_solo_red_local(self):
        for ip in ("192.168.1.20", "10.0.0.3", "172.16.4.1", "127.0.0.1"):
            self.assertTrue(remote.local_client(ip), ip)
        for ip in ("8.8.8.8", "1.1.1.1", "basura"):
            self.assertFalse(remote.local_client(ip), ip)

    def test_servidor(self):
        app = QApplication.instance() or QApplication([])  # noqa: F841
        r = remote.Remote(b"<html>Estela</html>", b"png")
        if not r.start("secreto"):
            self.skipTest("puerto ocupado")
        r.set_state({"now": None})
        got = []
        r.command.connect(lambda *a: got.append(a))
        base = f"http://127.0.0.1:{remote.PORT}"

        def get(path, key=None):
            req = urllib.request.Request(base + path, headers={"X-Estela-Key": key} if key else {})
            with urllib.request.urlopen(req, timeout=3) as res:
                return res.read()

        def post(body, key):
            req = urllib.request.Request(base + "/api/cmd", json.dumps(body).encode(), method="POST",
                                         headers={"X-Estela-Key": key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as res:
                return res.status
        try:
            self.assertIn(b"Estela", get("/"))  # la página no lleva datos: se sirve sin código
            for key in (None, "otro"):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    get("/api/state", key)
                self.assertEqual(e.exception.code, 401)
                with self.assertRaises(urllib.error.HTTPError):
                    post({"name": "control", "value": "play"}, key or "")
            self.assertEqual(json.loads(get("/api/state", "secreto")), {"now": None})
            with self.assertRaises(urllib.error.HTTPError) as e:
                post({"name": "control", "value": "borrar"}, "secreto")
            self.assertEqual(e.exception.code, 400)
            self.assertEqual(post({"name": "control", "value": "next"}, "secreto"), 204)
            app.processEvents()
            self.assertEqual(got, [("control", "next")])
        finally:
            r.stop()


if __name__ == "__main__":
    unittest.main()
