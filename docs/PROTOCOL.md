# otprelay protocol, version 1

This document is the contract between the three components:

* the **source**: the Android app that detects codes in notifications,
* the **relay**: the server that routes encrypted messages, and
* the **receiver**: the desktop client that decrypts codes and copies them.

The relay only ever handles ciphertext; it cannot read codes.

## Roles

| Role               | Holds                                                                           | Trust                                   |
|--------------------|---------------------------------------------------------------------------------|-----------------------------------------|
| Source (phone)     | its source token; the public key of every paired receiver                       | Root of trust for its own receiver list |
| Relay              | hashes of tokens; receiver names and timestamps; ciphertext for up to 2 minutes | Untrusted for confidentiality           |
| Receiver (desktop) | its receiver token; its own private key                                         | Decrypts only codes meant for it        |

Each source has its own independent list of receivers. A user can have several sources, such as two phones.

## The trust rule

A source encrypts only to public keys it obtained itself by scanning a QR code. The relay may tell a source that a receiver was **removed** (and the source must then forget it), but a source never accepts a receiver or key **from** the relay. A compromised relay can therefore hide receivers from a source, but it can never add its own key to the list.

## Encryption

* Primitive: libsodium sealed box (`crypto_box_seal`): X25519 key agreement with a fresh ephemeral key per message, XSalsa20-Poly1305 authenticated encryption.
* Each receiver generates its own X25519 key pair at pairing time.
* On the wire, a ciphertext is the sealed box encoded as standard base64 (with padding), at most 4096 characters.
* Every message also carries mac: base64 of HMAC-SHA256 over the UTF-8 bytes of the base64 ciphertext, keyed with the pairing’s auth key. Receivers reject any message whose MAC does not verify, so neither the relay nor anyone holding only the source token can inject codes.
* The plaintext is UTF-8 JSON:

```json
{"v": 1, "code": "624950", "app": "Messages", "from": "242227", "ts": 1792345154}
```

| Field  | Required | Meaning                                          |
|--------|----------|--------------------------------------------------|
| `v`    | yes      | Plaintext format version, currently 1            |
| `code` | yes      | The extracted one-time code                      |
| `app`  | no       | Display name of the app the code came from       |
| `ts`   | yes      | Unix time the source saw the code                |

Receivers should ignore codes whose `ts` is more than 5 minutes old.

## Pairing QR code

The receiver shows a QR code containing this URI:

```
otprelay://pair?v=1&u=<relay>&k=<public key>&s=<secret>&n=<name>&p=<platform>
```

| Parameter | Meaning                                                            |
|-----------|--------------------------------------------------------------------|
| `v`       | Pairing format version, currently 1                                |
| `u`       | Relay base URL, URL-encoded (e.g. `https%3A%2F%2Fotp.example.com`) |
| `k`       | Receiver public key, 32 bytes, base64url without padding           |
| `s`       | Pairing secret, 32 random bytes, base64url without padding         |
| `n`       | Receiver name, URL-encoded, at most 64 characters                  |
| `p`       | Platform: `linux`, `windows`, or `macos`                           |

The source must refuse the QR code if `u` does not match its own relay (after removing any trailing slash), and must check that `k` decodes to 32 bytes.

### Derived values

`s` itself is never sent to the relay. Both sides derive, with
HMAC-SHA256 keyed by the raw 32 bytes of `s`:
`claim = HMAC (s, "otprelay claim v1")`, base64url unpadded, sent as
`pairing_secret` to `POST /api/receivers` and `POST /api/pair/claim`;
`auth = HMAC (s, "otprelay auth v1")`, the 32-byte MAC key, kept by both and never
transmitted.

## Authentication

Sources and receivers authenticate with `Authorization: Bearer <token>`. Tokens are never put in URLs. The relay stores only SHA-256 hashes of tokens.

Errors are JSON: `{"error": "message"}` with an appropriate HTTP status.

## Endpoints

### `GET /health`

Returns `ok`. No authentication. For monitoring and reverse proxy checks.

### `POST /api/register` (no auth)

Trade an invite for a source token. Invites look like `a1b2-c3d4-e5f6-a7b8`; case, dashes and spaces are ignored. They are single-use and expire after 24 hours.

Request: `{"invite": "a1b2-c3d4-e5f6-a7b8"}`

Response: `{"source_id": "...", "source_token": "..."}`

Errors: `403` invalid or expired invite.

### `GET /api/receivers` (source)

Response:

```json
{"receivers": [{"id": "f400d5c36ad2d0d8", "name": "My-Desktop", "platform": "linux",
                "created": 1792423221, "last_seen": 1792423300,
                "claimed": true, "online": true}]}
```

`claimed` is false until the receiver has collected its token. The source must drop any locally stored receiver that is missing from this list. A listed receiver without a locally stored key should be shown as needing re-pairing.

### `POST /api/receivers` (source)

Register a receiver after scanning its QR code.

Request: `{"pairing_secret": "<claim derived from s>"}`

Response: `{"receiver_id": "..."}`

The source then stores `receiver_id` with the public key from the QR code. Errors: `400` secret too short, `409` receiver limit (20) reached or secret already used. A receiver that does not claim its pairing within 10 minutes is removed automatically.

### `DELETE /api/receivers/{receiver_id}` (source)

Unpair a receiver. If it is connected, the relay closes its WebSocket with code 4001. Errors: `404` no such receiver.

### `POST /api/send` (source)

Deliver one ciphertext per receiver.

Request:

```json
{"messages": [{"receiver_id": "f400d5c36ad2d0d8", "ciphertext": "<base64>",
  "mac": "<base64>"}]}
```

Response:

```json
{"sent": ["f400d5c36ad2d0d8"], "queued": [], "unknown": []}
```

`sent` went out over a live connection, `queued` will be delivered if the receiver connects within 2 minutes, and `unknown` receivers no longer exist. The source must forget `unknown` receivers.

### `POST /api/pair/claim` (no auth)

The receiver polls this (every 2 seconds is reasonable) after showing its QR code.

Request: `{"pairing_secret": "<claim derived from s>"}`

Response: `{"receiver_id": "...", "receiver_token": "...", "source_name": "Galaxy S24"}`

`source_name` is the name the source registered with, so the receiver can show which phone it is paired with. It is display-only: it comes from the relay rather than from the source itself, so it carries no authority and must not be presented as verified.

Errors: `404` until the source has registered the receiver. A secret can be claimed only once.

### `DELETE /api/receiver` (receiver)

The receiver unpairs itself. The source learns about it on its next `GET /api/receivers` or `POST /api/send`.

### `GET /api/ws` (receiver)

WebSocket for receiving codes. On connect, the relay first sends anything still queued, then live messages:

```json
{"type": "code", "id": "9f2c...", "ciphertext": "<base64>", "sent": 1792345155,
  "mac": "<base64>"}
```

The receiver replies `{"type": "ack", "id": "9f2c..."}` once it has handled a message, including one it failed to decrypt. Unacknowledged messages are resent on reconnect until they expire, so receivers must ignore an `id` they have already handled.

The relay sends WebSocket pings every 30 seconds. Close codes:

| Code  | Meaning                                                | Receiver should                                     |
|-------|--------------------------------------------------------|-----------------------------------------------------|
| 4000  | Another connection for this receiver replaced this one | Back off; another instance is running               |
| 4001  | The receiver was unpaired                              | Delete its credentials and stop                     |
| other | Relay restart or network issue                         | Reconnect with exponential backoff (1 s up to 60 s) |

## Limits

| Limit                   | Value                  |
|-------------------------|------------------------|
| Request body            | 64 KiB                 |
| Receivers per source    | 20                     |
| Ciphertext              | 4096 base64 characters |
| Names                   | 64 characters          |
| Queued message lifetime | 2 minutes              |
| Pairing window          | 10 minutes             |
| Invite lifetime         | 24 hours               |
