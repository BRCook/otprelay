# otprelay receiver

The desktop half of [otprelay](../README.md): a tray app that receives one-time codes from your phones, decrypts them, and copies them to the clipboard.

Read the warning in the [main README](../README.md) before using this. Anyone who can use this computer can read your codes.

## What it does

* Sits in the system tray and keeps one connection open per paired phone.
* Pairs by showing a QR code for the phone to scan. Each pairing has its own key pair, so one computer can receive from several phones.
* Copies each code to the clipboard and shows a notification.
* Lets you unpair any phone, and notices when a phone unpairs this computer.

## Requirements

Python 3.10 or newer, PySide6, PyNaCl, and qrcode. A system tray is required, which KDE Plasma, GNOME (with an extension), and Windows all provide.

## Development setup

Dependencies are declared in `pyproject.toml`.

## Testing without the phone app

`tools/dev-source.py` stands in for an Android phone. With a relay running:

```sh
# On the relay: otprelay-admin invite yourname
python ../tools/dev-source.py register https://otp.example.com a1b2-c3d4-e5f6-a7b8

# In the receiver, choose "Add a phone", then "Copy link instead", and paste it here:
python ../tools/dev-source.py add 'otprelay://pair?v=1&u=...'

python ../tools/dev-source.py list
python ../tools/dev-source.py send 482913
```

## Where things are stored

Settings and pairings live in `~/.config/otprelay/receiver.json` (`%APPDATA%\otprelay\receiver.json` on Windows), created readable only by you. **It holds one private key per pairing**: anyone who can read it can decrypt this computer's codes, so use full-disk encryption.

Set `OTPRELAY_CONFIG_DIR` to use a different location, which is handy for testing.

## Clipboard support

Wayland does not let an unfocused app set the clipboard, so the receiver asks Klipper over D-Bus on KDE, uses `wl-copy` on other Wayland desktops, and uses Qt's own clipboard on X11, Windows and macOS.

## Starting at login

The Settings window has a "Start automatically at login" option, which writes an XDG autostart entry on Linux or a Run key on Windows. A tray app belongs in the desktop session rather than in a systemd service, so this is the supported way to start it.
