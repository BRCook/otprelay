# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings and pairings, stored as JSON readable only by the current user."""
import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "otprelay"


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


@dataclass
class Pairing:
    receiver_id: str
    receiver_token: str
    relay: str
    private_key: str          # base64 X25519 private key; never leaves this file
    source_name: str = "Phone"
    paired_at: int = 0


@dataclass
class Settings:
    name: str = field(default_factory=lambda: socket.gethostname()[:64] or "Desktop")
    notifications: bool = True
    last_relay: str = ""


def _from_dict(cls, data):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "receiver.json"
        self.settings = Settings()
        self.pairings: list[Pairing] = []
        self.load()

    def load(self):
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.settings = _from_dict(Settings, data.get("settings", {}))
        self.pairings = [_from_dict(Pairing, p) for p in data.get("pairings", [])]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.path.parent, 0o700)
        data = {"settings": asdict(self.settings), "pairings": [asdict(p) for p in self.pairings]}
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def add(self, pairing: Pairing):
        self.pairings.append(pairing)
        self.save()

    def remove(self, pairing: Pairing):
        self.pairings = [p for p in self.pairings if p.receiver_id != pairing.receiver_id]
        self.save()
