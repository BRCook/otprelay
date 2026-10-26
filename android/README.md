# otprelay source (Android)

The phone half of [otprelay](../README.md): it spots one-time codes in notifications, encrypts them, and sends the ciphertext to the relay.

Read the warning in the [main README](../README.md) before using this. Forwarding your codes to a computer means whoever can use that computer has your second factor.

## What it does

* Watches notifications from every app except the ones you exclude.
* Copies detected codes to the clipboard.
* Encrypts each code per paired receiver.
* Sends the encrypted code to the relay to be forwarded to the receiver.

## Requirements

Android 8.0 or newer, and a relay reachable over HTTPS. The app refuses a plain `http://` address, because the relay token would travel in the clear even though the codes would not.

## Building

Open this folder in Android Studio and let it sync, then **Run** to install a debug build. From a terminal:

```sh
./gradlew assembleDebug      # app/build/outputs/apk/debug/
./gradlew assembleRelease    # app/build/outputs/apk/release/
./gradlew test               # code detection and pairing tests, no device needed
```

## Setting up a phone

1. **Get an invite.** On the relay: `otprelay-admin invite yourname`.
2. **Register.** Open the app, enter the relay address and the invite, and give the phone a name.
3. **Allow notification access.** Tap the button on the main screen. A sideloaded app may be blocked here: if Android says the setting is restricted, go to **Settings → Apps → otprelay**, open the three-dot menu, choose **Allow restricted settings**, and try again.
4. **Allow sensitive notifications.** Since Android 15, apps are handed a redacted copy of any notification the system thinks contains a code, which leaves nothing to detect. Grant the hidden permission over ADB:

   ```sh
   adb shell appops set ca.brcook.otprelay RECEIVE_SENSITIVE_NOTIFICATIONS allow
   ```

   Then turn notification access off and on again, so the listener rebinds and picks the change up. Without this step the app sees a truncated preview and finds nothing. This survives a reboot, but not a reinstall.
5. **Pair a computer.** Open otprelay on the computer, choose **Add a phone**, and scan the QR code with **Add a computer** here.

Repeat step 5 for each computer. 

## Settings

* **Forward codes** pauses sending without unpairing anything.
* **Mark messages read** acts on a message once its code has been forwarded, and only if the send succeeded. Where the notifying app offers its own mark-as-read action (Samsung Messages does), that action is used, so the message is marked read in the app. Otherwise the notification is dismissed from the shade and the app's own read state is left alone. Off by default.
* **Copy to this phone** copies the detected code to the phone's clipboard in addition to forwarding codes, if selected.
* **Unpair** removes a computer. It stops receiving immediately, since the phone deletes its key.
* **Detection words** edits the list of words that mark a message as being about a code. Spaces and hyphens are interchangeable, so `one time` also matches `one-time` and `onetime`, and a word is matched literally, so punctuation in it is safe. Emptying the list switches detection off entirely.
* **Forget this relay** clears everything: the token, every paired computer, and the relay address. A new invite is needed afterwards.

## When a code does not arrive

With a debug build installed:

```sh
adb logcat -c; adb logcat -s otprelay:V
```

Then trigger a code.

| What the log says | What it means |
| --- | --- |
| `no code in message=37 chars` | The text arrived truncated: step 4 above has not taken effect |
| `posted 900s ago` | An old notification being replayed, ignored on purpose |
| `app is excluded` | That package is on the exclusion list |
| `no receivers paired` | Pair a computer first |
| `could not send a code: ...` | The relay rejected the send or was unreachable |

Nothing at all means the listener is not bound: check notification access, then toggle it off and on.