# SPDX-License-Identifier: AGPL-3.0-or-later
"""Talking to the relay: plain HTTP calls and one persistent WebSocket per pairing."""
import json
import logging
import time

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from .config import Pairing
from .crypto import decrypt

log = logging.getLogger(__name__)

CLOSE_REPLACED = 4000   # another connection for this receiver took over
CLOSE_UNPAIRED = 4001   # the receiver was unpaired
MAX_CODE_AGE = 300      # seconds; older codes are ignored
PING_INTERVAL = 30_000  # ms
PONG_TIMEOUT = 15_000   # ms


class Http(QObject):
    """Minimal JSON-over-HTTP helper. Callbacks get (status, data, error_text)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)

    def call(self, method, url, body=None, token=None, callback=None):
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(15_000)
        if token:
            request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
        payload = QByteArray(json.dumps(body).encode() if body is not None else b"")
        reply = self.nam.sendCustomRequest(request, QByteArray(method.encode()), payload)

        def finished():
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
            raw = bytes(reply.readAll().data())
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {}
            error = ""
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error = data.get("error") if isinstance(data, dict) and data.get("error") \
                    else reply.errorString()
            reply.deleteLater()
            if callback:
                callback(int(status), data, error)

        reply.finished.connect(finished)


def ws_url(relay: str) -> str:
    if relay.startswith("https://"):
        return "wss://" + relay[len("https://"):] + "/api/ws"
    return "ws://" + relay[len("http://"):] + "/api/ws"


class Connection(QObject):
    """Keeps one pairing connected, decrypts codes, and reconnects with backoff."""

    status_changed = Signal()
    code_received = Signal(object, dict)  # (Pairing, plaintext)
    unpaired = Signal(object, str)        # (Pairing, reason)

    def __init__(self, pairing: Pairing, parent=None):
        super().__init__(parent)
        self.pairing = pairing
        self.status = "connecting"
        self.stopped = False
        self.backoff = 1
        self.seen: dict[str, float] = {}

        self.ws = QWebSocket(parent=self)
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_text)
        self.ws.pong.connect(lambda *_: self.pong_deadline.stop())

        self.retry_timer = QTimer(self, singleShot=True)
        self.retry_timer.timeout.connect(self._open)
        self.ping_timer = QTimer(self, interval=PING_INTERVAL)
        self.ping_timer.timeout.connect(self._ping)
        self.pong_deadline = QTimer(self, singleShot=True, interval=PONG_TIMEOUT)
        self.pong_deadline.timeout.connect(self.ws.abort)

    def start(self):
        self.stopped = False
        self._open()

    def stop(self):
        self.stopped = True
        for timer in (self.retry_timer, self.ping_timer, self.pong_deadline):
            timer.stop()
        self.ws.close()

    def _set_status(self, status):
        if status != self.status:
            self.status = status
            self.status_changed.emit()

    def _open(self):
        if self.stopped:
            return
        request = QNetworkRequest(QUrl(ws_url(self.pairing.relay)))
        request.setRawHeader(b"Authorization", f"Bearer {self.pairing.receiver_token}".encode())
        self.ws.open(request)

    def _ping(self):
        # A text message, not just a protocol ping: reverse proxies often count
        # only real data frames as activity and close an otherwise idle
        # connection. The relay answers with {"type": "pong"}.
        self.ws.sendTextMessage(json.dumps({"type": "ping"}))
        self.ws.ping()
        self.pong_deadline.start()

    def _on_connected(self):
        self.backoff = 1
        self.ping_timer.start()
        self._set_status("connected")
        log.info("connected to %s for %s", self.pairing.relay, self.pairing.source_name)

    def _on_disconnected(self):
        self.ping_timer.stop()
        self.pong_deadline.stop()
        if self.stopped:
            return
        code = self.ws.closeCode()
        code = int(getattr(code, "value", code))
        reason = self.ws.closeReason()
        if code == CLOSE_UNPAIRED:
            self.stopped = True
            self._set_status("unpaired")
            self.unpaired.emit(self.pairing, reason or "unpaired")
            return
        delay = 60 if code == CLOSE_REPLACED else self.backoff
        self.backoff = min(self.backoff * 2, 60)
        self._set_status("reconnecting")
        log.info("disconnected from %s (code %s); retrying in %ss", self.pairing.relay, code, delay)
        self.retry_timer.start(delay * 1000)

    def _on_text(self, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "pong":
            self.pong_deadline.stop()
            return
        if msg.get("type") != "code":
            return
        msg_id = str(msg.get("id", ""))
        now = time.time()
        if msg_id and msg_id not in self.seen:
            self.seen[msg_id] = now
            self._handle(msg)
        self.seen = {k: t for k, t in self.seen.items() if now - t < 600}
        self.ws.sendTextMessage(json.dumps({"type": "ack", "id": msg_id}))

    def _handle(self, msg):
        try:
            data = decrypt(self.pairing.private_key, str(msg.get("ciphertext", "")))
        except ValueError as e:
            log.warning("ignored a message from %s: %s", self.pairing.source_name, e)
            return
        if abs(time.time() - data["ts"]) > MAX_CODE_AGE:
            log.info("ignored a stale code from %s", self.pairing.source_name)
            return
        self.code_received.emit(self.pairing, data)
