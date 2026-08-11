r"""
app/review/matching.py

Shared term-matching logic for LEXICAL and JUDGMENT trigger_terms,
used by both deterministic.py (run_lexical_rules) and judgment.py
(_select_candidate_rules). Previously each did naive substring
matching (`term.lower() in text_lower`), which caused real, confirmed
false-positive candidate matches: "who" matched inside "whole"/
"wholesale", "less" matched inside "unless"/"nevertheless", "trust"
matched inside "Trust Solutions"/"entrust" - inflating LLM call
volume with irrelevant candidates and diluting prompt quality.

NOT using \b (word boundary) either - confirmed by direct test that
\b fails for trigger terms starting/ending in a non-word character
(e.g. "&" in punc-ampersand-misuse: r'\b&\b' does not match "risk &
capital" at all, since '&' and the surrounding space are BOTH
non-word characters and \b only fires at a word/non-word transition
- the exact same failure class as an earlier bug in the acronym
regex). Using negative lookaround instead - (?<!\w)term(?!\w) -
which correctly handles both ordinary words and symbol/punctuation
trigger terms.
"""

from __future__ import annotations

import re


def find_term_match(term: str, text: str) -> re.Match | None:
    """Case-insensitive match of `term` in `text`, bounded so it
    can't match as a substring of a longer word - but without \\b's
    failure mode on symbol-starting/ending terms. Returns the Match
    (so callers can extract the ACTUAL matched substring, preserving
    the document's real casing, rather than reusing the trigger
    term's own casing) or None."""

    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE)


def term_matches(term: str, text: str) -> bool:
    return find_term_match(term, text) is not None