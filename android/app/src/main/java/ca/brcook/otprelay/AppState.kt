// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** One row in the receivers list: what this phone stores, plus what the relay reports. */
data class ReceiverRow(
    val id: String,
    val name: String,
    val platform: String,
    val online: Boolean,
    val claimed: Boolean,
    /** False when the relay lists a receiver this phone has no key for. */
    val hasKey: Boolean,
)

/**
 * Everything the screens read and act on.
 *
 * Relay calls block, so they run on [Dispatchers.IO] and only the resulting
 * state is written back on the main thread.
 */
class AppState(context: Context) {
    private val store = Store(context)
    private val relay = RelayClient(store)

    var registered by mutableStateOf(store.isRegistered)
        private set
    var relayUrl by mutableStateOf(store.relayUrl)
        private set
    var sourceName by mutableStateOf(store.sourceName)
        private set
    var forwarding by mutableStateOf(store.forwarding)
        private set
    var markAsRead by mutableStateOf(store.markAsRead)
        private set
    var keywords by mutableStateOf(store.keywords.sorted())
        private set
    var receivers by mutableStateOf(emptyList<ReceiverRow>())
        private set
    var busy by mutableStateOf(false)
        private set
    var notificationAccess by mutableStateOf(false)
        private set
    var lastEvent by mutableStateOf("")
        private set

    /** Bumped whenever the screen comes back, so the list refreshes itself. */
    var resumeTick by mutableStateOf(0)
        private set
    var message by mutableStateOf<String?>(null)

    fun dismissMessage() {
        message = null
    }

    /** Re-read the things the system or the listener service can change behind us. */
    fun refreshFromSystem(context: Context) {
        notificationAccess = NotificationListener.isEnabled(context)
        lastEvent = store.lastEvent
        resumeTick++
    }

    fun updateForwarding(enabled: Boolean) {
        store.forwarding = enabled
        forwarding = enabled
    }

    fun updateMarkAsRead(enabled: Boolean) {
        store.markAsRead = enabled
        markAsRead = enabled
    }

    fun addKeyword(word: String) {
        val cleaned = word.trim().lowercase()
        if (cleaned.isEmpty()) return
        if (cleaned in keywords) {
            message = "\"$cleaned\" is already in the list."
            return
        }
        store.keywords = store.keywords + cleaned
        keywords = store.keywords.sorted()
    }

    fun removeKeyword(word: String) {
        store.keywords = store.keywords - word
        keywords = store.keywords.sorted()
        if (keywords.isEmpty()) {
            message = "With no words left, no codes will be detected at all."
        }
    }

    fun restoreKeywords() {
        store.keywords = CodeExtractor.DEFAULT_KEYWORDS.toSet()
        keywords = store.keywords.sorted()
    }

    /** Local view of the receivers, shown before the relay answers. */
    private fun localRows(): List<ReceiverRow> = store.receivers().map {
        ReceiverRow(it.id, it.name, it.platform, online = false, claimed = true, hasKey = true)
    }

    fun register(scope: CoroutineScope, url: String, invite: String, name: String) {
        run(scope, "Registered with the relay.") {
            relay.register(url, invite, name)
            registered = true
            relayUrl = store.relayUrl
            sourceName = store.sourceName
            receivers = localRows()
        }
    }

    fun refresh(scope: CoroutineScope) {
        if (!store.isRegistered) return
        receivers = localRows()
        run(scope, null) { receivers = rowsFromRelay() }
    }

    /** Add the receiver described by a scanned pairing link. */
    fun pair(scope: CoroutineScope, scanned: String) {
        val link = PairingLinkParser.parse(scanned, store.relayUrl).getOrElse {
            message = it.message
            return
        }
        run(scope, "Paired with ${link.name}.") {
            val id = relay.addReceiver(Crypto.deriveClaim(link.secret))
            store.addReceiver(
                Receiver(
                    id = id,
                    name = link.name,
                    platform = link.platform,
                    publicKey = link.publicKey,
                    authKey = Crypto.deriveAuthKey(link.secret),
                    pairedAt = System.currentTimeMillis() / 1000,
                )
            )
            var rows = rowsFromRelay()
            if (rows.any { it.id == id && !it.claimed }) {
                delay(2_500)
                rows = rowsFromRelay()
            }
            receivers = rows
        }
    }

    private fun rowsFromRelay(): List<ReceiverRow> {
        val local = store.receivers().associateBy { it.id }
        return relay.listReceivers().map {
            val mine = local[it.id]
            ReceiverRow(it.id, mine?.name ?: "Unknown computer", mine?.platform ?: "unknown",
                it.online, it.claimed, mine != null)
        }
    }

    fun unpair(scope: CoroutineScope, row: ReceiverRow) {
        run(scope, "Unpaired ${row.name}.") {
            relay.removeReceiver(row.id)
            receivers = receivers.filterNot { it.id == row.id }
        }
    }

    /** Forget this relay entirely, leaving the app as it was on first run. */
    fun reset() {
        store.clear()
        registered = false
        relayUrl = ""
        receivers = emptyList()
        message = "This phone is no longer registered with a relay."
    }

    private fun run(scope: CoroutineScope, success: String?, work: suspend () -> Unit) {
        if (busy) return
        busy = true
        scope.launch {
            try {
                withContext(Dispatchers.IO) { work() }
                if (success != null) message = success
            } catch (e: RelayException) {
                message = e.message
            } catch (e: Exception) {
                message = "Something went wrong: ${e.message}"
            } finally {
                busy = false
            }
        }
    }
}
