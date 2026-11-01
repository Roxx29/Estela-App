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

from PySide6.QtCore import QCoreApplication, QLockFile, QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import artwork, audio, backdrop, beat, bridge, color, cover, history, intro, kinetic, lyrics, nyc, quality, transitions, ytm
from .color import FALLBACK
from .constellation import HELP, SEARCHING, LyricsWindow
from .glow import Glow
from .media import MediaWatcher, position
from .vsync import FrameClock

# la app se llama Estela; la carpeta de datos y los ajustes conservan el nombre antiguo para no perder nada
DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BrilloMusical")
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


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


class Controller(QObject):
    lyrics_ready = Signal(object, str, object)  # (clave de canción, estado, líneas)
    hd_ready = Signal(object, str, object, object)  # (clave de canción, art_id, fondo, carátula)
    genre_ready = Signal(object, object)  # (clave de canción, género de iTunes o None)

    def __init__(self, app):
        super().__init__()
        self.settings = QSettings("BrilloMusical", "BrilloMusical")
        self.frames = FrameClock()  # fotogramas al ritmo de la pantalla para el borde y la letra
        app.aboutToQuit.connect(self.frames.stop)
        self.quality = quality.Governor(self.settings.value("quality", "auto"))  # común a borde y letra
        self.glow = Glow(self.frames, self.quality)
        self.glow.set_enabled(self.settings.value("glow_enabled", True, type=bool))
        self.window = LyricsWindow(self.settings, self.frames, self.quality)
        self.window.offset_step.connect(self._shift_offset)
        self.track = None  # clave de la canción actual
        self.track_snap = None
        self.no_lyrics = None  # canción para la que LRCLIB no dio letra
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
        # dos fuentes: el navegador (controles multimedia de Windows) y YouTube Music integrado
        self.sources, self.active = {"ext": None, "ytm": None}, "ext"
        self.watcher.snapshot.connect(lambda s: self._source("ext", s))
        self.ytm = ytm.Ytm(DATA)
        self.ytm.snapshot.connect(lambda s: self._source("ytm", s))
        self.ytm_queue = []
        # extensión de Chrome: la cola de YouTube Music del navegador (con tu cuenta) en el carrusel
        self.bridge, self.browser = bridge.Bridge(), (None, 0.0)
        self.bridge.state.connect(self._on_browser)
        app.aboutToQuit.connect(self.bridge.stop)
        self.ytm.queue.connect(self._on_queue)
        # letra de la pestaña "Letra" de YouTube Music: respaldo si LRCLIB no tiene (o no responde)
        self.page_lyrics = {}  # título en minúsculas -> texto
        self.ytm.lyrics.connect(self._on_page_lyrics)
        self.window.open_ytm.connect(self.ytm.show)
        self.window.song.connect(self._song)
        self.window.control.connect(self._control)
        self.genre_ready.connect(self._on_genre)
        self.seek = (None, 0.0)  # (destino, hora) del último ±10 s: dos seguidos suman aunque SMTC no haya respondido
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
        menu.addAction("Abrir YouTube Music", self.ytm.show)
        menu.addAction("Ver tu playlist (extensión de Chrome)…", self._extension)
        menu.addAction("Pantalla completa", self._fullscreen)
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
        style = self._choice(menu, "Estilo de letra", kinetic.SETTINGS, kinetic.SETTING_NAMES, self.window.style_setting,
                             lambda k: self.window.set_style(k, show=True) or self._on_style(k))
        self.window.style_changed.connect(lambda k: style[k].setChecked(True) or self._on_style(k))
        nyc_bg = self._choice(menu, "Fondo de Nueva York", nyc.BGS, nyc.BG_NAMES, self.window.nyc_bg,
                              lambda k: self.window.set_nyc_bg(k, show=True))
        self.window.nyc_bg_changed.connect(lambda k: nyc_bg[k].setChecked(True))
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
        qual = self._choice(menu, "Calidad", quality.SETTINGS, quality.NAMES, self.quality.setting,
                            lambda k: self.window.set_quality(k, show=True))
        self.window.quality_changed.connect(lambda k: qual[k].setChecked(True))
        menu.addSeparator()
        anim = QAction("Animación de inicio", menu, checkable=True,
                       checked=self.settings.value("intro_enabled", True, type=bool))
        anim.toggled.connect(lambda on: self.settings.setValue("intro_enabled", on))
        menu.addAction(anim)
        menu.addAction("Atajos de teclado", lambda: self.tray.showMessage("Atajos en la ventana de la letra", HELP))
        menu.addAction("Salir de Estela", self._quit)
        self.tray = QSystemTrayIcon(QIcon(resource("icon.ico")), app)
        self.tray.setToolTip("Estela")
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.DoubleClick and self._open())
        self.tray.show()
        self.menu = menu  # que no lo recoja el recolector

    @staticmethod
    def _choice(menu, title, keys, names, current, pick):
        """Submenú de opciones exclusivas; devuelve {clave: acción} para marcarlas desde fuera."""
        sub = menu.addMenu(title)
        group, acts = QActionGroup(sub), {}
        for key in keys:
            act = QAction(names[key], sub, checkable=True, checked=key == current)
            act.triggered.connect(lambda _=False, k=key: pick(k))
            group.addAction(act)
            sub.addAction(act)
            acts[key] = act
        return acts

    def _on_style(self, setting):
        if setting == "auto" and self.track_snap:  # a mitad de canción: su género ya
            self._fetch_genre(self.track, self.track_snap)
        self._update_audio(self.window.snap)

    def _fetch_genre(self, key, snap):
        """Género de la canción en iTunes (manda título y artista, como la portada HD), en segundo plano."""
        def work():
            try:
                self.genre_ready.emit(key, artwork.genre(snap["title"], snap["artist"]))
            except Exception as e:  # sin conexión: se queda el estilo que hay
                log.info("Género no disponible: %s", e)

        threading.Thread(target=work, daemon=True).start()

    def _on_genre(self, key, genre):
        if key == self.track:
            self.window.set_auto_style(kinetic.style_for(genre), genre)
            self._update_audio(self.window.snap)  # Latido y Cinta necesitan el medidor; Constelación no

    def _source(self, name, snap):
        """Llega una foto de una fuente; manda la integrada salvo que solo suene el navegador."""
        self.sources[name] = snap
        self.active, chosen = ytm.choose(self.sources["ext"], self.sources["ytm"], self.active)
        self._show_queue()
        self._on_snapshot(chosen)

    def _on_queue(self, queue):
        self.ytm_queue = queue
        self._show_queue()

    def _on_browser(self, state):
        self.browser = (state, time.monotonic())
        if state["lyrics"]:
            self._on_page_lyrics(state["now"]["title"], state["lyrics"])
        self._show_queue()

    def _on_page_lyrics(self, title, text):
        if self.page_lyrics.get(title.lower()) == text:
            return
        if len(self.page_lyrics) > 30:
            self.page_lyrics.clear()
        self.page_lyrics[title.lower()] = text
        if self.track and self.no_lyrics == self.track:
            self._use_page_lyrics()

    def _use_page_lyrics(self):
        """Letra de YouTube Music repartida en el tiempo (no trae tiempos) si es de la canción que suena."""
        snap = self.track_snap
        text = snap and self.page_lyrics.get(snap["title"].lower())
        if not text or not snap["duration"]:
            return False
        log.info("Letra de YouTube Music (sin tiempos)")
        self.no_lyrics = None
        self.window.set_lyrics("", lyrics.spread(text, snap["duration"]))
        return True

    def _browser_queue(self):
        """La cola que manda la extensión, si es reciente y de la canción que suena en el navegador."""
        state, t = self.browser
        snap = self.sources["ext"]
        if state and time.monotonic() - t < 5 and snap and state["now"]["title"] == snap["title"]:
            return state["queue"]
        return None

    def _show_queue(self):
        queue = self.ytm_queue if self.active == "ytm" else self._browser_queue() or []
        if queue is not self.window.queue:
            self.window.set_queue(queue)

    def _song(self, entry):
        """Clic en el carrusel: de la cola se reproduce; las anteriores se buscan en YouTube Music."""
        kind, it, ref = entry
        if kind == "next" and self.active == "ytm":
            self.ytm.command("queue", ref)
        elif kind == "next":
            self.bridge.command("queue", ref, it["title"])  # la extensión la pone en la pestaña de YouTube Music
        elif kind == "now" and self.active == "ytm":
            self.ytm.show()
        elif self.active == "ytm":
            self.ytm.search(it["title"], it["artist"])
        elif kind != "now":
            QDesktopServices.openUrl(QUrl(history.search_url(it["title"], it["artist"])))

    def _control(self, action):
        """Controles de YouTube Music: los manda a la sesión del navegador que se ve en pantalla."""
        snap = self.window.snap
        if action in ("back", "fwd"):
            if not snap or snap["ad"]:
                return
            last, t = self.seek
            base = last if last is not None and time.monotonic() - t < 1.0 else position(snap)  # sin el desfase de la letra
            target = base + (10 if action == "fwd" else -10)
            end = snap["duration"] - 1 if snap["duration"] else target
            target = max(0.0, min(target, end))
            self.seek = (target, time.monotonic())
            self._send("seek", target)
        elif not (action == "next" and snap and snap["ad"]):  # en un anuncio YouTube no deja saltar
            self._send(action)

    def _send(self, name, arg=None):
        (self.ytm if self.active == "ytm" else self.watcher).command(name, arg)

    def _extension(self):
        """Abre la carpeta de la extensión y explica cómo instalarla en Chrome o Edge."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(os.path.dirname(os.path.dirname(resource("x"))), "extension")))
        self.tray.showMessage("Extensión de Chrome", "1. Abre chrome://extensions (o edge://extensions)\n"
                              "2. Activa el Modo de desarrollador\n3. «Cargar descomprimida» y elige la carpeta que se acaba "
                              "de abrir\n4. Recarga YouTube Music: tu cola aparece en el carrusel")

    def _fullscreen(self):
        self._open()
        if not self.window.isFullScreen():
            self.window.toggle_fullscreen()

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
        if not self.window.isFullScreen():  # en pantalla completa se queda así
            self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _quit(self):
        if self.window.isVisible():
            self.window.close()  # guarda la posición
        self.tray.hide()
        self.ytm.quit()
        QApplication.quit()

    def _on_snapshot(self, snap):
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
        if not self.art_requested:  # una vez por canción, tras la espera de 400 ms
            self.art_requested = True
            if self.hd_art:
                self._fetch_art(key, snap)
            if self.window.style_setting == "auto":
                self._fetch_genre(key, snap)
        if not snap["duration"] and self.wait_duration < DURATION_WAITS:
            # Windows aún no da la duración: sin ella LRCLIB puede devolver otra versión
            self.wait_duration += 1
            QTimer.singleShot(1000, lambda: key == self.track and self._fetch())
            return

        def work():
            try:
                lines = lyrics.fetch(snap["title"], snap["artist"], snap["album"], snap["duration"])
                self.lyrics_ready.emit(key, NO_LYRICS if not lines else "", lines)
            except Exception as e:
                log.warning("Letra no disponible: %r", e, exc_info=not isinstance(e, OSError))
                self.lyrics_ready.emit(key, lyrics.reason(e), None)

        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics(self, key, status, lines):
        if key != self.track:
            return  # respuesta de una canción anterior
        if lines is None:
            if self.retry < len(RETRIES_S):
                QTimer.singleShot(RETRIES_S[self.retry] * 1000, lambda: key == self.track and self._fetch())
                self.retry += 1
            else:
                status = "Sin conexión con el servidor de letras"  # ya no reintenta: que no diga "reintentando"
            lines = []
        if not lines:
            self.no_lyrics = key  # si luego llega la de YouTube Music, se usa
            if self._use_page_lyrics():
                return
        self.window.set_lyrics(status, lines)


def main():
    os.makedirs(DATA, exist_ok=True)
    debug = os.environ.get("BRILLOMUSICAL_DEBUG") == "1"  # anclas de SMTC y origen de la letra en el log
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.handlers.RotatingFileHandler(os.path.join(DATA, "app.log"), maxBytes=2_000_000 if debug
                                                       else 512_000, backupCount=1, encoding="utf-8")])
    logging.getLogger("PIL").setLevel(logging.INFO)  # su DEBUG solo ensucia el log
    sys.excepthook = lambda *exc: log.critical("Error no controlado", exc_info=exc)
    try:  # verificar HTTPS como el navegador: Windows descarga las raíces nuevas (p. ej. las de Let's Encrypt 2026)
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        log.warning("truststore no disponible: HTTPS con el almacén de Python", exc_info=True)
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)  # YouTube Music integrado se carga más tarde
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Estela")
    app.setWindowIcon(QIcon(resource("icon.ico")))
    lock = QLockFile(os.path.join(DATA, "app.lock"))
    if not lock.tryLock(100):
        return 0  # ya hay una copia abierta
    controller = Controller(app)
    st = controller.settings
    if st.value("intro_enabled", True, type=bool) and not intro.system_quiet():
        splash = intro.Intro(first=not st.value("intro_seen", False, type=bool), light=controller.quality.light)
        splash.show()  # noqa: F841 (se borra sola al terminar)
        st.setValue("intro_seen", True)
    log.info("Inicio")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
