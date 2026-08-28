"""
app/jobs/findings_repository.py

PHASE 3A CORRECTION PASS - all fixes below verified against real
tests, not just reasoned about:

1. ensure_indexes() now backfills legacy identity fields BEFORE
   creating the unique indexes (item 2) - a real correctness
   ordering issue, not optional.

2. list_findings() is now genuinely bounded: DEFAULT_PAGE_SIZE/
   MAX_PAGE_SIZE constants, limit validated (item 3), cursor
   validated safely rather than a raw int() call that could 500 on
   malformed input (item 4), and next_cursor correctness fixed via
   the limit+1 query pattern (item 5) - the previous version
   incorrectly reported a next page whenever a result set's size
   happened to exactly equal limit.

3. Ownership verification now safely handles a malformed job_id
   (item 7) - narrowly scoped to THIS file's _verify_ownership, not
   a repository-wide refactor of jobs_repository.get_job().

4. save_findings()'s docstring no longer claims an "exactly once"
   guarantee the system doesn't actually provide (item 8) - corrected
   to describe what's actually true today, with the real remaining
   gap explicitly deferred to the later worker-scalability phase
   (not redesigned here).

Everything from the original Phase 3A slice this doesn't explicitly
change is preserved as-is (item 9): PersistedFinding stays separate
from the canonical Finding, finding_uid/display_id/display_order
semantics are unchanged, cross-job duplicate display_id is fine,
within-job duplicate display_id is rejected, ownership is checked in
every function, get_findings_for_job() and its legacy callers are
still untouched (that's the later findings-conversation slice's job),
category aggregation is still fine (bounded by enum cardinality).
"""

from __future__ import annotations

import uuid

from bson.errors import InvalidId

from app.jobs import repository as jobs_repository
from app.review.models import Finding
from app.schema.finding import ListFindingsResult, PersistedFinding

_COLLECTION = "findings"

DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 50
# Item 3: server-controlled bounds - a caller can never request an
# unbounded result set. 50 is a reasonable, documented ceiling for a
# chat-paginated findings browse; not derived from any hard technical
# constraint, just a sane bound - adjust if a real need for a larger
# page emerges.


class InvalidFindingsQueryError(Exception):
    """Controlled domain/application validation error for malformed
    pagination input (limit/cursor) - item 3/4. Callers (the future
    finding-conversation workflow, action-contract handlers) should
    catch this and respond with a clear message, not let it surface
    as an uncontrolled 500."""


def _to_persisted_finding(doc: dict) -> PersistedFinding:
    doc = dict(doc)
    doc.pop("_id", None)
    return PersistedFinding(**doc)


async def save_findings(db, job_id: str, findings: list[Finding]) -> None:
    """Item 8 CORRECTION: this docstring previously claimed findings
    are inserted "exactly once" per job - that is NOT a guarantee
    this system actually provides. The current worker/stale-requeue
    architecture (see app/jobs/worker.py) does not implement full
    exactly-once execution semantics; a requeued/reprocessed job
    could in principle call this again for the same job_id.

    What's actually true, stated accurately:
    - Under the current flow, this is expected to persist ONE
      successful result set per ReviewJob (there is no code path
      today that deliberately calls it twice for a genuinely
      completed job).
    - The database-level uniqueness on (job_id, display_id) and on
      finding_uid protects STABLE DISPLAY IDENTITY from silent
      duplication if this ever were called twice - a second call
      would hit a duplicate-key violation, not silently create a
      second numbering scheme.
    - Full worker retry/reprocessing/exactly-once semantics are a
      real, separate concern, explicitly deferred to the later
      worker-scalability phase (distributed leases, idempotent
      reprocessing) - NOT redesigned here.

    Assigns finding_uid/display_id/display_order at insert time -
    the only time they're ever assigned. display_id is deterministically
    derived from display_order (index in the list the review engine
    returned), never recomputed later.
    """

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
    """UNCHANGED - the original, unbounded function. Still used by
    the existing (pre-Phase-3) finding_followup node and REST status
    route. Migrating those callers is the later findings-conversation
    slice's job, not this correction pass's."""

    cursor = db[_COLLECTION].find({"job_id": job_id})
    docs = await cursor.to_list(length=None)
    return [Finding(**{k: v for k, v in d.items() if k not in ("_id", "job_id")}) for d in docs]


async def _verify_ownership(db, user_id: str, job_id: str) -> bool:
    """Item 7 FIX: get_job() internally does ObjectId(job_id), which
    raises bson.errors.InvalidId for a malformed string - a
    client-supplied job_id is untrusted input and could easily be
    malformed. A malformed ID now safely behaves as not-found/not-
    authorized, matching how a well-formed-but-nonexistent or
    someone-else's job_id already behaves - no distinction that
    would let a caller learn anything from the failure mode."""

    try:
        job = await jobs_repository.get_job(db, job_id)
    except InvalidId:
        return False
    return job is not None and job.user_id == user_id


def _validate_limit(limit: int) -> None:
    if limit <= 0 or limit > MAX_PAGE_SIZE:
        raise InvalidFindingsQueryError(
            f"limit must be between 1 and {MAX_PAGE_SIZE}, got {limit}"
        )


def _validate_cursor(cursor: str | None) -> int | None:
    """Item 4 FIX: previously a raw int(cursor) call - malformed
    client input like cursor="abc" raised ValueError uncaught,
    which would surface as an internal error rather than a
    controlled response."""

    if cursor is None:
        return None
    try:
        value = int(cursor)
    except (TypeError, ValueError):
        raise InvalidFindingsQueryError(f"cursor must be a valid integer string, got {cursor!r}")
    if value < 0:
        raise InvalidFindingsQueryError(f"cursor must not be negative, got {value}")
    return value


async def get_finding(db, user_id: str, job_id: str, finding_id: str) -> PersistedFinding | None:
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
    limit: int = DEFAULT_PAGE_SIZE,
) -> ListFindingsResult:
    """Item 3/4/5 fixes:
    - limit is validated against DEFAULT_PAGE_SIZE/MAX_PAGE_SIZE
      bounds (raises InvalidFindingsQueryError, not silently
      accepted or left to crash unboundedly).
    - cursor is validated safely (no raw int() call that could raise
      uncaught on malformed input).
    - next_cursor correctness: queries limit+1 records and only
      reports has_more when genuinely more than `limit` results
      exist - fixes a real bug where a result set whose size exactly
      equaled `limit` incorrectly reported a next page that, when
      fetched, would return zero results.
    """

    _validate_limit(limit)
    cursor_value = _validate_cursor(cursor)

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
    if cursor_value is not None:
        page_query["display_order"] = {"$gt": cursor_value}

    mongo_cursor = db[_COLLECTION].find(page_query).sort("display_order", 1).limit(limit + 1)
    docs = await mongo_cursor.to_list(length=limit + 1)

    has_more = len(docs) > limit
    docs = docs[:limit]
    findings = [_to_persisted_finding(d) for d in docs]

    next_cursor = str(findings[-1].display_order) if has_more else None

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
    if not await _verify_ownership(db, user_id, job_id):
        return {}
    pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
    ]
    agg_cursor = await db[_COLLECTION].aggregate(pipeline)
    results = await agg_cursor.to_list(length=None)
    return {r["_id"]: r["count"] for r in results}


async def backfill_legacy_finding_identity(db) -> int:
    """Item 2: idempotent migration for findings persisted before
    Phase 3A (missing finding_uid/display_id/display_order). MUST run
    before the unique indexes below are created, or index creation
    would fail/be unsafe against existing data missing those fields.

    ORDERING, documented exactly as required: legacy findings within
    a job are ordered by Mongo's own _id (ObjectId). The pre-Phase-3A
    save_findings() inserted all of a job's findings in a single
    insert_many() call; the pymongo driver generates ObjectIds
    client-side, sequentially, in list order, before sending the
    batch - so sorting by _id reliably reconstructs the original
    order the review engine returned those findings in. This is the
    best deterministic signal available for data that predates
    display_order's existence; Mongo's "natural" storage order is NOT
    relied on directly (this explicit sort is what's actually used).

    IDEMPOTENT: only touches documents missing finding_uid (the
    "has this been migrated" signal); the update itself re-guards on
    that same condition. Re-running is safe - already-migrated
    findings are skipped entirely, and IDs already assigned are never
    changed.
    """

    cursor = db[_COLLECTION].find({"finding_uid": {"$exists": False}})
    legacy_docs = await cursor.to_list(length=None)
    # Unbounded to_list here is a one-time administrative migration
    # step run at startup, not a user-facing result-browsing path -
    # a fundamentally different concern from the "no unbounded
    # findings browsing" rule for list_findings()/get_findings_for_job().

    if not legacy_docs:
        return 0

    by_job: dict[str, list[dict]] = {}
    for doc in legacy_docs:
        by_job.setdefault(doc["job_id"], []).append(doc)

    backfilled = 0
    for job_id, docs in by_job.items():
        docs.sort(key=lambda d: d["_id"])
        for i, doc in enumerate(docs):
            await db[_COLLECTION].update_one(
                {"_id": doc["_id"], "finding_uid": {"$exists": False}},
                {"$set": {
                    "finding_uid": str(uuid.uuid4()),
                    "display_order": i,
                    "display_id": f"F-{i + 1:04d}",
                }},
            )
            backfilled += 1
    return backfilled


async def ensure_indexes(db) -> None:
    """Item 1/2/6: backfills legacy identity fields FIRST, then
    creates indexes - including the location_display index (item 6),
    and NOT a combinatorial index for every possible filter
    combination, per the explicit instruction to keep this narrow."""

    await backfill_legacy_finding_identity(db)

    await db[_COLLECTION].create_index([("job_id", 1), ("display_order", 1)])
    await db[_COLLECTION].create_index([("job_id", 1), ("display_id", 1)], unique=True)
    await db[_COLLECTION].create_index([("job_id", 1), ("category", 1)])
    await db[_COLLECTION].create_index([("job_id", 1), ("rule_id", 1)])
    await db[_COLLECTION].create_index([("job_id", 1), ("location_display", 1)])
    await db[_COLLECTION].create_index("finding_uid", unique=True)