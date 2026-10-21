// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * These run on the desktop (Run 'CodeExtractorTest'), with no device needed,
 * because CodeExtractor is plain Kotlin.
 *
 * Add a case here whenever a real message is missed or a false positive turns
 * up; it is much faster than retesting on a phone.
 */
class CodeExtractorTest {

    private fun assertCode(expected: String?, body: String) =
        assertEquals(body, expected, CodeExtractor.extract(body))

    @Test
    fun findsCodesInRealMessages() {
        assertCode(
            "018777",
            "Please use 018777 as your 2-Step verification code. We will never contact " +
                "you for this code. Do not reveal it to anyone else."
        )
        assertCode(
            "856980",
            "Do not share this code with anyone. CIBC will never call you for this code. " +
                "Enter code: 856980 to authorize sign on to online banking."
        )
        // A trailing app hash (used by Android's SMS Retriever) must not win.
        assertCode(
            "624950",
            "Do not share this code with anyone. CIBC will never call you for this code. " +
                "Enter code: 624950 to authorize sign on to online banking. oj8q54qM2Vm"
        )
        assertCode("123456", "G-123456 is your Google verification code")
        assertCode("4821", "Your PIN is 4821")
        assertCode("482913", "482913 is your verification code for Example.")
        assertCode("123456", "Use 123456 to log in to Example. Expires in 10 minutes.")
        assertCode("93718", "Your one-time passcode is 93718.")
        assertCode("ABCD12", "Use code ABCD12 at checkout")
        assertCode("123456", "Your code is 123456, valid for 10 minutes")
    }

    @Test
    fun ignoresTextDirectionMarksAndroidAdds() {
        assertCode("624950", "\u2068242227\u2069 Your code is 624950")
    }

    @Test
    fun ignoresMessagesWithoutACodeWord() {
        assertCode(null, "Your order 123456 shipped")
        assertCode(null, "Your package 1Z999AA10123456784 will arrive Oct 20")
        assertCode(null, "Reply STOP to unsubscribe")
        assertCode(null, "Meeting moved to 2026-10-20, see you then")
    }

    @Test
    fun ignoresNumbersThatAreNotCodes() {
        assertCode(null, "Payment of \$50.00 received from John")
        assertCode(null, "Your balance is \$1,234.56 as of today")
        assertCode(null, "Your Uber arrives at 12:30")
        assertCode(null, "Copyright 2024 Example Inc. All rights reserved.")
        assertCode(null, "Battery at 85% now")
        assertCode(null, "Version 1.2345 is available")
    }

    @Test
    fun picksTheCodeRatherThanOtherNumbersNearby() {
        assertCode("7781", "Your appointment in 2026 is confirmed. Code 7781 at the desk.")
        assertCode("889900", "Pay 12.50 using code 889900.")
    }

    @Test
    fun usesTheKeywordListItIsGiven() {
        val withSpanish = CodeExtractor.DEFAULT_KEYWORDS + "clave"
        assertEquals("445566", CodeExtractor.extract("Su clave es 445566", withSpanish))
        // The same message means nothing without that word in the list.
        assertEquals(null, CodeExtractor.extract("Su clave es 445566"))
        assertEquals(null, CodeExtractor.extract("Your code is 123456", listOf("clave")))
    }

    @Test
    fun treatsSpacesAndHyphensInAPhraseAsInterchangeable() {
        for (wording in listOf("one-time", "one time", "onetime")) {
            assertEquals("1234", CodeExtractor.extract("Your $wording passcode is 1234"))
        }
    }

    @Test
    fun aWordWithRegexCharactersMatchesLiterally() {
        val odd = listOf("c.de")
        assertEquals("123456", CodeExtractor.extract("your c.de is 123456", odd))
        assertEquals(null, CodeExtractor.extract("your cXde is 123456", odd))
    }

    @Test
    fun anEmptyKeywordListDetectsNothing() {
        assertEquals(null, CodeExtractor.extract("Your code is 123456", emptyList()))
    }

    @Test
    fun handlesEmptyInput() {
        assertCode(null, "")
        assertEquals(null, CodeExtractor.extract(null))
    }
}
