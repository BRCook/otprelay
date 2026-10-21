#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stand-in for the phone, for testing a relay and receivers from a terminal.

It implements the source side of docs/PROTOCOL.md, so it also serves as a
reference while the Android app is written. It is a development tool: it keeps
its token in a plain file and is not meant for real use.

    dev-source.py register https://otp.example.com a1b2-c3d4-e5f6-a7b8
    dev-source.py add 'otprelay://pair?v=1&u=...'   # paste a receiver's link
    dev-source.py list
    dev-source.py send 482913
    dev-source.py remove <receiver_id>

Requires: pynacl, and requests (or Python 3 with urllib, used here).
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import hashlib
import hmac
from pathlib import Path

from nacl.public import PublicKey, SealedBox

STATE = Path(os.environ.get("DEV_SOURCE_STATE", Path.home() / ".config" / "otprelay"
                            / "dev-source.json"))


def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"relay": "", "token": "", "source_id": "", "receivers": {}}


def save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))
    os.chmod(STATE, 0o600)


def call(state, method, path, body=None, token=True):
    url = state["relay"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token and state.get("token"):
        request.add_header("Authorization", "Bearer " + state["token"])
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")[:200]}
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {url}: {e.reason}")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def derive(secret: str, label: bytes) -> bytes:
    return hmac.new(b64url_decode(secret), label, hashlib.sha256).digest()


def cmd_register(args):
    state = load()
    state["relay"] = args.relay.rstrip("/")
    status, data = call(state, "POST", "/api/register",
                        {"invite": args.invite, "name": args.name}, token=False)
    if status != 200:
        sys.exit(f"Registration failed ({status}): {data.get('error', 'unknown error')}")
    state["token"] = data["source_token"]
    state["source_id"] = data["source_id"]
    state["receivers"] = {}
    save(state)
    print(f"Registered with {state['relay']} as source {state['source_id']}.")


def cmd_add(args):
    state = load()
    if not state.get("token"):
        sys.exit("Register first.")
    parsed = urllib.parse.urlparse(args.link)
    if parsed.scheme != "otprelay" or parsed.netloc != "pair":
        sys.exit("That does not look like an otprelay://pair link.")
    q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    if q.get("v") != "1":
        sys.exit(f"Unsupported pairing link version: {q.get('v')}")
    if q.get("u", "").rstrip("/") != state["relay"]:
        sys.exit(f"That receiver uses relay {q.get('u')}, but this source is "
                 f"registered with {state['relay']}.")
    key = b64url_decode(q.get("k", ""))
    if len(key) != 32:
        sys.exit("The link does not carry a valid public key.")
    secret = q.get("s", "")
    claim = base64.urlsafe_b64encode(
        derive(secret, b"otprelay claim v1")).rstrip(b"=").decode()
    status, data = call(state, "POST", "/api/receivers",
                        {"pairing_secret": claim,
                         "name": q.get("n", "Receiver"),
                         "platform": q.get("p", "unknown")})
    if status != 200:
        sys.exit(f"Could not add the receiver ({status}): {data.get('error', '')}")
    receiver_id = data["receiver_id"]
    state["receivers"][receiver_id] = {"name": q.get("n", "Receiver"),
                                       "platform": q.get("p", "unknown"),
                                       "public_key": base64.b64encode(key).decode(),
                                       "auth_key": base64.b64encode(
                                           derive(secret,
                                                  b"otprelay auth v1")).decode()}
    save(state)
    print(f"Added receiver {receiver_id} ({q.get('n')}).")


def cmd_list(args):
    state = load()
    status, data = call(state, "GET", "/api/receivers")
    if status != 200:
        sys.exit(f"Could not list receivers ({status}): {data.get('error', '')}")
    remote = data.get("receivers", [])
    known = state.get("receivers", {})
    # The trust rule: forget receivers the relay no longer lists, but never
    # add one the relay reports that we did not pair with ourselves.
    for receiver_id in list(known):
        if not any(r["id"] == receiver_id for r in remote):
            print(f"  (forgetting {receiver_id}: the relay no longer lists it)")
            del known[receiver_id]
    save(state)
    if not remote:
        print("No receivers.")
    for r in remote:
        mark = "" if r["id"] in known else "  [no key stored; needs re-pairing]"
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"])) \
            if r.get("last_seen") else "never"
        print(f"{r['id']}  {r['name']:<20} {r['platform']:<8} "
              f"{'online' if r.get('online') else 'offline':<8} last seen: {seen}{mark}")


def cmd_send(args):
    state = load()
    known = state.get("receivers", {})
    if not known:
        sys.exit("No receivers with stored keys. Add one first.")
    plaintext = json.dumps({"v": 1, "code": args.code, "app": args.app,
                            "ts": int(time.time())}).encode()
    messages = []
    for receiver_id, info in known.items():
        box = SealedBox(PublicKey(base64.b64decode(info["public_key"])))
        ciphertext = base64.b64encode(box.encrypt(plaintext)).decode()
        mac = base64.b64encode(hmac.new(base64.b64decode(info["auth_key"]),
                                        ciphertext.encode(),
                                        hashlib.sha256).digest()).decode()
        messages.append(
            {"receiver_id": receiver_id, "ciphertext": ciphertext, "mac": mac})
    status, data = call(state, "POST", "/api/send", {"messages": messages})
    if status != 200:
        sys.exit(f"Send failed ({status}): {data.get('error', '')}")
    for receiver_id in data.get("unknown", []):
        known.pop(receiver_id, None)
    save(state)
    print(f"sent: {len(data.get('sent', []))}  queued: {len(data.get('queued', []))}  "
          f"unknown: {len(data.get('unknown', []))}")


def cmd_remove(args):
    state = load()
    status, data = call(state, "DELETE", f"/api/receivers/{args.receiver_id}")
    if status != 200:
        sys.exit(f"Could not unpair ({status}): {data.get('error', '')}")
    state.get("receivers", {}).pop(args.receiver_id, None)
    save(state)
    print(f"Unpaired {args.receiver_id}.")


def main():
    parser = argparse.ArgumentParser(description="Development stand-in for the phone app")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register", help="redeem an invite")
    p.add_argument("relay")
    p.add_argument("invite")
    p.add_argument("--name", default="dev-source")

    p = sub.add_parser("add", help="pair with a receiver from its otprelay:// link")
    p.add_argument("link")

    sub.add_parser("list", help="list receivers as the relay sees them")

    p = sub.add_parser("send", help="encrypt and send a code to every paired receiver")
    p.add_argument("code")
    p.add_argument("--app", default="Messages")

    p = sub.add_parser("remove", help="unpair a receiver")
    p.add_argument("receiver_id")

    args = parser.parse_args()
    {"register": cmd_register, "add": cmd_add, "list": cmd_list,
     "send": cmd_send, "remove": cmd_remove}[args.command](args)


if __name__ == "__main__":
    main()
