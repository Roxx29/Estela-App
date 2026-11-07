"""Lee la canción del navegador desde los controles multimedia de Windows (SMTC).

Un hilo con asyncio consulta cada 500 ms (Chrome no siempre lanza eventos) y emite
una "foto" del estado a Qt con la señal `snapshot` (dict o None).
"""
import asyncio
import hashlib
import logging
import threading
import time

from PySide6.QtCore import QObject, Signal
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
from winrt.windows.storage.streams import DataReader

from . import color, lyrics

BROWSERS = ("chrome", "msedge", "brave", "opera", "vivaldi", "308046b0af4a39cb", "_crx_")  # 308046… = Firefox
PLAYING = 4
POLL = 0.5
GONE_AFTER = 5.0  # segundos sin sesión antes de avisar "sin música"
ART_WINDOW = 10.0  # segundos reintentando la portada de una canción nueva

log = logging.getLogger(__name__)


async def read_thumbnail(ref):
    stream = await ref.open_read_async()
    reader = DataReader(stream.get_input_stream_at(0))
    n = await reader.load_async(stream.size)
    buf = bytearray(n)
    reader.read_bytes(buf)
    return bytes(buf)


class MediaWatcher(QObject):
    snapshot = Signal(object)
    command_failed = Signal(str, object)  # (orden, valor): el navegador no la aceptó

    def __init__(self):
        super().__init__()
        self._arts = {}  # (app, título, artista, álbum) -> portada leída
        self._last_hash = None
        self._anchor = {}  # app -> (posición, stamp) de la última foto
        self._last_seen = 0.0
        self._loop = self._session = None  # sesión de la última foto: las órdenes van a esa
        self._stop = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name="smtc")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def command(self, kind, value=None):
        """Orden a la sesión de la última foto desde el hilo de Qt: play, pause, seek (s) o rate."""
        loop = self._loop
        if not loop:
            self.command_failed.emit(kind, value)
            return
        fut = asyncio.run_coroutine_threadsafe(self._command(kind, value), loop)
        fut.add_done_callback(lambda f: (f.cancelled() or f.exception() or not f.result())
                              and self.command_failed.emit(kind, value))

    async def _command(self, kind, value):
        s = self._session
        if s is None:
            return False
        if kind == "play":
            return await s.try_play_async()
        if kind == "pause":
            return await s.try_pause_async()
        if kind == "seek":
            return await s.try_change_playback_position_async(int(max(0.0, value) * 10_000_000))
        if kind == "rate":
            return await s.try_change_playback_rate_async(value)
        return False

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        manager = None
        while not self._stop.is_set():
            snap = None
            try:
                manager = manager or await Manager.request_async()
                snap = await self._poll(manager)
            except Exception:
                log.exception("Error leyendo SMTC")
                manager = None
            if self._stop.is_set():
                break
            if snap:
                self._last_seen = time.time()
                self.snapshot.emit(snap)
            elif time.time() - self._last_seen > GONE_AFTER:
                self.snapshot.emit(None)
            await asyncio.sleep(POLL)

    async def _poll(self, manager):
        sessions = []
        for s in manager.get_sessions():
            try:
                if any(b in s.source_app_user_model_id.lower() for b in BROWSERS):
                    sessions.append((s.get_playback_info().playback_status != PLAYING, s))
            except Exception:
                log.debug("Sesión no disponible", exc_info=True)  # p.ej. pestaña recién cerrada
        for _, s in sorted(sessions, key=lambda x: x[0]):
            try:
                snap = await self._read(s)
            except Exception:
                log.warning("Sesión con error, se ignora", exc_info=True)
                continue
            if snap:
                self._session = s
                return snap
        return None

    async def _read(self, session):
        props = await session.try_get_media_properties_async()
        if not props or not props.title:
            return None
        timeline = session.get_timeline_properties()
        info = session.get_playback_info()
        duration = timeline.end_time.total_seconds()
        key = (session.source_app_user_model_id, props.title, props.artist, props.album_title)
        if len(self._arts) > 20:
            self._arts.clear()
        now = time.time()
        art = self._arts.setdefault(key, {"hash": None, "art": (None, None), "t0": now, "next": 0.0,
                                          "prev": self._last_hash})
        if props.thumbnail and now >= art["next"] and now - art["t0"] < ART_WINDOW:
            art["next"] = now + (POLL if art["hash"] is None else 2.0)
            try:
                data = await read_thumbnail(props.thumbnail)
                digest = hashlib.sha1(data).digest()
                # la portada llega después que el título: la de la canción anterior no vale
                # (salvo al final de la ventana, por si es el mismo álbum)
                stale = digest == art["prev"] and now - art["t0"] < ART_WINDOW - 2
                if digest != art["hash"] and not stale:
                    art["hash"], art["art"] = digest, color.from_bytes(data)
                    art["backdrop"] = color.backdrop(data)  # difuminada aquí, fuera del hilo de la interfaz
                    art["cover"] = color.cover(data)  # carátula nítida
                    self._last_hash = digest
            except Exception:
                log.exception("No se pudo leer la portada")
        pal, aspect = art["art"]
        ad = lyrics.is_ad(props.title, props.artist, props.album_title, duration)
        # YouTube Music: tiene álbum o portada cuadrada (YouTube normal es 16:9)
        if not ad and not props.album_title and not (aspect and 0.9 <= aspect <= 1.1):
            return None
        pos = timeline.position.total_seconds()
        stamp = timeline.last_updated_time.timestamp()
        stamp_ok = 0 <= stamp <= time.time()
        playing = info.playback_status == PLAYING
        if not stamp_ok:  # fecha desconocida (1601) o reloj raro
            prev = self._anchor.get(session.source_app_user_model_id)
            # misma posición sonando sin parar: es la misma ancla (con "ahora" la letra retrocedería cada 0,5 s).
            # Tras una pausa no: la fecha vieja adelantaría la letra todo lo que duró la pausa
            stamp = prev[1] if prev and prev[0] == pos and prev[2] and playing else time.time()
        self._anchor[session.source_app_user_model_id] = (pos, stamp, playing)
        log.debug("ancla %s pos=%.2f stamp_ok=%s extrapolada=%.2f", props.title, pos, stamp_ok,
                  pos + (time.time() - stamp))
        return {
            "title": props.title, "artist": props.artist, "album": props.album_title,
            "duration": duration, "ad": ad, "color": pal[0] if pal else None, "palette": pal,
            "backdrop": art.get("backdrop"), "cover": art.get("cover"),
            "art_id": art["hash"].hex() if art["hash"] else None,
            "playing": playing,
            "pos": pos, "stamp": stamp, "stamp_ok": stamp_ok,
            "rate": info.playback_rate or 1.0,
        }


def position(snap):
    """Posición actual extrapolada desde la última actualización de Windows."""
    pos = snap["pos"]
    if snap["playing"]:
        pos += (time.time() - snap["stamp"]) * snap["rate"]
    return min(pos, snap["duration"]) if snap["duration"] else pos
