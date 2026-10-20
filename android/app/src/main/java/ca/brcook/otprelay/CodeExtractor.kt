// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import kotlin.math.abs

/**
 * Finds a one-time code in notification text.
 *
 * Only the body is searched, never the title: the title is usually the sender,
 * and senders are often short codes made of digits, which would otherwise look
 * exactly like a passcode.
 *
 * Plain Kotlin with no Android imports, so it can be unit tested on a desktop.
 */
object CodeExtractor {

    /** Words that mark a message as being about a code. Without one, nothing is sent. */
    private val KEYWORDS = Regex(
        "(?i)\\b(code|otp|passcode|pass code|password|pin|2fa|mfa|verification|verify|" +
            "authenticate|authentication|token|one[ -]?time|sign[ -]?in|sign[ -]?on|" +
            "log[ -]?in|login)\\b"
    )

    /**
     * A candidate code: 4 to 8 letters and digits standing on their own, with at
     * least one digit. An optional single-letter prefix in the style Google uses
     * ("G-123456") is dropped, since only the digits are the code.
     */
    private val CANDIDATE =
        Regex("(?<![\\p{L}\\p{N}])(?:[A-Za-z]-)?([A-Za-z0-9]{4,8})(?![\\p{L}\\p{N}])")

    /** Characters Android inserts around text runs, which would break matching. */
    private val INVISIBLE = Regex("[\u2066-\u2069\u200E\u200F\u00A0]")

    private val LOOKS_LIKE_YEAR = Regex("^(19|20)\\d{2}$")

    /**
     * Returns the code in [body], or null if there is none.
     *
     * [body] should be the notification's text, not its title.
     */
    fun extract(body: String?): String? {
        if (body.isNullOrBlank()) return null
        val text = INVISIBLE.replace(body, "")
        val keywords = KEYWORDS.findAll(text).map { it.range.first }.toList()
        if (keywords.isEmpty()) return null

        var best: String? = null
        var bestScore = Int.MIN_VALUE
        for (match in CANDIDATE.findAll(text)) {
            val candidate = match.groupValues[1]
            if (!isPlausible(candidate, text, match.range)) continue
            val score = score(candidate, match.range.first, keywords)
            if (score > bestScore) {
                bestScore = score
                best = candidate
            }
        }
        return best
    }

    private fun isPlausible(candidate: String, text: String, range: IntRange): Boolean {
        if (candidate.none { it.isDigit() }) return false
        // A year on its own is almost always part of a date or a copyright line.
        if (LOOKS_LIKE_YEAR.matches(candidate)) return false

        val before = text.getOrNull(range.first - 1)
        val beforeThat = text.getOrNull(range.first - 2)
        val after = text.getOrNull(range.last + 1)
        val afterThat = text.getOrNull(range.last + 2)

        // Inside a longer number: 1,234 or 12.50 or 10/2026. A trailing full stop
        // or comma is just punctuation, so only digits on the far side disqualify it.
        if ((before == ',' || before == '.' || before == '/') && beforeThat?.isDigit() == true) return false
        if ((after == ',' || after == '.' || after == '/') && afterThat?.isDigit() == true) return false
        if (after == '%') return false
        // Money is not a code.
        if (before == '$' || before == '\u00A3' || before == '\u20AC' || before == '\u00A5') return false
        // A time (12:30) or a date or range (2026-10-20, 100-200), but not "code:123456".
        if (before == ':' && beforeThat?.isDigit() == true) return false
        if (after == ':' && afterThat?.isDigit() == true) return false
        if (before == '-' && beforeThat?.isDigit() == true) return false
        if (after == '-' && afterThat?.isDigit() == true) return false
        return true
    }

    /**
     * Prefers candidates near a keyword, then six-digit codes (much the most
     * common length), then all-digit codes.
     */
    private fun score(candidate: String, position: Int, keywordPositions: List<Int>): Int {
        val distance = keywordPositions.minOf { abs(it - position) }
        var score = 1000 - distance.coerceAtMost(1000)
        if (candidate.length == 6) score += 50
        if (candidate.all { it.isDigit() }) score += 25
        return score
    }
}
