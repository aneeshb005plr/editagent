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
from app.review.matching import find_term_match
from app.review.models import Finding
from app.rules.schema import Rule

logger = logging.getLogger("app.review.deterministic")


def run_lexical_rules(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
) -> list[Finding]:
    """rules should already be filtered to LEXICAL-only and to the
    applicable AppliesTo set (caller's responsibility). Bounded,
    case-insensitive match against trigger_terms via
    app.review.matching (fixed real bug: was naive substring
    matching, which candidate-matched inside unrelated longer
    words) - no regex pattern beyond that, no LLM. Fixed message per
    rule (source guidance for these is unconditional - see
    DetectionType.LEXICAL's docstring)."""

    findings: list[Finding] = []

    for block in blocks:
        for rule in rules:
            if not rule.trigger_terms:
                logger.warning(
                    "Lexical rule %s has no trigger_terms - skipping", rule.rule_id
                )
                continue

            matches = [
                (term, m)
                for term in rule.trigger_terms
                if (m := find_term_match(term, block.text)) is not None
            ]
            if not matches:
                continue

            # Use the ACTUAL matched substring from the document's own
            # text (m.group(0)), not the trigger term's own casing -
            # fixed real bug: previously stored the lowercased/as-
            # written trigger term regardless of how it actually
            # appeared in the document (e.g. "Customer" in the
            # document would have been reported as "customer").
            first_term, first_match = matches[0]
            explanation = rule.explanation or rule.description
            if len(matches) > 1:
                matched_texts = ", ".join(m.group(0) for _, m in matches)
                explanation = f"{explanation} (matched: {matched_texts})"

            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    detection_type=rule.detection_type,
                    location_display=block.location.display(),
                    original_text=first_match.group(0),
                    explanation=explanation,
                    suggested_rewrite=rule.alternative,
                    source_reference=rule.source_reference,
                )
            )

    logger.info(
        "Lexical pass: %d rules x %d blocks -> %d findings",
        len(rules),
        len(blocks),
        len(findings),
    )

    return findings


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