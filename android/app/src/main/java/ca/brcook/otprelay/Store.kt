// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

/** One paired desktop, as this phone knows it. */
data class Receiver(
    val id: String,
    val name: String,
    val platform: String,
    /** The receiver's X25519 public key, standard base64. Scanned from its QR code. */
    val publicKey: String,
    /** Base64 HMAC key derived from the pairing secret. Empty on pairings made before sender auth. */
    val authKey: String,
    val pairedAt: Long,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id)
        .put("name", name)
        .put("platform", platform)
        .put("public_key", publicKey)
        .put("paired_at", pairedAt)
        .put("auth_key", authKey)

    companion object {
        fun fromJson(o: JSONObject) = Receiver(
            id = o.getString("id"),
            name = o.optString("name", "Desktop"),
            platform = o.optString("platform", "unknown"),
            publicKey = o.getString("public_key"),
            pairedAt = o.optLong("paired_at", 0L),
            authKey = o.optString("auth_key", ""),
        )
    }
}

/**
 * Everything this app remembers: its relay credentials, the receivers it has
 * scanned, and which apps to ignore.
 *
 * Public keys are not secret, but the source token is: it lets the holder send
 * to this phone's receivers and manage its pairing list.
 */
class Store(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("otprelay", Context.MODE_PRIVATE)

    var relayUrl: String
        get() = prefs.getString(KEY_RELAY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_RELAY, value.trimEnd('/')).apply()

    var sourceId: String
        get() = prefs.getString(KEY_SOURCE_ID, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SOURCE_ID, value).apply()

    var sourceToken: String
        get() = Vault.open(prefs.getString(KEY_TOKEN, "") ?: "") ?: ""
        set(value) = prefs.edit().putString(KEY_TOKEN, Vault.seal(value)).apply()

    /** The name this phone registered under, shown on receivers. */
    var sourceName: String
        get() = prefs.getString(KEY_SOURCE_NAME, android.os.Build.MODEL) ?: android.os.Build.MODEL
        set(value) = prefs.edit().putString(KEY_SOURCE_NAME, value.take(64)).apply()

    /** Whether forwarding is switched on. Pausing stops sending without unpairing. */
    var forwarding: Boolean
        get() = prefs.getBoolean(KEY_FORWARDING, true)
        set(value) = prefs.edit().putBoolean(KEY_FORWARDING, value).apply()

    /** Whether to also put the code on this phone's own clipboard. */
    var copyLocally: Boolean
        get() = prefs.getBoolean(KEY_COPY_LOCAL, false)
        set(value) = prefs.edit().putBoolean(KEY_COPY_LOCAL, value).apply()

    /**
     * The words that mark a message as being about a code. Editable, so an
     * unusual bank or another language needs no new build. Stored sorted for a
     * stable order on screen.
     */
    var keywords: Set<String>
        get() = prefs.getStringSet(KEY_KEYWORDS, null) ?: CodeExtractor.DEFAULT_KEYWORDS.toSet()
        set(value) = prefs.edit()
            .putStringSet(KEY_KEYWORDS, value.map { it.trim().lowercase() }
                .filter { it.isNotEmpty() }.toSet())
            .apply()

    /**
     * Whether to mark a message read once its code has been forwarded. Uses the
     * notifying app's own "mark as read" action where there is one, and
     * otherwise just dismisses the notification.
     */
    var markAsRead: Boolean
        get() = prefs.getBoolean(KEY_MARK_READ, false)
        set(value) = prefs.edit().putBoolean(KEY_MARK_READ, value).apply()

    /** A short line about the most recent code, shown on the main screen. */
    var lastEvent: String
        get() = prefs.getString(KEY_LAST_EVENT, "") ?: ""
        set(value) = prefs.edit()
            .putString(KEY_LAST_EVENT, value)
            .putLong(KEY_LAST_EVENT_AT, System.currentTimeMillis())
            .apply()

    /** When [lastEvent] was recorded, in milliseconds since the epoch. */
    val lastEventAt: Long get() = prefs.getLong(KEY_LAST_EVENT_AT, 0L)

    /**
     * Packages whose notifications are ignored. Everything else is watched, so
     * codes from apps the user has not thought of still get through.
     */
    var excludedPackages: Set<String>
        get() = prefs.getStringSet(KEY_EXCLUDED, DEFAULT_EXCLUDED) ?: DEFAULT_EXCLUDED
        set(value) = prefs.edit().putStringSet(KEY_EXCLUDED, value).apply()

    val isRegistered: Boolean get() = relayUrl.isNotEmpty() && sourceToken.isNotEmpty()

    // --- receivers ----------------------------------------------------------

    fun receivers(): List<Receiver> {
        val raw = Vault.open(prefs.getString(KEY_RECEIVERS, "") ?: "").orEmpty().ifEmpty { "[]" }
        return runCatching {
            val array = JSONArray(raw)
            (0 until array.length()).map { Receiver.fromJson(array.getJSONObject(it)) }
        }.getOrDefault(emptyList())
    }

    private fun writeReceivers(list: List<Receiver>) {
        val array = JSONArray()
        list.forEach { array.put(it.toJson()) }
        prefs.edit().putString(KEY_RECEIVERS, Vault.seal(array.toString())).apply()
    }

    fun addReceiver(receiver: Receiver) {
        writeReceivers(receivers().filterNot { it.id == receiver.id } + receiver)
    }

    fun removeReceiver(id: String) {
        writeReceivers(receivers().filterNot { it.id == id })
    }

    /**
     * Drop receivers the relay no longer lists.
     *
     * This is the only direction the relay may influence the list: it can say a
     * receiver is gone, but a receiver is only ever added by scanning its QR
     * code, so a compromised relay cannot redirect codes to a key of its own.
     */
    fun forgetMissing(idsStillOnRelay: Collection<String>) {
        val kept = receivers().filter { it.id in idsStillOnRelay }
        if (kept.size != receivers().size) writeReceivers(kept)
    }

    fun clear() = prefs.edit().clear().apply()

    private companion object {
        const val KEY_RELAY = "relay_url"
        const val KEY_SOURCE_ID = "source_id"
        const val KEY_TOKEN = "source_token"
        const val KEY_SOURCE_NAME = "source_name"
        const val KEY_FORWARDING = "forwarding"
        const val KEY_EXCLUDED = "excluded_packages"
        const val KEY_RECEIVERS = "receivers"
        const val KEY_KEYWORDS = "keywords"
        const val KEY_MARK_READ = "mark_as_read"
        const val KEY_LAST_EVENT = "last_event"
        const val KEY_LAST_EVENT_AT = "last_event_at"
        const val KEY_COPY_LOCAL = "copy_locally"

        /** This app's own notifications are never worth scanning. */
        val DEFAULT_EXCLUDED = setOf("ca.brcook.otprelay", "android", "com.android.systemui")
    }
}
