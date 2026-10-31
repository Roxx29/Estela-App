"""Ventana de letras: cada estrofa es una constelación aleatoria que crece palabra a palabra.

Las formas costosas (fondo de estrellas, halo, nebulosa) se pintan una vez en QPixmap y se
reutilizan en cada fotograma; solo se anima lo que se mueve.
"""
import bisect
import ctypes
import math
import random
import threading
import time
import zlib
from collections import OrderedDict, deque

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QFontInfo, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from . import (backdrop, cover, effects, history, kinetic, layout, loco, lyrics, media, nyc, panels, quality, ribbon,
               transitions, wheel, ytm)
from .color import FALLBACK

# tamaños en fracción de la altura de la ventana: (estrella, texto)
SIZE_CURRENT, SIZE_PREV, SIZE_REST = (0.045, 0.06), (0.025, 0.035), (0.012, 0.022)
REST_ALPHA, OLD_ALPHA, OLD_AFTER = 0.45, 0.25, 12  # las palabras con más de 12 de antigüedad, más tenues
DRAW_LINE = 0.35  # trazo de la línea hacia la palabra nueva
FLASH, SPARKS = 0.3, 0.5
BG_STARS, TWINKLERS = 120, 20
TOAST = 1.5  # avisos breves (desfase, modo de fondo)
SEARCHING = "Buscando letra…"
HELP = "Espacio play/pausa · N/P siguiente/anterior · Mayús+←/→ ±10 s · M menú · F pantalla completa · L estilo · B fondo · C carátula · T transición · ←/→ sincronizar · H atajos"
FIRST_TIP = "Acerca el ratón a los bordes: menú arriba, controles a la izquierda, canciones a la derecha"
CURSOR_HIDE = 2500  # ms quieto en pantalla completa antes de ocultar el cursor
# Latido: letra (·alto), mínimo, base de la última fila (·alto), margen izquierdo y anchura máxima (·ancho)
KIN_SIZE, KIN_MIN, KIN_Y, KIN_X, KIN_W = 0.13, 0.08, 0.90, 0.06, 0.88
NYC_PAD, NYC_RADIUS, NYC_BAND = 0.16, 0.18, 0.07  # Nueva York: margen y radio de la placa (unidades), banda de estación (·alto)


def _out_cubic(t):
    return 1 - (1 - t) ** 3


def _out_back(t):
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _star_path():
    path = QPainterPath()
    pts = [(0, -1), (0.2, -0.2), (1, 0), (0.2, 0.2), (0, 1), (-0.2, 0.2), (-1, 0), (-0.2, -0.2)]
    path.moveTo(*pts[0])
    for x, y in pts[1:]:
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def _font_family():
    f = QFont("Segoe UI Variable Display")
    return f.family() if f.exactMatch() else "Segoe UI"


class LyricsWindow(QWidget):
    offset_step = Signal(float)  # ← / → : mover la letra ±0,1 s
    bg_changed = Signal(str)  # B: siguiente modo de fondo
    cover_changed = Signal(str)  # C: siguiente posición de la carátula
    transition_changed = Signal(str)  # T: siguiente transición
    control = Signal(str)  # play / next / prev / back / fwd hacia YouTube Music
    song = Signal(object)  # clic en el carrusel: (tipo, canción, índice en la cola)
    open_ytm = Signal()  # rueda: abrir YouTube Music integrado
    nyc_bg_changed = Signal(str)  # B en Nueva York: siguiente fondo de ese estilo
    style_changed = Signal(str)  # L: Automático / Constelación / Latido / Cinta
    quality_changed = Signal(str)

    def __init__(self, settings, frames, governor):
        super().__init__()
        self.settings = settings
        self.frames = frames
        self.quality = governor
        self.style_setting = settings.value("lyrics_style", "constellation")  # lo elegido (puede ser "auto")
        if self.style_setting not in kinetic.SETTINGS:
            self.style_setting = "constellation"
        # lo que se pinta: Automático y Loco empiezan en el principal y cambian después
        self.style = self.style_setting if self.style_setting not in ("auto", "loco") else "constellation"
        self.kin = {}  # Latido: (efecto, fuente) elegidos al aparecer cada línea
        self.glyphs, self.rib, self.rib_track = {}, None, None  # Cinta: letras sueltas, texto y curva en caché
        self.beat_t = -1e9
        self.beats = deque(maxlen=6)  # golpes recientes: anillos de Latido
        self.beats_total, self.chladni = 0, {}
        # Loco: estrofa vista, estrofas desde el último cambio, último cambio, siguiente y transición en curso
        self.loco_k, self.loco_n, self.loco_t, self.loco_last, self.loco_seq = None, 0, -1e9, None, 0
        self.loco_next = self.loco_tr = None
        self.loco_cache = {}
        self.nyc_bg = settings.value("nyc_bg", "asphalt")
        if self.nyc_bg not in nyc.BGS:
            self.nyc_bg = "asphalt"
        self.nyc_cache, self.nyc_words, self.grain, self.vignette, self.gray = None, {}, None, None, (None, None)
        self.nyc_plates, self.nyc_sky, self.nyc_prev = {}, None, None
        self.wheel = wheel.Wheel()
        self.panels = panels.Panels()
        self.history = history.History()
        self.queue, self.qthumbs, self.carousel = [], {}, []  # cola de YouTube Music integrado
        self.wheel_on = self.skip = False
        self.setMouseTracking(True)
        self.cursor_timer = QTimer(self, singleShot=True, interval=CURSOR_HIDE, timeout=self._hide_cursor)
        self.clock = lyrics.Clock()
        self.offset = 0.0
        self.toast_text, self.toast_t, self.toast_secs = "", -1e9, TOAST
        self.backdrop = backdrop.Backdrop(settings.value("lyrics_bg", "auto"))
        # transiciones: opción elegida, estilo en curso y contexto musical para la Automática
        self.trans_setting = settings.value("lyrics_transition", "auto")
        if self.trans_setting not in transitions.SETTINGS:
            self.trans_setting = "auto"
        self.trans_P, self.prev_style = None, None
        self.mood, self.drop_t = "normal", -1e9
        self.chorus, self.gaps = set(), []
        self.times = []
        self.labels = OrderedDict()  # caché LRU de etiquetas ya dibujadas
        self.rands = {}
        self.setWindowTitle("Estela · Letras")
        self.setMinimumSize(480, 270)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.family = _font_family()
        self.fonts = {}
        self.cover = cover.Cover(settings.value("lyrics_cover", "card"), self.family)
        self.hd_key = None  # canción cuya portada grande (iTunes) ya está puesta: la de Windows no la pisa
        self.snap = None
        self.lines, self.groups = [], []
        self.status = "Esperando música…"
        self.rgb = FALLBACK
        self.star = _star_path()
        self.halo = self.nebula = self.bg = None
        self.twinklers = []
        self.stanza_cache = {}
        self.shown = None  # estrofa en pantalla
        self.trans_t0 = -1e9
        self.old_items, self.last_items, self.leaving_now = [], [], []
        self.animating = False
        frames.tick.connect(self._on_tick)  # 60 fps sincronizados con la pantalla, solo si hace falta
        # al final: restaurar una ventana maximizada lanza changeEvent, que ya necesita el estado de arriba
        geo = settings.value("lyrics_geometry")
        if geo is None or not self.restoreGeometry(geo):
            self.resize(960, 540)

    # --- estado que llega desde main ---
    def set_snapshot(self, snap):
        self.snap = snap
        if snap and snap["color"] and tuple(snap["color"]) != tuple(self.rgb):
            self.rgb = tuple(snap["color"])
            self.halo = self.nebula = None  # se regeneran con el color nuevo
            self.labels.clear()
        if snap and not snap["ad"]:
            key = (snap["title"], snap["artist"], snap["album"])
            if key != self.cover.key:
                self.hd_key = None  # canción nueva: hasta que llegue su portada grande vale la de Windows
                self.history.add(key, snap["title"], snap["artist"])
            self.cover.set_track(key, snap["title"], snap["artist"], snap["album"])
            if self.hd_key != key and snap.get("art_id"):  # solo convierte si cambió la portada
                if snap.get("backdrop"):
                    self.backdrop.set_art(snap["art_id"], *snap["backdrop"])
                if snap.get("cover"):
                    self.cover.set_art(snap["art_id"], *snap["cover"])
                    self._thumb(key)
        self._update_timer()
        self.update()

    def set_lyrics(self, status, lines):
        self.status, self.lines = status, lines
        self.loco_k, self.loco_n, self.loco_next = None, 0, None  # canción nueva: Loco vuelve a contar
        self.times = [t for t, _ in lines]
        self.groups = layout.stanzas(lines)
        self.chorus = layout.chorus(lines, self.groups)  # estribillos: transición de impacto en Automática
        self.gaps = layout.gaps(lines)  # instrumentales largos: carátula grande
        self.stanza_cache.clear()
        self.kin.clear()
        # la última estrofa de la canción anterior termina de irse (no desaparece de golpe)
        self.old_items = [dict(it) for it in self.last_items] if self.last_items else self.leaving_now
        self.shown, self.last_items, self.trans_t0 = None, [], time.monotonic()
        self._update_timer()
        self.update()

    def set_offset(self, seconds, show=False):
        self.offset = seconds
        if show:
            self.toast(f"Letra {seconds:+.2f} s".replace(".", ","))

    def toast(self, text, secs=TOAST):
        """Aviso breve arriba a la derecha."""
        self.toast_text, self.toast_t, self.toast_secs = text, time.monotonic(), secs
        self.update()
        QTimer.singleShot(int(secs * 1000) + 100, self.update)

    def set_bg(self, mode, show=False):
        self.backdrop.set_mode(mode)
        self.settings.setValue("lyrics_bg", self.backdrop.setting)
        if show:
            self.toast(f"Fondo: {backdrop.NAMES[self.backdrop.setting]}")
        self.update()

    def bg_mode(self):
        return self.backdrop.setting

    def set_auto_bg(self, mode):
        """Fondo que decide el ambiente (solo cuenta con la opción Automático)."""
        self.backdrop.set_auto(mode)

    def set_transition(self, setting, show=False):
        if setting in transitions.SETTINGS:
            self.trans_setting = setting
            self.settings.setValue("lyrics_transition", setting)
            if show:
                self.toast(f"Transición: {transitions.SETTING_NAMES[setting]}")

    def set_mood(self, mood):
        self.mood = mood

    def set_drop(self):
        self.drop_t = time.monotonic()
        self.beats.extend((self.drop_t, self.drop_t + 0.09, self.drop_t + 0.18))  # Latido: tres anillos

    def in_gap(self):
        """¿Está sonando un instrumental largo? (para el fondo automático)."""
        if not self.snap or not self.gaps:
            return False
        pos = self.position()
        return any(a <= pos <= b for a, b in self.gaps)

    def set_hd_art(self, key, art_id, backdrop_data, cover_data):
        """Portada grande (iTunes) de la canción `key`; se ignora si ya suena otra (un anuncio no cuenta)."""
        if key != self.cover.key:
            return
        self.hd_key = key
        self.backdrop.set_art(art_id, *backdrop_data)
        self.cover.set_art(art_id, *cover_data)
        self._thumb(key)
        self.update()

    def set_queue(self, queue):
        self.queue = queue

    def _carousel(self):
        """Lo escuchado arriba, la que suena en el medio y la cola abajo, listo para pintar."""
        items = self.history.items
        out = []
        playing = items[0] if items and self.snap and not self.snap["ad"] else None  # sin música no hay "suena ahora"
        for kind, it, ref in history.carousel(items[1:] if playing else items, playing, self.queue):
            thumb = it.get("thumb") if kind != "next" else self._queue_thumb(it["thumb"])
            out.append({"title": it["title"], "artist": it["artist"], "thumb": thumb, "kind": kind,
                        "entry": (kind, it, ref)})
        self.carousel = out
        return out

    def _queue_thumb(self, url):
        """Miniatura de la cola: se baja una vez en un hilo y se convierte aquí (hilo de Qt)."""
        got = self.qthumbs.get(url)
        if isinstance(got, bytes):
            img = QImage.fromData(got)
            got = self.qthumbs[url] = img if not img.isNull() else None
        elif url not in self.qthumbs and url.startswith("https://"):
            self.qthumbs[url] = False  # en camino
            if len(self.qthumbs) > 100:
                self.qthumbs.clear()

            def work():
                try:
                    self.qthumbs[url] = ytm.fetch(url, 1_000_000)
                except Exception:
                    self.qthumbs[url] = None

            threading.Thread(target=work, daemon=True).start()
        return got or None

    def _thumb(self, key):
        """Miniatura de la portada actual para "Sonó antes" (pequeña: no guarda la de 1000 px)."""
        if self.cover.img is not None:
            self.history.set_thumb(key, self.cover.img.scaled(192, 192, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))

    def set_cover(self, mode, show=False):
        self.cover.set_mode(mode)
        self.settings.setValue("lyrics_cover", self.cover.mode)
        self.stanza_cache.clear()  # la esquina reservada cambia
        if show:
            self.toast(f"Carátula: {cover.NAMES[self.cover.mode]}")
        self.update()

    def cover_mode(self):
        return self.cover.mode

    def wants_audio(self):
        """El fondo Al ritmo y los automáticos necesitan el medidor aunque el borde esté apagado."""
        return (self.isVisible() and not self.isMinimized()
                and (self.backdrop.setting in ("beat", "auto") or self.trans_setting == "auto"
                     or self.style != "constellation" or self.style_setting == "loco"))

    def set_energy(self, e):
        self.backdrop.energy = e

    def beat(self):
        self.backdrop.beat()
        self.beat_t = time.monotonic()
        self.beats.append(self.beat_t)
        self.beats_total += 1  # Chladni: la figura cambia cada 4 golpes

    def set_nyc_bg(self, bg, show=False):
        if bg in nyc.BGS:
            self.nyc_bg = bg
            self.settings.setValue("nyc_bg", bg)
            if show:
                self.toast(f"Fondo: {nyc.BG_NAMES[bg]}")
            self.update()

    def set_style(self, setting, show=False):
        if setting in kinetic.SETTINGS:
            self.style_setting = setting
            self.settings.setValue("lyrics_style", setting)
            self.loco_next = self.loco_tr = None  # un cambio a mano corta lo que hubiera en marcha
            self.loco_k, self.loco_n, self.loco_t = None, 0, time.monotonic()
            if setting not in ("auto", "loco"):  # Automático y Loco siguen con el que hay hasta su próximo cambio
                self._apply_style(setting)
            if show:
                self.toast(f"Estilo: {kinetic.SETTING_NAMES[setting]}")

    def set_auto_style(self, style, genre):
        """Estilo que pide el género de la canción (solo con la opción Automático)."""
        style = style or "constellation"  # género desconocido o sin género: el principal
        if self.style_setting == "auto" and style != self.style:
            self._apply_style(style)
            self.toast(f"{kinetic.STYLE_NAMES[style]} · {genre}" if genre else f"Estilo: {kinetic.STYLE_NAMES[style]}")

    def _apply_style(self, style):
        self.style = style
        if self.style_setting != "loco":  # en Loco se quedan: volver a Nueva York no da tirones
            self.nyc_words.clear()  # pixmaps grandes de Nueva York: fuera al cambiar de estilo
            self.nyc_sky = None
        # al volver a Constelación no hay transición desde una estrofa vieja
        self.shown, self.last_items, self.old_items, self.leaving_now = None, [], [], []
        self.update()

    def set_quality(self, setting, show=False):
        if setting in quality.SETTINGS:
            self.quality.setting = setting
            self.settings.setValue("quality", setting)
            if show:
                self.toast(f"Calidad: {quality.NAMES[setting]}")

    def toggle_fullscreen(self):
        if not self.isFullScreen():
            self.normal_geo = self.saveGeometry()  # la que se guarda al cerrar: no arranca a pantalla completa
        self.setWindowState(self.windowState() ^ Qt.WindowFullScreen)  # vuelve a normal o maximizada
        if self.isFullScreen():
            self.toast("Esc o F11 para salir de pantalla completa", 2.5)
            self.cursor_timer.start()
        else:
            self.unsetCursor()

    def position(self):
        """Posición de la canción para la letra: reloj suavizado + desfase manual."""
        snap = self.snap
        if not snap:
            return 0.0
        key = (snap["title"], snap["artist"], snap["album"])
        return self.clock.get(media.position(snap), snap["playing"], time.monotonic(), key) + self.offset

    def line_index(self, pos):
        return bisect.bisect_right(self.times, pos) - 1

    def _update_timer(self):
        playing = bool(self.snap) and self.snap["playing"] and not self.snap["ad"]
        run = self.isVisible() and not self.isMinimized() and playing
        if run != self.animating:
            self.animating = run
            self.frames.want("lyrics", run)

    def _on_tick(self):
        if self.animating or self.wheel_on:
            self.skip = self.quality.light and not self.skip  # Ligera: uno de cada dos (30 fps)
            if not self.skip:
                self.update()

    def _wheel_frames(self):
        now = time.monotonic()
        on = self.wheel.busy(now) or self.panels.busy(now)  # rueda y paneles: fotogramas solo mientras se animan
        if on != self.wheel_on:
            self.wheel_on = on
            self.frames.want("wheel", on)

    def cycle(self, action):
        """Siguiente opción de una acción (teclas y rueda); avisa a la bandeja para marcarla."""
        if action == "bg" and self.style == "nyc":  # Nueva York tiene sus propios fondos
            self.set_nyc_bg(nyc.next_bg(self.nyc_bg), show=True)
            self.nyc_bg_changed.emit(self.nyc_bg)
        elif action == "bg":
            self.set_bg(backdrop.next_mode(self.backdrop.setting), show=True)
            self.bg_changed.emit(self.backdrop.setting)
        elif action == "transition":
            self.set_transition(transitions.next_setting(self.trans_setting), show=True)
            self.transition_changed.emit(self.trans_setting)
        elif action == "cover":
            self.set_cover(cover.next_mode(self.cover.mode), show=True)
            self.cover_changed.emit(self.cover.mode)
        elif action == "style":
            self.set_style(kinetic.next_style(self.style_setting), show=True)
            self.style_changed.emit(self.style_setting)
        elif action == "quality":
            self.set_quality(quality.next_setting(self.quality.setting), show=True)
            self.quality_changed.emit(self.quality.setting)
        elif action == "fullscreen":
            self.toggle_fullscreen()
        elif action == "ytm":
            self.open_ytm.emit()

    def wheel_value(self, action):
        style = kinetic.STYLE_NAMES[self.style]
        return {"style": {"auto": f"Automático · {style}", "loco": f"Loco · {style}"}.get(self.style_setting, style), "transition": transitions.SETTING_NAMES[self.trans_setting],
                "bg": nyc.BG_NAMES[self.nyc_bg] if self.style == "nyc" else backdrop.NAMES[self.backdrop.setting], "cover": cover.NAMES[self.cover.mode],
                "quality": quality.NAMES[self.quality.setting],
                "fullscreen": "salir" if self.isFullScreen() else "activar", "ytm": "abrir"}[action]

    KEYS = {Qt.Key_B: "bg", Qt.Key_T: "transition", Qt.Key_C: "cover", Qt.Key_L: "style",
            Qt.Key_F: "fullscreen", Qt.Key_F11: "fullscreen"}

    CONTROLS = {Qt.Key_Space: "play", Qt.Key_N: "next", Qt.Key_P: "prev"}

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and e.modifiers() & Qt.ShiftModifier:
            self.control.emit("fwd" if e.key() == Qt.Key_Right else "back")
        elif e.key() in self.CONTROLS and e.isAutoRepeat():
            pass  # mantener Espacio o N no alterna ni salta sin parar
        elif e.key() in self.CONTROLS:
            self.control.emit(self.CONTROLS[e.key()])
        elif e.key() in (Qt.Key_Left, Qt.Key_Right):
            self.offset_step.emit(0.1 if e.key() == Qt.Key_Right else -0.1)
        elif e.key() in self.KEYS:
            self.cycle(self.KEYS[e.key()])
        elif e.key() == Qt.Key_H:
            self.toast(HELP, 6)
        elif e.key() == Qt.Key_M:
            self.wheel.toggle()  # abierta con tecla: no se cierra sola hasta que el ratón pase y se vaya
            self._wheel_frames()
            self.update()
        elif e.key() == Qt.Key_Escape:
            if self.wheel.target:
                self.wheel.toggle(False)
                self._wheel_frames()
            elif self.isFullScreen():
                self.toggle_fullscreen()
        else:
            super().keyPressEvent(e)

    # --- ratón: rueda, doble clic y cursor en pantalla completa ---
    def mouseMoveEvent(self, e):
        x, y, now = e.position().x(), e.position().y(), time.monotonic()
        pn = self.panels
        before = (self.wheel.near, self.wheel.hover, pn.near_l, pn.near_r, pn.btn, pn.item)
        self.wheel.move(x, y, self.width(), now)
        pn.move(x, y, self.width(), self.height(), now, block=bool(self.wheel.target))
        self._wheel_frames()
        if self.isFullScreen():
            self.unsetCursor()
            self.cursor_timer.start()
        if (self.wheel.near, self.wheel.hover, pn.near_l, pn.near_r, pn.btn, pn.item) != before and not self.animating:
            self.update()  # sonando ya repinta el reloj

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            x, y = e.position().x(), e.position().y()
            action = self.wheel.press(x, y, self.width())
            hit = None if action else self.panels.press(x, y, self.width(), self.height(), time.monotonic())
            if action and action != "toggle":
                self.cycle(action)
            elif hit and hit[0] == "control":
                self.control.emit(hit[1])
            elif hit and hit[1] < len(self.carousel):
                self.song.emit(self.carousel[hit[1]]["entry"])
            self._wheel_frames()
            self.update()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if e.button() != Qt.LeftButton:
            return
        if self.panels.hit(x, y, self.width(), self.height()):
            self.mousePressEvent(e)  # dos clics rápidos en "siguiente" o "+10 s" cuentan dos veces
        elif not self.wheel.hit(x, y, self.width()):
            self.toggle_fullscreen()

    def leaveEvent(self, e):
        now = time.monotonic()
        self.wheel.move(-1e4, -1e4, self.width(), now)
        self.panels.move(self.width() / 2, -1e4, self.width(), self.height(), now)
        self._wheel_frames()
        super().leaveEvent(e)

    def _hide_cursor(self):
        if self.isFullScreen() and not self.wheel.target and not self.panels.left and not self.panels.right:
            self.setCursor(Qt.BlankCursor)

    # --- ventana ---
    def showEvent(self, e):
        try:  # barra de título oscura (Windows 10 2004+ / 11)
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(on), 4)
        except OSError:
            pass
        self.shown, self.last_items, self.old_items = None, [], []  # sin transición de una estrofa vieja
        super().showEvent(e)
        self._update_timer()
        if not self.settings.value("help_shown", False, type=bool):  # la primera vez: los atajos
            self.settings.setValue("help_shown", True)
            self.toast(FIRST_TIP, 6)  # corto: el de atajos taparía el botón de la rueda
            self.wheel.hint_until = self.panels.hint_until = time.monotonic() + 4  # que se vea dónde está todo
            self._wheel_frames()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._update_timer()
        self.wheel.reset()  # oculta no se pinta: una animación a medias pediría fotogramas para siempre
        self.panels.reset()
        self._wheel_frames()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:  # minimizar / restaurar
            self._update_timer()
            if self.isMinimized():
                self.wheel.reset()
                self.panels.reset()
                self._wheel_frames()
            if not self.isFullScreen():  # salió por la bandeja, Win+↓…: el cursor vuelve
                self.cursor_timer.stop()
                self.unsetCursor()

    def resizeEvent(self, e):
        self.bg = self.nebula = None
        self.loco_tr = None  # la instantánea es del tamaño de antes
        self.glyphs.clear()
        self.nyc_words.clear()  # Nueva York dibuja cada palabra a 0,3 del alto
        self.labels.clear()  # los tamaños de letra dependen de la altura
        self.stanza_cache.clear()  # y la esquina reservada para la tarjeta
        super().resizeEvent(e)

    def closeEvent(self, e):
        self.settings.setValue("lyrics_geometry", self.normal_geo if self.isFullScreen() else self.saveGeometry())
        super().closeEvent(e)

    # --- cachés ---
    def _font(self, px, bold=False, kind=""):
        """kind: "" normal, "light" fina, "heavy" Bahnschrift gruesa (Latido intenso)."""
        key = (round(px), bold, kind)
        if key not in self.fonts:
            f = QFont(self.family)
            if kind == "heavy":
                f = QFont("Bahnschrift")  # Windows 10 1709+; si falta, la de siempre
                if not f.exactMatch():
                    f = QFont(self.family)
            elif kind == "condensed":  # Cinta: palo seco estrecho y grueso, como el del vídeo de referencia
                f = QFont("Bahnschrift Condensed")
                if "Bahnschrift" not in QFontInfo(f).family():
                    f = QFont("Bahnschrift")
                    f.setStretch(QFont.Condensed)
                if "Bahnschrift" not in QFontInfo(f).family():
                    f = QFont("Impact")  # en todos los Windows
            f.setPixelSize(max(8, key[0]))
            f.setWeight({"light": QFont.Light, "heavy": QFont.Bold, "condensed": QFont.Bold}.get(kind, QFont.DemiBold if bold else QFont.Normal))
            self.fonts[key] = f
        return self.fonts[key]

    def _color(self, alpha, rgb=None):
        return QColor(*(rgb or self.rgb), max(0, min(255, round(255 * alpha))))

    def _build_bg(self):
        """Estrellas de fondo en una capa transparente: el fondo (portada o negro) las hornea en su caché."""
        w, h = self.width(), self.height()
        rnd = random.Random(42)
        pm = QPixmap(self.size())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for _ in range(BG_STARS):
            p.setBrush(QColor(255, 255, 255, rnd.randint(30, 120)))
            r = rnd.uniform(0.4, 1.2)
            p.drawEllipse(QPointF(rnd.uniform(0, w), rnd.uniform(0, h)), r, r)
        p.end()
        self.bg = pm
        self.backdrop.set_stars(pm)
        self.twinklers = [(rnd.random(), rnd.random(), rnd.uniform(2, 5), rnd.uniform(0, 6.28), rnd.uniform(0.8, 1.6))
                          for _ in range(TWINKLERS)]

    def _build_halo(self):
        pm = QPixmap(128, 128)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        g = QRadialGradient(QPointF(64, 64), 64)
        g.setColorAt(0, self._color(0.9))
        g.setColorAt(0.25, self._color(0.35))
        g.setColorAt(1, self._color(0))
        p.fillRect(pm.rect(), g)
        p.end()
        self.halo = pm

    def _build_nebula(self):
        w, h = self.width(), self.height()
        pm = QPixmap(max(1, w // 4), max(1, h // 4))  # pequeña y ampliada: suave y barata
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        g = QRadialGradient(QPointF(pm.width() * 0.5, pm.height() * 0.55), max(pm.width(), pm.height()) * 0.6)
        g.setColorAt(0, self._color(1))
        g.setColorAt(1, self._color(0))
        p.fillRect(pm.rect(), g)
        p.end()
        self.nebula = pm

    def _stanza(self, k):
        """(palabras [(texto, inicio)], puntos 0..1) de la estrofa k, calculado una vez."""
        if k not in self.stanza_cache:
            words = []
            for li in self.groups[k]:
                words += zip(lyrics.words(self.lines[li][1]), lyrics.word_times(self.lines, li))
            seed = zlib.crc32(f"{self.snap['title'] if self.snap else ''}|{k}".encode())
            avoid = self.cover.avoid(self.width(), self.height())  # sin estrellas bajo la tarjeta
            self.stanza_cache[k] = (words, layout.place(len(words), seed, avoid))
        return self.stanza_cache[k]

    # --- dibujo ---
    def paintEvent(self, e):
        t0 = time.perf_counter()
        now = time.monotonic()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)  # halo, nebulosa y etiquetas escaladas o en posición decimal
        if self.animating:
            self._loco_check(now)
            if self.loco_next:
                self._loco_begin(now)
        self._paint(p, now)
        if self.loco_tr:
            self._loco_draw(p, now)
        w = self.width()
        if now - self.toast_t < self.toast_secs and self.wheel.open == 0:  # aviso (con la rueda, su etiqueta) en una pastilla oscura (legible sobre portadas claras)
            pm = self._text_pixmap(self.toast_text, 13, False, (255, 255, 255), False)
            tw, th = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            pill = QRectF(w - tw - 16 - 12, 16 - 6, tw + 24, th + 12)
            p.setOpacity(0.75 * min(1.0, 3 * (1 - (now - self.toast_t) / self.toast_secs)))
            path = QPainterPath()
            path.addRoundedRect(pill, 8, 8)
            p.fillPath(path, QColor(0, 0, 0, 90 if self.style != "ribbon" else 200))
            p.drawPixmap(QPointF(pill.x() + 12, pill.y() + 6), pm)
            p.setOpacity(1.0)
        snap = self.snap
        self.panels.draw(p, self.width(), self.height(), now, {
            "playing": bool(snap) and snap["playing"], "rgb": self.rgb, "paper": self.style == "ribbon",
            "style": self.style, "items": self._carousel(), "thumb": lambda it, side: it["thumb"],
            "label": lambda t, px, bold: self._text_pixmap(t, px, bold, (255, 255, 255), False),
            "beat": effects.env(now - self.beat_t, 0.04, 0.22), "light": self.quality.light})
        if self.wheel.open > 0:  # la letra se apaga un poco bajo la rueda abierta
            p.fillRect(self.rect(), QColor(0, 0, 0, round(110 * self.wheel.open)))
        self.wheel.draw(p, self.width(), self.rgb, self.wheel_value,
                        lambda t: self._text_pixmap(t, 13, True, (255, 255, 255), False), now)
        p.end()
        self._wheel_frames()
        if self.animating and self.quality.feed((time.perf_counter() - t0) * 1000, now):
            self.toast("Calidad ligera para ir más fluido", 3)
            self.quality_changed.emit(self.quality.setting)

    def _paint(self, p, now):
        w, h = self.width(), self.height()
        light = self.quality.light
        if self.style == "ribbon":
            return self._ribbon(p, now, w, h)
        if self.style == "nyc":
            return self._nyc(p, now, w, h)
        if self.bg is None:
            self._build_bg()
        age = now - self.trans_t0  # tiempo desde el último cambio de estrofa
        push = transitions.push(self.trans_P, age) if self.trans_P and not light else 0.0  # Ligera: sin ampliar el fondo
        pulse = self.style == "kinetic"  # Latido: color liso de la portada que late, sin estrellas
        if pulse:
            self._pulse(p, now, w, h)
        else:
            self.backdrop.draw(p, w, h, push=push, live=self.animating)
        snap = self.snap
        pos = self.position() if snap else 0.0
        playing_song = bool(snap) and not snap["ad"]
        # carátula grande: sin letra (estado final, no mientras se busca) o instrumental largo
        # entre dos canciones sin letra la carátula no va y vuelve mientras se busca la siguiente
        no_lyrics = (playing_song and not self.lines and self.status != ""
                     and (self.status != SEARCHING or self.cover.hero_on))
        gap = playing_song and bool(self.lines) and any(a <= pos <= b for a, b in self.gaps)
        self.cover.set_hero(no_lyrics or gap, 1.0 if no_lyrics else 0.7)
        if self.cover.mode == "center" and not pulse:  # se apaga a la vez que llega la carátula grande (sin parpadeo)
            self.cover.draw_watermark(p, w, h, push, self.devicePixelRatioF(), 1 - self.cover.hero_progress())
        if not light and not pulse:  # Ligera: sin estrellas que titilan ni nebulosa
            self._background(p, now, w, h)
        prog = cover.progress(pos - self.offset, snap["duration"]) if snap else None
        native = 1000 if self.hd_key and self.hd_key == self.cover.key else 120
        hero = playing_song and self.cover.draw_hero(p, w, h, self.rgb, prog, self.backdrop.energy,
                                                     self.devicePixelRatioF(), native)
        if playing_song and not hero and (self.cover.mode == "top" or self.cover.mode == "card" and not pulse):
            self.cover.draw_front(p, w, h, self.rgb, prog, self.devicePixelRatioF())
        elif snap and not hero:
            p.setOpacity(0.4)
            p.drawPixmap(QPointF(16, 10), self._text_pixmap(f"{snap['title']} · {snap['artist']}", 13, False,
                                                             (255, 255, 255), False))
            p.setOpacity(1.0)
        if not snap:
            return self._message(p, "Pon música en tu reproductor ♪")
        if snap["ad"]:
            return self._message(p, "Anuncio · la música vuelve enseguida")
        if self.style == "kinetic" and self.lines:
            return self._kinetic(p, pos, now, w, h, gap, hero)
        if not self.lines:
            self._leaving(p, self.trans_P, age)  # la última estrofa termina de irse
            if hero:  # pie discreto del grupo carátula + textos + barra
                return self._message(p, self.status, y=0.86, alpha=0.45, size=0.020 * h, rgb=(255, 255, 255))
            return self._message(p, self.status)

        # tras un instrumental (hasta la siguiente línea) no vuelve la estrofa de antes
        after_gap = any(b < pos < b + 1.6 for _, b in self.gaps)
        k = None if gap or after_gap else layout.stanza_of(self.groups, self.line_index(pos))
        if k != self.shown:  # cambio de estrofa (o salto, o instrumental): transición
            self._start_transition(k, now)
            age = 0.0
        P = self.trans_P
        old_pts = self._leaving(p, P, age)
        if k is None:
            self.last_items = []
            self._extras(p, P, age, old_pts, [])
            if not hero:
                self._message(p, "♪")
            return

        words, pts = self._stanza(k)
        cur = max(0, sum(1 for _, s in words if s <= pos) - 1)
        vis = layout.visible(range(len(words)), cur)
        items = []
        for i in vis:
            rank = cur - i  # palabras desde la actual (no confundir con `age` de la transición)
            size, alpha = ((SIZE_CURRENT, 1.0) if rank == 0 else (SIZE_PREV, 0.85) if rank == 1
                           else (SIZE_REST, OLD_ALPHA if rank > OLD_AFTER else REST_ALPHA))
            it = self._item(pts[i], words[i][0], size, alpha, now, i, current=rank == 0)
            # estado en pantalla según la transición de llegada (se guarda: si cambia a mitad, sin saltos)
            if P is not None:
                src = old_pts[i] if i < len(old_pts) else (old_pts[-1] if old_pts else None)
                st = transitions.in_state(P, age, it["x"], it["y"], i, len(words), w, h, src)
            else:
                st = (it["x"], it["y"], 1.0, 1.0, 1.0, 0.0)
            it["dx"], it["dy"], it["s"], it["sa"], it["la"], it["trail"] = st
            items.append(it)
        self.last_items = items
        self._extras(p, P, age, old_pts, [(it["x"], it["y"]) for it in items])
        start = words[cur][1]
        self._draw(p, items, (pos - start, start))

    def _glyph(self, ch, px, rgb):
        """Una letra de la Cinta, sin sombra, en caché (se transforma una a una cada fotograma)."""
        key = (ch, round(px), rgb, self.devicePixelRatioF())  # otra pantalla con otra escala: nítidas
        pm = self.glyphs.get(key)
        if pm is None:
            if len(self.glyphs) > 400:
                self.glyphs.clear()
            font = self._font(px, True, "condensed")
            fm = QFontMetricsF(font)
            dpr = self.devicePixelRatioF()
            pm = QPixmap(math.ceil((fm.horizontalAdvance(ch) + 4) * dpr), math.ceil((fm.height() + 4) * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.TextAntialiasing)
            q.setFont(font)
            q.setPen(QColor(*rgb))
            q.drawText(QPointF(2, 2 + fm.ascent()), ch)
            q.end()
            self.glyphs[key] = pm
        return pm

    def _ribbon_text(self, px, fm, title):
        """(tiempos, letras por palabra, anchos, desplazamientos, paso) del texto de la Cinta; una vez por canción y tamaño."""
        snap = self.snap
        key = (round(px), title)
        if self.rib and self.rib[0] == key and self.rib[2] is self.lines:
            return self.rib[1]
        words = [(t, x.upper()) for i, (_, text) in enumerate(self.lines)
                 for t, x in zip(lyrics.word_times(self.lines, i), lyrics.words(text))]
        step = ribbon.WORD_STEP
        if not words:  # sin letra: el título repetido, avanzando sin parar (como en el vídeo)
            words = [(k * 2.5, title) for k in range(int((snap["duration"] or 600) / 2.5) + 3)]
            step = None
        elif words[0][0] > 0:
            words.insert(0, (0.0, title))  # antes de la primera línea pasa el título
        space = (0.12 if step else 0.05) * px  # casi pegadas, como en el vídeo
        times, lets, widths, offs, m = [], [], [], [], 0.0
        for t, x in words:
            adv = [fm.horizontalAdvance(x[:j]) for j in range(len(x) + 1)]
            times.append(t)
            lets.append([(ch, adv[j], adv[j + 1] - adv[j]) for j, ch in enumerate(x)])
            widths.append(adv[-1])
            offs.append(m)
            m += adv[-1] + space
        self.rib = (key, (times, lets, widths, offs, step), self.lines)
        return self.rib[1]

    def _ribbon(self, p, now, w, h):
        """Cinta: papel claro, la letra recorriendo una cinta en S que se retuerce, y el rótulo del título al empezar."""
        p.fillRect(self.rect(), QColor(*ribbon.PAPER))
        snap = self.snap
        if not snap:
            return self._message(p, "Pon música en tu reproductor ♪", rgb=ribbon.INK)
        if snap["ad"]:
            return self._message(p, "Anuncio · la música vuelve enseguida", rgb=ribbon.INK)
        pos, light = self.position(), self.quality.light
        px = (0.18 if light else 0.16) * h  # Ligera: letra más grande, menos letras que mover
        font = self._font(px, True, "condensed")
        fm = QFontMetricsF(font)
        title = snap["title"].upper()
        times, lets, widths, offs, step = self._ribbon_text(px, fm, title)
        if not self.rib_track or self.rib_track[0] != (w, h):
            self.rib_track = ((w, h), ribbon.track(w, h))
        tr = self.rib_track[1]
        length = tr[0][-1]
        anchor = ribbon.ANCHOR * length
        m_now = ribbon.scroll(pos, times, offs, widths, step)
        cur = bisect.bisect_right(times, pos) - 1
        acc = QColor(*self.rgb)  # acento: la portada oscurecida hasta que se lea sobre el papel
        acc.setHslF(acc.hslHueF(), max(0.5, acc.hslSaturationF()), min(0.42, acc.lightnessF()))
        accent = (acc.red(), acc.green(), acc.blue())
        beat = 0.0 if light else effects.env(now - self.beat_t, 0.04, 0.22)
        self._speakers(p, w, h, now, beat, pos)
        p.setRenderHint(QPainter.SmoothPixmapTransform, not light)
        # la cinta aparece cuando se va el rótulo (si se entra a mitad de canción, ya está)
        fade = min(1.0, max(0.0, (pos - ribbon.CARD_END) / 0.4)) if pos < ribbon.CARD_END + 0.4 else 1.0
        p.setOpacity(fade)
        lo, hi = m_now - anchor, (m_now + length - anchor) if fade > 0 else -1e18  # bajo el rótulo, nada
        for i in range(max(0, bisect.bisect_right(offs, lo) - 1), len(offs)):
            if offs[i] > hi:
                break
            for ch, dx, adv in lets[i]:
                a = ribbon.at(tr, anchor + offs[i] + dx + adv / 2 - m_now)
                if a is None or ch == " ":
                    continue
                x, y, ang, u = a
                c = math.cos(ribbon.twist(u, 0.0 if light else now, beat))
                if abs(c) < 0.08 or (c < 0 and light):  # de canto no se ve; en Ligera, sin reverso
                    continue
                pm = self._glyph(ch, px, (accent if i == cur else ribbon.INK) if c > 0 else ribbon.BACK)
                dpr = pm.devicePixelRatio()
                p.save()
                p.translate(x, y)
                p.shear(0, max(-1.2, min(1.2, math.tan(ang))))  # se inclina con la curva, trazos verticales
                p.scale(1, c)  # media vuelta: escala negativa = reverso en espejo
                p.drawPixmap(QPointF(-pm.width() / dpr / 2, -pm.height() / dpr / 2), pm)
                p.restore()
        p.setOpacity(1.0)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._tide(p, w, h, 0.0 if light and not self.animating else now, beat, 24 if light else 64)
        gap = next(((a, b) for a, b in self.gaps if a <= pos <= b), None)
        if gap and gap[1] - gap[0] >= 4:  # instrumental: las flechas cruzan por el centro
            k = min(1.0, (pos - gap[0]) / 0.9, (gap[1] - pos) / 0.9)
            self._chevrons(p, w, 0.40 * h, 0.2 * h, pos, 0.0, 0.9 * max(0.0, k))
        if not self.lines and self.status:
            self._message(p, self.status, y=0.86, alpha=0.5, size=0.022 * h, rgb=ribbon.INK)
        self._ribbon_card(p, pos, w, h, title, snap["artist"])

    def _chevrons(self, p, w, y, bh, pos, shift, alpha=1.0):
        """Franja de flechas ››› de alto bh que avanza una flecha cada 0,4 s."""
        gap, thick = 0.9 * bh, 0.45 * bh
        chev = QPainterPath()
        chev.addPolygon([QPointF(0, 0), QPointF(thick, 0), QPointF(thick + bh / 2, bh / 2), QPointF(thick, bh),
                         QPointF(0, bh), QPointF(bh / 2, bh / 2)])
        p.setOpacity(alpha)
        x = shift + (pos / 0.4 * gap) % gap - gap
        while x < w:
            p.fillPath(chev.translated(x, y), QColor(*ribbon.INK))
            x += gap
        p.setOpacity(1.0)

    def _speakers(self, p, w, h, now, beat, pos):
        """Los dos aros del altavoz: arriba un tocadiscos, abajo un cono que bombea con polvo que salta y,
        en los estribillos, figuras de Chladni; los dos se deforman en ondas como un diafragma."""
        light = self.quality.light
        ink = QColor(*ribbon.INK)
        r, thick = 0.085 * h, 0.035 * h
        intense = 1.5 if self.mood == "intense" else 1.0
        age = now - self.beat_t
        p.setOpacity(1 - 0.7 * max(self.panels.left, self.panels.right))  # no compiten con los paneles
        snap = self.snap
        for corner, (cx, cy) in enumerate(((0.12 * w, 0.22 * h), (0.80 * w, 0.76 * h))):
            c = QPointF(cx, cy)
            # diafragma: el aro ondula con el golpe (3 lóbulos que giran)
            ring = QPainterPath()
            n = 24 if light else 48
            for i in range(n + 1):
                th = 2 * math.pi * i / n
                rr = r * (1 + 0.04 * beat * math.sin(3 * th + now * 5.0))
                pt = QPointF(cx + rr * math.cos(th), cy + rr * math.sin(th))
                ring.moveTo(pt) if i == 0 else ring.lineTo(pt)
            delay = ribbon.cone(age - 0.04, intense)  # el aro sigue a la cúpula 40 ms después
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ink, thick * (2 - delay)))
            p.drawPath(ring)
            inner = r - thick / 2
            if corner == 0:  # tocadiscos: surcos que giran (33⅓ rpm, 45 si va intensa) y aguja según la canción
                p.save()
                p.translate(c)
                p.rotate(pos * (270 if intense > 1 else 200))  # sale de la posición: la pausa lo para
                p.setPen(QPen(ink, 1.5))
                for k, rad in enumerate((0.35, 0.5, 0.65, 0.8)):
                    rr = inner * rad
                    p.drawArc(QRectF(-rr, -rr, 2 * rr, 2 * rr), k * 90 * 16, 40 * 16)
                p.setPen(Qt.NoPen)
                p.setBrush(ink)
                p.drawEllipse(QPointF(0, 0), 0.12 * inner, 0.12 * inner)
                p.restore()
                frac = min(1.0, pos / snap["duration"]) if snap and snap["duration"] else 0.0
                a = 2 * math.pi * frac - math.pi / 2
                p.setPen(QPen(ink, 1.5, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(cx + (r + thick) * math.cos(a), cy + (r + thick) * math.sin(a)),
                           QPointF(cx + (r + thick * 1.8) * math.cos(a), cy + (r + thick * 1.8) * math.sin(a)))
                continue
            # cono: la cúpula se hunde con el golpe y rebota
            dome = 0.4 * r * ribbon.cone(age, intense)
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawEllipse(c, dome, dome)
            # figuras de Chladni dentro del aro durante el estribillo (cambian cada 4 golpes)
            line = self.line_index(pos)
            if line >= 0 and layout.stanza_of(self.groups, line) in self.chorus:
                pm = self._chladni(self.beats_total // 4)
                p.setOpacity(p.opacity() * min(1.0, max(0.0, (pos - self.lines[line][0]) / 0.6)))
                p.save()
                clip = QPainterPath()
                clip.addEllipse(c, inner * 0.95, inner * 0.95)
                p.setClipPath(clip)
                p.drawPixmap(QRectF(cx - inner, cy - inner, 2 * inner, 2 * inner), pm, QRectF(pm.rect()))
                p.restore()
                p.setOpacity(1 - 0.7 * max(self.panels.left, self.panels.right))
            # polvo: motas sobre el aro que saltan con el golpe; en un drop saltan todas y las altas se tiñen
            drop = 0 <= now - self.drop_t < 0.4
            acc = QColor(*self.rgb)
            acc.setHslF(acc.hslHueF(), max(0.5, acc.hslSaturationF()), min(0.42, acc.lightnessF()))
            rnd = self._rand(4242, 72)
            motes = 8 if light else 24
            hops = []
            for i in range(motes):
                th = math.pi * (1.1 + 0.8 * rnd[i]) + (rnd[24 + i] - 0.5) * 0.3  # sobre la mitad de arriba
                v = (0.5 + 0.5 * rnd[48 + i]) * (1.6 if drop else 1.0) * 0.6 * r  # fijo durante el salto
                lift = ribbon.hop((now - self.drop_t) if drop else age, v / (0.6 * r), 0.4) * 0.6 * r
                hops.append((lift, th, i))
            top = sorted(hops, reverse=True)[:3] if drop else []
            for lift, th, i in hops:
                rr = r + thick / 2 + 2 + lift
                p.setBrush(acc if (lift, th, i) in top else ink)
                p.drawEllipse(QPointF(cx + rr * math.cos(th), cy + rr * math.sin(th)), 1.6, 1.6)
        p.setOpacity(1.0)

    def _chladni(self, k):
        """Figura de Chladni k (n, m distintos), dibujada una vez en 64 px y reutilizada."""
        n, m = (2, 3, 4, 5, 6, 3)[k % 6], (2, 3, 2, 3, 4, 4)[k % 6]
        if (n, m) not in self.chladni:
            size = 64  # pequeña: se dibuja una vez sin tirón y se amplía suave
            img = QImage(size, size, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            for y in range(size):
                for x in range(size):
                    u, v = 2 * x / (size - 1) - 1, 2 * y / (size - 1) - 1
                    if u * u + v * v <= 1 and abs(ribbon.chladni(n, m, u, v)) < 0.09:
                        img.setPixelColor(x, y, QColor(*ribbon.INK, 200))
            self.chladni[(n, m)] = QPixmap.fromImage(img)
        return self.chladni[(n, m)]

    def _tide(self, p, w, h, now, beat, n):
        """Marea de tinta abajo (el agua del vídeo): dos ondas sumadas que crecen con el golpe."""
        amp = 0.012 * h * (1 + 1.5 * beat)
        path = QPainterPath(QPointF(0, h))
        for i in range(n + 1):
            x = w * i / n
            y = 0.95 * h + amp * (math.sin(x / w * 7 + now * 1.3) + 0.6 * math.sin(x / w * 13 - now * 0.9))
            path.lineTo(x, y)
        path.lineTo(w, h)
        path.closeSubpath()
        p.fillPath(path, QColor(*ribbon.INK))

    def _ribbon_card(self, p, pos, w, h, title, artist):
        """Rótulo al empezar la canción: flechas que cruzan arriba, barra y título letra a letra."""
        c = ribbon.card(pos)
        if c is None:
            return
        arrows, bar, frac, out = c
        ink = QColor(*ribbon.INK)
        # franja de chevrones ››› arriba, entrando por la izquierda y saliendo por la derecha
        self._chevrons(p, w, -0.04 * h, 0.28 * h, pos, -w * (1 - arrows) + w * 1.1 * out)
        # título: barra vertical y como mucho 2 filas que se escriben letra a letra
        px = 0.10 * h
        font = self._font(px, True, "condensed")
        fm = QFontMetricsF(font)
        words = title.split()
        if not words:
            return
        rows = [" ".join(words[i] for i in r)
                for r in kinetic.wrap([fm.horizontalAdvance(x) for x in words], fm.horizontalAdvance(" "), 0.8 * w)][:2]
        lh = px * 1.0
        x0, top = 0.06 * w, 0.62 * h - len(rows) * lh
        p.fillRect(QRectF(x0, top, max(2.0, 0.004 * h), bar * (len(rows) * lh + 0.05 * h)), ink)
        shown = frac * sum(len(r) for r in rows)
        p.setFont(font)
        p.setPen(ink)
        tx = x0 + 0.012 * w
        for r, row in enumerate(rows):
            n = max(0.0, min(len(row), shown))
            shown -= len(row)
            vis = fm.horizontalAdvance(row[:math.ceil(n)]) * (1 - out)  # salida: se borra de derecha a izquierda
            if vis <= 0:
                continue
            p.save()
            p.setClipRect(QRectF(tx, top + r * lh - 0.2 * px, vis, lh + 0.4 * px))
            p.drawText(QPointF(tx, top + r * lh + fm.ascent() * 0.85), row)
            p.restore()
        if artist:
            p.setOpacity(0.6 * bar * (1 - out))
            p.setFont(self._font(0.03 * h, False))
            p.drawText(QPointF(tx, top + len(rows) * lh + 0.045 * h), artist)
            p.setOpacity(1.0)

    # --- modo Loco ---
    def _loco_check(self, now):
        """¿Toca cambiar de estilo? Por estrofa nueva (o estribillo) o por drop; deja preparado el cambio."""
        if self.style_setting != "loco" or not self.snap or self.snap["ad"] or self.loco_tr:
            return
        pos = self.position()
        line = self.line_index(pos) if self.lines else -1
        k = layout.stanza_of(self.groups, line) if line >= 0 else None  # sin letra: solo cambian los drops
        reason = None
        if now - self.drop_t < 0.5 and self.drop_t > self.loco_t:
            reason = "drop"
        elif k != self.loco_k:
            prev, self.loco_k = self.loco_k, k
            if prev is None or k is None or k < prev or k > prev + 1:  # salto o instrumental: vuelve a contar
                self.loco_n = 0
                return
            self.loco_n += 1
            reason = "chorus" if k in self.chorus and prev not in self.chorus else "stanza"
        if reason and loco.decide(reason, self.mood, self.loco_n, now - self.loco_t):
            seed = zlib.crc32(f"{self.snap['title']}|{k}|{self.loco_seq}".encode())
            kind = loco.pick(reason if reason != "stanza" else self.mood, self.mood, self.loco_last, seed,
                             self.quality.light)
            self.loco_next = (loco.next_style(kinetic.STYLES, self.style, seed), kind, reason)

    def _loco_begin(self, now):
        """En el mismo fotograma: instantánea del estilo que se va (sin parpadeo) y cambio al nuevo."""
        style, kind, _ = self.loco_next
        self.loco_next = None
        dpr = self.devicePixelRatioF() * (0.5 if self.quality.light else 1.0)  # Ligera: media resolución
        pm = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.setRenderHint(QPainter.SmoothPixmapTransform)
        self._paint(q, now)
        q.end()
        self._apply_style(style)
        self.loco_tr = (kind, now, pm, self.loco_seq, loco.duration(kind, self.mood))  # duración fija al empezar
        self.loco_cache = {}
        self.loco_last, self.loco_t, self.loco_n, self.loco_seq = kind, now, 0, self.loco_seq + 1

    def _loco_draw(self, p, now):
        """La instantánea del estilo anterior, recortada o transformada encima del nuevo según la transición."""
        kind, t0, pm, seed, dur = self.loco_tr
        pr = (now - t0) / dur
        if pr >= 1:
            self.loco_tr = None
            return
        w, h = self.width(), self.height()
        full = QRectF(0, 0, w, h)
        src = QRectF(pm.rect())
        c = QPointF(w / 2, h / 2)
        half = math.hypot(w, h) / 2
        beat = effects.env(now - self.beat_t, 0.02, 0.12)
        energy = self.backdrop.energy
        p.save()
        if kind in ("iris", "liquid", "shock"):  # se ve el nuevo dentro de una forma que crece
            hole = QPainterPath()
            n = 96
            for i in range(n + 1):
                th = 2 * math.pi * i / n
                if kind == "iris":
                    r = loco.iris(th, pr, beat)
                elif kind == "liquid":
                    r = loco.blob(th, pr, now, energy, beat)
                else:
                    r = loco.out_expo(pr) * 1.05
                pt = QPointF(c.x() + half * r * math.cos(th), c.y() + half * r * math.sin(th))
                hole.moveTo(pt) if i == 0 else hole.lineTo(pt)
            clip = QPainterPath()
            clip.addRect(full)
            clip = clip.subtracted(hole)
            p.setClipPath(clip)
            p.drawPixmap(full, pm, src)
            p.setClipping(False)
            edge = QColor(*self.rgb) if kind == "liquid" else QColor(255, 255, 255)
            if kind == "shock":  # frente de la onda: anillo que se adelgaza y refracción falsa detrás
                ring = half * loco.out_expo(pr) * 1.05
                band = QPainterPath()
                band.addEllipse(c, ring + 60 * (1 - pr), ring + 60 * (1 - pr))
                inner = QPainterPath()
                inner.addEllipse(c, ring, ring)
                p.setClipPath(band.subtracted(inner))
                p.setOpacity(0.8 * (1 - pr))
                p.drawPixmap(full.translated(12 * (1 - pr), 0), pm, src)
                p.setClipping(False)
                edge.setAlphaF(0.9 * (1 - pr))
                p.setPen(QPen(edge, 3 + 6 * (1 - pr)))
            else:
                edge.setAlphaF(0.8 * (1 - pr))
                p.setPen(QPen(edge, 4 if kind == "iris" else 10))
            p.setBrush(Qt.NoBrush)
            p.setOpacity(1.0)
            if hole.boundingRect().width() > 2:  # el iris aún cerrado no deja un punto en el centro
                p.drawPath(hole)
        elif kind == "shards":  # cristal roto: grietas y trozos que salen volando
            if pr < 0.12:
                p.drawPixmap(full, pm, src)
                p.setPen(QPen(QColor(255, 255, 255, round(200 * pr / 0.12)), 1.5))
                for tri in loco.shards(seed):
                    poly = [QPointF(x * w, y * h) for x, y in tri]
                    p.drawPolyline(poly + poly[:1])
            else:
                for i, tri in enumerate(loco.shards(seed)):
                    dx, dy, rot, sc, alpha = loco.shard_state(tri, i, pr, energy)
                    if alpha <= 0:
                        continue
                    cx = sum(v[0] for v in tri) / 3 * w
                    cy = sum(v[1] for v in tri) / 3 * h
                    path = QPainterPath()
                    path.addPolygon([QPointF(x * w, y * h) for x, y in tri])
                    path.closeSubpath()
                    box = path.boundingRect()
                    k = pm.width() / w  # de la ventana a la instantánea (DPR o media resolución)
                    p.save()
                    p.setOpacity(alpha)
                    p.translate(cx + dx * w, cy + dy * h)
                    p.rotate(rot)
                    p.scale(sc, sc)
                    p.translate(-cx, -cy)
                    p.setClipPath(path)
                    p.drawPixmap(box, pm, QRectF(box.x() * k, box.y() * k, box.width() * k, box.height() * k))
                    p.restore()
        elif kind == "swirl":  # remolino: gira y se hunde en el centro
            e = loco.in_cubic(loco.clamp((pr - 0.1) / 0.75))
            p.setOpacity(1 - 0.7 * e if pr < 0.85 else (1 - pr) / 0.15 * 0.3)
            p.translate(c)
            p.rotate(-8 * min(1.0, pr / 0.1) + 540 * e + 20 * beat)
            p.scale(1 - e, 1 - e)
            p.translate(-c)
            p.drawPixmap(full, pm, src)
        elif kind == "blinds":  # persiana inclinada: franjas que se cierran escalonadas
            p.translate(c)
            p.rotate(18)
            p.translate(-c)
            span = math.hypot(w, h) * 1.2
            band = span / 10
            path = QPainterPath()
            for i, wid in enumerate(loco.blinds(min(1.0, pr + 0.04 * beat))):
                if wid > 0:
                    path.addRect(QRectF(c.x() - span / 2 + i * band, c.y() - span / 2, band * wid, span))
            p.setClipPath(path)
            p.resetTransform()
            p.drawPixmap(full, pm, src)
        elif kind == "mosaic":  # se pixela y los bloques se apagan en orden aleatorio
            # se pixela y los bloques se apagan en orden aleatorio; todo en una imagen pequeña y un solo dibujado
            block = max(2, round(1 + 47 * loco.clamp(pr / 0.45) ** 2))
            gone = min(30, max(0, round(((pr - 0.45) / 0.55 + 0.08 * beat) * 30))) if pr >= 0.45 else 0
            key = (block, gone)
            if key not in self.loco_cache:
                small = pm.scaled(max(1, round(w / block)), max(1, round(h / block)), Qt.IgnoreAspectRatio,
                                  Qt.FastTransformation).toImage().convertToFormat(QImage.Format_ARGB32)
                if gone:
                    rnd = random.Random(seed * 13 + 1)
                    for j in range(small.height()):
                        for i in range(small.width()):
                            if rnd.random() * 30 < gone:
                                small.setPixelColor(i, j, QColor(0, 0, 0, 0))
                self.loco_cache = {key: small}  # solo la del paso actual
            small = self.loco_cache[key]
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(full, small, QRectF(small.rect()))
        elif kind == "shear":  # cizalla: bandas que salen en sentidos alternos
            for i in range(5):
                f = loco.out_cubic(loco.clamp((pr - 0.04 * i) / 0.8))
                p.save()
                p.setOpacity(1 - loco.clamp((pr - 0.7) / 0.3))
                p.setClipRect(QRectF(0, i * h / 5, w, h / 5 + 1))
                p.translate((1 if i % 2 else -1) * (w * 1.15 * f + 30 * beat), 0)
                p.shear(0.15 * f * (1 if i % 2 else -1), 0)
                p.drawPixmap(full, pm, src)
                p.restore()
        elif kind == "door":  # puerta: las dos mitades se abren hacia los lados con perspectiva
            f = loco.in_out_cubic(pr)
            for side in (-1, 1):
                p.save()
                p.setOpacity(1 - loco.clamp((pr - 0.6) / 0.4))
                p.setClipRect(QRectF(0 if side < 0 else w / 2, 0, w / 2, h))
                edge = 0 if side < 0 else w
                p.translate(edge + side * (w / 2) * f, h / 2)
                p.scale(1 - 0.5 * f, 1 + 0.15 * f)
                p.shear(0, -0.25 * f * side)
                p.translate(-edge, -h / 2)
                p.drawPixmap(full, pm, src)
                p.restore()
        elif kind == "glitch":  # franjas que saltan con el golpe y se tiñen
            gen = random.Random(seed * 7 + int(now * 16))  # cambia 16 veces por segundo (sin guardar nada)
            rnd = [gen.random() for _ in range(48)]
            amp = (0.04 + 0.12 * pr + 0.05 * beat) * w
            for i in range(12):
                y0 = i * h / 12
                p.save()
                p.setOpacity(1 - pr)
                p.setClipRect(QRectF(0, y0, w, h / 12 + 1))
                p.drawPixmap(full.translated((rnd[i] - 0.5) * 2 * amp, 0), pm, src)
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                p.setOpacity(0.35 * (1 - pr))
                p.fillRect(QRectF(0, y0, w, h / 12 + 1), QColor(255, 40, 80) if rnd[24 + i] > 0.5 else QColor(40, 220, 255))
                p.restore()
        else:  # zoom con fundido (Ligera)
            p.setOpacity(1 - pr)
            p.translate(c)
            p.scale(1 + 0.3 * pr, 1 + 0.3 * pr)
            p.translate(-c)
            p.drawPixmap(full, pm, src)
        p.restore()

    # --- Nueva York ---
    def _nyc_line(self, pos, fm):
        """Bloque que toca: (palabras, inicios, rectángulos, inicio de la siguiente, inicio de la línea, línea,
        siguiente verso); una vez por línea."""
        snap = self.snap
        title = snap["title"].upper()
        tl = [i for i, (_, x) in enumerate(self.lines) if any(c.isalnum() for c in x)]  # con texto (no "♪")
        k = bisect.bisect_right([self.lines[i][0] for i in tl], pos) - 1
        key = (tl[k] if k >= 0 else None, title)
        if self.nyc_cache and self.nyc_cache[0] == key and self.nyc_cache[1] is self.lines:
            return self.nyc_cache[2]
        h = fm.height()
        aspect = lambda x: fm.horizontalAdvance(x) / h  # noqa: E731
        if k < 0:  # antes de la primera línea: el título solo
            ws, starts = [title], [0.0]
        else:
            i = tl[k]
            ws = [x.upper() for x in lyrics.words(self.lines[i][1])]
            starts = lyrics.word_times(self.lines, i)
            if k > 0:  # la última de la línea anterior abre el bloque (de ahí viene el paso entre líneas)
                ws.insert(0, lyrics.words(self.lines[tl[k - 1]][1])[-1].upper())
            else:
                ws.insert(0, title)
            starts.insert(0, min(self.lines[i][0], starts[0]) - 1e-3)  # antes que la primera, siempre
        # la siguiente línea empieza con su primera palabra si viene antes que su marca (LRC con tiempos por palabra)
        t_next = (min(self.lines[tl[k + 1]][0], lyrics.word_times(self.lines, tl[k + 1])[0]) - 1e-3
                  if k + 1 < len(tl) else None)
        durs = [max(0.0, min(1.5, b - a)) for a, b in zip(starts, starts[1:] + [t_next or starts[-1] + 1.0])]
        info = [(aspect(x), d, len(x)) for x, d in zip(ws, durs)]
        first = None
        if k > 0:  # mismo alto que tenía al final de la línea anterior: el paso enlaza sin salto
            prev_last = lyrics.word_times(self.lines, tl[k - 1])[-1]
            first = nyc.size(len(ws[0]), max(0.0, min(1.5, starts[0] - prev_last))) * nyc.HERO
        rects = nyc.place(info, zlib.crc32(f"{title}|{key[0]}".encode()), first)
        nxt = " ".join(lyrics.words(self.lines[tl[k + 1]][1])) if k + 1 < len(tl) else ""
        data = (ws, starts, rects, t_next, starts[0] + 1e-3, key[0], nxt)
        # el bloque anterior queda detrás, como un fantasma (de otra canción, no)
        self.nyc_prev = self.nyc_cache[2] if self.nyc_cache and self.nyc_cache[1] is self.lines else None
        self.nyc_cache = (key, self.lines, data)
        self.nyc_plates = {}
        for x in [x for x in self.nyc_words if x[0] not in ws]:  # se queda la que pasa al bloque nuevo
            del self.nyc_words[x]
        return data

    def _nyc_plate(self, rects, n):
        """Una sola placa negra para las n primeras palabras (unión de rectángulos redondeados), en unidades."""
        if n not in self.nyc_plates:
            path = QPainterPath()
            for r in rects[:n]:
                q = QPainterPath()
                q.addRoundedRect(QRectF(r[0], r[1], r[2], r[3]).adjusted(-NYC_PAD, -NYC_PAD, NYC_PAD, NYC_PAD),
                                 NYC_RADIUS, NYC_RADIUS)
                path = path.united(q)
            self.nyc_plates[n] = path.simplified()
        return self.nyc_plates[n]

    def _nyc_skyline(self, w, h):
        """Silueta de rascacielos (una vez por tamaño) y sus ventanas: [(x, y, ancho, alto)]."""
        if self.nyc_sky is None or self.nyc_sky[0] != (w, h):
            rnd = random.Random(11)
            top0 = int(0.45 * h)  # solo la franja de abajo: menos memoria y menos píxeles por fotograma
            pm = QPixmap(w, h - top0)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            band = NYC_BAND * h
            x, wins = 0.0, []
            while x < w:
                bw = rnd.uniform(0.04, 0.11) * w
                bh = rnd.uniform(0.10, 0.30) * h * (1.4 if rnd.random() < 0.12 else 1.0)  # alguna torre
                top = h - band - bh
                q.fillRect(QRectF(x, top - top0, bw - 2, bh), QColor(3, 3, 4))
                if bh > 0.24 * h:  # aguja
                    q.fillRect(QRectF(x + bw / 2 - 2, top - top0 - 0.05 * h, 3, 0.05 * h), QColor(3, 3, 4))
                ww, wh = max(3.0, 0.006 * w), max(4.0, 0.009 * h)
                cols, rows = int((bw - 8) / (ww * 2.2)), int((bh - 8) / (wh * 2.2))
                for cx in range(cols):
                    for cy in range(rows):
                        wins.append((x + 6 + cx * ww * 2.2, top + 6 + cy * wh * 2.2, ww, wh))
                x += bw
            q.end()
            self.nyc_sky = ((w, h), pm, wins, top0)
        return self.nyc_sky

    def _nyc_bg(self, p, w, h, now, pos, cam):
        """Asfalto, portada en blanco y negro o plano de Manhattan; vivos con la música."""
        light = self.quality.light
        if self.nyc_bg == "city":
            p.fillRect(self.rect(), QColor(24, 24, 26))
            p.save()
            p.translate(w / 2, h / 2)
            p.rotate(29)  # la cuadrícula de Manhattan va girada unos 29°
            k = h / 1080
            av, st = 300 * k, 64 * k  # avenidas y calles
            ox = 0.0 if light else -(cam[0] * cam[2] * 0.5) % av  # paralaje: la mitad que el texto
            oy = 0.0 if light else -(cam[1] * cam[2] * 0.5) % st
            reach = math.hypot(w, h) / 2 + av
            p.setPen(QPen(QColor(255, 255, 255, 8), 1))
            for i in range(-int(reach / st) - 2, int(reach / st) + 3):
                p.drawLine(QPointF(-reach, i * st + oy), QPointF(reach, i * st + oy))
            p.setPen(QPen(QColor(255, 255, 255, 20), 2))
            n_av = int(reach / av) + 2
            for i in range(-n_av, n_av + 1):
                p.drawLine(QPointF(i * av + ox, -reach), QPointF(i * av + ox, reach))
            # luces de coches por las avenidas: blancas en un sentido, rojas en el otro (se paran con la pausa)
            p.setPen(Qt.NoPen)
            for c in range(8 if light else 24):
                lane = (c * 7919) % (2 * n_av + 1) - n_av
                down = c % 2 == 0
                y = ((pos * (90 + c % 5 * 25) * k + c * 977) % (2 * reach)) - reach
                y = y if down else -y
                p.setBrush(QColor(255, 244, 220, 200) if down else QColor(255, 60, 50, 200))
                x = lane * av + ox + (-4 if down else 4) * k
                p.drawEllipse(QPointF(x - 3 * k, y), 2.2 * k, 2.2 * k)
                p.drawEllipse(QPointF(x + 3 * k, y), 2.2 * k, 2.2 * k)
                p.setBrush(QColor(255, 244, 220, 40) if down else QColor(255, 60, 50, 40))
                p.drawRect(QRectF(x - 5 * k, y - (30 * k if down else 0), 10 * k, 30 * k))  # estela
            p.restore()
        else:
            if self.nyc_bg == "cover" and self.backdrop.tex is not None:
                img = self.backdrop.tex[0]
                if self.gray[0] is not img:
                    self.gray = (img, img.convertToFormat(QImage.Format_Grayscale8))
                x, y, cw, ch = backdrop.cover_rect(w / max(1, h), 1.15)
                g = self.gray[1]
                p.drawImage(QRectF(0, 0, w, h), g, QRectF(x * g.width(), y * g.height(), cw * g.width(), ch * g.height()))
                p.fillRect(self.rect(), QColor(0, 0, 0, 160))  # oscurecida: las placas negras siguen mandando
            else:
                grad = QLinearGradient(0, 0, 0, h)
                grad.setColorAt(0, QColor(40, 40, 43))  # gris asfalto: las placas negras se recortan encima
                grad.setColorAt(1, QColor(20, 20, 22))
                p.fillRect(self.rect(), grad)
            # rascacielos al fondo: con cada golpe se encienden dos ventanas y se apagan en 1,5 s
            _, sky, wins, top0 = self._nyc_skyline(w, h)
            p.drawPixmap(0, top0, sky)
            if wins:
                for i, wn in enumerate(wins[::23]):  # unas pocas siempre encendidas
                    p.fillRect(QRectF(*wn), QColor(255, 214, 120, 28 + (i * 37) % 30))
                for t in self.beats:
                    age = now - t
                    if 0 <= age < 1.5:
                        for j in (0, 1):
                            wn = wins[(int(t * 1000) * 2654435761 + j * 40503) % len(wins)]
                            p.fillRect(QRectF(*wn), QColor(255, 214, 120, round(220 * (1 - age / 1.5))))
        if not light:  # grano de película, cambia a 12 fps
            if self.grain is None:
                rnd = random.Random(7)
                g = QImage(128, 128, QImage.Format_ARGB32)
                for yy in range(128):
                    for xx in range(128):
                        v = rnd.randint(0, 255)
                        g.setPixelColor(xx, yy, QColor(v, v, v, 8))
                self.grain = QPixmap.fromImage(g)
            f = int(now * 12)
            p.drawTiledPixmap(QRectF(self.rect()), self.grain, offset=QPointF((f * 37) % 128, (f * 71) % 128))
            if self.vignette is None or self.vignette.size() != self.size():
                self.vignette = QPixmap(self.size())
                self.vignette.fill(Qt.transparent)
                q = QPainter(self.vignette)
                grad = QRadialGradient(QPointF(w / 2, h / 2), math.hypot(w, h) / 2)
                grad.setColorAt(0.55, QColor(0, 0, 0, 0))
                grad.setColorAt(1, QColor(0, 0, 0, 90))
                q.fillRect(self.vignette.rect(), grad)
                q.end()
            p.drawPixmap(0, 0, self.vignette)

    def _nyc_word(self, text, px):
        """Palabra blanca sin sombra en caché (se apaga a gris con opacidad, no con otra imagen)."""
        font = self._font(px, True, "heavy")
        fm = QFontMetricsF(font)
        px = min(px, px * 1.5 * self.width() / max(1.0, fm.horizontalAdvance(text)))  # 4K o título largo: memoria acotada
        key = (text, round(px))
        pm = self.nyc_words.get(key)
        if pm is None:
            font = self._font(px, True, "heavy")
            fm = QFontMetricsF(font)
            dpr = self.devicePixelRatioF()
            pm = QPixmap(math.ceil(fm.horizontalAdvance(text) * dpr) + 2, math.ceil(fm.height() * dpr) + 2)
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.TextAntialiasing)
            q.setFont(font)
            q.setPen(QColor(255, 255, 255))
            q.drawText(QPointF(0, fm.ascent()), text)
            q.end()
            self.nyc_words[key] = pm
        return pm

    def _nyc(self, p, now, w, h):
        """Nueva York: carteles de palabras en una placa negra; la cámara vive con la línea y la atraviesa al acabar."""
        snap = self.snap
        if not snap or snap["ad"]:
            self._nyc_bg(p, w, h, now, 0.0, (0, 0, 1))
            return self._message(p, "Pon música en tu reproductor ♪" if not snap else "Anuncio · la música vuelve enseguida",
                                 rgb=(255, 255, 255))
        pos, light = self.position(), self.quality.light
        ref = 0.3 * h  # tamaño al que se dibuja cada palabra (luego se escala con la cámara)
        fm = QFontMetricsF(self._font(ref, True, "heavy"))
        ws, starts, rects, t_next, line_start, line, nxt = self._nyc_line(pos, fm)
        view = h * (1 - NYC_BAND)  # la cámara encuadra por encima de la banda de estación
        cx, cy, sc, others = nyc.camera(pos, starts, rects, t_next, w, view)
        end = t_next if t_next is not None else line_start + 4.0
        # acercamiento lento que vuelve a 1 al acabar la línea (el paso a la siguiente empieza sin salto)
        sc *= 1 + 0.04 * math.sin(math.pi * min(1.0, max(0.0, (pos - line_start) / max(0.1, end - line_start))))
        if not light:
            cx += math.sin(pos * 0.23) * 0.006 * w / sc  # deriva
        m = nyc.dive(pos, line_start, t_next)
        sc *= m
        self._nyc_bg(p, w, h, now, pos, (cx, cy, sc))
        k = max(0, bisect.bisect_right(starts, pos) - 1)
        accent = QColor(*nyc.mta(self.rgb))
        if not light:
            self._nyc_rain(p, w, h, pos)
            self._nyc_ghost(p, w, view, ref)
        d = now - self.drop_t  # drop: temblor que se apaga en 0,3 s
        shake = (6 * (1 - d / 0.3) * math.sin(d * 90), 6 * (1 - d / 0.3) * math.cos(d * 77)) if 0 <= d < 0.3 and not light else (0, 0)
        chorus = line is not None and layout.stanza_of(self.groups, line) in self.chorus
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform, not light)
        p.translate(w / 2 + shake[0], view / 2 + shake[1])
        if not light:
            p.rotate(0.8 * math.sin(pos * 0.17))  # leve balanceo de cámara
        p.scale(sc, sc)
        p.translate(-cx, -cy)
        plate = QColor(5, 5, 5)
        # la placa: las ya puestas juntas, y la que entra como un sello desde el lado por donde se engancha
        if k > 0:
            p.setOpacity(others)
            p.fillPath(self._nyc_plate(rects, k), plate)
        r = rects[k]
        tw = pos - starts[k]
        grow = 1.0 if light and tw > 0.08 or k == 0 else 1 - 2 ** (-10 * min(1.0, max(0.0, tw / 0.12)))
        prev = rects[k - 1] if k else r
        side = "left" if k and r[0] >= prev[0] + prev[2] - 1e-6 else "top"
        box = QRectF(r[0], r[1], r[2], r[3]).adjusted(-NYC_PAD, -NYC_PAD, NYC_PAD, NYC_PAD)
        beat = 1.0 if light else 1 + 0.02 * effects.env(now - self.beat_t, 0.04, 0.18)
        cur = QRectF(box.x(), box.y(), box.width() * (grow if side == "left" else 1), box.height() * (grow if side == "top" else 1))
        p.setOpacity(1.0)
        p.save()
        p.translate(box.center())
        p.scale(beat, beat)
        p.translate(-box.center())
        path = QPainterPath()
        path.addRoundedRect(cur, NYC_RADIUS, NYC_RADIUS)
        p.fillPath(path, plate)
        p.restore()
        if chorus and d >= 0.29:  # estribillo: borde de neón del color de la línea de metro (se enciende parpadeando)
            flick = (1, 0, 1, 0, 1, 1)[min(5, int(max(0.0, pos - line_start) / 0.22 * 6))]
            if flick:
                outline = self._nyc_plate(rects, k + 1)
                if not light:
                    glow = QColor(accent)
                    glow.setAlpha(60)
                    p.strokePath(outline, QPen(glow, 0.12))
                p.strokePath(outline, QPen(accent, 0.03))
        open_t = pos - line_start  # al abrir el bloque, líneas finas que encuadran las palabras (títulos de Saul Bass)
        if not light and 0 <= open_t < 0.9:
            reach = min(1.0, open_t / 0.3) * 40
            fade = 1 - max(0.0, (open_t - 0.5) / 0.4)
            p.setOpacity(0.35 * fade)
            p.setPen(QPen(QColor(255, 255, 255), 1.0 / sc))
            for r in rects[:k + 1]:
                for x in (r[0] - NYC_PAD, r[0] + r[2] + NYC_PAD):
                    p.drawLine(QPointF(x, r[1] - reach), QPointF(x, r[1] + r[3] + reach))
                for y in (r[1] - NYC_PAD, r[1] + r[3] + NYC_PAD):
                    p.drawLine(QPointF(r[0] - reach, y), QPointF(r[0] + r[2] + reach, y))
            p.setOpacity(1.0)
        # palabras: las cantadas al 55 %, la que suena se rellena de gris a blanco (karaoke) y entra deslizando
        intro = line is None and pos < 2.5 and not light  # antes de la primera línea: título de cine
        for i in range(k + 1):
            r = rects[i]
            if intro and i == 0:
                self._nyc_title(p, r, ws[0], pos)
                continue
            pm = self._nyc_word(ws[i], ref)
            t = pos - starts[i]
            dur = (starts[i + 1] if i + 1 < len(starts) else end) - starts[i]
            p.save()
            box = QRectF(r[0], r[1], r[2], r[3])
            if i == k:
                p.translate(box.center())
                p.scale(beat, beat)
                p.translate(-box.center())
                if i:
                    p.setClipRect(cur)
                    slide = 0.0 if light else 0.25 * (1 - _out_back(min(1.0, max(0.0, (t - 0.04) / 0.16))))
                    box.translate(-slide if side == "left" else 0, -slide if side == "top" else 0)
            if r[4]:
                p.translate(box.center())
                p.rotate(-90)  # vertical, leyendo hacia arriba
                box = QRectF(-box.height() / 2, -box.width() / 2, box.height(), box.width())
            alpha = 1.0 if i == len(rects) - 1 else others
            if i < k:
                p.setOpacity(0.55 * alpha)
                p.drawPixmap(box, pm, QRectF(pm.rect()))
            else:
                p.setOpacity(0.55 * alpha)
                p.drawPixmap(box, pm, QRectF(pm.rect()))
                fill = 1.0 if i == 0 or dur <= 0 else min(1.0, max(0.0, t / dur))
                if fill > 0:
                    p.setOpacity(alpha)
                    src = QRectF(pm.rect())
                    p.drawPixmap(QRectF(box.x(), box.y(), box.width() * fill, box.height()), pm,
                                 QRectF(0, 0, src.width() * fill, src.height()))
            p.restore()
        p.restore()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if m > 3:  # dentro de la placa: negro (también tapa el cambio de bloque)
            p.fillRect(self.rect(), QColor(0, 0, 0, round(255 * min(1.0, (m - 3) / 4))))
        self._nyc_station(p, w, h, snap, accent, nxt)
        if not self.lines and self.status:
            self._message(p, self.status, y=0.80, alpha=0.5, size=0.022 * h, rgb=(255, 255, 255))
        if 0 <= d < 0.29:  # drop: la pantalla se invierte un instante
            a = min(1.0, d / 0.06) if d < 0.15 else max(0.0, 1 - (d - 0.15) / 0.14)
            p.save()
            p.setCompositionMode(QPainter.CompositionMode_Difference)
            p.fillRect(self.rect(), QColor(255, 255, 255, round(255 * a * (0.6 if light else 1.0))))
            p.restore()

    def _nyc_rain(self, p, w, h, pos):
        """Lluvia fina a 29° (la cuadrícula de Manhattan) cuando la canción va tranquila; sale de la posición."""
        if self.mood != "calm":
            return
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        rnd = self._rand(2929, 100)
        dx, dy = math.sin(math.radians(29)), math.cos(math.radians(29))
        for i in range(50):
            ln = (0.03 + 0.04 * rnd[i]) * h
            y = ((pos * (0.9 + 0.5 * rnd[50 + i]) * h + rnd[i] * h * 3) % (h + ln)) - ln
            x = rnd[50 + i] * (w + 0.6 * h) - 0.6 * h + y * dx / dy  # cae en la dirección en que está inclinada
            p.drawLine(QPointF(x, y), QPointF(x + ln * dx, y + ln * dy))

    def _nyc_ghost(self, p, w, view, ref):
        """El bloque anterior detrás, enorme y casi transparente (profundidad sin coste de desenfoque)."""
        prev = self.nyc_prev
        if not prev:
            return
        ws, _, rects = prev[:3]
        cx, cy, sc = nyc.frame(rects, w, view)
        p.save()
        p.translate(w / 2, view / 2)
        p.scale(sc * 1.6, sc * 1.6)
        p.translate(-cx, -cy)
        p.setOpacity(0.05)
        for word, r in zip(ws, rects):
            pm = self._nyc_word(word, ref)
            box = QRectF(r[0], r[1], r[2], r[3])
            if r[4]:
                p.save()
                p.translate(box.center())
                p.rotate(-90)
                p.drawPixmap(QRectF(-r[3] / 2, -r[2] / 2, r[3], r[2]), pm, QRectF(pm.rect()))
                p.restore()
            else:
                p.drawPixmap(box, pm, QRectF(pm.rect()))
        p.restore()

    def _nyc_title(self, p, r, text, pos):
        """Título de entrada: las letras empiezan separadas y se juntan en 2 s (como en los créditos de cine)."""
        e = 1 - (1 - min(1.0, pos / 2.0)) ** 4
        font = QFont(self._font(100, True, "heavy"))
        font.setLetterSpacing(QFont.PercentageSpacing, 100 + 60 * (1 - e))
        fm = QFontMetricsF(font)
        p.save()
        p.translate(r[0] + r[2] / 2, r[1] + r[3] / 2)
        k = r[3] / fm.height()
        p.scale(k, k)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255))
        p.setOpacity(min(1.0, pos / 0.4))
        tw = fm.horizontalAdvance(text)
        p.drawText(QPointF(-tw / 2, fm.ascent() - fm.height() / 2), text)
        p.restore()

    def _nyc_station(self, p, w, h, snap, accent, nxt):
        """Banda de estación de metro abajo: filete blanco, círculo de línea con la inicial, título y el verso siguiente."""
        band = NYC_BAND * h
        y = h - band
        p.fillRect(QRectF(0, y, w, band), QColor(0, 0, 0))
        p.fillRect(QRectF(0, y + 0.12 * band, w, max(2.0, 0.035 * band)), QColor(255, 255, 255))
        d = 0.5 * band
        c = QPointF(0.03 * h + d / 2, y + 0.58 * band)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(c, d / 2, d / 2)
        dark = accent.lightnessF() > 0.6  # amarillo: inicial en negro, como en el metro
        p.setPen(QColor(0, 0, 0) if dark else QColor(255, 255, 255))
        p.setFont(self._font(0.6 * d, True, "heavy"))
        p.drawText(QRectF(c.x() - d / 2, c.y() - d / 2, d, d), Qt.AlignCenter, (snap["artist"] or "?")[:1].upper())
        font = self._font(0.36 * band, True)
        fm = QFontMetricsF(font)
        tx = c.x() + d / 2 + 0.4 * d
        title = fm.elidedText(snap["title"].upper(), Qt.ElideRight, 0.4 * w)
        p.setPen(QColor(255, 255, 255))
        p.setFont(font)
        p.drawText(QRectF(tx, y + 0.2 * band, 0.42 * w, 0.76 * band), Qt.AlignVCenter | Qt.AlignLeft, title)
        if nxt:
            small = self._font(0.26 * band, False)
            fs = QFontMetricsF(small)
            label = fs.elidedText(f"SIGUIENTE  ·  {nxt.upper()}", Qt.ElideRight, 0.44 * w)
            p.setPen(QColor(255, 255, 255, 150))
            p.setFont(small)
            p.drawText(QRectF(w - 0.03 * h - 0.46 * w, y + 0.2 * band, 0.46 * w, 0.76 * band),
                       Qt.AlignVCenter | Qt.AlignRight, label)

    def _pulse_color(self, now):
        """Fondo de Latido: el color de la portada, intenso y oscuro para que se lea el blanco; se aclara con el golpe."""
        c = QColor(*self.rgb)
        lift = (0.14 if now - self.drop_t < 0.3 else 0.08) * effects.env(now - self.beat_t, 0.04, 0.26)
        if self.quality.light:
            lift = min(lift, 0.05)
        c.setHslF(c.hslHueF(), max(0.6, min(0.85, c.hslSaturationF())), max(0.38, min(0.45, c.lightnessF())) + lift)
        return c

    def _pulse(self, p, now, w, h):
        """Latido: fondo liso que late y un anillo que sale del centro con cada golpe (tres en un drop)."""
        p.fillRect(self.rect(), self._pulse_color(now))
        ring = QColor(*self.rgb)
        ring.setHslF(ring.hslHueF(), max(0.6, min(0.85, ring.hslSaturationF())), min(0.9, max(0.38, min(0.45, ring.lightnessF())) + 0.22))
        diag = math.hypot(w, h)
        light = self.quality.light
        live = [t for t in self.beats if 0 <= now - t < 0.9][-(2 if light else 6):]
        p.setBrush(Qt.NoBrush)
        for t in live:
            f = (now - t) / 0.9
            e = 1 - (1 - f) ** 2  # ease-out
            ring.setAlphaF(0.55 * (1 - f))
            p.setPen(QPen(ring, 3 if light else 6 - 5 * f))
            r = diag * (0.05 + 0.95 * e) / 2
            p.drawEllipse(QPointF(w / 2, h / 2), r, r)

    def _kinetic(self, p, pos, now, w, h, gap, hero):
        """Latido: la línea que suena, blanca, gigante y abajo a la izquierda; la anterior encima, tenue."""
        idx = self.line_index(pos)
        words = [x.upper() for x in lyrics.words(self.lines[idx][1])] if idx >= 0 else []
        if gap or not words:
            if not hero:
                self._message(p, "♪", rgb=(255, 255, 255))
            return
        start = self.lines[idx][0]
        if idx not in self.kin:  # efecto fijo por línea: un cambio de ambiente no la hace saltar
            self.kin[idx] = kinetic.pick(zlib.crc32(f"{self.snap['title']}|{idx}".encode()), self.mood)
        effect = self.kin[idx]
        times = lyrics.word_times(self.lines, idx)
        end = self.lines[idx + 1][0] if idx + 1 < len(self.lines) else start + 6
        light = self.quality.light
        sung_rgb = tuple(round(c + (255 - c) * 0.55) for c in self._pulse_color(-1e9).getRgb()[:3])  # tono sobre tono
        fit = (idx, w, h)
        px = self.kin.get(fit) or KIN_SIZE * h
        for _ in range(5):  # como mucho 3 filas: si no caben, letra más pequeña (se calcula una vez por línea)
            pms = [self._text_pixmap(x, px, True, (255, 255, 255), False, "heavy") for x in words]
            dpr = pms[0].devicePixelRatio()
            widths = [pm.width() / dpr - 8 for pm in pms]  # sin el margen de 4 px de cada lado
            space = 0.25 * px
            rows = kinetic.wrap(widths, space, KIN_W * w)
            if fit in self.kin or len(rows) <= 3 or px < KIN_MIN * h:
                break
            px *= 0.85
        self.kin[fit] = px
        lh = px * 1.05
        top = KIN_Y * h - lh * len(rows)
        energy, beat = self.backdrop.energy, effects.env(now - self.beat_t, 0.05, 0.22)
        line_age = pos - start
        for r, row in enumerate(rows):
            x = KIN_X * w
            for i in row:
                we = times[i + 1] if i + 1 < len(times) else end
                tw = pos - times[i]
                dy, scale, alpha, fill = kinetic.word_state(effect, i, tw, min(we - times[i], 1.5), line_age,
                                                            0.0 if light else energy, 0.0 if light else beat, now)
                if 0 <= tw < we - times[i] and not light:
                    scale *= 1 + 0.10 * beat  # la que suena crece con el golpe
                sung = pos >= we and fill is None
                pm = self._text_pixmap(words[i], px, True, sung_rgb, False, "heavy") if sung else pms[i]
                pw, ph = pm.width() / dpr * scale, pm.height() / dpr * scale
                cx, cy = x + widths[i] / 2, top + r * lh + lh / 2 + dy * px
                target = QRectF(cx - pw / 2, cy - ph / 2, pw, ph)
                p.setOpacity(min(1.0, alpha * (kinetic.PENDING_ALPHA if fill is not None else 1.0)))
                p.drawPixmap(target, pm, QRectF(pm.rect()))
                if fill:  # barrido: la parte ya cantada encima, a plena luz
                    p.save()
                    p.setClipRect(QRectF(target.x(), target.y(), (4 + widths[i] * fill) * scale, ph))
                    p.setOpacity(min(1.0, alpha))
                    p.drawPixmap(target, pm, QRectF(pm.rect()))
                    p.restore()
                x += widths[i] + space
        # la línea anterior, más pequeña y tenue, encima
        prev = " ".join(lyrics.words(self.lines[idx - 1][1])).upper() if idx > 0 else ""
        if prev:
            pm = self._text_pixmap(prev, 0.45 * px, True, (255, 255, 255), False, "heavy")
            pw, ph = pm.width() / dpr, pm.height() / dpr
            f = min(1.0, KIN_W * w / pw)
            p.setOpacity(0.45 * min(1.0, max(0.0, line_age / kinetic.LINE_IN)))
            p.drawPixmap(QRectF(KIN_X * w, top - ph * f - 0.1 * px, pw * f, ph * f), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)

    def _start_transition(self, k, now):
        """Elige estilo y variación para el cambio de estrofa; la vieja guarda su estado real en pantalla."""
        prev, self.shown = self.shown, k
        seed = zlib.crc32(f"{self.snap['title'] if self.snap else ''}|{k}|{prev}".encode())
        jump = prev is not None and k is not None and (k < prev or k > prev + 1)  # salto atrás o de varias
        special = now - self.drop_t < 2.0 or (k in self.chorus and prev not in self.chorus)
        style = "cut" if jump else transitions.pick(self.trans_setting, seed, self.mood, self.prev_style, special)
        self.trans_P = transitions.params(style, seed, self.mood)
        self.prev_style = style if style != "cut" else self.prev_style
        # sin estrofa en pantalla (p. ej. tras cambiar de canción), sigue desde lo que se está yendo
        self.old_items = [dict(it) for it in self.last_items] if self.last_items else self.leaving_now
        self.trans_t0 = now

    def _background(self, p, now, w, h):
        p.setPen(Qt.NoPen)
        for x, y, period, phase, r in self.twinklers:
            a = 0.25 + 0.55 * (0.5 + 0.5 * math.sin(2 * math.pi * now / period + phase))
            p.setBrush(QColor(255, 255, 255, round(255 * a)))
            p.drawEllipse(QPointF(x * w, y * h), r, r)
        if self.nebula is None:
            self._build_nebula()
        p.setOpacity(0.08 + 0.07 * (0.5 + 0.5 * math.sin(2 * math.pi * now / 10)))
        p.drawPixmap(QRectF(0, 0, w, h), self.nebula, QRectF(self.nebula.rect()))
        p.setOpacity(1.0)

    def _rand(self, seed, n):
        """n números aleatorios fijos para una semilla (calculados una vez, no en cada fotograma)."""
        if seed not in self.rands:
            rnd = random.Random(seed)
            self.rands[seed] = [rnd.random() for _ in range(n)]
        return self.rands[seed]

    def _item(self, pt, word, size, alpha, now, i, current=False):
        """Estrella con su posición en pantalla (flota ±3 px) y su titileo."""
        r = self._rand(i * 7919, 5)
        w, h = self.width(), self.height()
        drift = 6 + 3 * r[0]
        x = pt[0] * w + 3 * math.sin(2 * math.pi * now / drift + 6.28 * r[1])
        y = pt[1] * h + 3 * math.cos(2 * math.pi * now / (drift + 1) + 6.28 * r[2])
        tw = 0.85 + 0.15 * math.sin(2 * math.pi * now / (2 + 3 * r[3]) + 6.28 * r[4])
        return {"x": x, "y": y, "word": word, "r": size[0] * h, "font": size[1] * h, "alpha": alpha,
                "tw": tw, "current": current, "seed": i}

    def _draw(self, p, items, timing):
        """Dibuja la constelación con el estado en pantalla de cada estrella (dx, dy, s, sa, la, trail)."""
        if not items:
            return
        if self.halo is None:
            self._build_halo()
        # líneas: la última se traza hacia la palabra nueva
        grow = _out_cubic(min(1.0, timing[0] / DRAW_LINE)) if timing else 1.0
        for n, (a, b) in enumerate(zip(items, items[1:])):
            last = n == len(items) - 2
            pa, pb = QPointF(a["dx"], a["dy"]), QPointF(b["dx"], b["dy"])
            if last and grow < 1:
                pb = pa + (pb - pa) * grow
            fade = min(a["sa"], b["sa"])
            if "ox" in a:  # al salir, una línea que se estira más del doble se apaga (no cruza la pantalla)
                base = math.hypot(a["ox"] - b["ox"], a["oy"] - b["oy"]) + 1
                fade *= max(0.0, min(1.0, 2 - math.hypot(pa.x() - pb.x(), pa.y() - pb.y()) / base))
            if fade > 0.01:
                p.setPen(QPen(self._color((0.6 if last and grow < 1 else 0.35) * fade), 1))
                p.drawLine(pa, pb)
        for it in items:
            f = it["sa"]
            if f <= 0.01 and it["la"] <= 0.01:
                continue
            pt, r = QPointF(it["dx"], it["dy"]), it["r"] * it["s"]
            if it["trail"] > 1:  # estela (lluvia)
                p.setPen(QPen(self._color(0.5 * f), 1.2))
                p.drawLine(QPointF(pt.x(), pt.y() - it["trail"]), pt)
            if it["current"] and timing:
                t = min(1.0, timing[0] / FLASH)
                r *= 0.6 + 0.4 * _out_back(t)  # destello: 0,6 → 1,15 → 1
                if not self.quality.light:
                    self._sparks(p, pt, it["r"] * it["s"], timing[0], it["seed"], f)
            a = it["alpha"] * it["tw"] * f
            p.setOpacity(min(1.0, a))
            p.drawPixmap(QRectF(pt.x() - r * 3, pt.y() - r * 3, r * 6, r * 6), self.halo, QRectF(self.halo.rect()))
            p.setOpacity(1.0)
            p.save()
            p.translate(pt)
            p.scale(r, r)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, round(255 * min(1.0, a))) if it["current"] else self._color(min(1.0, a + 0.2)))
            p.drawPath(self.star)
            p.restore()
            if it["la"] > 0.01:
                self._label(p, pt, it, r, it["la"], it["s"])

    def _text_pixmap(self, text, px, bold, rgb, glow, kind=""):
        """Texto dibujado una vez en una imagen: se mueve con precisión subpíxel sin vibrar."""
        key = (text, round(px), bold, rgb, glow, kind)
        pm = self.labels.get(key)
        if pm is None:
            font = self._font(px, bold, kind)
            fm = QFontMetricsF(font)
            pad = 4
            dpr = self.devicePixelRatioF()
            pm = QPixmap(round((fm.horizontalAdvance(text) + 2 * pad) * dpr), round((fm.height() + 2 * pad) * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.TextAntialiasing)
            q.setFont(font)
            base = QPointF(pad, pad + fm.ascent())
            # sombra horneada (gratis por fotograma): contorno fino + sombra abajo, para fondos claros
            q.setPen(QColor(0, 0, 0, 90))
            for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
                q.drawText(base + QPointF(dx, dy), text)
            q.setPen(QColor(0, 0, 0, 120))
            q.drawText(base + QPointF(0, 2), text)
            if glow:  # halo del color de la portada alrededor del texto
                q.setPen(self._color(0.3))
                for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                    q.drawText(base + QPointF(dx, dy), text)
            q.setPen(QColor(*rgb))
            q.drawText(base, text)
            q.end()
            self.labels[key] = pm
            if len(self.labels) > 200:
                self.labels.popitem(last=False)
        else:
            self.labels.move_to_end(key)
        return pm

    def _label(self, p, pt, it, r, fade, scale=1.0):
        current = it["current"]
        # las no actuales: color de la portada aclarado un 25 % para no perderse sobre un fondo del mismo tono
        rgb = (255, 255, 255) if current else tuple(round(c + (255 - c) * 0.25) for c in self.rgb)
        pm = self._text_pixmap(it["word"], it["font"], current, rgb, current)
        dpr = pm.devicePixelRatio()
        pw, ph = pm.width() / dpr * scale, pm.height() / dpr * scale  # escala con el viaje (suavizada)
        below = it["seed"] % 2 == 1
        y = pt.y() + r - 2 if below else pt.y() - r + 2 - ph
        p.setOpacity(min(1.0, it["alpha"] * fade))
        p.drawPixmap(QRectF(pt.x() - pw / 2, y, pw, ph), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)

    def _sparks(self, p, pt, r, age, seed, fade):
        t = age / SPARKS
        if not 0 <= t < 1:
            return
        rs = self._rand(seed * 31 + 7, 17)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color((1 - t) * fade, (255, 255, 255)))
        for k in range(6 + int(rs[16] * 3)):
            ang = rs[k] * 2 * math.pi
            d = r * (1.2 + 3.5 * _out_cubic(t)) * (0.7 + 0.5 * rs[k + 8 if k + 8 < 16 else k])
            p.drawEllipse(QPointF(pt.x() + d * math.cos(ang), pt.y() + d * math.sin(ang)), 1.6, 1.6)

    def _leaving(self, p, P, age):
        """Estrofa anterior según el estilo de la transición; devuelve dónde estaban sus estrellas."""
        old = self.old_items
        pts = [(it["dx"], it["dy"]) for it in old]
        if not old or P is None or age >= transitions.out_time(P):
            self.leaving_now = []
            return pts
        w, h = self.width(), self.height()
        moved = []
        for i, it in enumerate(old):
            x, y, s, sa, la, trail = transitions.out_state(P, age, it["dx"], it["dy"], i, len(old), w, h)
            m = dict(it)  # parte del estado real que tenía en pantalla (sin saltos si cambió a mitad)
            m.update(dx=x, dy=y, s=it["s"] * s, sa=it["sa"] * sa, la=it["la"] * la, trail=trail, current=False,
                     ox=it["dx"], oy=it["dy"])
            moved.append(m)
        self._draw(p, moved, None)
        self.leaving_now = moved  # su estado real ahora, por si empieza otra transición a mitad
        return pts

    def _extras(self, p, P, age, old_pts, new_pts):
        """Efectos propios de cada transición: estelas, chispas, motas, anillos, destello."""
        if P is None or age > transitions.total(P, len(new_pts)) + 0.1:
            return
        w, h = self.width(), self.height()
        tint = tuple(round(0.7 * 255 + 0.3 * v) for v in self.rgb)
        p.setPen(Qt.NoPen)
        for prim in transitions.extras(P, age, old_pts, new_pts, w, h):
            kind = prim[0]
            if kind == "line":
                p.setPen(QPen(self._color(prim[5], tint), 1.2))
                p.drawLine(QPointF(prim[1], prim[2]), QPointF(prim[3], prim[4]))
                p.setPen(Qt.NoPen)
            elif kind == "dot":
                p.setBrush(self._color(prim[4], tint))
                p.drawEllipse(QPointF(prim[1], prim[2]), prim[3], prim[3])
            elif kind == "ring":  # brillo ancho y suave + trazo fino
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(self._color(prim[4] * 0.25, tint), 7.0))
                p.drawEllipse(QPointF(prim[1], prim[2]), prim[3], prim[3])
                p.setPen(QPen(self._color(prim[4], tint), 2.0))
                p.drawEllipse(QPointF(prim[1], prim[2]), prim[3], prim[3])
                p.setPen(Qt.NoPen)
            elif kind == "flash":
                p.fillRect(self.rect(), self._color(prim[1], tint))

    def _message(self, p, text, y=0.5, alpha=0.6, size=None, rgb=None):
        size = size or max(14, min(32, self.height() * 0.035))
        pm = self._text_pixmap(text, max(11, size), False, rgb or self.rgb, False)
        dpr = pm.devicePixelRatio()
        pw, ph = pm.width() / dpr, pm.height() / dpr
        p.setOpacity(alpha)
        p.drawPixmap(QRectF((self.width() - pw) / 2, self.height() * y - ph / 2, pw, ph), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
