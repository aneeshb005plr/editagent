"""
app/jobs/findings_repository.py

Phase 3A additions: stable finding identity assigned at save time,
plus ownership-scoped, bounded query APIs (get_finding, list_findings,
count_findings, count_findings_by_category) - replacing the pattern
of loading every finding for a job into one prompt/response.

save_findings() and get_findings_for_job() (the original, unbounded
function) are UNCHANGED in behavior for existing callers - see this
slice's delivery notes for why get_findings_for_job() itself was
deliberately NOT replaced yet (that's Phase 3D's job, migrating the
actual conversational callers). Confirmed via direct test that
Pydantic v2 silently ignores the new extra fields on documents when
parsed back into the old Finding model, so leaving it as-is doesn't
break anything.

EVERY new function verifies job ownership (job.user_id == user_id)
BEFORE querying findings, directly inside the function - not relying
on callers to remember to check. This is deliberately in the
repository layer itself (see this slice's design-decision notes for
why a separate service-layer wrapper wasn't added on top).
"""

from __future__ import annotations

import uuid

from app.jobs import repository as jobs_repository
from app.review.models import Finding
from app.schema.finding import ListFindingsResult, PersistedFinding

_COLLECTION = "findings"


def _to_persisted_finding(doc: dict) -> PersistedFinding:
    doc = dict(doc)
    doc.pop("_id", None)
    return PersistedFinding(**doc)


async def save_findings(db, job_id: str, findings: list[Finding]) -> None:
    """UNCHANGED signature/callers (worker.py calls this exactly as
    before). NOW assigns finding_uid/display_id/display_order at
    insert time - the ONLY time these are ever assigned. display_id
    is deterministically derived from display_order (index in the
    list the review engine returned), never recomputed later."""

    if not findings:
        return
    docs = []
    for i, f in enumerate(findings):
        docs.append({
            **f.model_dump(),
            "job_id": job_id,
            "finding_uid": str(uuid.uuid4()),
            "display_id": f"F-{i + 1:04d}",
            "display_order": i,
        })
    await db[_COLLECTION].insert_many(docs)


async def get_findings_for_job(db, job_id):
    """UNCHANGED - the original, unbounded function. Deliberately
    left as-is in this slice; still used by the existing (pre-Phase-3)
    finding_followup node and REST status route. See this slice's
    delivery notes."""

    cursor = db[_COLLECTION].find({"job_id": job_id})
    docs = await cursor.to_list(length=None)
    return [Finding(**{k: v for k, v in d.items() if k not in ("_id", "job_id")}) for d in docs]


async def _verify_ownership(db, user_id: str, job_id: str) -> bool:
    job = await jobs_repository.get_job(db, job_id)
    return job is not None and job.user_id == user_id


async def get_finding(db, user_id: str, job_id: str, finding_id: str) -> PersistedFinding | None:
    """finding_id is the display_id form (e.g. "F-0012") - matches
    how the Phase 3 doc's ChatAction contract references findings.
    Ownership-scoped: returns None for a job that doesn't exist or
    doesn't belong to user_id, exactly as if the finding didn't
    exist - no distinction that would let a caller probe for other
    users' job IDs."""

    if not await _verify_ownership(db, user_id, job_id):
        return None
    doc = await db[_COLLECTION].find_one({"job_id": job_id, "display_id": finding_id})
    return _to_persisted_finding(doc) if doc else None


async def list_findings(
    db, user_id: str, job_id: str,
    category: str | None = None,
    rule_id: str | None = None,
    location: str | None = None,
    cursor: str | None = None,
    limit: int = 5,
) -> ListFindingsResult:
    """Bounded, cursor-paginated, ownership-scoped. NEVER uses
    to_list(length=None) - limit is always applied. cursor is the
    display_order of the last finding on the previous page (an
    opaque string from the caller's perspective, but concretely just
    that integer stringified)."""

    if not await _verify_ownership(db, user_id, job_id):
        return ListFindingsResult(findings=[], next_cursor=None, total_matching=0)

    filter_query: dict = {"job_id": job_id}
    if category is not None:
        filter_query["category"] = category
    if rule_id is not None:
        filter_query["rule_id"] = rule_id
    if location is not None:
        filter_query["location_display"] = location

    total_matching = await db[_COLLECTION].count_documents(filter_query)

    page_query = dict(filter_query)
    if cursor is not None:
        page_query["display_order"] = {"$gt": int(cursor)}

    mongo_cursor = db[_COLLECTION].find(page_query).sort("display_order", 1).limit(limit)
    docs = await mongo_cursor.to_list(length=limit)
    findings = [_to_persisted_finding(d) for d in docs]

    next_cursor = str(findings[-1].display_order) if len(findings) == limit else None

    return ListFindingsResult(findings=findings, next_cursor=next_cursor, total_matching=total_matching)


async def count_findings(db, user_id: str, job_id: str, filters: dict | None = None) -> int:
    if not await _verify_ownership(db, user_id, job_id):
        return 0
    query: dict = {"job_id": job_id}
    if filters:
        if filters.get("category") is not None:
            query["category"] = filters["category"]
        if filters.get("rule_id") is not None:
            query["rule_id"] = filters["rule_id"]
        if filters.get("location") is not None:
            query["location_display"] = filters["location"]
    return await db[_COLLECTION].count_documents(query)


async def count_findings_by_category(db, user_id: str, job_id: str) -> dict[str, int]:
    """Result set is inherently bounded by RuleCategory's small,
    fixed cardinality (~8 values), not by finding count - this is
    NOT the "unbounded result browsing" pattern the Phase 3 doc
    prohibits (to_list(length=None) on a findings LIST), it's a
    bounded aggregation whose row count is capped by the enum size
    regardless of how many findings exist."""

    if not await _verify_ownership(db, user_id, job_id):
        return {}
    pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
    ]
    agg_cursor = await db[_COLLECTION].aggregate(pipeline)
    results = await agg_cursor.to_list(length=None)
    return {r["_id"]: r["count"] for r in results}


async def ensure_indexes(db) -> None:
    """Phase 3A: supports every query pattern above.
    - (job_id, display_order): the primary paginated-list query.
    - (job_id, display_id), UNIQUE: get_finding()'s exact lookup -
      compound-unique so "F-0012" can exist once PER JOB but freely
      repeat across different jobs (confirmed intentional per the
      Phase 3 doc: "duplicate F-0012 across jobs is acceptable only
      because every lookup is also job-scoped").
    - (job_id, category) / (job_id, rule_id): filtered listing/
      counting.
    - finding_uid, UNIQUE (global): the durable internal identifier
      should never collide, defensively enforced even though UUID4
      collision is astronomically unlikely in practice.
    """

    await db[_COLLECTION].create_index([("job_id", 1), ("display_order", 1)])
    await db[_COLLECTION].create_index([("job_id", 1), ("display_id", 1)], unique=True)
    await db[_COLLECTION].create_index([("job_id", 1), ("category", 1)])
    await db[_COLLECTION].create_index([("job_id", 1), ("rule_id", 1)])
    await db[_COLLECTION].create_index("finding_uid", unique=True)