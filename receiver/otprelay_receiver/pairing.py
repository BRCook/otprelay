# SPDX-License-Identifier: AGPL-3.0-or-later
"""One attempt at pairing: show a link, wait for a phone to scan it."""
import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .config import Pairing, platform_name
from .crypto import new_keypair, new_pairing_secret, pairing_uri
from .relay import Http

log = logging.getLogger(__name__)

POLL_INTERVAL = 2_000  # ms
WINDOW = 600           # seconds the relay keeps a pairing open


class PairingSession(QObject):
    """Generates a key pair and pairing link, then polls the relay until a
    source registers this receiver (or the window closes)."""

    paired = Signal(object)    # Pairing
    failed = Signal(str)
    tick = Signal(int)         # seconds remaining

    def __init__(self, relay: str, name: str, http: Http, parent=None):
        super().__init__(parent)
        self.relay = relay
        self.name = name
        self.http = http
        self.private_key, public_key = new_keypair()
        self.secret = new_pairing_secret()
        self.uri = pairing_uri(relay, public_key, self.secret, name, platform_name())
        self.deadline = time.time() + WINDOW
        self.in_flight = False
        self.finished = False

        self.poll_timer = QTimer(self, interval=POLL_INTERVAL)
        self.poll_timer.timeout.connect(self._poll)
        self.countdown = QTimer(self, interval=1_000)
        self.countdown.timeout.connect(self._tick)

    def start(self):
        self.poll_timer.start()
        self.countdown.start()
        self._tick()

    def stop(self):
        self.finished = True
        self.poll_timer.stop()
        self.countdown.stop()

    def _tick(self):
        remaining = int(self.deadline - time.time())
        self.tick.emit(max(0, remaining))
        if remaining <= 0 and not self.finished:
            self.stop()
            self.failed.emit("The pairing code expired before a phone scanned it.")

    def _poll(self):
        if self.in_flight or self.finished:
            return
        self.in_flight = True

        def done(status, data, error):
            self.in_flight = False
            if self.finished:
                return
            if status == 404:
                return  # no phone has scanned it yet
            if status != 200 or not isinstance(data, dict):
                # Keep polling through transient network trouble; the countdown
                # ends the attempt if it never recovers.
                log.debug("claim attempt failed: %s %s", status, error)
                return
            try:
                pairing = Pairing(
                    receiver_id=str(data["receiver_id"]),
                    receiver_token=str(data["receiver_token"]),
                    relay=self.relay,
                    private_key=self.private_key,
                    source_name=str(data.get("source_name") or "Phone")[:64],
                    paired_at=int(time.time()))
            except (KeyError, TypeError, ValueError):
                self.stop()
                self.failed.emit("The relay sent a reply this app did not understand.")
                return
            self.stop()
            self.paired.emit(pairing)

        self.http.call("POST", f"{self.relay}/api/pair/claim",
                       {"pairing_secret": self.secret}, callback=done)
