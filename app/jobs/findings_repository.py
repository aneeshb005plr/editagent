"""
app/jobs/findings_repository.py

Persists the review engine's Finding output, keyed by job_id -
matches the data model already laid out in the architecture doc's
Section 4. Kept separate from app/review/ (the engine itself has no
Mongo dependency at all - it's a pure computation, takes a
ParsedDocument and a RuleSet, returns findings in memory; persistence
is this module's job, not the engine's).
"""

from __future__ import annotations

from pymongo.asynchronous.database import AsyncDatabase

from app.review.models import Finding

_COLLECTION = "findings"


async def save_findings(db: AsyncDatabase, job_id: str, findings: list[Finding]) -> None:
    if not findings:
        return
    docs = [{**f.model_dump(), "job_id": job_id} for f in findings]
    await db[_COLLECTION].insert_many(docs)


async def get_findings_for_job(db: AsyncDatabase, job_id: str) -> list[Finding]:
    cursor = db[_COLLECTION].find({"job_id": job_id})
    docs = await cursor.to_list(length=None)
    return [Finding(**{k: v for k, v in d.items() if k not in ("_id", "job_id")}) for d in docs]