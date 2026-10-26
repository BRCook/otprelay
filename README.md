# otprelay

Forward one-time codes from your Android phone to your desktop, end-to-end encrypted, through a small relay.

> [!WARNING]
> **otprelay deliberately breaks multi-factor authentication.**
>
> A second factor is designed to be a separate device. otprelay copies that second factor to every receiver you pair. Anyone who can use one of your receivers (someone at your unlocked desktop, malware, a thief with your laptop, etc) gets your codes as they arrive. If your passwords are saved on that same receiver, **they need nothing else to get into your accounts**. In effect, whoever controls any one of your receivers has both factors, so your accounts are only as safe as your least secure receiver.
>
> otprelay is a convenience tool. It does not preserve the protection MFA is meant to provide.
>
> - This project is for personal use and I, the author, accept these risks for my own accounts.
> - The code is distributed on the understanding that anyone who uses it, or runs a relay for others, accepts the same risks. As the AGPL-3.0 license states, it comes with no warranty of any kind.
> - Think carefully before using otprelay for accounts where a breach would be serious. If you run a relay for family or friends, make sure they understand this too.
>
> If you use otprelay anyway: pair only devices you control, use full-disk encryption and a screen lock on every receiver, and unpair any receiver that is lost, sold, or no longer trusted.

## Design

```
 source -> relay -> receiver -> clipboard
```

* **Source** (Android app, Kotlin): detects codes in notifications, copies them to the clipboard, encrypts them separately for each paired receiver, and sends them to the relay.
* **Relay** (Python): routes ciphertext. It stores only hashes of tokens, and forgets undelivered messages after two minutes.
* **Receiver** (desktop tray app for Linux, later Windows): decrypts codes, copies them to the clipboard, and shows a notification. Pairs by showing a QR code for the source to scan. See [receiver/README.md](receiver/README.md).

The full API and encryption scheme are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Status

| Component          | State            |
|--------------------|------------------|
| Relay              | Working          |
| Receiver (desktop) | Working on Linux |
| Source (Android)   | Working          |

## Security model

* Codes are encrypted on the source with libsodium sealed boxes, one copy per receiver.
* A source trusts only keys it scanned itself. The relay can tell a source that a receiver was removed, but never that one was added, so a compromised relay cannot redirect codes to itself.
* There are no usernames or passwords. Sources join with single-use invites; receivers join by being scanned by a source.
* Every message carries an HMAC keyed with a secret derived from the pairing QR code, which the relay never sees. A receiver rejects anything without a valid MAC, so neither the relay nor anyone who obtains a phone's relay token can inject codes.
* The relay stores no device names or secrets: only token hashes, random IDs, and timestamps. Both apps keep names locally.

## Running the relay

The relay speaks plain HTTP on port 8787 by default and can be used with a reverse proxy in front for TLS and the public hostname. It can also serve TLS itself, but both sides require HTTPS in any case.

Requirements: Python 3.9 or newer and aiohttp.

### From a package

Each release carries a `.deb` and an Alpine `.apk` on the [releases page](https://github.com/BRCook/otprelay/releases). Both install a package called `otprelay-server`, so installing, upgrading and removing go through the usual package manager.

```sh
# Alpine
apk add --allow-untrusted otprelay-server-alpine-0.1.0.apk

# Debian, Ubuntu and relatives
apt install ./otprelay-server-debian-0.1.0.deb
```

The package creates the `otprelay` user, installs the service for that init system, puts the relay in `/usr/lib/otprelay`, and leaves settings in `/etc/conf.d/otprelay` (Alpine) or `/etc/default/otprelay` (Debian). Upgrading keeps both those settings and the database.

Neither package starts the service, so you can set the listen address or TLS first. Then:

```sh
systemctl start otprelay              # Debian (already enabled at boot)
rc-update add otprelay default        # Alpine
rc-service otprelay start
```

### Alpine checkout (OpenRC)

```sh
apk add python3 py3-aiohttp git
addgroup -S otprelay
adduser -S -D -H -h /var/lib/otprelay -s /sbin/nologin -G otprelay otprelay
git clone https://github.com/BRCook/otprelay/ /opt/otprelay
cp /opt/otprelay/server/packaging/openrc/otprelay /etc/init.d/otprelay
cp /opt/otprelay/server/packaging/openrc/otprelay.confd /etc/conf.d/otprelay
ln -s /opt/otprelay/server/otprelay-admin /usr/local/bin/otprelay-admin
rc-update add otprelay default
rc-service otprelay start
```

Logs go to `/var/log/otprelay.log`.

### Debian (systemd)

```sh
apt install python3 python3-aiohttp git
useradd --system --home-dir /var/lib/otprelay --shell /usr/sbin/nologin otprelay
git clone https://github.com/BRCook/otprelay/ /opt/otprelay
cp /opt/otprelay/server/packaging/systemd/otprelay.service /etc/systemd/system/
cp /opt/otprelay/server/packaging/systemd/otprelay.env.example /etc/default/otprelay
ln -s /opt/otprelay/server/otprelay-admin /usr/local/bin/otprelay-admin
systemctl daemon-reload
systemctl enable --now otprelay
```

Logs: `journalctl -u otprelay`.

### Package signing

The `.deb` is not signed, and the Alpine `.apk` is signed with a key generated fresh for each build, so it must be installed with `apk add --allow-untrusted`. Both are protected only by TLS to GitHub Releases: download them from the release page **directly**, and check the file name matches the release.

### Configuration

Set in `/etc/conf.d/otprelay` (Alpine) or `/etc/default/otprelay` (Debian):

| Variable                                 | Default                         | Meaning            |
|------------------------------------------|---------------------------------|--------------------|
| `OTPRELAY_HOST`                          | `0.0.0.0`                       | Listen address     |
| `OTPRELAY_PORT`                          | `8787`                          | Listen port        |
| `OTPRELAY_DB`                            | `/var/lib/otprelay/otprelay.db` | SQLite database    |
| `OTPRELAY_TLS_CERT` / `OTPRELAY_TLS_KEY` | unset                           | Serve TLS directly |
| `OTPRELAY_RATE_LIMIT`                    | `20`                            | Attempts per window on the endpoints that need no token (`/api/register`) |
| `OTPRELAY_RATE_WINDOW`                   | `300`                           | Length of that window, in seconds |
| `OTPRELAY_TRUST_PROXY`                   | unset                           | Count rate limits per real client IP from `X-Forwarded-For`. Only set this when a reverse proxy sets that header, or the limit can be dodged with a forged one |

### Reverse proxy

Point the proxy at `http://<relay-ip>:8787`. It must pass the request headers through for `/api/ws`, since the WebSocket handshake is carried in them.

Receivers send an application-level ping every 30 seconds, so a proxy that only counts data frames as activity will not treat an idle connection as dead. Set `OTPRELAY_TRUST_PROXY=1` so rate limits count the real client rather than the proxy.

### Updating

From a package, install the new version the same way as the first; settings and the database are kept. From a checkout:

```sh
cd /opt/otprelay && git pull
rc-service otprelay restart    # Alpine
systemctl restart otprelay     # Debian
```

### Removing

Removing the package leaves `/var/lib/otprelay` alone, so the database survives a reinstall:

```sh
apk del otprelay-server             # Alpine; delete /var/lib/otprelay by hand
apt purge otprelay-server           # Debian; purge also deletes the database
```

## Building the packages

```sh
server/packaging/build-deb.sh 0.1.0 dist     # needs dpkg-deb
server/packaging/build-apk.sh 0.1.0 dist     # needs Alpine with alpine-sdk
```

## Administration

Run on the relay as root; the wrapper switches to the service user.

```sh
otprelay-admin invite <user>         # create <user> if needed, print an invite
otprelay-admin users                 # users with source and receiver counts
otprelay-admin sources               # registered sources (phones)
otprelay-admin revoke-source <id>    # remove a source and its paired receivers
otprelay-admin deluser <user>        # remove a user and everything they own
```

One relay can serve multiple users. Their phones (sources) redeem the invite, and their desktops (receivers) pair with that source, so nobody can receive anyone else's codes.

## Setting it up

1. **Run a relay** (above) and put it behind HTTPS.
2. **Create an invite**: `otprelay-admin invite yourname`.
3. **Set up the phone**: see [android/README.md](android/README.md). One step is easy to miss: Android hands apps a redacted copy of any notification that looks like it holds a code, so the app needs the `RECEIVE_SENSITIVE_NOTIFICATIONS` permission granted over ADB.
4. **Set up each computer**: see [receiver/README.md](receiver/README.md), then scan its QR code with the phone.

## Testing without a phone

`tools/dev-source.py` implements the source side of the protocol in Python, so you can exercise a relay and a receiver from a terminal. See [receiver/README.md](receiver/README.md#testing-without-the-phone-app).

## License

[GNU Affero General Public License v3.0 or later](LICENSE).
