"""Mando en el móvil: Estela sirve una página en el Wi‑Fi de casa y el móvil la abre escaneando un QR.

Apagado de serie. Solo responde a direcciones de la red local y a quien traiga el código secreto del QR
(que va tras el # de la dirección: no sale en registros). Qt rellena el estado; el servidor solo lo entrega
y pasa las órdenes, validadas, al hilo de Qt.
"""
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

PORT = 38918  # fijo: el acceso directo del móvil sigue valiendo tras reiniciar Estela
MAX_BODY = 2048
CONTROLS = ("play", "next", "prev", "back", "fwd")

log = logging.getLogger(__name__)


def new_token():
    return secrets.token_urlsafe(16)  # 128 bits


def lan_ip():
    """IP de este PC en la red local (no envía nada: un socket UDP solo elige la interfaz)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_client(addr):
    """Solo la red de casa: nada que llegue desde internet aunque el router abriera el puerto."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def parse_cmd(obj):
    """Orden del móvil validada: (nombre, valor) o None."""
    if not isinstance(obj, dict):
        return None
    name, value = obj.get("name"), obj.get("value")
    if name == "control" and value in CONTROLS:
        return name, value
    if name in ("seek", "volume") and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != value or not 0 <= value <= (100 if name == "volume" else 86400):  # NaN fuera
            return None
        return name, int(value) if name == "volume" else float(value)
    if name == "queue" and isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 500:
        return name, value
    if name == "action" and isinstance(value, str) and 0 < len(value) <= 20 and all(c in "0123456789." for c in value):
        return name, value
    return None


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: el puerto es solo nuestro
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Remote(QObject):
    command = Signal(str, object)  # (nombre, valor), ya validado
    connected = Signal(str)  # IP del móvil, la primera vez que entra

    def __init__(self, page, icon_png):
        super().__init__()
        self.page, self.icon = page, icon_png
        self.token = ""
        self.state, self.cover = b"{}", b""
        self.lock = threading.Lock()
        self.server = None
        self.seen = set()

    @property
    def running(self):
        return self.server is not None

    def set_state(self, state):
        data = json.dumps(state, ensure_ascii=False).encode()
        with self.lock:
            self.state = data

    def set_cover(self, jpeg):
        with self.lock:
            self.cover = jpeg

    def url(self):
        return f"http://{lan_ip()}:{PORT}/#k={self.token}"

    def start(self, token):
        self.token = token
        if self.server:
            return True
        remote = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def _ok(self):
                key = self.headers.get("X-Estela-Key", "")
                return bool(remote.token) and hmac.compare_digest(key.encode(), remote.token.encode())

            def _send(self, code, body=b"", ctype="application/json", cache="no-store"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if ctype.startswith("text/html"):
                    self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: blob: data:; "
                                     "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                                     "frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not local_client(self.client_address[0]):
                    return self._send(403)
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    return self._send(200, remote.page, "text/html; charset=utf-8")
                if path == "/icon.png":
                    return self._send(200, remote.icon, "image/png", "max-age=86400")
                if path == "/manifest.webmanifest":
                    return self._send(200, MANIFEST, "application/manifest+json")
                if not self._ok():
                    return self._send(401)
                if self.client_address[0] not in remote.seen:
                    remote.seen.add(self.client_address[0])
                    remote.connected.emit(self.client_address[0])
                with remote.lock:
                    state, cover = remote.state, remote.cover
                if path == "/api/state":
                    return self._send(200, state)
                if path == "/api/cover":
                    return self._send(200, cover, "image/jpeg", "private, max-age=3600") if cover else self._send(404)
                self._send(404)

            def do_POST(self):
                if not local_client(self.client_address[0]) or self.path != "/api/cmd":
                    return self._send(403)
                if not self._ok():
                    return self._send(401)
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    size = -1
                if not 0 < size <= MAX_BODY:
                    return self._send(413)
                try:
                    cmd = parse_cmd(json.loads(self.rfile.read(size)))
                except ValueError:
                    cmd = None
                if cmd is None:
                    return self._send(400)
                remote.command.emit(*cmd)  # Qt la entrega en su hilo
                self._send(204)

            def log_message(self, *args):
                pass

        try:
            self.server = _Server(("0.0.0.0", PORT), Handler)
        except OSError as e:
            log.warning("Mando del móvil: no se pudo abrir el puerto %s (%s)", PORT, e)
            return False
        threading.Thread(target=self.server.serve_forever, daemon=True, name="remote").start()
        log.info("Mando del móvil activo en %s:%s", lan_ip(), PORT)
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.seen.clear()


MANIFEST = json.dumps({
    "name": "Estela", "short_name": "Estela", "start_url": "/", "display": "standalone",
    "background_color": "#0A1430", "theme_color": "#0A1430", "lang": "es",
    "icons": [{"src": "/icon.png", "sizes": "192x192", "type": "image/png"}],
}).encode()


def qr_png(text, scale=8):
    import io

    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=3, dark="#0A1430", light="#E6E1D7")
    return buf.getvalue()


def pair_window(url, renew, off):
    """Ventana con el QR para emparejar el móvil. renew() y off() las pone Controller."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

    w = QWidget(None, Qt.Window)
    w.setAttribute(Qt.WA_DeleteOnClose)
    w.setWindowTitle("Estela · Controlar desde el móvil")
    w.setStyleSheet("QWidget{background:#0A1430;color:#E6E1D7;font-family:'Segoe UI';font-size:14px}"
                    "QLabel#t{font-size:26px;font-weight:600;color:#FFFFFF}QLabel#u{color:#4DA3FF}"
                    "QPushButton{background:#1B2A55;border:1px solid #2A7FA8;border-radius:16px;padding:8px 16px}"
                    "QPushButton:hover{background:#25386F}")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(12)
    title = QLabel("Tu móvil, el mando.", objectName="t")
    sub = QLabel("Escanéalo con la cámara del móvil. El móvil y el PC tienen que estar en el mismo Wi‑Fi.")
    sub.setWordWrap(True)
    qr = QLabel(alignment=Qt.AlignCenter)
    link = QLabel(objectName="u", alignment=Qt.AlignCenter)
    link.setTextInteractionFlags(Qt.TextSelectableByMouse)
    note = QLabel("Si Windows pide permiso, marca <b>Redes privadas</b> y pulsa <b>Permitir</b>; si no, el móvil no "
                  "podrá entrar. En redes de invitados, de la universidad o con VPN no funciona.")
    note.setWordWrap(True)

    def show(u):
        pix = QPixmap()
        pix.loadFromData(qr_png(u))
        qr.setPixmap(pix)
        link.setText(u.split("#")[0])  # el código no se enseña en claro: va solo en el QR

    show(url)
    row = QHBoxLayout()
    new = QPushButton("Nuevo código")
    new.setToolTip("Los móviles conectados tendrán que volver a escanear")
    new.clicked.connect(lambda: show(renew()))
    stop = QPushButton("Apagar el mando")
    stop.clicked.connect(lambda: (off(), w.close()))
    row.addWidget(new)
    row.addWidget(stop)
    for x in (title, sub, qr, link, note):
        lay.addWidget(x)
    lay.addLayout(row)
    w.setFixedWidth(420)
    w.show()
    w.raise_()
    w.activateWindow()
    return w
