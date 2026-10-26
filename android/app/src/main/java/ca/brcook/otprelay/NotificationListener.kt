// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import android.app.Notification
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.os.Build
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.os.PersistableBundle

/**
 * Watches notifications for one-time codes and forwards them encrypted.
 *
 * Every app is watched except those the user excludes, so codes from apps they
 * never thought to add still get through. The message text never leaves the
 * phone. Only the code, the app’s name, and the notification title (usually
 * the sender) are sent, sealed for each paired receiver, and the relay cannot
 * read any of them.
 */
class NotificationListener : NotificationListenerService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var store: Store
    private lateinit var relay: RelayClient

    /** Codes seen recently, so a notification that updates itself is not resent. */
    private val recent = mutableMapOf<String, Long>()

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        relay = RelayClient(store)
        debug("listener service created")
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        debug("listener connected to the notification stream")
    }

    override fun onListenerDisconnected() {
        Log.w(TAG, "listener disconnected from the notification stream")
        super.onListenerDisconnected()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        try {
            handle(sbn)
        } catch (e: Exception) {
            // A listener that throws gets disconnected by Android, so swallow everything.
            Log.w(TAG, "could not handle a notification", e)
        }
    }

    private fun handle(sbn: StatusBarNotification) {
        // Logged at every exit so a missed code can be traced without guessing.
        // Message text is never logged, only its length.
        val from = sbn.packageName
        if (!store.forwarding) return skip(from, "forwarding is paused")
        if (!store.isRegistered) return skip(from, "not registered with a relay")
        if (from in store.excludedPackages) return skip(from, "app is excluded")

        // Android replays notifications that are already showing when the listener
        // binds, so anything posted a while ago is ignored rather than resent.
        val age = System.currentTimeMillis() - sbn.postTime
        if (sbn.postTime > 0 && age > NOTIFICATION_MAX_AGE_MS) {
            return skip(from, "posted ${age / 1000}s ago")
        }

        val notification = sbn.notification ?: return skip(from, "no notification")
        // Persistent and summary notifications are not messages.
        if (notification.flags and Notification.FLAG_ONGOING_EVENT != 0) {
            return skip(from, "ongoing notification")
        }
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) {
            return skip(from, "group summary")
        }

        val extras = notification.extras ?: return skip(from, "no extras")
        val bodies = textsFrom(notification, extras)
        if (bodies.isEmpty()) {
            return skip(from, "no text (keys: ${extras.keySet().joinToString(",")})")
        }

        // The collapsed preview is often truncated mid-sentence, so every source
        // is tried and the first that yields a code wins.
        val words = store.keywords
        val code = bodies.firstNotNullOfOrNull { (_, text) -> CodeExtractor.extract(text, words) }
            ?: return skip(
                from,
                "no code in " + bodies.joinToString(", ") { "${it.first}=${it.second.length} chars" }
            )

        val now = System.currentTimeMillis()
        recent.entries.removeAll { now - it.value > DEDUPE_MS }
        val key = "${sbn.packageName}:$code"
        if (recent.put(key, now) != null) return

        debug("found a ${code.length}-character code in a notification from $from")
        copyLocally(code)
        send(code, appLabel(sbn.packageName), sbn)
    }

    /**
     * Every place a notification might carry its text, longest first.
     *
     * A plain SMS puts it in EXTRA_TEXT, but a messaging app usually sends a
     * MessagingStyle notification where EXTRA_TEXT is only a shortened preview
     * and the full message sits in the style's own list.
     */
    private fun textsFrom(
        notification: Notification,
        extras: android.os.Bundle,
    ): List<Pair<String, String>> {
        val found = mutableListOf<Pair<String, String>>()

        fun add(source: String, value: CharSequence?) {
            val text = value?.toString()?.trim().orEmpty()
            if (text.isNotEmpty()) found += source to text
        }

        // A messaging notification carries the whole conversation, so the newest
        // message is taken first and older ones are left alone: otherwise a
        // previous code further up the thread could be picked instead.
        runCatching {
            val style = NotificationCompat.MessagingStyle
                .extractMessagingStyleFromNotification(notification)
            val messages = style?.messages.orEmpty().reversed()
            messages.forEachIndexed { index, message ->
                val stamped = message.timestamp > 0L
                val recentEnough =
                    (stamped && System.currentTimeMillis() - message.timestamp <= MESSAGE_MAX_AGE_MS) ||
                        (!stamped && index == 0)
                if (recentEnough) add("message", message.text)
            }
        }
        add("bigText", extras.getCharSequence(Notification.EXTRA_BIG_TEXT))
        extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.reversed()
            ?.forEach { add("line", it) }
        add("text", extras.getCharSequence(Notification.EXTRA_TEXT))
        add("summary", extras.getCharSequence(Notification.EXTRA_SUMMARY_TEXT))

        // Order matters: the first source that yields a code wins, so this runs
        // newest to oldest rather than longest to shortest.
        return found.distinctBy { it.second }
    }

    /**
     * True only for builds installed from the IDE. Release builds stay quiet, so
     * the app never writes which apps notify you, or how long their text is, to
     * a log other apps with log access could read.
     */
    private val verbose: Boolean
        get() = (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0

    private fun debug(message: String) {
        if (verbose) Log.d(TAG, message)
    }

    private fun skip(packageName: String, reason: String) {
        debug("ignored a notification from $packageName: $reason")
    }

    private fun send(code: String, app: String, sbn: StatusBarNotification) {
        if (store.receivers().isEmpty()) {
            store.lastEvent = if (store.copyLocally) "Copied a code to this phone"
            else "Found a code but no computer is paired"
            return
        }
        val plaintext = JSONObject()
            .put("v", 1)
            .put("code", code)
            .put("app", app)
            .put("ts", System.currentTimeMillis() / 1000)
            .toString()
            .toByteArray()

        scope.launch {
            var lastError: String? = null
            repeat(SEND_ATTEMPTS) { attempt ->
                try {
                    val result = relay.send(plaintext)
                    val reached = result.sent.size + result.queued.size
                    debug("sent to ${result.sent.size}, queued for ${result.queued.size}")
                    store.lastEvent = if (reached > 0) {
                        "Sent a code to $reached computer${if (reached == 1) "" else "s"}"
                    } else if (store.copyLocally) {
                        "Copied a code to this phone"
                    } else {
                        "Found a code but no computer is paired"
                    }
                    // Only once the code is safely away, so a failed send never
                    // hides the message it came from.
                    if (reached > 0) markRead(sbn)
                    return@launch
                } catch (e: RelayException) {
                    lastError = e.message
                    // A rejected token or a bad request will not fix itself.
                    if (e.status in 400..499) return@launch finish(lastError)
                    delay(RETRY_DELAY_MS * (attempt + 1))
                } catch (e: Exception) {
                    lastError = e.message
                    delay(RETRY_DELAY_MS * (attempt + 1))
                }
            }
            finish(lastError)
        }
    }

    private fun finish(error: String?) {
        Log.w(TAG, "could not send a code: $error")
        store.lastEvent = "Could not send a code: ${error ?: "unknown error"}"
    }

    /**
     * Mark the message read, if the user asked for that.
     *
     * The notifying app's own action is used where it offers one, since that
     * marks the message read in the app itself. Otherwise the notification is
     * simply dismissed, which clears the shade without touching the app.
     */
    private fun markRead(sbn: StatusBarNotification) {
        if (!store.markAsRead) return
        val action = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            sbn.notification?.actions?.firstOrNull {
                it.semanticAction == Notification.Action.SEMANTIC_ACTION_MARK_AS_READ
            }
        } else {
            null
        }
        try {
            val intent = action?.actionIntent
            if (intent != null) {
                intent.send()
                debug("marked the message read with the app's own action")
            } else {
                cancelNotification(sbn.key)
                debug("dismissed the notification; the app offers no mark-as-read action")
            }
        } catch (e: Exception) {
            Log.w(TAG, "could not mark the message read", e)
        }
    }

        /**
         * Put the code on this phone's clipboard as well.
         *
         * Marked sensitive, so Android 13 and newer keep it out of the clipboard
         * preview and out of anything that syncs clipboards between devices.
         */
    private fun copyLocally(code: String) {
        if (!store.copyLocally) return
        try {
            val clipboard = getSystemService(ClipboardManager::class.java) ?: return
            val clip = ClipData.newPlainText("One-time code", code)
            clip.description.extras = PersistableBundle().apply {
                putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
                // Same flag under its older name, for Android 12 and earlier.
                putBoolean("android.content.extra.IS_SENSITIVE", true)
            }
            clipboard.setPrimaryClip(clip)
            debug("copied the code to this phone's clipboard")
        } catch (e: Exception) {
            Log.w(TAG, "could not copy to this phone's clipboard", e)
        }
    }

    private fun appLabel(packageName: String): String = try {
        val info = packageManager.getApplicationInfo(packageName, 0)
        packageManager.getApplicationLabel(info).toString()
    } catch (e: Exception) {
        packageName
    }

    companion object {
        private const val TAG = "otprelay"
        private const val DEDUPE_MS = 120_000L
        private const val NOTIFICATION_MAX_AGE_MS = 300_000L
        private const val MESSAGE_MAX_AGE_MS = 300_000L
        private const val SEND_ATTEMPTS = 3
        private const val RETRY_DELAY_MS = 2_000L

        /** Whether the user has granted this app access to notifications. */
        fun isEnabled(context: Context): Boolean =
            context.packageName in NotificationManagerCompat.getEnabledListenerPackages(context)

        /** The system screen where notification access is granted. */
        fun settingsIntent(): Intent =
            Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}
