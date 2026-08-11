"""
app/review/engine.py

The single entry point tying together app/documents/ (parsing),
app/rules/ (taxonomy), and this package's three rule runners into
one function: review_document().
"""

from __future__ import annotations

import logging

from langchain_core.runnables import Runnable

from app.documents.base import ParsedDocument
from app.review.consistency import run_consistency_pass
from app.review.deterministic import run_deterministic_rules, run_lexical_rules
from app.review.judgment import run_judgment_rules
from app.review.models import Finding
from app.rules.schema import AppliesTo, DetectionType, EnglishVariant, RuleCategory, RuleSet

logger = logging.getLogger("app.review.engine")


async def review_document(
    parsed: ParsedDocument,
    rule_set: RuleSet,
    applies_to: AppliesTo,
    judgment_model: Runnable,
    english_variant: EnglishVariant = EnglishVariant.US,
    is_pcs: bool = False,
) -> list[Finding]:
    """Runs the full review pipeline against an already-parsed
    document (see app.documents.pipeline.parse_and_extract - image
    text should already be extracted into parsed.blocks before this
    is called, so it gets reviewed like any other text).

    judgment_model is the SHARED, UNBOUND GenAI client (from
    app.llm.get_genai_client) - both the judgment and consistency
    passes apply their own with_structured_output() on top of it
    independently.

    applies_to determines which rule subset runs - GENERAL always;
    GENERAL+AUDIT only when the document was confirmed as an audit/
    assurance proposal at intake.

    is_pcs: FIXED REAL BUG - this parameter did not exist until now,
    meaning the entire PCS (Private Company Services) carve-out
    built into the taxonomy (pcs_exception field, RuleSet.
    for_applies_to_with_pcs()) was fully implemented but completely
    unreachable - a PCS audit proposal would have been false-
    positived on "advisor"/"collaborate" with no way to suppress it.
    Confirmed by direct code inspection before this fix. Requires a
    real intake follow-up question ("is this specifically a PCS/
    private-company audit?") once the conversational shell exists -
    this parameter is the wiring point for that answer.

    english_variant defaults to US - matches the taxonomy's implicit
    default (built from a US-oriented style guide). Passing GLOBAL
    requires the caller to have actually asked the user at intake
    which variant the document targets - nothing here infers it
    automatically.
    """

    # FIXED REAL BUG: previously called rule_set.for_applies_to()
    # directly and hand-rolled the english_variant filter inline,
    # bypassing for_applies_to_with_pcs() and for_english_variant()
    # entirely - meaning those two RuleSet methods were dead code and
    # is_pcs had no effect anywhere (it didn't even exist as a
    # parameter). Now actually uses both.
    applicable_rules = rule_set.for_applies_to_with_pcs(applies_to, is_pcs)
    variant_allowed_ids = {r.rule_id for r in rule_set.for_english_variant(english_variant)}
    applicable_rules = tuple(
        r for r in applicable_rules if r.rule_id in variant_allowed_ids
    )

    # CONSISTENCY-category rules need the whole document, not one
    # block at a time - routed to a structurally separate pass.
    consistency_rules = tuple(
        r for r in applicable_rules if r.category == RuleCategory.CONSISTENCY
    )
    per_block_rules = tuple(
        r for r in applicable_rules if r.category != RuleCategory.CONSISTENCY
    )

    deterministic_rules = tuple(
        r for r in per_block_rules if r.detection_type == DetectionType.DETERMINISTIC
    )
    lexical_rules = tuple(
        r for r in per_block_rules if r.detection_type == DetectionType.LEXICAL
    )
    judgment_rules = tuple(
        r for r in per_block_rules if r.detection_type == DetectionType.JUDGMENT
    )

    logger.info(
        "Reviewing %s: %d blocks, applies_to=%s (%d deterministic, %d lexical, "
        "%d judgment, %d consistency rules active)",
        parsed.source_filename,
        len(parsed.blocks),
        applies_to.value,
        len(deterministic_rules),
        len(lexical_rules),
        len(judgment_rules),
        len(consistency_rules),
    )

    findings: list[Finding] = []

    findings.extend(run_deterministic_rules(parsed.blocks, deterministic_rules))
    findings.extend(run_lexical_rules(parsed.blocks, lexical_rules))

    findings.extend(
        await run_judgment_rules(parsed.blocks, judgment_rules, judgment_model)
    )

    findings.extend(
        await run_consistency_pass(parsed.blocks, consistency_rules, judgment_model)
    )

    logger.info(
        "Review complete for %s: %d total findings",
        parsed.source_filename,
        len(findings),
    )

    return findings