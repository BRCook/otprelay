# SPDX-License-Identifier: AGPL-3.0-or-later
"""Key generation, pairing links, and decryption (see docs/PROTOCOL.md)."""
import base64
import json
import secrets
from urllib.parse import quote, urlencode

from nacl.public import PrivateKey, SealedBox

MAX_CODE_LENGTH = 32


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_keypair() -> tuple[str, str]:
    """Return (private key as base64, public key as unpadded base64url)."""
    key = PrivateKey.generate()
    return base64.b64encode(bytes(key)).decode(), b64url(bytes(key.public_key))


def new_pairing_secret() -> str:
    return b64url(secrets.token_bytes(32))


def normalize_relay(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise ValueError("The relay address must start with https:// or http://")
    return url


def pairing_uri(relay: str, public_key: str, secret: str, name: str, platform: str) -> str:
    query = urlencode({"v": "1", "u": relay, "k": public_key, "s": secret,
                       "n": name[:64], "p": platform}, quote_via=quote)
    return "otprelay://pair?" + query


def decrypt(private_key_b64: str, ciphertext_b64: str) -> dict:
    """Open a sealed box and validate the plaintext. Raises ValueError on any problem."""
    try:
        box = SealedBox(PrivateKey(base64.b64decode(private_key_b64)))
        data = json.loads(box.decrypt(base64.b64decode(ciphertext_b64)).decode("utf-8"))
    except Exception as e:
        raise ValueError(f"could not decrypt message: {e}") from None
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("unsupported message format")
    code = data.get("code")
    if not isinstance(code, str) or not 0 < len(code) <= MAX_CODE_LENGTH:
        raise ValueError("message has no valid code")
    if not isinstance(data.get("ts"), int):
        raise ValueError("message has no timestamp")
    return data
