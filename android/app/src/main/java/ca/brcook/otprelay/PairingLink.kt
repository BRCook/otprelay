// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import java.net.URLDecoder
import java.util.Base64

/** A receiver's pairing offer, read from the QR code it displays. */
data class PairingLink(
    val relay: String,
    /** The receiver's X25519 public key, re-encoded as standard base64 for storage. */
    val publicKey: String,
    val secret: String,
    val name: String,
    val platform: String,
)

/**
 * Reads `otprelay://pair?...` links (see docs/PROTOCOL.md).
 *
 * Parsed by hand rather than with android.net.Uri so it can be unit tested on
 * the desktop. Anything malformed is rejected with a message worth showing to
 * the person holding the phone.
 */
object PairingLinkParser {

    private const val PREFIX = "otprelay://pair?"
    private val PLATFORMS = setOf("linux", "windows", "macos")

    /**
     * @param expectedRelay the relay this phone is registered with; a link for
     *   any other relay is refused, since codes must never leave it.
     */
    fun parse(text: String, expectedRelay: String): Result<PairingLink> {
        val trimmed = text.trim()
        if (!trimmed.startsWith(PREFIX)) {
            return failure("That QR code is not an otprelay pairing code.")
        }
        val params = mutableMapOf<String, String>()
        for (pair in trimmed.removePrefix(PREFIX).split("&")) {
            val index = pair.indexOf('=')
            if (index <= 0) continue
            val key = decode(pair.substring(0, index)) ?: continue
            val value = decode(pair.substring(index + 1)) ?: return failure("That QR code is damaged.")
            params[key] = value
        }

        if (params["v"] != "1") {
            return failure("That receiver uses a newer pairing format. Update this app.")
        }
        val relay = (params["u"] ?: return failure("That QR code names no relay."))
            .trimEnd('/')
        if (relay != expectedRelay.trimEnd('/')) {
            return failure("That receiver belongs to a different relay ($relay).")
        }
        val secret = params["s"].orEmpty()
        if (secret.length < 32) return failure("That QR code is missing its pairing secret.")

        val keyBytes = runCatching { Base64.getUrlDecoder().decode(params["k"].orEmpty()) }
            .getOrElse { return failure("That QR code has an unreadable key.") }
        if (keyBytes.size != 32) {
            return failure("That QR code has a key of the wrong size.")
        }

        val platform = params["p"].orEmpty().lowercase()
        return Result.success(
            PairingLink(
                relay = relay,
                publicKey = Base64.getEncoder().encodeToString(keyBytes),
                secret = secret,
                name = params["n"].orEmpty().ifBlank { "Desktop" }.take(64),
                platform = if (platform in PLATFORMS) platform else "unknown",
            )
        )
    }

    private fun decode(value: String): String? =
        runCatching { URLDecoder.decode(value, "UTF-8") }.getOrNull()

    private fun failure(message: String): Result<PairingLink> =
        Result.failure(IllegalArgumentException(message))
}
