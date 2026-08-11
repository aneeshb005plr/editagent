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
) -> list[Finding]:
    """english_variant defaults to US. Passing GLOBAL requires the
    caller to have actually asked the user at intake which variant
    the document targets - nothing here infers it automatically."""

    applicable_rules = rule_set.for_applies_to(applies_to)
    applicable_rules = tuple(
        r for r in applicable_rules
        if r.english_variant is None or r.english_variant == english_variant
    )

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
        parsed.source_filename, len(parsed.blocks), applies_to.value,
        len(deterministic_rules), len(lexical_rules), len(judgment_rules), len(consistency_rules),
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
        parsed.source_filename, len(findings),
    )

    return findings