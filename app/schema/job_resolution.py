"""
app/schema/job_resolution.py

Phase 3B: typed contract for job resolution, matching the same
app/schema/ + service-logic separation used throughout this project
(app/schema/finding.py holds PersistedFinding/ListFindingsResult,
app/jobs/findings_repository.py holds the logic that uses them;
app/schema/staged_upload.py holds StagedUpload,
app/repository/staged_upload_repository.py holds the logic).

CORRECTION: this was originally a bare @dataclass defined directly
inside app/services/job_resolver.py - inconsistent with every other
typed result contract in this build, which are real Pydantic models
living in app/schema/. Moved here as a proper BaseModel, matching
ListFindingsResult's own shape and reasoning exactly. Confirmed via
direct test that Pydantic v2 correctly validates a nested BaseModel
inside a list[tuple[...]] field (ReviewJob is itself a BaseModel).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.jobs.schema import ReviewJob


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    NO_CONTEXT = "no_context"


class JobResolutionResult(BaseModel):
    status: ResolutionStatus
    job_id: str | None = None
    job: ReviewJob | None = None
    candidates: list[tuple[str, ReviewJob]] = Field(default_factory=list)
    # (job_id, ReviewJob) pairs - ReviewJob doesn't carry its own
    # Mongo _id, same convention used throughout this codebase's
    # other repositories. Populated only when status == AMBIGUOUS,
    # bounded by construction (the queries producing them are limited).