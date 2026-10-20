# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copying to the clipboard from a background app.

On Wayland, apps without keyboard focus may not set the clipboard directly,
so on KDE we ask Klipper over D-Bus, elsewhere on Wayland we use wl-copy,
and on Windows, macOS and X11 we use Qt's own clipboard.
"""
import logging
import os
import shutil
import subprocess
import sys

from PySide6.QtGui import QGuiApplication

log = logging.getLogger(__name__)


def _klipper(text: str) -> bool:
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    except ImportError:
        return False
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    iface = QDBusInterface("org.kde.klipper", "/klipper", "org.kde.klipper.klipper", bus)
    if not iface.isValid():
        return False
    reply = iface.call("setClipboardContents", text)
    return reply.type() != QDBusMessage.MessageType.ErrorMessage


def _wl_copy(text: str) -> bool:
    if not os.environ.get("WAYLAND_DISPLAY") or not shutil.which("wl-copy"):
        return False
    try:
        subprocess.run(["wl-copy"], input=text.encode(), check=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def copy(text: str) -> str:
    """Copy text and return the name of the method that worked."""
    if sys.platform.startswith("linux"):
        if _klipper(text):
            return "klipper"
        if _wl_copy(text):
            return "wl-copy"
    QGuiApplication.clipboard().setText(text)
    return "qt"
