"""
app/review/deterministic.py

Runs every DETERMINISTIC rule against every block via regex, no LLM
call - the cheap, instant first pass. Findings from this pass never
touch the network, so this can run on the full document unconditionally
regardless of size/cost budget concerns.

One Finding per (rule, block) pair, not one per regex match occurrence
- Location's granularity is per-block (paragraph/slide/cell/page),
not per-character-offset, so multiple matches within the same block
would all report the identical location; consolidating avoids
near-duplicate findings that point at the same place. The finding's
original_text is the first match; explanation notes the count if
more than one occurrence was found.
"""

from __future__ import annotations

import logging
import re

from app.documents.base import ContentBlock
from app.review.models import Finding
from app.rules.schema import Rule

logger = logging.getLogger("app.review.deterministic")


def run_deterministic_rules(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
) -> list[Finding]:
    """rules should already be filtered to DETERMINISTIC-only and to
    the applicable AppliesTo set (RuleSet.deterministic() combined
    with RuleSet.for_applies_to() - the caller, app/review/engine.py,
    is responsible for that filtering)."""

    findings: list[Finding] = []

    # Pre-compile every pattern once, not per-block - real cost
    # matters at 100MB scale with many blocks.
    compiled: list[tuple[Rule, re.Pattern]] = []
    for rule in rules:
        if not rule.pattern:
            logger.warning(
                "Deterministic rule %s has no pattern - skipping", rule.rule_id
            )
            continue
        try:
            compiled.append((rule, re.compile(rule.pattern, re.IGNORECASE)))
        except re.error:
            logger.error(
                "Deterministic rule %s has an invalid pattern - skipping",
                rule.rule_id,
                exc_info=True,
            )

    for block in blocks:
        for rule, pattern in compiled:
            matches = list(pattern.finditer(block.text))
            if not matches:
                continue

            explanation = rule.explanation or rule.description
            if len(matches) > 1:
                explanation = f"{explanation} ({len(matches)} occurrences in this block)"

            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    detection_type=rule.detection_type,
                    location_display=block.location.display(),
                    original_text=matches[0].group(0),
                    explanation=explanation,
                    suggested_rewrite=rule.alternative,
                    source_reference=rule.source_reference,
                )
            )

    logger.info(
        "Deterministic pass: %d rules x %d blocks -> %d findings",
        len(compiled),
        len(blocks),
        len(findings),
    )

    return findings