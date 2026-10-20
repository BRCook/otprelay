#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
otprelay: an end-to-end-encrypted relay for one-time codes.

Sources (phones) encrypt each code separately for each paired receiver
(desktop) and post the ciphertext here. The relay routes ciphertext to the
correct receiver over a WebSocket.

See docs/PROTOCOL.md for the full API.
"""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import ssl
import time

from aiohttp import WSMsgType, web

DB_PATH = os.environ.get("OTPRELAY_DB", "/var/lib/otprelay/otprelay.db")
HOST = os.environ.get("OTPRELAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("OTPRELAY_PORT", "8787"))
TLS_CERT = os.environ.get("OTPRELAY_TLS_CERT")
TLS_KEY = os.environ.get("OTPRELAY_TLS_KEY")

MESSAGE_TTL = 120      # seconds a code waits for an offline receiver
PAIRING_TTL = 600      # seconds a desktop has to claim its pairing
INVITE_TTL = 86400     # seconds an invite stays valid
MAX_RECEIVERS = 20     # paired receivers per source
MAX_CIPHERTEXT = 4096  # characters of base64 per message
MAX_NAME = 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created INTEGER NOT NULL,
    last_seen INTEGER);
CREATE TABLE IF NOT EXISTS receivers (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    created INTEGER NOT NULL,
    last_seen INTEGER);
CREATE TABLE IF NOT EXISTS pairings (
    secret_hash TEXT PRIMARY KEY,
    receiver_id TEXT NOT NULL REFERENCES receivers(id) ON DELETE CASCADE,
    expires INTEGER NOT NULL);
"""


def now():
    return int(time.time())


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_invite(code):
    return "".join(c for c in str(code).lower() if c.isalnum())


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "never"


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def open_db():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


def fail(cls, message):
    raise cls(text=json.dumps({"error": message}), content_type="application/json")


def bearer(request):
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.startswith("Bearer ") else ""


def clean_name(value, default):
    name = str(value or "").strip()[:MAX_NAME]
    return name or default


async def read_json(request):
    try:
        data = await request.json()
    except Exception:
        fail(web.HTTPBadRequest, "body must be JSON")
    if not isinstance(data, dict):
        fail(web.HTTPBadRequest, "body must be a JSON object")
    return data


class Relay:
    def __init__(self, db):
        self.db = db
        self.sockets = {}  # receiver_id -> WebSocketResponse
        self.queues = {}   # receiver_id -> {msg_id: (expires, payload)}

    # --- authentication -----------------------------------------------------

    def source(self, request):
        token = bearer(request)
        row = token and self.db.execute(
            "SELECT * FROM sources WHERE token_hash = ?", (token_hash(token),)).fetchone()
        if not row:
            fail(web.HTTPUnauthorized, "invalid source token")
        self.db.execute("UPDATE sources SET last_seen = ? WHERE id = ?", (now(), row["id"]))
        return row

    def receiver(self, request):
        token = bearer(request)
        row = token and self.db.execute(
            "SELECT * FROM receivers WHERE token_hash = ?", (token_hash(token),)).fetchone()
        if not row:
            fail(web.HTTPUnauthorized, "invalid receiver token")
        return row

    def touch_receiver(self, receiver_id):
        self.db.execute("UPDATE receivers SET last_seen = ? WHERE id = ?", (now(), receiver_id))

    async def disconnect(self, receiver_id, reason):
        self.queues.pop(receiver_id, None)
        ws = self.sockets.pop(receiver_id, None)
        if ws is not None:
            await ws.close(code=4001, message=reason.encode())

    # --- endpoints ----------------------------------------------------------

    async def health(self, request):
        return web.Response(text="ok\n")

    async def register(self, request):
        data = await read_json(request)
        invite_hash = token_hash(normalize_invite(data.get("invite", "")))
        row = self.db.execute(
            "SELECT user_id FROM invites WHERE token_hash = ? AND expires > ?",
            (invite_hash, now())).fetchone()
        if not row:
            fail(web.HTTPForbidden, "invalid or expired invite")
        self.db.execute("DELETE FROM invites WHERE token_hash = ?", (invite_hash,))
        source_id = secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO sources (id, user_id, token_hash, name, created, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, row["user_id"], token_hash(token),
             clean_name(data.get("name"), "Phone"), now(), now()))
        log(f"registered source {source_id}")
        return web.json_response({"source_id": source_id, "source_token": token})

    async def list_receivers(self, request):
        source = self.source(request)
        rows = self.db.execute(
            "SELECT id, name, platform, created, last_seen, token_hash IS NOT NULL AS claimed "
            "FROM receivers WHERE source_id = ? ORDER BY created", (source["id"],)).fetchall()
        return web.json_response({"receivers": [
            {"id": r["id"], "name": r["name"], "platform": r["platform"],
             "created": r["created"], "last_seen": r["last_seen"],
             "claimed": bool(r["claimed"]), "online": r["id"] in self.sockets}
            for r in rows]})

    async def add_receiver(self, request):
        data = await read_json(request)
        source = self.source(request)
        secret = str(data.get("pairing_secret", ""))
        if len(secret) < 32:
            fail(web.HTTPBadRequest, "pairing_secret too short")
        count = self.db.execute(
            "SELECT COUNT(*) FROM receivers WHERE source_id = ?", (source["id"],)).fetchone()[0]
        if count >= MAX_RECEIVERS:
            fail(web.HTTPConflict, "receiver limit reached")
        receiver_id = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO receivers (id, source_id, token_hash, name, platform, created) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (receiver_id, source["id"], clean_name(data.get("name"), "Receiver"),
             clean_name(data.get("platform"), "unknown"), now()))
        try:
            self.db.execute(
                "INSERT INTO pairings (secret_hash, receiver_id, expires) VALUES (?, ?, ?)",
                (token_hash(secret), receiver_id, now() + PAIRING_TTL))
        except sqlite3.IntegrityError:
            self.db.execute("DELETE FROM receivers WHERE id = ?", (receiver_id,))
            fail(web.HTTPConflict, "pairing code already used")
        log(f"source {source['id']} added receiver {receiver_id}")
        return web.json_response({"receiver_id": receiver_id})

    async def remove_receiver(self, request):
        source = self.source(request)
        receiver_id = request.match_info["receiver_id"]
        cur = self.db.execute(
            "DELETE FROM receivers WHERE id = ? AND source_id = ?", (receiver_id, source["id"]))
        if cur.rowcount == 0:
            fail(web.HTTPNotFound, "no such receiver")
        await self.disconnect(receiver_id, "unpaired by source")
        log(f"source {source['id']} removed receiver {receiver_id}")
        return web.json_response({"ok": True})

    async def claim(self, request):
        data = await read_json(request)
        secret_hash = token_hash(str(data.get("pairing_secret", "")))
        row = self.db.execute(
            "SELECT receiver_id FROM pairings WHERE secret_hash = ? AND expires > ?",
            (secret_hash, now())).fetchone()
        if not row:
            fail(web.HTTPNotFound, "not paired yet")
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "UPDATE receivers SET token_hash = ? WHERE id = ?", (token_hash(token), row["receiver_id"]))
        self.db.execute("DELETE FROM pairings WHERE secret_hash = ?", (secret_hash,))
        log(f"receiver {row['receiver_id']} claimed its pairing")
        return web.json_response({"receiver_id": row["receiver_id"], "receiver_token": token})

    async def remove_self(self, request):
        receiver = self.receiver(request)
        self.db.execute("DELETE FROM receivers WHERE id = ?", (receiver["id"],))
        await self.disconnect(receiver["id"], "unpaired by receiver")
        log(f"receiver {receiver['id']} unpaired itself")
        return web.json_response({"ok": True})

    async def send(self, request):
        data = await read_json(request)
        source = self.source(request)
        messages = data.get("messages")
        if not isinstance(messages, list) or len(messages) > MAX_RECEIVERS:
            fail(web.HTTPBadRequest, f"messages must be a list of at most {MAX_RECEIVERS}")
        owned = {r["id"] for r in self.db.execute(
            "SELECT id FROM receivers WHERE source_id = ?", (source["id"],))}
        result = {"sent": [], "queued": [], "unknown": []}
        outgoing = []
        for m in messages:
            if not isinstance(m, dict):
                fail(web.HTTPBadRequest, "each message must be an object")
            receiver_id = str(m.get("receiver_id", ""))
            ciphertext = m.get("ciphertext")
            if not isinstance(ciphertext, str) or not 0 < len(ciphertext) <= MAX_CIPHERTEXT:
                fail(web.HTTPBadRequest, "invalid ciphertext")
            if receiver_id in owned:
                outgoing.append((receiver_id, ciphertext))
            else:
                result["unknown"].append(receiver_id)
        for receiver_id, ciphertext in outgoing:
            msg_id = secrets.token_hex(8)
            payload = {"type": "code", "id": msg_id, "ciphertext": ciphertext, "sent": now()}
            self.queues.setdefault(receiver_id, {})[msg_id] = (now() + MESSAGE_TTL, payload)
            ws = self.sockets.get(receiver_id)
            if ws is not None and not ws.closed:
                try:
                    await ws.send_json(payload)
                    result["sent"].append(receiver_id)
                    continue
                except ConnectionError:
                    pass
            result["queued"].append(receiver_id)
        return web.json_response(result)

    async def websocket(self, request):
        receiver = self.receiver(request)
        receiver_id = receiver["id"]
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        old = self.sockets.get(receiver_id)
        self.sockets[receiver_id] = ws
        if old is not None:
            await old.close(code=4000, message=b"replaced by a newer connection")
        self.touch_receiver(receiver_id)
        log(f"receiver {receiver_id} connected")
        try:
            for expires, payload in list(self.queues.get(receiver_id, {}).values()):
                if expires > now():
                    await ws.send_json(payload)
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "ack":
                    self.queues.get(receiver_id, {}).pop(str(data.get("id")), None)
        finally:
            if self.sockets.get(receiver_id) is ws:
                del self.sockets[receiver_id]
            self.touch_receiver(receiver_id)
            log(f"receiver {receiver_id} disconnected")
        return ws

    # --- housekeeping -------------------------------------------------------

    async def janitor(self):
        while True:
            await asyncio.sleep(30)
            try:
                t = now()
                self.db.execute(
                    "DELETE FROM receivers WHERE token_hash IS NULL AND id IN "
                    "(SELECT receiver_id FROM pairings WHERE expires <= ?)", (t,))
                self.db.execute("DELETE FROM pairings WHERE expires <= ?", (t,))
                self.db.execute("DELETE FROM invites WHERE expires <= ?", (t,))
                live = {r["id"] for r in self.db.execute("SELECT id FROM receivers")}
                for receiver_id in list(self.sockets):
                    if receiver_id not in live:
                        await self.disconnect(receiver_id, "unpaired")
                for receiver_id in list(self.queues):
                    fresh = {k: v for k, v in self.queues[receiver_id].items() if v[0] > t}
                    if fresh and receiver_id in live:
                        self.queues[receiver_id] = fresh
                    else:
                        del self.queues[receiver_id]
            except Exception as e:
                log(f"janitor error: {e}")


def make_app():
    relay = Relay(open_db())
    app = web.Application(client_max_size=64 * 1024)
    app.add_routes([
        web.get("/health", relay.health),
        web.post("/api/register", relay.register),
        web.get("/api/receivers", relay.list_receivers),
        web.post("/api/receivers", relay.add_receiver),
        web.delete("/api/receivers/{receiver_id}", relay.remove_receiver),
        web.post("/api/pair/claim", relay.claim),
        web.delete("/api/receiver", relay.remove_self),
        web.post("/api/send", relay.send),
        web.get("/api/ws", relay.websocket),
    ])

    async def background(app):
        task = asyncio.create_task(relay.janitor())
        yield
        task.cancel()

    app.cleanup_ctx.append(background)
    return app


# --- command-line interface --------------------------------------------------

def cmd_serve(args):
    ssl_ctx = None
    if TLS_CERT and TLS_KEY:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(TLS_CERT, TLS_KEY)
    log(f"otprelay listening on {'https' if ssl_ctx else 'http'}://{HOST}:{PORT}")
    web.run_app(make_app(), host=HOST, port=PORT, ssl_context=ssl_ctx,
                access_log=None, print=None)


def cmd_invite(args):
    db = open_db()
    db.execute("INSERT OR IGNORE INTO users (name, created) VALUES (?, ?)", (args.user, now()))
    user_id = db.execute("SELECT id FROM users WHERE name = ?", (args.user,)).fetchone()["id"]
    raw = secrets.token_hex(8)
    db.execute("INSERT INTO invites (token_hash, user_id, expires) VALUES (?, ?, ?)",
               (token_hash(raw), user_id, now() + INVITE_TTL))
    code = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    print(f"Invite for {args.user} (single use, expires {fmt_time(now() + INVITE_TTL)}):")
    print(code)


def cmd_users(args):
    db = open_db()
    rows = db.execute(
        "SELECT u.name, "
        "(SELECT COUNT(*) FROM sources s WHERE s.user_id = u.id) AS sources, "
        "(SELECT COUNT(*) FROM receivers rc JOIN sources s ON rc.source_id = s.id "
        " WHERE s.user_id = u.id) AS receivers "
        "FROM users u ORDER BY u.name").fetchall()
    if not rows:
        print("No users yet.")
    for r in rows:
        print(f"{r['name']:<20} sources: {r['sources']:<3} receivers: {r['receivers']}")


def cmd_sources(args):
    db = open_db()
    rows = db.execute(
        "SELECT s.id, s.name, u.name AS user, s.last_seen, "
        "(SELECT COUNT(*) FROM receivers rc WHERE rc.source_id = s.id) AS receivers "
        "FROM sources s JOIN users u ON s.user_id = u.id ORDER BY u.name, s.created").fetchall()
    if not rows:
        print("No sources (phones) registered yet.")
    for r in rows:
        print(f"{r['id']}  {r['user']:<15} {r['name']:<20} receivers: {r['receivers']:<3} "
              f"last seen: {fmt_time(r['last_seen'])}")


def cmd_revoke_source(args):
    db = open_db()
    cur = db.execute("DELETE FROM sources WHERE id = ?", (args.source_id,))
    print("Source removed, along with its paired receivers." if cur.rowcount else "No such source.")


def cmd_deluser(args):
    db = open_db()
    cur = db.execute("DELETE FROM users WHERE name = ?", (args.user,))
    print(f"User {args.user} deleted, with all sources and receivers." if cur.rowcount
          else "No such user.")


def main():
    parser = argparse.ArgumentParser(prog="otprelay", description="End-to-end-encrypted OTP relay")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the relay server")
    p = sub.add_parser("invite", help="create a single-use invite (creates the user if needed)")
    p.add_argument("user")
    sub.add_parser("users", help="list users")
    sub.add_parser("sources", help="list sources (phones)")
    p = sub.add_parser("revoke-source", help="remove a source (phone) and all its paired receivers")
    p.add_argument("source_id")
    p = sub.add_parser("deluser", help="delete a user with all their sources and receivers")
    p.add_argument("user")
    args = parser.parse_args()
    {"serve": cmd_serve, "invite": cmd_invite, "users": cmd_users, "sources": cmd_sources,
     "revoke-source": cmd_revoke_source, "deluser": cmd_deluser}[args.command](args)


if __name__ == "__main__":
    main()
