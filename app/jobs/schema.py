"""
app/jobs/schema.py

The review job record - the durable state that makes async document
review possible. This is a real boundary type (Mongo-persisted,
status queried across process restarts) - Pydantic, same reasoning
as app/review/models.py's Finding, not the internal hot-path
dataclasses in app/documents/base.py.

DELIBERATE SCOPE NOTE vs. knowledge-sync-worker: that service is
shared, multi-agent infrastructure (its own agent_registry, admin
API, per-agent DB routing) because it serves every QuickSuite agent
from one deployment. EditEdge's job system has none of that - it's
single-agent, in-process (see worker.py's own docstring for why),
and this schema is correspondingly simpler: no agent_id, no registry
lookup, just a job belonging to a user.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.rules.schema import AppliesTo, EnglishVariant


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewJob(BaseModel):
    """One document review job. job_id is the Mongo _id as a string
    (set by the repository on insert, not here) - this model
    describes the document's FIELDS, insertion/ID assignment is the
    repository's job."""

    user_id: str
    filename: str
    file_size_bytes: int
    gridfs_file_id: str
    # References the raw uploaded bytes in GridFS (see storage.py) -
    # NOT stored inline in this document. Durability of the actual
    # file (not just the job's status) is what makes heartbeat/stale-
    # job requeue actually meaningful for an in-process worker: if
    # the API process crashes, only Mongo-persisted state survives -
    # in-memory-only bytes would be gone regardless of what this
    # job record says. See worker.py's docstring.

    applies_to: AppliesTo = AppliesTo.GENERAL
    is_pcs: bool = False
    english_variant: EnglishVariant = EnglishVariant.US
    # The three real intake answers this review depends on - see
    # app/review/engine.py's review_document() for why each of these
    # must be a real answered question, never auto-detected.

    status: JobStatus = JobStatus.PENDING
    error_message: str | None = None
    finding_count: int | None = None
    # Denormalized count, set on completion - lets a status check
    # avoid querying the (potentially large) findings collection just
    # to answer "how many findings did this produce."

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    # See worker.py's docstring for an honest limitation: heartbeat
    # currently updates at phase boundaries (claimed, after-parse,
    # after-review, completed) - NOT mid-LLM-call, since
    # review_document() is currently one atomic async call from the
    # worker's perspective. A single long judgment/consistency batch
    # on a large document could exceed a naively-short stale
    # threshold with no heartbeat in between. STALE_JOB_THRESHOLD_
    # SECONDS needs to be set generously until/unless finer-grained
    # heartbeating is added (a real, contained future change - see
    # worker.py).