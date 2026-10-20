// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class PairingLinkTest {

    private val relay = "https://otp.example.com"
    private val key = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 7 })
    private val secret = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 9 })

    private fun link(
        v: String = "1",
        u: String = "https%3A%2F%2Fotp.example.com",
        k: String = key,
        s: String = secret,
        n: String = "My%20Desktop",
        p: String = "linux",
    ) = "otprelay://pair?v=$v&u=$u&k=$k&s=$s&n=$n&p=$p"

    @Test
    fun readsAValidLink() {
        val parsed = PairingLinkParser.parse(link(), relay).getOrThrow()
        assertEquals(relay, parsed.relay)
        assertEquals("My Desktop", parsed.name)
        assertEquals("linux", parsed.platform)
        assertEquals(secret, parsed.secret)
        // Stored as standard base64, which is what the relay and sealed boxes use.
        assertEquals(Base64.getEncoder().encodeToString(ByteArray(32) { 7 }), parsed.publicKey)
    }

    @Test
    fun acceptsATrailingSlashOnEitherSide() {
        assertTrue(PairingLinkParser.parse(link(), "$relay/").isSuccess)
    }

    @Test
    fun refusesALinkForAnotherRelay() {
        val other = PairingLinkParser.parse(link(u = "https%3A%2F%2Fevil.example.com"), relay)
        assertTrue(other.isFailure)
    }

    @Test
    fun refusesAKeyOfTheWrongSize() {
        val short = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(16))
        assertTrue(PairingLinkParser.parse(link(k = short), relay).isFailure)
        assertTrue(PairingLinkParser.parse(link(k = "not base64!"), relay).isFailure)
    }

    @Test
    fun refusesAShortSecret() {
        assertTrue(PairingLinkParser.parse(link(s = "tooshort"), relay).isFailure)
    }

    @Test
    fun refusesAFutureVersionAndOtherText() {
        assertTrue(PairingLinkParser.parse(link(v = "2"), relay).isFailure)
        assertTrue(PairingLinkParser.parse("https://example.com", relay).isFailure)
        assertTrue(PairingLinkParser.parse("", relay).isFailure)
    }

    @Test
    fun fallsBackWhenOptionalPartsAreOdd() {
        val parsed = PairingLinkParser.parse(link(n = "", p = "haiku"), relay).getOrThrow()
        assertEquals("Desktop", parsed.name)
        assertEquals("unknown", parsed.platform)
    }
}
