# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app's windows: starting a pairing, listing pairings, and settings."""
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QInputDialog)

from . import autostart
from .config import Pairing, Settings
from .crypto import normalize_relay
from .graphics import STATUS_COLORS, qr_pixmap
from .pairing import PairingSession

STATUS_TEXT = {
    "connected": "Connected",
    "connecting": "Connecting…",
    "reconnecting": "Reconnecting…",
    "offline": "Offline",
    "unpaired": "Unpaired",
}


def ago(timestamp: int) -> str:
    if not timestamp:
        return "unknown"
    return time.strftime("%d %b %Y", time.localtime(timestamp))


class NewPairingDialog(QDialog):
    """Asks which relay to pair through and what to call this computer."""

    def __init__(self, relay: str, name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair with a phone")
        self.relay_edit = QLineEdit(relay or "https://")
        self.relay_edit.setPlaceholderText("https://otp.example.com")
        self.name_edit = QLineEdit(name)
        self.name_edit.setMaxLength(64)

        form = QFormLayout()
        form.addRow("Relay address:", self.relay_edit)
        form.addRow("This computer:", self.name_edit)

        hint = QLabel("Your phone will show this name in its list of receivers.")
        hint.setWordWrap(True)
        hint.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.relay_edit.setFocus()

    def _accept(self):
        try:
            self.relay = normalize_relay(self.relay_edit.text())
        except ValueError as e:
            QMessageBox.warning(self, "Check the relay address", str(e))
            return
        self.name = self.name_edit.text().strip() or "Desktop"
        self.accept()


class PairingDialog(QDialog):
    """Shows the QR code and waits for the phone to scan it."""

    def __init__(self, session: PairingSession, parent=None):
        super().__init__(parent)
        self.session = session
        self.result_pairing: Pairing | None = None
        self.setWindowTitle("Scan this with your phone")

        qr = QLabel()
        qr.setPixmap(qr_pixmap(session.uri))
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr.setFrameShape(QFrame.Shape.StyledPanel)

        intro = QLabel("In the otprelay app on your phone, choose <b>Add receiver</b> "
                       "and scan this code.")
        intro.setWordWrap(True)

        self.countdown = QLabel()
        self.countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.link = QLineEdit(session.uri)
        self.link.setReadOnly(True)
        self.link.setVisible(False)
        copy_button = QPushButton("Copy link instead")
        copy_button.clicked.connect(self._copy_link)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(copy_button)
        row.addStretch(1)
        row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(qr)
        layout.addWidget(self.countdown)
        layout.addWidget(self.link)
        layout.addLayout(row)

        session.tick.connect(self._on_tick)
        session.paired.connect(self._on_paired)
        session.failed.connect(self._on_failed)
        session.start()

    def _copy_link(self):
        QGuiApplication.clipboard().setText(self.session.uri)
        self.link.setVisible(True)
        self.link.selectAll()

    def _on_tick(self, remaining: int):
        self.countdown.setText(f"This code expires in {remaining // 60}:{remaining % 60:02d}")

    def _on_paired(self, pairing: Pairing):
        self.result_pairing = pairing
        self.accept()

    def _on_failed(self, message: str):
        QMessageBox.warning(self, "Pairing did not finish", message)
        self.reject()

    def reject(self):
        self.session.stop()
        super().reject()


class PairingsWindow(QWidget):
    """Lists every phone this computer receives codes from."""

    add_requested = Signal()
    remove_requested = Signal(str)  # receiver_id
    rename_requested = Signal(str, str)  # receiver_id, new name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("otprelay receivers")
        self.resize(620, 260)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Phone", "Relay", "Paired", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self.empty_note = QLabel("No phones are paired yet. Choose <b>Add a phone</b> to start.")
        self.empty_note.setWordWrap(True)

        add_button = QPushButton("Add a phone…")
        add_button.clicked.connect(self.add_requested)
        self.remove_button = QPushButton("Unpair")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove_selected)
        self.rename_button = QPushButton("Rename…")
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self._rename_selected)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        self.table.itemSelectionChanged.connect(self._on_selection)

        row = QHBoxLayout()
        row.addWidget(add_button)
        row.addWidget(self.remove_button)
        row.addWidget(self.rename_button)
        row.addStretch(1)
        row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.empty_note)
        layout.addWidget(self.table)
        layout.addLayout(row)

    def refresh(self, pairings: list[Pairing], statuses: dict[str, str]):
        self.empty_note.setVisible(not pairings)
        self.table.setVisible(bool(pairings))
        self.table.setRowCount(len(pairings))
        for row, pairing in enumerate(pairings):
            status = statuses.get(pairing.receiver_id, "offline")
            cells = [pairing.source_name, pairing.relay, ago(pairing.paired_at),
                     STATUS_TEXT.get(status, status)]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, pairing.receiver_id)
                if column == 3:
                    item.setForeground(Qt.GlobalColor.gray if status == "offline"
                                       else Qt.GlobalColor.black)
                    item.setToolTip(STATUS_COLORS.get(status, ""))
                self.table.setItem(row, column, item)

    def _on_selection(self):
        has = bool(self.table.selectedItems())
        self.remove_button.setEnabled(has)
        self.rename_button.setEnabled(has)

    def _remove_selected(self):
        rows = {item.row() for item in self.table.selectedItems()}
        if not rows:
            return
        item = self.table.item(sorted(rows)[0], 0)
        receiver_id = item.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self, "Unpair this phone?",
            f"Stop receiving codes from {item.text()}?\n\n"
            "This computer will no longer be able to read its codes. "
            "You can pair again at any time.")
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_requested.emit(receiver_id)

    def _rename_selected(self):
        rows = {item.row() for item in self.table.selectedItems()}
        if not rows:
            return
        item = self.table.item(sorted(rows)[0], 0)
        name, ok = QInputDialog.getText(self, "Rename phone",
                                        "Name for this phone:",
                                        text=item.text())
        name = name.strip()[:64]
        if ok and name:
            self.rename_requested.emit(item.data(Qt.ItemDataRole.UserRole),
                                       name)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("otprelay settings")
        self.name_edit = QLineEdit(settings.name)
        self.name_edit.setMaxLength(64)
        self.notify_box = QCheckBox("Show a notification when a code is copied")
        self.notify_box.setChecked(settings.notifications)
        self.autostart_box = QCheckBox("Start automatically at login")
        self.autostart_box.setChecked(autostart.is_enabled())

        form = QFormLayout()
        form.addRow("This computer:", self.name_edit)

        note = QLabel("The name is only used for new pairings. Phones keep the name they "
                      "already have for this computer.")
        note.setWordWrap(True)
        note.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.notify_box)
        layout.addWidget(self.autostart_box)
        layout.addWidget(buttons)

    def apply_to(self, settings: Settings):
        settings.name = self.name_edit.text().strip() or settings.name
        settings.notifications = self.notify_box.isChecked()
        try:
            autostart.set_enabled(self.autostart_box.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "Could not change startup setting", str(e))
