"""Puente con la extensión de Chrome: recibe la cola de YouTube Music del navegador y le manda órdenes.

Servidor HTTP mínimo solo en 127.0.0.1 (no sale del PC). Solo acepta peticiones de extensiones del
navegador (cabecera Origin chrome-extension://, que una web no puede falsificar) y valida todo lo que llega.
"""
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

PORT = 38917
# ID fijo de nuestra extensión (sale de la clave pública "key" de extension/manifest.json): ninguna otra entra
EXTENSION = "chrome-extension://adojdlfkiohcjpodphaecndmkodipobn"
MAX_BODY = 256 * 1024
MAX_ITEMS, MAX_TEXT, MAX_LYRICS = 200, 300, 20000

log = logging.getLogger(__name__)


def _text(v):
    return v[:MAX_TEXT] if isinstance(v, str) else ""


def clean(state):
    """Lo que manda la extensión, validado: {"now": {title, artist}, "queue": [{title, artist, thumb, selected}], "lyrics": texto}."""
    if not isinstance(state, dict):
        return None
    now = state.get("now") if isinstance(state.get("now"), dict) else {}
    queue = state.get("queue") if isinstance(state.get("queue"), list) else []
    out = []
    for q in queue[:MAX_ITEMS]:
        if isinstance(q, dict):
            thumb = _text(q.get("thumb"))
            out.append({"title": _text(q.get("title")), "artist": _text(q.get("artist")),
                        "thumb": thumb if thumb.startswith("https://") else "", "selected": q.get("selected") is True})
    lyrics = state.get("lyrics")
    return {"now": {"title": _text(now.get("title")), "artist": _text(now.get("artist"))}, "queue": out,
            "lyrics": lyrics[:MAX_LYRICS].strip() if isinstance(lyrics, str) else ""}


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # en Windows, con esto otro programa podría compartir el puerto sin error

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: el puerto es solo nuestro
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Bridge(QObject):
    state = Signal(object)

    def __init__(self):
        super().__init__()
        self.pending, self.lock = [], threading.Lock()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5  # un cliente lento no deja un hilo colgado

            def do_POST(self):
                if (self.path != "/state" or self.headers.get("Origin", "").rstrip("/") != EXTENSION
                        or not self.headers.get("Content-Type", "").startswith("application/json")):
                    return self._send(403, {})
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    size = -1
                if not 0 < size <= MAX_BODY:
                    return self._send(413, {})
                try:
                    st = clean(json.loads(self.rfile.read(size)))
                except ValueError:
                    st = None
                if st is None:
                    return self._send(400, {})
                bridge.state.emit(st)  # Qt la entrega en su hilo
                with bridge.lock:
                    cmds, bridge.pending = bridge.pending, []
                self._send(200, {"commands": cmds})

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass  # sin ruido en el log

        try:
            self.server = _Server(("127.0.0.1", PORT), Handler)
        except OSError:
            log.warning("Puerto %s ocupado: la extensión de Chrome no podrá enviar la cola", PORT)
            self.server = None
            return
        threading.Thread(target=self.server.serve_forever, daemon=True, name="bridge").start()

    def command(self, name, index, title=""):
        """Orden para la pestaña; lleva el título para que la extensión no reproduzca otra si la cola cambió."""
        with self.lock:
            self.pending = (self.pending + [{"name": name, "index": int(index), "title": title}])[-5:]

    def stop(self):
        if self.server:
            self.server.shutdown()
