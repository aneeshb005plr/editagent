"""
app/rules/schema.py

The structured representation every curated rule (from the style
guide, Grammatical Topics.docx, etc.) gets normalized into. This is
the taxonomy's data model - app/rules/taxonomy.py is the actual
curated content built against this shape.

Deliberately plain dataclasses (frozen), NOT Pydantic: this is
static, code-authored data loaded once at startup/first use, never
constructed from untrusted input - same reasoning as app/documents/
base.py. If a rule set is ever loaded from an external/admin-managed
source in a later phase (see architecture doc's deferred items),
that boundary is where Pydantic validation would be added - not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class RuleCategory(str, Enum):
    GRAMMAR = "grammar"
    PUNCTUATION = "punctuation"
    CAPITALIZATION = "capitalization"
    # RESERVED, not actively used: gram-capitalization-consistency was
    # moved to CONSISTENCY (it's a document-level check - a single
    # block can't answer "is this capitalized the same way elsewhere
    # in the document"). Kept as an enum value rather than deleted in
    # case a genuinely per-block capitalization rule is added later
    # (e.g. "titles use sentence case" - a self-contained, single-
    # block-answerable check, unlike consistency).
    NUMBERS_FORMATTING = "numbers_formatting"
    RISK_LANGUAGE = "risk_language"
    AUDIENCE_SENSITIVITY = "audience_sensitivity"
    BRAND_VOICE = "brand_voice"
    CONSISTENCY = "consistency"
    # Document-level only (terminology drift, duplicate content) -
    # rules in this category are NOT run per-block by the engine's
    # per-block passes; they're handled by the document-level pass
    # described in the architecture doc. Included in the taxonomy for
    # completeness/documentation even though execution differs.


class DetectionType(str, Enum):
    DETERMINISTIC = "deterministic"
    # Regex/lookup - cheap, instant, no LLM call. Runs on every block
    # first, always - the primary lever for cost/latency control at
    # 100MB scale (see review engine design discussion).
    LEXICAL = "lexical"
    # A keyword/phrase match that is UNCONDITIONALLY restricted per
    # the source (the alternative column says "avoid using" / "delete"
    # with no "when..." qualifier) - fixed message, no LLM round-trip,
    # no regex sense-check needed because there IS no sense to judge:
    # the word itself is the problem regardless of context. Distinct
    # from DETERMINISTIC in that it's a literal term match rather than
    # a structural pattern (dashes, digit grouping), but shares the
    # same "no LLM needed" cost profile. Rule of thumb for classifying
    # a restricted-word rule as LEXICAL vs JUDGMENT: if the source's
    # own alternative-language column says "avoid using" or "delete"
    # unconditionally, it's LEXICAL; if it says "may be acceptable
    # when..." or "avoid when describing PwC's services" (implying
    # other uses ARE fine), it's JUDGMENT.
    JUDGMENT = "judgment"
    # Requires LLM reasoning about sense/context - confirmed necessary
    # for most of Appendix B and most grammar rules (subject-verb
    # agreement, tense drift, etc.) - these cannot be reliably caught
    # by pattern matching alone.


class EnglishVariant(str, Enum):
    US = "us"
    GLOBAL = "global"
    # A rule with english_variant=None (the default, see Rule below)
    # applies regardless of which variant the document targets - this
    # covers the vast majority of the taxonomy so far, which has been
    # implicitly US-oriented but doesn't actually conflict with
    # Global English usage. Only rules that are TRUE OPPOSITES between
    # the two variants (e.g. "the government have" is correct Global
    # English, incorrect US English) need an explicit variant tag -
    # applying those blindly without knowing the target would flag
    # correct usage as wrong exactly as often as it catches real
    # errors. See Rule.english_variant's docstring for how this gets
    # resolved (an intake question, same pattern as AppliesTo).


class AppliesTo(str, Enum):
    GENERAL = "general"
    AUDIT = "audit"
    # "audit" rules are ADDITIONAL to general rules for audit/Trust
    # Solutions proposals, and can directly CONTRADICT a general rule
    # (e.g. "assist/collaborate" are fine generally, restricted in
    # audit proposals - confirmed from real Appendix B content).
    # Resolved once per review, at intake, by asking the user whether
    # the document is an audit/assurance proposal - NOT auto-detected.


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: RuleCategory
    detection_type: DetectionType
    applies_to: AppliesTo

    description: str
    # What this rule checks for - written to be usable BOTH as
    # human-readable documentation AND as part of the prompt fed to
    # the LLM for judgment-type rules. Should describe the rule
    # clearly enough to stand alone in a prompt.

    # --- Deterministic-only fields (None for judgment rules) ---
    pattern: str | None = None
    # A regex pattern, for DETERMINISTIC rules only.

    match_validator: Callable[[re.Match], bool] | None = None
    # OPTIONAL, DETERMINISTIC-only: a post-match filter for cases pure
    # regex can't express - confirmed real need from production
    # testing against a real audit RFP: numbers-range-should-use-
    # en-dash's pattern (\d+-\d+) can't tell a genuine ascending range
    # ("pages 15-22") from a phone-number-shaped fragment ("858-677",
    # descending - real numbers ARE almost never expressed as a
    # descending "range"). Regex can match the shape but can't compare
    # the two numbers' magnitudes - that needs actual code. When set,
    # a regex match is only kept if match_validator(match) returns
    # True; when None (the default, true for nearly every rule), any
    # regex match is accepted as before. Kept as a narrow escape
    # hatch, not a general mechanism to lean on - most deterministic
    # rules should stay pure pattern matching; reach for this only
    # when a real false-positive pattern is confirmed, the same way
    # this one was.

    # --- Judgment-only fields (empty for deterministic rules) ---
    trigger_terms: tuple[str, ...] = field(default_factory=tuple)
    # For JUDGMENT rules keyed to specific words/phrases (most of
    # Appendix B): the literal terms that make a block a CANDIDATE
    # for this rule's LLM check. This is still a real optimization,
    # not a contradiction of "judgment needs the LLM" - a block
    # containing none of these terms can skip this rule's LLM check
    # entirely; a block containing one still needs the LLM to decide
    # whether it's used in the restricted SENSE. Empty for rules with
    # no useful keyword pre-filter (e.g. subject-verb agreement,
    # which can appear anywhere).

    alternative: str | None = None
    # Suggested replacement wording, where the source provides one.

    explanation: str | None = None
    # WHY this matters - fed into the finding's explanation field
    # shown to the end user. Distinct from `description` (which is
    # instruction-oriented, for the LLM) - this is user-facing.

    example_before: str | None = None
    example_after: str | None = None

    source_reference: str = ""
    # Human-readable citation ONLY (e.g. "Style Guide, Appendix B,
    # p.89" or "Grammatical Topics.docx, PBI 782913") - NEVER derived
    # from any parser's page_number/paragraph_index. A parser's
    # page_number is physical document order, which can diverge from
    # a document's own PRINTED page numbers (cover pages, TOCs,
    # restarted numbering) - this field exists so a human curator (or
    # a reviewing Claude instance with the real source files) can go
    # verify the rule against what they actually see on the page,
    # independent of how any of our parsers count pages internally.
    #
    # KNOWN LIMITATION, confirmed by direct review against the org's
    # own file access: the style guide's Table of Contents and the
    # printed footer on Appendix B's own intro page may disagree on
    # what page Appendix B starts at. This field intentionally cites
    # the PRINTED FOOTER (what a human actually flips to), not the
    # TOC - noted here as the chosen convention rather than silently
    # picking one without explanation.

    pcs_exception: bool = False
    # PCS (Private Company Services) carve-out: the style guide marks
    # certain AUDIT-restricted terms as acceptable specifically in
    # PCS audit proposals (an asterisked exception in Appendix B).
    # CONFIRMED directly against real source content (p.95-96): the
    # exception applies to exactly two rows - advisor/business
    # advisor/trusted advisor/business insights/business perspective,
    # and collaborate/collaborative - no other AUDIT rule carries this
    # flag. At intake, a real implementation needs a SECOND follow-up
    # question beyond "is this an audit proposal?" - "is it
    # specifically a PCS/private-company audit?" - to know when to
    # suppress pcs_exception=True rules (see RuleSet.for_applies_to_
    # with_pcs()).

    english_variant: EnglishVariant | None = None
    # None (default) means "applies regardless of target English
    # variant" - true for almost every rule in this taxonomy, since
    # US-oriented style guidance mostly doesn't conflict with Global
    # English (e.g. "avoid unsubstantiated superlatives" is true
    # either way). Set explicitly to EnglishVariant.GLOBAL or .US only
    # for rules that are genuine OPPOSITES between the two variants
    # (confirmed real examples: "agree" taking a preposition in US
    # English but not Global; collective nouns preferring a plural
    # verb in Global English; day/month/year date order for Global;
    # no serial comma by default in Global). Applying a variant-
    # specific rule without knowing the document's actual target
    # would flag CORRECT usage as an error exactly as often as it
    # catches a real one - this field exists so the review engine can
    # gate these rules behind a real intake answer (mirroring how
    # AppliesTo.AUDIT is gated behind "is this an audit proposal?"),
    # not a guess. As of this taxonomy version, NOTHING in the
    # engine's default call path selects EnglishVariant.GLOBAL rules -
    # they're populated and ready, but inert until that intake
    # question is actually built.


@dataclass(frozen=True)
class RuleSet:
    """The full curated taxonomy - what gets loaded once and handed
    to the review engine."""

    rules: tuple[Rule, ...]

    def for_category(self, category: RuleCategory) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.category == category)

    def for_applies_to(self, applies_to: AppliesTo) -> tuple[Rule, ...]:
        # GENERAL rules always apply; AUDIT rules apply ONLY when
        # applies_to == AUDIT was requested - confirmed from Appendix
        # B that audit rules are ADDITIVE/overriding, not a full
        # replacement of general rules.
        if applies_to == AppliesTo.GENERAL:
            return tuple(r for r in self.rules if r.applies_to == AppliesTo.GENERAL)
        return tuple(
            r
            for r in self.rules
            if r.applies_to in (AppliesTo.GENERAL, AppliesTo.AUDIT)
        )

    def deterministic(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.detection_type == DetectionType.DETERMINISTIC)

    def lexical(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.detection_type == DetectionType.LEXICAL)

    def judgment(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.detection_type == DetectionType.JUDGMENT)

    def for_applies_to_with_pcs(
        self, applies_to: AppliesTo, is_pcs: bool = False
    ) -> tuple[Rule, ...]:
        """Same as for_applies_to(), with the PCS carve-out applied:
        when is_pcs=True, AUDIT rules flagged pcs_exception=True are
        excluded (they're normally audit-restricted but explicitly
        permitted for PCS audit proposals per the style guide - see
        Rule.pcs_exception's docstring on why the actual rule-by-rule
        flags aren't populated yet)."""

        base = self.for_applies_to(applies_to)
        if not is_pcs:
            return base
        return tuple(r for r in base if not r.pcs_exception)

    def for_english_variant(self, variant: EnglishVariant) -> tuple[Rule, ...]:
        """Rules with english_variant=None apply regardless of the
        target variant (the vast majority); rules with an explicit
        variant only apply when it matches. See Rule.english_variant's
        docstring - as of this taxonomy version, nothing in the
        engine's default call path passes EnglishVariant.GLOBAL, so
        GLOBAL-tagged rules are populated but currently inert."""

        return tuple(
            r for r in self.rules if r.english_variant is None or r.english_variant == variant
        )

    def validate(self) -> None:
        """Startup-time validation - catches curator errors (a rule
        with an inconsistent field combination, a duplicate id, a
        missing citation) at import/deploy time rather than silently
        producing wrong behavior the first time a real document hits
        that rule. Called once at the bottom of taxonomy.py, not on
        every review."""

        errors: list[str] = []
        seen_ids: set[str] = set()

        for r in self.rules:
            if r.rule_id in seen_ids:
                errors.append(f"{r.rule_id}: duplicate rule_id")
            seen_ids.add(r.rule_id)

            if not r.source_reference:
                errors.append(f"{r.rule_id}: missing source_reference")

            if r.detection_type == DetectionType.DETERMINISTIC:
                if not r.pattern:
                    errors.append(f"{r.rule_id}: DETERMINISTIC rule must have a pattern")
                if r.trigger_terms:
                    errors.append(
                        f"{r.rule_id}: DETERMINISTIC rule should not set trigger_terms "
                        f"(pattern IS the match logic - trigger_terms is a judgment-rule "
                        f"pre-filter concept and would be silently unused here)"
                    )

            elif r.detection_type == DetectionType.LEXICAL:
                if not r.trigger_terms:
                    errors.append(f"{r.rule_id}: LEXICAL rule must have trigger_terms")
                if r.pattern:
                    errors.append(
                        f"{r.rule_id}: LEXICAL rule should not set pattern "
                        f"(trigger_terms IS the match logic for lexical rules)"
                    )
                if r.match_validator is not None:
                    errors.append(
                        f"{r.rule_id}: LEXICAL rule should not set match_validator "
                        f"(match_validator only applies to regex matches, which LEXICAL "
                        f"rules don't use)"
                    )

            elif r.detection_type == DetectionType.JUDGMENT:
                if r.pattern:
                    errors.append(
                        f"{r.rule_id}: JUDGMENT rule should not set pattern "
                        f"(pattern is unused for judgment rules - if this rule is "
                        f"actually unconditional, it should be LEXICAL or DETERMINISTIC)"
                    )
                if r.match_validator is not None:
                    errors.append(
                        f"{r.rule_id}: JUDGMENT rule should not set match_validator "
                        f"(match_validator only applies to regex matches, which JUDGMENT "
                        f"rules don't use)"
                    )

        if errors:
            raise ValueError(
                "Rule taxonomy validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )