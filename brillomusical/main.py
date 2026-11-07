"""Punto de entrada: bandeja del sistema, brillo, ventana de letras y conexión con SMTC."""
import colorsys
import hashlib
import json
import logging
import logging.handlers
import os
import sys
import threading
import time

from PySide6.QtCore import QLockFile, QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import artwork, audio, backdrop, beat, color, constellation, cover, lyrics, transitions
from .color import FALLBACK
from .constellation import SEARCHING, LyricsWindow
from .glow import Glow
from .media import MediaWatcher
from .room_ui import RoomPanel
from .vsync import FrameClock

APPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
DATA = os.path.join(APPDATA, "Estela")
OLD_DATA = os.path.join(APPDATA, "BrilloMusical")  # nombre anterior: se migra al arrancar
DEBOUNCE_MS = 400  # cambio rápido de canción: esperar antes de pedir la letra
RETRIES_S = (5, 15, 45)  # sin internet
PAUSED = 0.4
DURATION_WAITS = 3  # segundos esperando a que Windows dé la duración
OFFSET_STEP = 0.25
MODE_NAMES = (("auto", "Automático (según la música)"), ("static", "Estático"), ("breathe", "Respirar"),
              ("beat", "Al ritmo"), ("spin", "Giratorio"), ("aurora", "Aurora"), ("flash", "Destello"),
              ("fire", "Fuego"), ("waves", "Ondas"), ("rainbow", "Arcoíris"))
GLOW_FIRST = 8.0  # el borde automático decide a los 8 s de música…
GLOW_EVERY = 15.0  # …y después cambia como mucho cada 15 s, al cambiar el ambiente
BG_EVERY = 20.0  # el fondo automático, como mucho cada 20 s
NO_LYRICS, OFFLINE = "Sin letra para esta canción", "Sin conexión · reintentando…"

log = logging.getLogger("brillomusical")
# J abre Escuchar juntos (la tecla H lee constellation.HELP al pulsarla)
if "J " not in constellation.HELP:
    constellation.HELP += " · J escuchar juntos"


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


class Controller(QObject):
    lyrics_ready = Signal(object, str, object)  # (clave de canción, estado, líneas)
    hd_ready = Signal(object, str, object, object)  # (clave de canción, art_id, fondo, carátula)

    def __init__(self, app):
        super().__init__()
        self.settings = QSettings("Estela", "Estela")
        self.frames = FrameClock()  # fotogramas al ritmo de la pantalla para el borde y la letra
        app.aboutToQuit.connect(self.frames.stop)
        self.glow = Glow(self.frames)
        self.glow.set_enabled(self.settings.value("glow_enabled", True, type=bool))
        self.window = LyricsWindow(self.settings, self.frames)
        self.window.offset_step.connect(self._shift_offset)
        self.track = None  # clave de la canción actual
        self.track_snap = None
        self.retry = 0
        self.wait_duration = 0
        self.rgb = FALLBACK
        try:
            self.offsets = lyrics.Offsets(json.loads(self.settings.value("lyrics_offsets", "{}")))
        except (TypeError, ValueError):
            self.offsets = lyrics.Offsets({})
        self.lyrics_ready.connect(self._on_lyrics)
        self.hd_art = self.settings.value("hd_art", True, type=bool)
        # una descarga que termina después de desactivar la alta resolución no se aplica
        self.hd_ready.connect(lambda *a: self.hd_art and self.window.set_hd_art(*a))
        self.art_requested = False
        self.debounce = QTimer(self, singleShot=True, interval=DEBOUNCE_MS, timeout=self._fetch)
        self.watcher = MediaWatcher()
        self.watcher.snapshot.connect(self._on_snapshot)
        self.room = RoomPanel(self.window, self.settings, self.watcher)
        app.aboutToQuit.connect(lambda: self.room.leave(quiet=True))  # antes de parar SMTC: deja la velocidad a 1
        app.aboutToQuit.connect(self.watcher.stop)
        # modo del borde: "auto" decide por canción; uno fijado a mano gana siempre
        self.manual = self.settings.value("glow_mode", "auto")
        self.line_pulse = self.settings.value("line_pulse", True, type=bool)
        # ambiente de la canción en tiempo real: decide borde, fondo y transiciones en "Automático"
        self.mood = beat.Mood()
        self.glow_auto, self.glow_auto_t, self.glow_state, self.glow_pending = None, -1e9, None, True
        self.bg_auto, self.bg_auto_t, self.bg_check_t, self.bg_gap = None, -1e9, -1e9, False
        self.detector = beat.BeatDetector()
        self.envelope = beat.Envelope()
        self.meter, self.audio_fails, self.audio_on = None, 0, False
        self.audio_t = time.monotonic()
        self.frames.tick.connect(self._frame)  # el audio se lee en el mismo fotograma: sin timer extra
        self.last_line = None
        self.line_timer = QTimer(self, interval=100, timeout=self._line_tick)  # pulso aunque la ventana esté cerrada
        self.line_timer.start()
        app.aboutToQuit.connect(self._close_audio)
        self._apply_mode()
        self._tray(app)

    def _tray(self, app):
        menu = QMenu()
        show = menu.addAction("Mostrar letra", self._open)
        f = show.font()
        f.setBold(True)
        show.setFont(f)
        menu.addSeparator()
        toggle = QAction("Brillo en los bordes", menu, checkable=True, checked=self.glow.enabled)
        toggle.toggled.connect(self._toggle_glow)
        menu.addAction(toggle)
        modes = menu.addMenu("Efecto del borde")
        group = QActionGroup(modes)
        for key, name in MODE_NAMES:
            act = QAction(name, modes, checkable=True, checked=key == self.manual)
            act.triggered.connect(lambda _=False, k=key: self._set_manual(k))
            group.addAction(act)
            modes.addAction(act)
            if key == "auto":
                modes.addSeparator()
        pulse = QAction("Destello en cada verso", menu, checkable=True, checked=self.line_pulse)
        pulse.toggled.connect(self._toggle_pulse)
        menu.addAction(pulse)
        menu.addSeparator()
        trans = menu.addMenu("Transición entre estrofas")
        trans_group = QActionGroup(trans)
        self.trans_actions = {}
        for key in transitions.SETTINGS:
            act = QAction(transitions.SETTING_NAMES[key], trans, checkable=True,
                          checked=key == self.window.trans_setting)
            act.triggered.connect(lambda _=False, k=key: self._set_transition(k))
            trans_group.addAction(act)
            trans.addAction(act)
            self.trans_actions[key] = act
            if key == "random":
                trans.addSeparator()
        self.window.transition_changed.connect(self._on_transition_changed)  # tecla T
        bg = menu.addMenu("Fondo de la letra")
        bg_group = QActionGroup(bg)
        self.bg_actions = {}
        for key in backdrop.MODES:
            act = QAction(backdrop.NAMES[key], bg, checkable=True, checked=key == self.window.bg_mode())
            act.triggered.connect(lambda _=False, k=key: self._set_bg(k))
            bg_group.addAction(act)
            bg.addAction(act)
            self.bg_actions[key] = act
            if key == "auto":
                bg.addSeparator()
        self.window.bg_changed.connect(self._on_bg_changed)  # tecla B en la ventana
        cov = menu.addMenu("Carátula")
        cov_group = QActionGroup(cov)
        self.cover_actions = {}
        for key in cover.MODES:
            act = QAction(cover.NAMES[key], cov, checkable=True, checked=key == self.window.cover_mode())
            act.triggered.connect(lambda _=False, k=key: self.window.set_cover(k, show=True))
            cov_group.addAction(act)
            cov.addAction(act)
            self.cover_actions[key] = act
        cov.addSeparator()
        hd = QAction("Portada HD (iTunes y Deezer)", cov, checkable=True, checked=self.hd_art)
        hd.toggled.connect(self._toggle_hd)
        cov.addAction(hd)
        self.window.cover_changed.connect(lambda m: self.cover_actions[m].setChecked(True))  # tecla C
        offset = menu.addMenu("Sincronizar letra")
        step = f"{OFFSET_STEP:.2f}".replace(".", ",")
        offset.addAction(f"Adelantar letra (+{step} s)", lambda: self._shift_offset(OFFSET_STEP))
        offset.addAction(f"Retrasar letra (−{step} s)", lambda: self._shift_offset(-OFFSET_STEP))
        offset.addAction("Restablecer sincronía", lambda: self._shift_offset(None))
        menu.addSeparator()
        menu.addAction("Escuchar juntos…", self._open_room)
        menu.addAction("Atajos de teclado",
                       lambda: self.tray.showMessage("Atajos en la ventana de la letra", constellation.HELP))
        menu.addAction("Salir de Estela", self._quit)
        self.tray = QSystemTrayIcon(QIcon(resource("icon.ico")), app)
        self.tray.setToolTip("Estela")
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.DoubleClick and self._open())
        self.tray.show()
        self.menu = menu  # que no lo recoja el recolector

    def _toggle_glow(self, on):
        self.settings.setValue("glow_enabled", on)
        self.glow.set_enabled(on)

    def _set_manual(self, key):
        self.manual = key
        self.settings.setValue("glow_mode", key)
        self._apply_mode()

    def _on_transition_changed(self, setting):
        self.trans_actions[setting].setChecked(True)
        self._update_audio(self.window.snap)  # "Automática" necesita el medidor; las demás no

    def _set_transition(self, setting):
        self.window.set_transition(setting, show=True)
        self._update_audio(self.window.snap)

    def _set_bg(self, mode):
        self.window.set_bg(mode, show=True)
        self._update_audio(self.window.snap)

    def _on_bg_changed(self, mode):
        self.bg_actions[mode].setChecked(True)
        self._update_audio(self.window.snap)

    def _toggle_hd(self, on):
        self.hd_art = on
        self.settings.setValue("hd_art", on)
        if on and self.track_snap:
            self._fetch_art(self.track, self.track_snap)
        elif not on:
            self.window.hd_key = None  # la portada de Windows vuelve con la siguiente foto

    def _fetch_art(self, key, snap):
        """Portada HD (iTunes → Deezer) en segundo plano; si no hay o falla, se queda la de Windows."""
        title, artist, duration = snap["title"], snap["artist"], snap["duration"]

        def work():
            try:
                data = artwork.fetch(title, artist, duration)
                if data:
                    art_id = "hd:" + hashlib.sha1(data).hexdigest()[:16]
                    self.hd_ready.emit(key, art_id, color.backdrop(data), color.cover(data))
            except Exception as e:  # sin conexión, imagen rara…: no es grave
                log.info("Portada grande no disponible: %s", e)

        threading.Thread(target=work, daemon=True).start()

    def _toggle_pulse(self, on):
        self.line_pulse = on
        self.settings.setValue("line_pulse", on)

    def _apply_mode(self):
        # en automático, hasta decidir sigue el modo de la canción anterior (sin parpadeo)
        mode = beat.resolve(self.manual, self.glow_auto or "breathe")
        if mode != self.glow.mode:
            log.info("Modo del borde: %s (%s)", mode, "manual" if self.manual != "auto" else "automático")
        self.glow.set_mode(mode)

    # --- desfase manual de la letra (por canción) ---
    def _offset_key(self):
        return f"{self.track[0]}|{self.track[1]}" if self.track else None

    def _shift_offset(self, delta):
        key = self._offset_key()
        if not key:
            return
        if delta is None:
            self.offsets.reset(key)
        else:
            self.offsets.add(key, delta)
        value = self.offsets.get(key)
        self.settings.setValue("lyrics_offsets", json.dumps(self.offsets.data))
        self.window.set_offset(value, show=True)
        log.info("Desfase de la letra %s: %+.2f s", key, value)

    # --- audio: energía y golpes para todos los modos, y clasificación del modo automático ---
    def _frame(self):
        if not self.audio_on:
            return
        now = time.monotonic()
        dt, self.audio_t = min(0.1, now - self.audio_t), now
        try:
            if self.meter is None:
                self.meter = audio.Meter()
            e = min(1.0, self.meter.peak())
            self.audio_fails = 0
        except OSError:
            self.audio_fails += 1
            if self.audio_fails >= 3:  # sin dispositivo de audio: se reintenta en la siguiente canción
                log.warning("Medidor de audio no disponible", exc_info=True)
                self._set_audio(False)
                self._close_audio()
            return
        energy = self.envelope.feed(e, dt)
        hit = self.detector.feed(e, now)
        if self.glow.visible():  # con el borde oculto no se despierta
            self.glow.set_energy(energy)
            if hit:
                self.glow.beat()
        self.window.set_energy(energy)  # fondo Al ritmo
        if hit:
            self.window.beat()
        if not self.meter.fallback:  # la salida general mezcla otras apps: no sirve para el ambiente
            pal = (self.track_snap["palette"] if self.track_snap else None) or []  # la de esta canción
            warm = bool(pal) and color.is_warm(pal[0])
            sat = colorsys.rgb_to_hsv(*(c / 255 for c in pal[0]))[1] if pal else 0.0
            self.mood.set_context(warm, sat, chorus=self.window.shown in self.window.chorus)
            self.mood.feed(e, hit, dt)
            self._auto_update(now, len(pal), warm)

    def _auto_update(self, now, n_colors, warm):
        """Aplica el ambiente a los modos en Automático (borde, fondo, transiciones) con sus límites."""
        state = self.mood.state
        self.window.set_mood(state)
        if self.mood.pop_drop():  # subida fuerte tras una calma
            log.info("Drop: destello y transición de impacto")
            self.glow.pulse()
            self.window.set_drop()
        if self.manual == "auto":
            if self.glow_pending and self.mood.t >= GLOW_FIRST:
                first = self.mood.raw()  # primera decisión con lo oído hasta ahora
                self.glow_auto, self.glow_state, self.glow_pending, self.glow_auto_t = (
                    beat.choose_glow(first, n_colors, warm), first, False, now)
                self.mood.state, self.mood.since = first, 0.0  # el ambiente parte de ahí (sin cambio extra)
            elif not self.glow_pending and state != self.glow_state and now - self.glow_auto_t >= GLOW_EVERY:
                self.glow_auto, self.glow_state, self.glow_auto_t = beat.choose_glow(state, n_colors, warm), state, now
            self._apply_mode()
        if now - self.bg_check_t >= 1.0:  # el fondo automático se revisa una vez por segundo
            self.bg_check_t = now
            gap = self.window.in_gap()
            target = beat.choose_bg(state, self.mood.since if state == "calm" else 0.0, gap)
            # entrar o salir de un instrumental no espera los 20 s (si no, casi nunca llegaría a tiempo)
            if target != self.bg_auto and (self.bg_auto is None or gap != self.bg_gap
                                           or now - self.bg_auto_t >= BG_EVERY):
                log.info("Fondo automático: %s (%s)", target, state)
                self.bg_auto, self.bg_auto_t = target, now
                self.window.set_auto_bg(target)
            self.bg_gap = gap

    def _set_audio(self, on):
        if on != self.audio_on:
            self.audio_on = on
            self.audio_t = time.monotonic()
            if not on:
                self.glow.set_energy(0.0)
                self.window.set_energy(0.0)

    def _update_audio(self, snap):
        """El medidor solo corre con música sonando (no en anuncios) y alguien que lo use:
        el borde visible o el fondo Al ritmo de la ventana de letras."""
        playing = bool(snap) and snap["playing"] and not snap["ad"]
        users = (self.glow.enabled and self.glow.visible()) or self.window.wants_audio()
        self._set_audio(playing and users and self.audio_fails < 3)

    def _close_audio(self):
        if self.meter:
            self.meter.close()
            self.meter = None

    def _line_tick(self):
        snap, lines = self.window.snap, self.window.lines
        idx = self.window.line_index(self.window.position()) if snap and lines and snap["playing"] else None
        if idx != self.last_line:
            if self.line_pulse and idx is not None and idx >= 0 and lines[idx][1].strip() and self.last_line is not None:
                self.glow.pulse()
            self.last_line = idx

    def _open(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _open_room(self):
        self._open()
        self.room.open()

    def _quit(self):
        if self.window.isVisible():
            self.window.close()  # guarda la posición
        self.tray.hide()
        QApplication.quit()

    def _on_snapshot(self, snap):
        self._on_track_snapshot(snap)
        self.room.on_snapshot(snap)  # después: el invitado compara con la foto ya aplicada

    def _on_track_snapshot(self, snap):
        if snap and (snap["title"], snap["artist"], snap["album"]) != self.track:
            self.audio_fails = 0  # p. ej. cambiaron los auriculares: se vuelve a intentar
        self._update_audio(snap)
        if snap is None:
            self.glow.set_target(self.rgb, 0.0)
            self.track = None
            self.window.set_lyrics("Esperando música…", [])
            self.window.set_snapshot(None)
            return
        if snap["color"] and not snap["ad"]:
            self.rgb = snap["color"]
            self.glow.set_palette(snap["palette"])
        self.glow.set_target(self.rgb, PAUSED if snap["ad"] or not snap["playing"] else 1.0)
        key = (snap["title"], snap["artist"], snap["album"])
        if key != self.track and not snap["ad"]:
            self.track, self.retry, self.wait_duration = key, 0, 0
            self.mood.start(key)
            self.glow_pending = True
            self.window.set_mood("normal")  # sin medidor (reserva) no se queda el ambiente de la anterior
            self._apply_mode()
            self.tray.setToolTip(f"Estela — {snap['title']} · {snap['artist']}"[:127])
            self.window.set_offset(self.offsets.get(self._offset_key()))
            self.window.set_lyrics(SEARCHING, [])
            self.debounce.start()  # también pide la portada grande: así no se dispara con cada salto rápido
            self.art_requested = False
        if key == self.track:
            self.track_snap = snap  # la de esta canción, no la de un anuncio posterior
        self.window.set_snapshot(snap)

    def _fetch(self):
        snap, key = self.track_snap, self.track
        if not snap or not key:
            return
        if self.hd_art and not self.art_requested:  # una vez por canción, tras la espera de 400 ms
            self.art_requested = True
            self._fetch_art(key, snap)
        if not snap["duration"] and self.wait_duration < DURATION_WAITS:
            # Windows aún no da la duración: sin ella LRCLIB puede devolver otra versión
            self.wait_duration += 1
            QTimer.singleShot(1000, lambda: key == self.track and self._fetch())
            return

        def work():
            try:
                # LRCLIB sale en cuanto llega; las letras mejores las aplica la ventana en la siguiente estrofa
                lines = lyrics.fetch_lines(snap["title"], snap["artist"], snap["album"], snap["duration"],
                                           lambda found, _source: self.lyrics_ready.emit(key, "", found))
                self.lyrics_ready.emit(key, NO_LYRICS if not lines else "", lines)
            except Exception as e:
                log.warning("Letra no disponible: %s", e)
                self.lyrics_ready.emit(key, OFFLINE, None)

        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics(self, key, status, lines):
        if key != self.track:
            return  # respuesta de una canción anterior
        if lines is None:
            if self.retry < len(RETRIES_S):
                QTimer.singleShot(RETRIES_S[self.retry] * 1000, lambda: key == self.track and self._fetch())
                self.retry += 1
            lines = []
        if lines:
            self.window.update_lyrics(lines)  # señal Qt: llega al hilo principal
        else:
            self.window.set_lyrics(status, lines)


def migrate():
    """BrilloMusical → Estela: mueve la carpeta de caché y copia los ajustes para no perderlos."""
    if os.path.isdir(OLD_DATA) and not os.path.exists(DATA):
        try:
            os.rename(OLD_DATA, DATA)
        except OSError:
            pass  # otra copia abierta o sin permisos: se empieza con caché nueva
    old, new = QSettings("BrilloMusical", "BrilloMusical"), QSettings("Estela", "Estela")
    if old.allKeys() and not new.allKeys():
        for k in old.allKeys():
            new.setValue(k, old.value(k))
        new.sync()


def main():
    migrate()
    os.makedirs(DATA, exist_ok=True)
    debug = os.environ.get("ESTELA_DEBUG") == "1"  # anclas de SMTC y origen de la letra en el log
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.handlers.RotatingFileHandler(os.path.join(DATA, "app.log"), maxBytes=2_000_000 if debug
                                                       else 512_000, backupCount=1, encoding="utf-8")])
    logging.getLogger("PIL").setLevel(logging.INFO)  # su DEBUG solo ensucia el log
    sys.excepthook = lambda *exc: log.critical("Error no controlado", exc_info=exc)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Estela")
    app.setWindowIcon(QIcon(resource("icon.ico")))
    lock = QLockFile(os.path.join(DATA, "app.lock"))
    if not lock.tryLock(100):
        return 0  # ya hay una copia abierta
    controller = Controller(app)  # noqa: F841 (debe vivir mientras corre la app)
    log.info("Inicio")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
