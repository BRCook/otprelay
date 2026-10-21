# SPDX-License-Identifier: AGPL-3.0-or-later
"""Key generation, pairing links, and decryption (see docs/PROTOCOL.md)."""
import base64
import json
import secrets
import hmac
import hashlib
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


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def derive_claim(secret: str) -> str:
    """What both sides hand the relay; the relay never sees the secret itself."""
    return b64url(hmac.new(_b64url_decode(secret), b"otprelay claim v1",
                           hashlib.sha256).digest())


def derive_auth_key(secret: str) -> str:
    """Base64 MAC key shared by phone and receiver, never sent anywhere."""
    return base64.b64encode(
        hmac.new(_b64url_decode(secret), b"otprelay auth v1",
                 hashlib.sha256).digest()).decode()


def verify_mac(auth_key_b64: str, ciphertext_b64: str, mac_b64: str) -> bool:
    expected = hmac.new(base64.b64decode(auth_key_b64), ciphertext_b64.encode(),
                        hashlib.sha256).digest()
    try:
        return hmac.compare_digest(expected, base64.b64decode(mac_b64))
    except Exception:
        return False


def normalize_relay(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError(
            "The relay address must start with https://. Over plain http anyone "
            "on the network could read this computer's relay token.")
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
