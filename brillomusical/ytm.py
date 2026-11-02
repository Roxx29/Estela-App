"""YouTube Music integrado: YouTube Music dentro de la app, en su propia ventana (QtWebEngine).

Se carga solo al abrirla (quien usa el navegador no paga su memoria). Cada 0,5 s se lee la página
por JavaScript (lo que suena, la posición y la cola) y se emite una "foto" con el mismo formato que
media.MediaWatcher. Cerrar la ventana la oculta: la música sigue sonando.
"""
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

import shiboken6
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from . import color, lyrics

URL = "https://music.youtube.com/"
UA = "Estela/1.0"  # solo para descargar portadas y miniaturas
POLL_MS = 500
GONE_AFTER = 5.0
FRESH = 1.0  # tras cambiar de canción, la duración del <video> aún puede ser la anterior
# selectores de YouTube Music (cambian sin aviso: si algo deja de ir, es aquí)
LYRICS_JS = r"""// Letra de la pestaña "Letra" de YouTube Music (con licencia de Google), una vez por canción. Si la pestaña
// no está abierta y se puede (ventana propia o pestaña en segundo plano), la abre, la lee y vuelve a "A continuación".
function estelaLyrics(key, canClick) {
  const old = window.__estelaLyr;
  let s = old;
  if (!old || old.key !== key) {
    // si cambió la canción a mitad de una lectura, se hereda el clic para volver luego a la pestaña del usuario
    const back = old && !old.done && old.clicked ? old.back : -1;
    s = window.__estelaLyr = {key, text: "", wait: 0, seen: 0, clicked: back >= 0, back, done: false,
                              prev: old ? old.text || old.prev : ""};
  }
  if (s.done || !key) return s.text;
  const tabs = [...document.querySelectorAll("ytmusic-player-page tp-yt-paper-tab")];
  const tab = tabs[1];
  const finish = () => { s.done = true; if (s.clicked && tabs[s.back]) tabs[s.back].click(); };
  if (!tab || tab.hasAttribute("disabled") || tab.getAttribute("aria-disabled") === "true") {
    if (++s.wait > 8) finish();  // canción sin letra en YouTube Music
    return "";
  }
  if (tab.getAttribute("aria-selected") !== "true" && !tab.classList.contains("iron-selected")) {
    if (canClick && !s.clicked) {
      s.back = Math.max(0, tabs.findIndex((t) => t.getAttribute("aria-selected") === "true"));
      s.clicked = true;
      tab.click();
    }
    return "";
  }
  if (++s.seen < 3) return "";
  const d = document.querySelector("ytmusic-player-page ytmusic-description-shelf-renderer .description");
  const t = d ? d.textContent.trim().slice(0, 20000) : "";
  if (t && t === s.prev && s.seen <= 10) return "";  // aún es la letra de la canción anterior
  s.text = t === s.prev ? "" : t;
  if (s.text || s.seen > 10) finish();
  return s.text;
}
"""
READ_JS = LYRICS_JS + r"""(() => {
  const m = navigator.mediaSession && navigator.mediaSession.metadata, v = document.querySelector('video');
  const txt = (q, s) => ((q.querySelector(s) || {}).textContent || '').trim();
  const queue = [...document.querySelectorAll('ytmusic-player-queue-item')].map(q => ({
    title: txt(q, '.song-title'), artist: txt(q, '.byline'),
    thumb: (q.querySelector('.thumbnail img') || {}).src || '',
    selected: q.hasAttribute('selected') || ['playing', 'paused'].includes(q.getAttribute('play-button-state'))}));
  return JSON.stringify({title: m ? m.title : '', artist: m ? m.artist : '', album: m ? m.album : '',
    art: m && m.artwork && m.artwork.length ? m.artwork[m.artwork.length - 1].src : '',
    pos: v ? v.currentTime : 0, duration: v && isFinite(v.duration) ? v.duration : 0,
    paused: v ? v.paused : true, rate: v ? v.playbackRate : 1,
    ad: !!document.querySelector('#movie_player.ad-showing'), queue,
    lyrics: m ? estelaLyrics(m.title + '|' + m.artist, document.hidden || !document.hasFocus()) : ''});
})()"""
COMMANDS = {
    "play": "(v => v && (v.paused ? v.play() : v.pause()))(document.querySelector('video'))",
    "next": "document.querySelector('ytmusic-player-bar .next-button').click()",
    "prev": "document.querySelector('ytmusic-player-bar .previous-button').click()",
    "seek": "document.querySelector('video').currentTime = {arg}",
    "queue": "(q => q && (q.querySelector('ytmusic-play-button-renderer') || q).click())"
             "(document.querySelectorAll('ytmusic-player-queue-item')[{arg}])",
}

log = logging.getLogger(__name__)


def parse(raw, prev, now):
    """Foto con el formato de media.MediaWatcher a partir de lo leído en la página; None si no suena nada.

    prev: (clave, hora del cambio) de la lectura anterior. Devuelve (foto, prev nuevo).
    """
    if not raw or not raw.get("title"):
        return None, prev
    key = (raw["title"], raw.get("artist", ""), raw.get("album", ""))
    if not prev or prev[0] != key:
        prev = (key, now)
    duration = float(raw.get("duration") or 0)
    pos = float(raw.get("pos") or 0)
    if now - prev[1] < FRESH and pos > 2:
        duration = 0.0  # el <video> aún trae la duración de la anterior: LRCLIB elegiría otra versión
    ad = bool(raw.get("ad")) or lyrics.is_ad(raw["title"], key[1], key[2], duration)
    return {"title": raw["title"], "artist": key[1], "album": key[2], "duration": duration, "ad": ad,
            "playing": not raw.get("paused", True), "pos": pos, "stamp": time.time(), "stamp_ok": True,
            "rate": float(raw.get("rate") or 1.0), "color": None, "palette": [], "backdrop": None, "cover": None,
            "art_id": None, "art_url": raw.get("art") or ""}, prev


def choose(ext, ytm, active="ext"):
    """Qué fuente manda: la que suena (la integrada si suenan las dos); si ninguna, sigue la que mandaba."""
    if ytm is None or ext is None:
        return ("ext", ext) if ytm is None else ("ytm", ytm)
    if ytm["playing"] or ext["playing"]:
        return ("ytm", ytm) if ytm["playing"] else ("ext", ext)
    return (active, ytm if active == "ytm" else ext)  # entre canciones no salta de una a otra


class Window(QWidget):
    def __init__(self, view):
        super().__init__()
        self.setWindowTitle("Estela · YouTube Music")
        self.resize(1100, 760)
        tip = QLabel("Google no permite iniciar sesión en navegadores integrados en apps: aquí YouTube Music funciona "
                     "sin cuenta. Para tu Premium y tus listas, usa YouTube Music en Chrome o Edge: Estela lo "
                     "detecta igual. Si cierras esta ventana, la música sigue sonando.")
        tip.setWordWrap(True)
        tip.setStyleSheet("padding: 6px 10px; background: #1b1b22; color: #ddd;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(tip)
        lay.addWidget(view)
        self.quitting = False

    def closeEvent(self, e):
        if not self.quitting:  # cerrar = ocultar: la música sigue
            e.ignore()
            self.hide()


class Ytm(QObject):
    snapshot = Signal(object)
    queue = Signal(object)
    lyrics = Signal(str, str, str)  # (título, artista, letra de la pestaña Letra)

    def __init__(self, data_dir):
        super().__init__()
        self.dir = os.path.join(data_dir, "web")
        self.win = self.page = None
        self.prev = None
        self.last_seen, self.gone = 0.0, False
        self.arts = {}  # url de portada -> (paleta y aspecto, fondo, carátula, art_id), calculado en un hilo
        self.last_queue = None
        self.last_lyrics = None
        self.timer = QTimer(self, interval=POLL_MS, timeout=self._poll)

    def show(self):
        if self.win is None:
            self._create()
        self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()

    def _create(self):
        # importado aquí: el modo navegador no carga Chromium
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        profile = QWebEngineProfile("BrilloMusical", self)  # con nombre: cookies y sesión en disco
        profile.setPersistentStoragePath(os.path.join(self.dir, "storage"))
        profile.setCachePath(os.path.join(self.dir, "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        profile.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        view = QWebEngineView()
        self.page = QWebEnginePage(profile, view)
        view.setPage(self.page)
        view.load(QUrl(URL))
        self.win = Window(view)
        self.timer.start()

    def quit(self):
        if self.win is not None:
            self.timer.stop()
            self.win.quitting = True
            self.win.close()
            shiboken6.delete(self.page)  # la página antes que su perfil (si no, Chromium avisa al salir)
            self.page = None

    def command(self, name, arg=None):
        if self.page is not None and name in COMMANDS:
            self.page.runJavaScript(COMMANDS[name].replace("{arg}", repr(arg)), 0)

    def search(self, title, artist):
        """Busca una canción dentro de la ventana integrada (sin abrir el navegador)."""
        self.show()
        self.page.load(QUrl(URL + "search?" + urllib.parse.urlencode({"q": f"{title} {artist}".strip()})))

    def _poll(self):
        self.page.runJavaScript(READ_JS, 0, self._on_read)

    def _on_read(self, text):
        try:
            raw = json.loads(text) if text else None
        except ValueError:
            raw = None
        now = time.time()
        snap, self.prev = parse(raw, self.prev, now)
        if snap:
            self.last_seen, self.gone = now, False
            self._art(snap)
            self.snapshot.emit(snap)
        elif now - self.last_seen > GONE_AFTER and not self.gone:
            self.gone = True  # una vez: sin sesión, cargando o sin música (manda el navegador, si suena)
            self.snapshot.emit(None)
        text = (raw or {}).get("lyrics")
        if snap and isinstance(text, str) and text and text != self.last_lyrics:
            self.last_lyrics = text
            self.lyrics.emit(snap["title"], snap["artist"], text)
        queue = (raw or {}).get("queue") or []
        if queue != self.last_queue:
            self.last_queue = queue
            self.queue.emit(queue)

    def _art(self, snap):
        """Colores, fondo y carátula de la portada (descargada una vez por URL, fuera del hilo de Qt)."""
        url = snap.pop("art_url")
        done = self.arts.get(url)
        if done is None and url.startswith("https://"):
            self.arts[url] = False  # en camino

            def work():
                try:
                    data = fetch(url)
                    self.arts[url] = (color.from_bytes(data), color.backdrop(data), color.cover(data),
                                      hashlib.sha1(data).hexdigest())
                except Exception as e:  # sin red o imagen rara: sin portada
                    log.info("Portada de YouTube Music no disponible: %s", e)
                    self.arts[url] = ()  # fallo: no se reintenta cada 0,5 s
                if len(self.arts) > 50:
                    self.arts.clear()

            threading.Thread(target=work, daemon=True).start()
        if done:
            (pal, _), backdrop, cover, art_id = done
            snap.update(color=pal[0] if pal else None, palette=pal, backdrop=backdrop, cover=cover, art_id=art_id)


def fetch(url, limit=5_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=6) as r:
        return r.read(limit)

