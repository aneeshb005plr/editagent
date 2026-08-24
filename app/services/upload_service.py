"""
app/services/upload_service.py

Phase 1: stages an attachment (bytes into GridFS + a StagedUpload
tracking record) BEFORE the graph is ever invoked - the graph only
ever sees a small upload_id reference from this point on, never raw
bytes. Matches the architecture doc's diagram (staging happens in
the service layer, at the edge, before orchestration).
"""

from __future__ import annotations

from pymongo.asynchronous.database import AsyncDatabase

from app.jobs.storage import delete_file, store_file
from app.repository.staged_upload_repository import (
    create_staged_upload,
    get_staged_upload,
    mark_abandoned,
)
from app.schema.staged_upload import StagedUpload


async def stage_upload(
    db: AsyncDatabase, user_id: str, file_bytes: bytes, filename: str, content_type: str | None = None
) -> tuple[str, StagedUpload]:
    """Stores bytes in GridFS immediately, creates the tracking
    record, returns (upload_id, upload). This is the ONLY place raw
    file bytes exist after the API boundary - everything downstream
    (graph state, job creation) works from the returned upload_id."""

    gridfs_file_id = await store_file(db, file_bytes, filename)
    upload = StagedUpload(
        user_id=user_id, gridfs_file_id=gridfs_file_id, filename=filename,
        content_type=content_type, size_bytes=len(file_bytes),
    )
    upload_id = await create_staged_upload(db, upload)
    return upload_id, upload


async def abandon_staged_upload(db: AsyncDatabase, upload_id: str) -> None:
    """Marks abandoned AND deletes the GridFS bytes - covers both
    Phase 1 acceptance criteria explicitly: a replaced pending
    upload is cleaned up, and a failed job creation does not orphan
    the staged file. No reason to keep bytes for an upload that will
    never become a real job."""

    upload = await get_staged_upload(db, upload_id)
    if upload is None:
        return
    await mark_abandoned(db, upload_id)
    await delete_file(db, upload.gridfs_file_id)