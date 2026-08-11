"""
app/rules/validators.py

Match validators for DETERMINISTIC rules whose regex pattern alone
can't express the real condition - see Rule.match_validator's
docstring in schema.py for when this is actually warranted (rare;
most rules should stay pure pattern matching).

Kept in a separate module from taxonomy.py deliberately: taxonomy.py
is meant to stay purely declarative data (Rule() calls), and the
small amount of real logic these validators need shouldn't be mixed
into that file's structure.
"""

from __future__ import annotations

import re


def is_ascending_range(match: re.Match) -> bool:
    """For numbers-range-should-use-en-dash: confirmed via real
    production testing against an actual audit RFP that the plain
    \\d+-\\d+ pattern flagged phone-number-shaped fragments like
    "858-677" as if they were genuine numeric ranges. A real range
    is almost never expressed in descending order ("pages 858-677"
    is not a thing anyone writes) - checking that the first number is
    smaller than the second is a cheap, real signal this is a
    genuine range rather than some other digit-hyphen-digit pattern
    (phone fragment, ID number, etc.).

    NOT a complete fix: an ascending phone-number-shaped fragment
    (e.g. "499-5701", confirmed to also appear in the same real test
    document) still passes this check and will still be flagged -
    this heuristic only catches the descending case, which is the
    one we have concrete confirmed evidence for. A full fix would
    need this to be a JUDGMENT rule instead, trading the free
    deterministic check for LLM-based context understanding - not
    done here since the ascending-only fix is a real, free
    improvement on its own and doesn't preclude revisiting this
    later if the remaining false-positive rate turns out to matter.
    """

    try:
        first, second = int(match.group(1)), int(match.group(2))
    except (ValueError, IndexError):
        # Pattern didn't have exactly two numeric capture groups -
        # degrade to "accept the match" rather than crash the whole
        # deterministic pass over one malformed validator usage.
        return True

    return first < second