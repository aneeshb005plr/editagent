"""
app/schema/finding.py

Phase 3A: stable finding identity, separate from the review engine's
canonical, in-memory-only Finding (app/review/models.py). Keeps the
same "what the engine computes" vs "what gets persisted/retrieved"
distinction already established throughout this project (e.g.
StagedUpload vs the raw upload bytes).

PersistedFinding carries every field the canonical Finding has,
unchanged, PLUS three stable-identity fields assigned exactly ONCE,
at save time (see app/jobs/findings_repository.py's save_findings()):

- finding_uid: a durable internal identifier (UUID4). Globally
  unique. Not derived from content - a UUID is simpler and
  sufficient here since findings are never deduplicated/re-saved
  across multiple save_findings() calls for the same job in the
  current design (each job's findings are inserted exactly once,
  during that job's single worker pass).

- display_id: e.g. "F-0001" - the human/action-contract-facing
  identifier (matches the ChatAction examples in the Phase 3 doc:
  ChatAction(type="view_finding", finding_id="F-0012")).

- display_order: the stable, persisted sort position. Assigned from
  the order the review engine originally returned findings in -
  never recomputed from retrieval order, and every query explicitly
  sorts by this field rather than relying on Mongo's natural/
  insertion order (which the Phase 3 doc explicitly says not to
  depend on).

display_id is deterministically derived FROM display_order at save
time (display_order=0 -> "F-0001"), so the two are always
consistent by construction - not two independently-settable fields
that could drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.rules.schema import DetectionType, RuleCategory


class PersistedFinding(BaseModel):
    job_id: str
    finding_uid: str
    display_id: str
    display_order: int
    rule_id: str
    category: RuleCategory
    detection_type: DetectionType
    location_display: str
    original_text: str
    explanation: str
    suggested_rewrite: str | None = None
    source_reference: str = ""


class ListFindingsResult(BaseModel):
    findings: list[PersistedFinding]
    next_cursor: str | None
    total_matching: int
    # total_matching reflects the full filter (category/rule_id/
    # location), independent of pagination position - not "remaining
    # from cursor". Matches the Phase 3 doc's "Showing 1-5 of 23" UX.