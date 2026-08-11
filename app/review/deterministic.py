"""
app/review/deterministic.py

Runs every DETERMINISTIC rule against every block via regex, no LLM
call - the cheap, instant first pass. Also houses run_lexical_rules()
- LEXICAL rules are a SEPARATE cheap path: a literal trigger-term
match with a fixed message, no regex involved.

One Finding per (rule, block) pair, not one per regex match occurrence.
"""

from __future__ import annotations

import logging
import re

from app.documents.base import ContentBlock
from app.review.models import Finding
from app.rules.schema import Rule

logger = logging.getLogger("app.review.deterministic")


def run_lexical_rules(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
) -> list[Finding]:
    findings: list[Finding] = []

    for block in blocks:
        text_lower = block.text.lower()
        for rule in rules:
            if not rule.trigger_terms:
                logger.warning(
                    "Lexical rule %s has no trigger_terms - skipping", rule.rule_id
                )
                continue

            matched_terms = [t for t in rule.trigger_terms if t.lower() in text_lower]
            if not matched_terms:
                continue

            explanation = rule.explanation or rule.description
            if len(matched_terms) > 1:
                explanation = f"{explanation} (matched: {', '.join(matched_terms)})"

            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    detection_type=rule.detection_type,
                    location_display=block.location.display(),
                    original_text=matched_terms[0],
                    explanation=explanation,
                    suggested_rewrite=rule.alternative,
                    source_reference=rule.source_reference,
                )
            )

    logger.info(
        "Lexical pass: %d rules x %d blocks -> %d findings",
        len(rules), len(blocks), len(findings),
    )

    return findings


def run_deterministic_rules(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
) -> list[Finding]:
    findings: list[Finding] = []

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
                rule.rule_id, exc_info=True,
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
        len(compiled), len(blocks), len(findings),
    )

    return findings