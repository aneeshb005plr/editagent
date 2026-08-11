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

from dataclasses import dataclass, field
from enum import Enum


class RuleCategory(str, Enum):
    GRAMMAR = "grammar"
    PUNCTUATION = "punctuation"
    CAPITALIZATION = "capitalization"
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
    JUDGMENT = "judgment"
    # Requires LLM reasoning about sense/context - confirmed necessary
    # for most of Appendix B and most grammar rules (subject-verb
    # agreement, tense drift, etc.) - these cannot be reliably caught
    # by pattern matching alone.


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

    def judgment(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.detection_type == DetectionType.JUDGMENT)