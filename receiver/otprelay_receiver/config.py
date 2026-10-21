# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings and pairings, stored as JSON readable only by the current user."""
import json
import os
import socket
import sys
import keyring
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE = "otprelay-receiver"
SECRET_FIELDS = ("receiver_token", "private_key", "auth_key")

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
    auth_key: str = ""  # base64 HMAC key derived from the pairing secret; empty on old pairings
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
        self.pairings = [_from_dict(Pairing, p) for p in
                         data.get("pairings", [])]
        migrated = False
        for p in self.pairings:
            if p.private_key:  # secrets still in the file: move them to the keyring
                self._store_secrets(p)
                migrated = True
            else:
                self._load_secrets(p)
        if migrated:
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.path.parent, 0o700)
        pairings = []
        for p in self.pairings:
            self._store_secrets(p)
            d = asdict(p)
            for f in SECRET_FIELDS:
                d[f] = ""
            pairings.append(d)
        data = {"settings": asdict(self.settings), "pairings": pairings}
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def remove(self, pairing: Pairing):
        self.pairings = [p for p in self.pairings if
                         p.receiver_id != pairing.receiver_id]
        try:
            keyring.delete_password(SERVICE, pairing.receiver_id)
        except Exception as e:
            log.warning("could not remove keyring entry: %s", e)
        self.save()

    def _store_secrets(self, p: Pairing):
        keyring.set_password(SERVICE, p.receiver_id,
                             json.dumps(
                                 {f: getattr(p, f) for f in SECRET_FIELDS}))

    def _load_secrets(self, p: Pairing):
        raw = keyring.get_password(SERVICE, p.receiver_id)
        if not raw:
            raise OSError(
                f"the keyring holds no secrets for pairing {p.receiver_id}; "
                "unpair it and pair again")
        for f, v in json.loads(raw).items():
            setattr(p, f, v)

    def add(self, pairing: Pairing):
        self.pairings.append(pairing)
        self.save()
