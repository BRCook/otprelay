// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import com.goterl.lazysodium.LazySodiumAndroid
import com.goterl.lazysodium.SodiumAndroid
import com.goterl.lazysodium.interfaces.Box
import java.util.Base64

/**
 * Sealed boxes, the sending half of the scheme in docs/PROTOCOL.md.
 *
 * A sealed box uses a fresh ephemeral key pair for every message, so only the
 * receiver that owns the public key can open it, and this app cannot read its
 * own ciphertext afterwards.
 */
object Crypto {
    private val sodium = LazySodiumAndroid(SodiumAndroid())

    /** Encrypt [plaintext] for [publicKey], returning standard base64 with padding. */
    fun seal(publicKey: ByteArray, plaintext: ByteArray): String {
        require(publicKey.size == Box.PUBLICKEYBYTES) {
            "a receiver public key must be ${Box.PUBLICKEYBYTES} bytes, got ${publicKey.size}"
        }
        val ciphertext = ByteArray(plaintext.size + Box.SEALBYTES)
        check(sodium.cryptoBoxSeal(ciphertext, plaintext, plaintext.size.toLong(), publicKey)) {
            "libsodium could not seal the message"
        }
        return Base64.getEncoder().encodeToString(ciphertext)
    }

    /** Decode the unpadded base64url used for keys and secrets in pairing links. */
    fun decodeB64Url(text: String): ByteArray = Base64.getUrlDecoder().decode(text)

    fun encodeB64(data: ByteArray): String = Base64.getEncoder().encodeToString(data)

    fun decodeB64(text: String): ByteArray = Base64.getDecoder().decode(text)
}
