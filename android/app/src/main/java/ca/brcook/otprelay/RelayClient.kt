// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** A relay call that failed. [status] is 0 when the request never got a reply. */
class RelayException(val status: Int, message: String) : IOException(message)

/** One receiver as the relay reports it. Display only; keys never come from here. */
data class RemoteReceiver(
    val id: String,
    val name: String,
    val platform: String,
    val lastSeen: Long,
    val claimed: Boolean,
    val online: Boolean,
)

data class SendResult(val sent: List<String>, val queued: List<String>, val unknown: List<String>)

/**
 * The source half of docs/PROTOCOL.md. Every call blocks, so callers run them
 * off the main thread.
 */
class RelayClient(private val store: Store) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /** Redeem an invite and remember the token this phone gets back. */
    fun register(relayUrl: String, invite: String, name: String) {
        val url = relayUrl.trimEnd('/')
        require(url.startsWith("https://")) { "The relay address must start with https://" }
        val body = JSONObject().put("invite", invite)
        val reply = call("POST", "$url/api/register", body, token = null)
        store.relayUrl = url
        store.sourceId = reply.getString("source_id")
        store.sourceToken = reply.getString("source_token")
        store.sourceName = name
    }

    fun listReceivers(): List<RemoteReceiver> {
        val reply = call("GET", "${base()}/api/receivers", null)
        val array = reply.optJSONArray("receivers") ?: JSONArray()
        val list = (0 until array.length()).map { i ->
            val o = array.getJSONObject(i)
            RemoteReceiver(
                id = o.getString("id"),
                name = o.optString("name", "Desktop"),
                platform = o.optString("platform", "unknown"),
                lastSeen = o.optLong("last_seen", 0L),
                claimed = o.optBoolean("claimed", false),
                online = o.optBoolean("online", false),
            )
        }
        store.forgetMissing(list.map { it.id })
        return list
    }

    /** Register a receiver this phone has just scanned. Returns its relay id. */
    fun addReceiver(pairingSecret: String): String {
        val body = JSONObject()
            .put("pairing_secret", pairingSecret)
        return call("POST", "${base()}/api/receivers", body).getString("receiver_id")
    }

    fun removeReceiver(id: String) {
        call("DELETE", "${base()}/api/receivers/$id", null)
        store.removeReceiver(id)
    }

    /** Encrypt [plaintext] once per receiver and hand the copies to the relay. */
    fun send(plaintext: ByteArray): SendResult {
        val receivers = store.receivers()
        if (receivers.isEmpty()) return SendResult(emptyList(), emptyList(), emptyList())
        val messages = JSONArray()
        for (receiver in receivers) {
            if (receiver.authKey.isEmpty()) continue  // paired before sender auth; needs re-pairing
            val ciphertext = Crypto.seal(Crypto.decodeB64(receiver.publicKey), plaintext)
            messages.put(
                JSONObject()
                    .put("receiver_id", receiver.id)
                    .put("ciphertext", ciphertext)
                    .put("mac", Crypto.mac(receiver.authKey, ciphertext))
            )
        }
        val reply = call("POST", "${base()}/api/send", JSONObject().put("messages", messages))
        val result = SendResult(
            sent = reply.strings("sent"),
            queued = reply.strings("queued"),
            unknown = reply.strings("unknown"),
        )
        // The relay says these no longer exist, so stop encrypting for them.
        result.unknown.forEach(store::removeReceiver)
        return result
    }

    // --- plumbing -----------------------------------------------------------

    private fun base(): String {
        check(store.isRegistered) { "this phone is not registered with a relay yet" }
        return store.relayUrl
    }

    private fun call(
        method: String,
        url: String,
        body: JSONObject?,
        token: String? = store.sourceToken,
    ): JSONObject {
        val request = Request.Builder()
            .url(url)
            .method(method, body?.toString()?.toRequestBody(JSON))
            .apply { if (!token.isNullOrEmpty()) header("Authorization", "Bearer $token") }
            .build()
        val response = try {
            http.newCall(request).execute()
        } catch (e: IOException) {
            throw RelayException(0, "Could not reach the relay: ${e.message}")
        }
        response.use {
            val text = it.body?.string().orEmpty()
            val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
            if (!it.isSuccessful) {
                val detail = json.optString("error").ifEmpty { "HTTP ${it.code}" }
                throw RelayException(it.code, detail)
            }
            return json
        }
    }

    private fun JSONObject.strings(key: String): List<String> {
        val array = optJSONArray(key) ?: return emptyList()
        return (0 until array.length()).map { array.getString(it) }
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
