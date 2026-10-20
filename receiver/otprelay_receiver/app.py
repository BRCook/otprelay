# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tray application: keeps every pairing connected and copies codes."""
import logging
import sys

from PySide6.QtCore import QObject, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import clipboard
from .config import Pairing, Store
from .graphics import tray_icon
from .pairing import PairingSession
from .relay import Connection, Http
from .windows import NewPairingDialog, PairingDialog, PairingsWindow, SettingsDialog

log = logging.getLogger(__name__)

SOCKET_NAME = "otprelay-receiver"
STATUS_LABEL = {
    "connected": "Connected",
    "connecting": "Connecting…",
    "reconnecting": "Reconnecting…",
    "offline": "Offline",
    "unpaired": "Unpaired",
}


class ReceiverApp(QObject):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.http = Http(self)
        self.connections: dict[str, object] = {}

        self.pairings_window = PairingsWindow()
        self.pairings_window.add_requested.connect(self.start_pairing)
        self.pairings_window.remove_requested.connect(self.unpair)

        self.tray = QSystemTrayIcon(tray_icon("none"), self)
        self.tray.setToolTip("otprelay receiver")
        self.tray.activated.connect(self._on_activated)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.show()

        for pairing in self.store.pairings:
            self._connect(pairing)
        self._refresh()

        if not self.store.pairings:
            self.show_pairings()

    # --- connections --------------------------------------------------------

    def _connect(self, pairing: Pairing):
        connection = Connection(pairing, self)
        connection.status_changed.connect(self._refresh)
        connection.code_received.connect(self._on_code)
        connection.unpaired.connect(self._on_remote_unpair)
        self.connections[pairing.receiver_id] = connection
        connection.start()

    def _statuses(self) -> dict[str, str]:
        return {receiver_id: getattr(c, "status", "offline")
                for receiver_id, c in self.connections.items()}

    def _overall_status(self) -> str:
        statuses = list(self._statuses().values())
        if not statuses:
            return "none"
        if any(s == "connected" for s in statuses):
            return "connected" if all(s == "connected" for s in statuses) else "connecting"
        return "reconnecting" if any(s == "reconnecting" for s in statuses) else "offline"

    # --- tray ---------------------------------------------------------------

    def _refresh(self):
        status = self._overall_status()
        self.tray.setIcon(tray_icon(status))
        statuses = self._statuses()
        self.menu.clear()

        if not self.store.pairings:
            action = self.menu.addAction("No phones paired")
            action.setEnabled(False)
        for pairing in self.store.pairings:
            label = STATUS_LABEL.get(statuses.get(pairing.receiver_id, "offline"), "Offline")
            action = self.menu.addAction(f"{pairing.source_name} — {label}")
            action.setEnabled(False)

        self.menu.addSeparator()
        self.menu.addAction("Add a phone…", self.start_pairing)
        self.menu.addAction("Receivers…", self.show_pairings)
        self.menu.addAction("Settings…", self.show_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit", QApplication.quit)

        connected = sum(1 for s in statuses.values() if s == "connected")
        self.tray.setToolTip(f"otprelay receiver — {connected} of {len(statuses)} phones connected"
                             if statuses else "otprelay receiver — no phones paired")
        if self.pairings_window.isVisible():
            self.pairings_window.refresh(self.store.pairings, statuses)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_pairings()

    def _notify(self, title: str, message: str):
        if self.store.settings.notifications:
            self.tray.showMessage(title, message, tray_icon("connected"), 5000)

    # --- actions ------------------------------------------------------------

    def show_pairings(self):
        self.pairings_window.refresh(self.store.pairings, self._statuses())
        self.pairings_window.show()
        self.pairings_window.raise_()
        self.pairings_window.activateWindow()

    def show_settings(self):
        dialog = SettingsDialog(self.store.settings)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            dialog.apply_to(self.store.settings)
            self.store.save()

    def start_pairing(self):
        first = NewPairingDialog(self.store.settings.last_relay or "",
                                 self.store.settings.name)
        if first.exec() != NewPairingDialog.DialogCode.Accepted:
            return
        self.store.settings.last_relay = first.relay
        self.store.settings.name = first.name
        self.store.save()

        session = PairingSession(first.relay, first.name, self.http, self)
        dialog = PairingDialog(session)
        if dialog.exec() == PairingDialog.DialogCode.Accepted and dialog.result_pairing:
            pairing = dialog.result_pairing
            self.store.add(pairing)
            self._connect(pairing)
            self._refresh()
            self._notify("Paired", f"This computer now receives codes from "
                                   f"{pairing.source_name}.")

    def unpair(self, receiver_id: str):
        pairing = next((p for p in self.store.pairings if p.receiver_id == receiver_id), None)
        if pairing is None:
            return
        # Tell the relay, but drop the pairing locally whatever it answers:
        # without the private key this computer can no longer read the codes.
        self.http.call("DELETE", f"{pairing.relay}/api/receiver", token=pairing.receiver_token,
                       callback=lambda status, data, error: None)
        self._drop(pairing)
        self._notify("Unpaired", f"Codes from {pairing.source_name} will no longer arrive here.")

    def _drop(self, pairing: Pairing):
        connection = self.connections.pop(pairing.receiver_id, None)
        if connection is not None:
            connection.stop()
            connection.deleteLater()
        self.store.remove(pairing)
        self._refresh()

    def _on_remote_unpair(self, pairing: Pairing, reason: str):
        log.info("relay says %s was unpaired: %s", pairing.receiver_id, reason)
        self._drop(pairing)
        self._notify("Unpaired from the phone",
                     f"{pairing.source_name} stopped sending codes to this computer.")

    def _on_code(self, pairing: Pairing, data: dict):
        code = data["code"]
        try:
            method = clipboard.copy(code)
        except Exception as e:  # a failed copy must not take the app down
            log.error("could not copy a code: %s", e)
            self._notify("Could not copy the code", str(e))
            return
        log.info("copied a code from %s via %s", pairing.source_name, method)
        source = data.get("app") or pairing.source_name
        self._notify("Code copied", f"{code} — from {source}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("otprelay receiver")
    app.setDesktopFileName("otprelay-receiver")
    app.setQuitOnLastWindowClosed(False)

    # One instance per user: a second copy would fight over the same pairings.
    probe = QLocalSocket()
    probe.connectToServer(SOCKET_NAME)
    if probe.waitForConnected(300):
        print("otprelay receiver is already running.", file=sys.stderr)
        return 0
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer()
    server.listen(SOCKET_NAME)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "No system tray",
                             "otprelay receiver needs a system tray to run.")
        return 1

    try:
        store = Store()
    except (OSError, ValueError) as e:
        QMessageBox.critical(None, "Could not read settings", str(e))
        return 1

    receiver = ReceiverApp(store, app)
    app.setProperty("receiver", receiver)  # keep a reference alive
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
