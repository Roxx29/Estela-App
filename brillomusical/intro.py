"""Animación de inicio (variante B): una estela entra en diagonal, dibuja la estrella de Estela de un trazo,
cierra el aro, "tac" (destello y onda) y el logo se va hacia la bandeja.

La geometría es pura (en radios del logo, la estrella mide 1) y sale del tiempo: sin estado.
La ventana es transparente y no recibe clics, como los bordes; clic o Esc la saltan.
"""
import ctypes
import math
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

FULL, SHORT = 2.2, 1.2  # la primera vez entera (con el nombre); después, y en Ligera, corta y sin partículas
# fin de cada fase en fracción de la duración: entra, traza la estrella, cierra el aro, tac; luego a la bandeja
ENTER, TRACE, RING, TAC = 0.23, 0.59, 0.73, 0.82
R_IN = 0.25  # radio interior de la estrella, como en el icono (0,09 / 0,36)
RING_R = 1.22
START = (-2.6, 1.7)  # abajo a la izquierda
TAIL = 0.12  # segundos de cola
BLUE, CYAN = QColor(77, 163, 255), QColor(42, 127, 168)
STAR = [((1 if i % 2 == 0 else R_IN) * math.cos(math.pi * i / 4 - math.pi / 2),
         (1 if i % 2 == 0 else R_IN) * math.sin(math.pi * i / 4 - math.pi / 2)) for i in range(8)]


def _clamp(v):
    return max(0.0, min(1.0, v))


def _ease(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _out(t):
    return 1 - (1 - t) ** 3


def along(f):
    """Punto del contorno de la estrella a la fracción f de su longitud, empezando por la punta de arriba."""
    pts = STAR + STAR[:1]
    s = _clamp(f) * sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b)
        if s <= d:
            return a[0] + (b[0] - a[0]) * s / d, a[1] + (b[1] - a[1]) * s / d
        s -= d
    return pts[-1]


def head(u):
    """Punta de luz en u (0..1 de la duración): entra hasta la punta de arriba y recorre la estrella."""
    u = _clamp(u)
    if u < ENTER:
        k = _out(u / ENTER)
        return START[0] * (1 - k), START[1] + (-1 - START[1]) * k
    return along(_ease(_clamp((u - ENTER) / (TRACE - ENTER))))


def state(u):
    """Cuánto va de cada parte en u: contorno trazado, aro, relleno, destello, onda y salida (0..1)."""
    return {"traced": _ease(_clamp((u - ENTER) / (TRACE - ENTER))),
            "ring": _out(_clamp((u - TRACE) / (RING - TRACE))),
            "fill": _clamp((u - TRACE) / (RING - TRACE)),
            "flash": _clamp(1 - abs(u - RING) / 0.06),
            "wave": _clamp((u - RING) / (1 - RING)),
            "out": _ease(_clamp((u - TAC) / (1 - TAC)))}


def system_quiet():
    """Windows pide calma: juego a pantalla completa, Escritorio remoto o animaciones desactivadas."""
    if sys.platform != "win32":
        return False
    from .glow import fullscreen_app, user32
    anim = ctypes.c_int(1)
    user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(anim), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(fullscreen_app() or user32.GetSystemMetrics(0x1000) or not anim.value)  # SM_REMOTESESSION


def _skip():
    if sys.platform != "win32":
        return False
    from .glow import user32
    return any(user32.GetAsyncKeyState(k) & 0x8000 for k in (0x1B, 0x01, 0x02))  # Esc, clic izquierdo o derecho


class Intro(QWidget):
    def __init__(self, first, light):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.name, self.light = first and not light, light
        self.dur = FULL if self.name else SHORT
        scr = QGuiApplication.primaryScreen().geometry()
        w, h = int(scr.width() * 0.7), int(scr.height() * 0.7)  # solo lo que ocupa la animación
        self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 2, w, h)
        self.t0 = None
        self.timer = QTimer(self, interval=16, timeout=self._tick)
        self.timer.setTimerType(Qt.PreciseTimer)

    def showEvent(self, e):
        if sys.platform == "win32":  # que no tape clics aunque Qt no ponga el estilo (igual que los bordes)
            from .glow import GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, user32
            hwnd = int(self.winId())
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_TRANSPARENT
                                     | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self.t0 = time.monotonic()
        self.timer.start()
        super().showEvent(e)

    def _tick(self):
        t = time.monotonic() - self.t0
        # 0,4 s de gracia: el doble clic que abrió el .exe puede seguir pulsado
        if t >= self.dur or (t > 0.4 and _skip()):
            self.timer.stop()
            self.close()
        else:
            self.update()

    def paintEvent(self, e):
        if self.t0 is None:
            return
        t = time.monotonic() - self.t0
        u, st = t / self.dur, state(t / self.dur)
        w, h = self.width(), self.height()
        r = 0.2 * min(w, h)  # la onda (×2,2) y la entrada (×2,6 en x) caben dentro
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        out = st["out"]
        alpha = 1 - out
        if self.name and st["fill"] > 0:  # el nombre, solo la primera vez
            p.setOpacity(st["fill"] * alpha)
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Segoe UI Light", max(12, int(r * 0.42))))
            p.drawText(QRectF(0, h / 2 + r * 1.45, w, r * 0.6), Qt.AlignCenter, "Estela")
            p.setPen(QColor(200, 225, 255))
            p.setFont(QFont("Segoe UI", max(8, int(r * 0.13))))
            p.drawText(QRectF(0, h / 2 + r * 2.05, w, r * 0.3), Qt.AlignCenter, "Lo que suena, brilla.")
        # al final, el logo encoge hacia la esquina de abajo a la derecha (donde está la bandeja)
        p.translate(w / 2 + out * (w / 2 - r * 0.4), h / 2 + out * (h / 2 - r * 0.4))
        p.scale(r * (1 - 0.85 * out), r * (1 - 0.85 * out))
        p.setOpacity(alpha)
        if st["flash"] > 0:
            g = QRadialGradient(QPointF(0, 0), 2.6)
            g.setColorAt(0, QColor(255, 255, 255, int(200 * st["flash"])))
            g.setColorAt(0.4, QColor(77, 163, 255, int(90 * st["flash"])))
            g.setColorAt(1, QColor(77, 163, 255, 0))
            p.fillRect(QRectF(-2.6, -2.6, 5.2, 5.2), g)
        if 0 < st["wave"] < 1:  # onda del tac
            p.setPen(QPen(QColor(120, 190, 255, int(220 * (1 - st["wave"]))), 0.05))
            p.setBrush(Qt.NoBrush)
            rr = RING_R * (1 + 0.8 * _out(st["wave"]))
            p.drawEllipse(QPointF(0, 0), rr, rr)
            if not self.light:  # chispas
                for i in range(18):
                    a = i * 2 * math.pi / 18 + 0.3 * (i % 3)
                    d0, d1 = RING_R * (1 + 0.6 * st["wave"]), RING_R * (1 + (0.8 + 0.15 * (i % 4)) * st["wave"])
                    p.drawLine(QPointF(d0 * math.cos(a), d0 * math.sin(a)), QPointF(d1 * math.cos(a), d1 * math.sin(a)))
        if st["ring"] > 0:
            g = QLinearGradient(0, -RING_R, 0, RING_R)
            g.setColorAt(0, BLUE)
            g.setColorAt(1, CYAN)
            p.setPen(QPen(g, 0.11, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(-RING_R, -RING_R, 2 * RING_R, 2 * RING_R), 90 * 16, -int(st["ring"] * 360 * 16))
        if st["fill"] > 0:
            star = QPainterPath()
            star.addPolygon([QPointF(x * 1.1, y * 1.1) for x, y in STAR])
            p.fillPath(star, QColor(77, 163, 255, int(140 * st["fill"])))
            star = QPainterPath()
            star.addPolygon([QPointF(x, y) for x, y in STAR])
            p.fillPath(star, QColor(255, 255, 255, int(255 * st["fill"])))
        if st["traced"] > 0:  # el trazo que deja la estela sobre la estrella
            path = QPainterPath(QPointF(*along(0)))
            n = max(2, int(64 * st["traced"]))
            for i in range(1, n + 1):
                path.lineTo(QPointF(*along(st["traced"] * i / n)))
            for width, col in ((0.16, QColor(77, 163, 255, 90)), (0.045, QColor(255, 255, 255))):
                p.strokePath(path, QPen(col, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if u < TRACE + 0.05:  # cola: la punta de luz en los instantes anteriores, cada vez más fina y apagada
            steps = 12
            prev = head(u)
            for k in range(1, steps + 1):
                pt = head(u - TAIL / self.dur * k / steps)
                f = 1 - k / steps
                p.setPen(QPen(QColor(150, 205, 255, int(230 * f)), 0.02 + 0.1 * f, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(*prev), QPointF(*pt))
                prev = pt
            if u <= TRACE:
                hx, hy = head(u)
                g = QRadialGradient(QPointF(hx, hy), 0.35)
                g.setColorAt(0, QColor(255, 255, 255, 255))
                g.setColorAt(0.3, QColor(160, 210, 255, 160))
                g.setColorAt(1, QColor(77, 163, 255, 0))
                p.fillRect(QRectF(hx - 0.35, hy - 0.35, 0.7, 0.7), g)
        p.end()
