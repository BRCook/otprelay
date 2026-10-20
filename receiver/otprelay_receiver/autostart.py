# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start at login: an XDG autostart entry on Linux, a Run key on Windows."""
import os
import shlex
import sys
from pathlib import Path

APP_ID = "otprelay-receiver"


def launch_command() -> list[str]:
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        exe = exe.with_name("pythonw.exe")  # no console window at login
    return [str(exe), "-m", "otprelay_receiver"]


def _desktop_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / f"{APP_ID}.desktop"


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_ID)
            return True
        except OSError:
            return False
    return _desktop_file().exists()


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_enabled(enabled: bool):
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                command = " ".join(f'"{part}"' for part in launch_command())
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_ID)
                except OSError:
                    pass
        return
    path = _desktop_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(shlex.quote(part) for part in launch_command())
        path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=otprelay receiver\n"
            "Comment=Copies one-time codes from your phones to the clipboard\n"
            f"Exec={command}\n"
            "Icon=dialog-password\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8")
    elif path.exists():
        path.unlink()
