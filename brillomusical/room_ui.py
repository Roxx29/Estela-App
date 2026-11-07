"""Panel de Escuchar juntos (tecla J): sala, invitación, miembros, chat, sugerencias y sincronía."""
import logging
import os
import threading
import time
import zlib

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import media, room, transitions, ytsearch
from .room import TXT

WIDTH = 320
SEEK_LEAD = 0.3  # el salto tarda en aplicarse
CMD_GAP = 3.0  # entre órdenes de salto/reproducción (Windows tarda en dar la posición nueva)
STYLE = """
#room { background: rgba(8, 10, 18, 205); border-left: 1px solid rgba(255,255,255,40); }
#room QLabel { color: #e8ecf4; } #room QLabel[hint="1"] { color: #9aa3b5; }
#room QLineEdit, #room QPlainTextEdit, #room QListWidget { background: rgba(255,255,255,18); color: #f2f4f8;
  border: 1px solid rgba(255,255,255,35); border-radius: 6px; padding: 4px; }
#room QPushButton { background: rgba(255,255,255,30); color: #f2f4f8; border: none; border-radius: 6px;
  padding: 6px 10px; } #room QPushButton:hover { background: rgba(255,255,255,55); }
"""
log = logging.getLogger(__name__)


def _label(text="", hint=False, wrap=True):
    lab = QLabel(text)
    lab.setTextFormat(Qt.PlainText)
    lab.setWordWrap(wrap)
    if hint:
        lab.setProperty("hint", "1")
    return lab


def _button(text, slot):
    b = QPushButton(text)
    b.clicked.connect(slot)
    return b


class RoomPanel(QFrame):
    found = Signal(object, object, bool)  # (canción del anfitrión, resultado o None, es invitado)

    def __init__(self, window, settings, watcher):
        super().__init__(window)
        self.window, self.settings, self.watcher = window, settings, watcher
        self.setObjectName("room")
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WA_StyledBackground)
        self.client = None
        self.me = self.host = self.code = self.state = None
        self.offset = 0.0  # ms, servidor − local
        self.members, self.requests, self.suggestions = [], {}, {}
        self.rate, self.rate_ok, self.cmd_t = 1.0, True, -1e9
        self.opening = self.before_open = None
        self.sent, self.sent_t = None, -1e9
        self.own_transition = None  # la del invitado, se restaura al salir
        self.found.connect(self._on_found)
        watcher.command_failed.connect(self._on_command_failed)
        self._build()
        self.hide()
        window.installEventFilter(self)
        QShortcut(QKeySequence(Qt.Key_J), window, self._toggle_key)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close_panel, context=Qt.WidgetWithChildrenShortcut)

    # --- interfaz ---
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        title = _label("Escuchar juntos")
        f = QFont(title.font())
        f.setPointSize(14)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        self.notice = _label(hint=True)
        lay.addWidget(self.notice)

        # asistente de 3 pasos
        self.wizard = QWidget()
        w = QVBoxLayout(self.wizard)
        self.step = _label()
        self.step_n = 0
        self.server_edit = QLineEdit(placeholderText="https://estela.tu-usuario.workers.dev")
        self.next_btn = _button("Siguiente", self._wizard_next)
        w.addWidget(self.step)
        w.addWidget(self.server_edit)
        w.addWidget(self.next_btn)
        w.addStretch(1)
        self.pages.addWidget(self.wizard)

        # entrada
        self.lobby = QWidget()
        lo = QVBoxLayout(self.lobby)
        self.name_edit = QLineEdit(self.settings.value("room_name", os.environ.get("USERNAME", ""))[:32],
                                   placeholderText="Tu nombre", maxLength=32)
        lo.addWidget(_label("Tu nombre"))
        lo.addWidget(self.name_edit)
        lo.addWidget(_button("Crear sala", self._create))
        lo.addSpacing(12)
        lo.addWidget(_label("Entrar en una sala (pega la invitación)", hint=True))
        self.join_server = QLineEdit(placeholderText="Servidor")
        self.join_code = QLineEdit(placeholderText="Código", maxLength=200)
        for e in (self.join_server, self.join_code):
            e.textChanged.connect(self._fill_invite)
            lo.addWidget(e)
        lo.addWidget(_button("Entrar", self._join))
        lo.addStretch(1)
        lo.addWidget(_button("Servidor…", lambda: self._wizard(0)))
        self.pages.addWidget(self.lobby)

        # sala
        self.inroom = QWidget()
        r = QVBoxLayout(self.inroom)
        self.code_label = _label(wrap=False)
        f = QFont(self.code_label.font())
        f.setPointSize(26)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        self.code_label.setFont(f)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        r.addWidget(self.code_label)
        self.copy_btn = _button("Copiar invitación", self._copy)
        r.addWidget(self.copy_btn)
        self.status = _label(hint=True)
        r.addWidget(self.status)
        self.member_list = QListWidget(maximumHeight=110)
        r.addWidget(self.member_list)
        self.req_box = QVBoxLayout()
        r.addLayout(self.req_box)
        self.sug_box = QVBoxLayout()
        r.addLayout(self.sug_box)
        self.suggest = QLineEdit(placeholderText="Sugerir: título - artista", maxLength=200)
        self.suggest.returnPressed.connect(self._suggest)
        r.addWidget(self.suggest)
        self.chat = QPlainTextEdit(readOnly=True)
        self.chat.setMaximumBlockCount(200)
        r.addWidget(self.chat, 1)
        self.chat_edit = QLineEdit(placeholderText="Escribe un mensaje…", maxLength=room.CHAT_MAX)
        self.chat_edit.returnPressed.connect(self._send_chat)
        r.addWidget(self.chat_edit)
        r.addWidget(_button("Salir de la sala", lambda: self.leave()))
        self.pages.addWidget(self.inroom)

    def eventFilter(self, obj, e):
        if obj is self.window and e.type() == QEvent.Resize:
            self._place()
        return False

    def _place(self):
        self.setGeometry(self.window.width() - WIDTH, 0, WIDTH, self.window.height())

    def _reserve(self, px):
        self.window.set_reserved_right(px)  # la constelación deja libre la franja del panel

    def open(self):
        self._place()
        server = self.settings.value("room_server", "")
        if self.client:
            self.pages.setCurrentWidget(self.inroom)
        elif not server:
            self._wizard(0)
        else:
            self.join_server.setText(self.join_server.text() or server)
            self.pages.setCurrentWidget(self.lobby)
        self.show()
        self.raise_()
        self._reserve(WIDTH)

    def close_panel(self):
        self.hide()
        self._reserve(0)
        self.window.setFocus()

    def _toggle_key(self):
        if isinstance(QGuiApplication.focusObject(), (QLineEdit, QPlainTextEdit)):
            return  # escribiendo: J es una letra
        self.close_panel() if self.isVisible() else self.open()

    def _say(self, text):
        self.notice.setText(text)
        if text:
            self.window.toast(text, 5)

    def _wizard(self, n):
        self.step_n = n
        self.step.setText((
            "Paso 1 de 3. Escuchar juntos usa tu propio servidor gratuito en Cloudflare. "
            "Solo hay que hacerlo una vez.",
            "Paso 2 de 3. Ejecuta deploy_servidor.bat (carpeta de Estela) y copia la dirección "
            "que muestra al terminar (acaba en workers.dev).",
            "Paso 3 de 3. Pega aquí la dirección de tu servidor:")[n])
        self.server_edit.setVisible(n == 2)
        self.server_edit.setText(self.settings.value("room_server", ""))
        self.next_btn.setText("Guardar" if n == 2 else "Siguiente")
        self.pages.setCurrentWidget(self.wizard)

    def _wizard_next(self):
        if self.step_n < 2:
            return self._wizard(self.step_n + 1)
        server = room.normalize_server(self.server_edit.text())
        if not server:
            return self._say(TXT["unreachable"])
        self.settings.setValue("room_server", server)
        self.join_server.setText(server)
        self._say("")
        self.pages.setCurrentWidget(self.lobby)

    def _fill_invite(self, text):
        server, code = room.parse_invite(text)
        if server and code:  # invitación completa pegada en cualquiera de los dos campos
            for e, v in ((self.join_server, server), (self.join_code, code)):
                e.blockSignals(True)
                e.setText(v)
                e.blockSignals(False)

    def _copy(self):
        QGuiApplication.clipboard().setText(room.invite_text(self.settings.value("room_server", ""), self.code))
        self.copy_btn.setText("Invitación copiada")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("Copiar invitación"))

    # --- conexión ---
    def _name(self):
        name = room.clean_text(self.name_edit.text(), 32) or "Invitado"
        self.settings.setValue("room_name", name)
        return name

    def _create(self):
        server = self.settings.value("room_server", "")
        if not server:
            return self._say(TXT["no-server"])
        self._connect(server, {"tipo": "crear", "nombre": self._name()})

    def _join(self):
        server = room.normalize_server(self.join_server.text())
        code = room.normalize_code(self.join_code.text())
        if not server:
            return self._say(TXT["no-server"] if not self.join_server.text().strip() else TXT["unreachable"])
        if not code:
            return self._say(TXT["sala-no-existe"].format(code=self.join_code.text().strip()[:12]))
        self._connect(server, {"tipo": "unirse", "codigo": code, "nombre": self._name()})

    def _connect(self, server, first):
        self.settings.setValue("room_server", server)
        self.client = room.RoomClient(server, first)
        self.client.received.connect(self._on_message)
        self.client.failed.connect(self._on_failed)
        self.client.clock.connect(lambda ms: setattr(self, "offset", ms))
        self.code = first.get("codigo", "")
        self.code_label.setText(self.code or "······")
        self.status.setText(TXT["pending"] if first["tipo"] == "unirse" else "")
        self._say("")
        self.chat.clear()
        self.pages.setCurrentWidget(self.inroom)

    def leave(self, quiet=False):
        if self.client:
            self.client.leave()
            self.client = None
        self._reset()
        if not quiet:
            self._say(TXT["left"])

    def _on_failed(self, text):
        if self.sender() is not self.client:
            return  # de una conexión anterior
        self.client = None
        self._reset()
        self._say(text)

    def _reset(self):
        self.me = self.host = self.state = None
        self.opening, self.sent = None, None
        if self.rate != 1.0:
            self.watcher.command("rate", 1.0)
        self.rate, self.rate_ok = 1.0, True
        if self.own_transition:
            self.window.set_transition(self.own_transition)
            self.own_transition = None
        for d in (self.requests, self.suggestions):
            for w in d.values():
                w.deleteLater()
            d.clear()
        self.member_list.clear()
        self.pages.setCurrentWidget(self.lobby)

    def is_host(self):
        return bool(self.me) and self.me == self.host

    # --- mensajes del servidor ---
    def _on_message(self, m):
        if self.sender() is not self.client:
            return
        t = m.get("tipo")
        if t == "bienvenida":
            self.me, self.code = m.get("id"), m.get("codigo") or self.code
            self.code_label.setText(self.code)
            self.status.setText("")
            self._members(m)
            if isinstance(m.get("estado"), dict):
                self._on_state(m["estado"])
        elif t == "pendiente":
            self.status.setText(TXT["pending"])
        elif t == "miembros":
            self._members(m)
        elif t == "estado":
            self._on_state(m)
        elif t == "solicitud":
            self._row(self.requests, self.req_box, m.get("id"), f"{room.clean_text(m.get('nombre'), 32)} quiere entrar",
                      (("Aceptar", {"tipo": "aprobar"}), ("Rechazar", {"tipo": "rechazar"})))
        elif t == "solicitud-cancelada":
            w = self.requests.pop(m.get("id"), None)
            w and w.deleteLater()
        elif t == "chat":
            self.chat.appendPlainText(f"{room.clean_text(m.get('nombre'), 32)}: {room.clean_text(m.get('texto'))}")
        elif t == "sugerencia" and isinstance(m.get("sid"), int):
            song = f"{room.clean_text(m.get('titulo'), 200)} — {room.clean_text(m.get('artista'), 200)}"
            self._row(self.suggestions, self.sug_box, m["sid"], f"{room.clean_text(m.get('nombre'), 32)} sugiere {song}",
                      (("Poner", {"tipo": "aprobar-sugerencia"}),), key="sid")
        elif t == "sugerencia-aprobada":
            w = self.suggestions.pop(m.get("sid"), None)
            w and w.deleteLater()
            if self.is_host():
                st = {"titulo": room.clean_text(m.get("titulo"), 200), "artista": room.clean_text(m.get("artista"), 200),
                      "duracion": 0, "videoId": m.get("videoId")}
                self._open_song(st, guest=False)
        elif t == "error":
            log.info("Sala: error %s (%s)", m.get("codigo"), m.get("ref"))

    def _members(self, m):
        self.host = m.get("anfitrion")
        self.members = [x for x in m.get("miembros") or [] if isinstance(x, dict)]
        self.member_list.clear()
        for x in self.members:
            name = room.clean_text(x.get("nombre"), 32)
            mark = " ★" if x.get("id") == self.host else ""
            off = "" if x.get("conectado", True) else " (sin conexión)"
            self.member_list.addItem(f"{name}{mark}{off}{' · tú' if x.get('id') == self.me else ''}")
        self.suggest.setVisible(not self.is_host())
        if self.is_host():
            self.state = None  # el anfitrión manda; no sigue a nadie
            if self.own_transition:
                self.window.set_transition(self.own_transition)
                self.own_transition = None

    def _row(self, store, box, ident, text, actions, key="id"):
        if ident is None or ident in store:
            return
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(_label(text), 1)
        for name, msg in actions:
            h.addWidget(_button(name, lambda _=False, msg=msg: self._act(store, ident, {**msg, key: ident})))
        box.addWidget(w)
        store[ident] = w

    def _act(self, store, ident, msg):
        if self.client:
            self.client.send(msg)
        w = store.pop(ident, None)
        w and w.deleteLater()

    def _send_chat(self):
        text = room.clean_text(self.chat_edit.text())
        if text and self.client:
            self.client.send({"tipo": "chat", "texto": text})
        self.chat_edit.clear()

    def _suggest(self):
        title, _, artist = room.clean_text(self.suggest.text(), 200).partition(" - ")
        if title.strip() and self.client:
            self.client.send({"tipo": "sugerir", "titulo": title.strip(), "artista": artist.strip()})
            self._say("Sugerencia enviada.")
        self.suggest.clear()

    # --- sincronía ---
    def on_snapshot(self, snap):
        """Cada foto de SMTC (0,5 s): el anfitrión publica; el invitado se ajusta."""
        if not self.client or not self.me:
            return
        if self.is_host():
            self._publish(snap)
        else:
            self._sync(snap)

    def _publish(self, snap):
        if not snap or snap["ad"]:
            return
        now, pos = time.monotonic(), media.position(snap)
        key = (snap["title"], snap["artist"], snap["playing"])
        if self.sent:  # ¿saltó la barra?
            k, p, t = self.sent
            jumped = abs(pos - (p + (now - t if k[2] else 0))) > 1.5
        if self.sent is None or key != self.sent[0] or jumped or now - self.sent_t >= 5.0:
            seed = zlib.crc32(f"{snap['title']}|{snap['artist']}".encode())
            self.client.send(room.host_state(snap, pos, self.window.trans_setting, seed))
            self.sent, self.sent_t = (key, pos, now), now

    def _on_state(self, st):
        need = ("titulo", "artista", "duracion", "posicion", "reproduciendo", "ts")
        if self.is_host() or not all(k in st for k in need):
            return
        self.state = st
        tr = st.get("transicion")
        if tr in transitions.SETTINGS and tr != self.window.trans_setting:
            self.own_transition = self.own_transition or self.window.trans_setting
            self.window.set_transition(tr)
        self._sync(self.window.snap)

    def _matches(self, snap, st):
        if room.same_song(snap["title"], snap["artist"], snap["duration"], st["titulo"], st["artista"], st["duracion"]):
            return True
        # la que abrimos nosotros con otro título (otra versión): vale si la duración encaja
        return (self.opening == (st["titulo"], st["artista"]) and (snap["title"], snap["artist"]) != self.before_open
                and abs((snap["duration"] or 0) - st["duracion"]) <= room.SAME_DUR)

    def _sync(self, snap):
        st = self.state
        if not st or (snap and snap["ad"]):  # en un anuncio no se toca nada
            return
        now = time.monotonic()
        if not snap or not self._matches(snap, st):
            if self.opening != (st["titulo"], st["artista"]):
                self._open_song(st, guest=True)
            return
        target = room.host_position(st, time.time() * 1000 + self.offset)
        if st["reproduciendo"] != snap["playing"]:
            if now - self.cmd_t >= CMD_GAP:
                self.cmd_t = now
                self.watcher.command("play" if st["reproduciendo"] else "pause")
            return
        if not snap["playing"]:
            return
        act, rate = room.sync_action(media.position(snap) - target, self.rate != 1.0, self.rate_ok)
        if act == "seek":
            if now - self.cmd_t >= CMD_GAP:
                self.cmd_t = now
                rate = 1.0
                self.watcher.command("seek", target + SEEK_LEAD)
            else:
                return
        if rate != self.rate:
            self.rate = rate
            self.watcher.command("rate", rate)

    def _open_song(self, st, guest):
        song_key = (st["titulo"], st["artista"])
        self.opening = song_key
        snap = self.window.snap
        self.before_open = (snap["title"], snap["artist"]) if snap else None
        if guest and snap and snap["playing"]:
            self.watcher.command("pause")
        vid = st.get("videoId")
        if vid:
            return self._on_found(song_key, (vid, st["titulo"], st["artista"], st["duracion"]), guest)

        def work():
            try:
                res = ytsearch.find(st["titulo"], st["artista"], st["duracion"])
            except Exception as e:
                log.info("Búsqueda en YouTube Music fallida: %s", e)
                res = None
            self.found.emit(song_key, res, guest)

        threading.Thread(target=work, daemon=True).start()

    def _on_found(self, song_key, res, guest):
        if song_key != self.opening:
            return
        name = room.clean_text(song_key[0], 80)
        if not res:
            return self._say(TXT["not-found"].format(song=name))
        text = TXT["opening"].format(song=name)
        if not room.same_song(res[1], res[2], 0, *song_key, 0):
            text += " " + TXT["other-version"]
        self._say(text)
        QDesktopServices.openUrl(QUrl(f"https://music.youtube.com/watch?v={res[0]}"))

    def _on_command_failed(self, kind, value):
        if not self.client:
            return
        if kind == "rate":
            self.rate_ok, self.rate = False, 1.0  # el navegador no cambia la velocidad: solo saltos
        elif kind == "seek":
            self._say(TXT["seek-failed"].format(mss=room.fmt_mss(value)))
