"""
app/services/upload_service.py

Stages an attachment (bytes into GridFS + a StagedUpload tracking
record) BEFORE the graph is ever invoked.

FIX for external review point 6: stage_upload() previously had a
real orphan window - if store_file() succeeded but
create_staged_upload() failed (e.g. a transient Mongo write error),
the GridFS bytes would exist with nothing tracking them, forever.
Now compensates by deleting the just-uploaded file if the tracking
record fails to write.

FIX for external review point 9: cleanup_expired_staged_uploads()
handles the case with no automatic cleanup before this - a user
attaches a file, starts intake, closes the browser, never returns.
Real gap, not previously handled (only explicit replacement and
explicit failed-job-creation cleanup existed). This function is
built and tested; WIRING it into a periodic caller (e.g. one worker
slot's idle cycle, matching how requeue_stale_jobs() already
piggybacks there) is the remaining integration step.
"""

from __future__ import annotations

import logging

from pymongo.asynchronous.database import AsyncDatabase

from app.jobs.storage import delete_file, store_file
from app.repository.staged_upload_repository import (
    create_staged_upload,
    find_expired_staged_uploads,
    get_staged_upload,
    mark_abandoned,
)
from app.schema.staged_upload import StagedUpload

logger = logging.getLogger("app.services.upload_service")


async def stage_upload(
    db: AsyncDatabase, user_id: str, file_bytes: bytes, filename: str, content_type: str | None = None
) -> tuple[str, StagedUpload]:
    gridfs_file_id = await store_file(db, file_bytes, filename)

    try:
        upload = StagedUpload(
            user_id=user_id, gridfs_file_id=gridfs_file_id, filename=filename,
            content_type=content_type, size_bytes=len(file_bytes),
        )
        upload_id = await create_staged_upload(db, upload)
    except Exception:
        # FIX for external review point 6 - compensate rather than
        # leave an orphaned GridFS object with nothing tracking it.
        logger.error("Staged upload record creation failed after GridFS upload succeeded - compensating", exc_info=True)
        await delete_file(db, gridfs_file_id)
        raise

    return upload_id, upload


async def abandon_staged_upload(db: AsyncDatabase, upload_id: str) -> None:
    upload = await get_staged_upload(db, upload_id)
    if upload is None:
        return
    await mark_abandoned(db, upload_id)
    await delete_file(db, upload.gridfs_file_id)


async def cleanup_expired_staged_uploads(db: AsyncDatabase, limit: int = 100) -> int:
    """FIX for external review point 9. Finds STAGED uploads past
    their expires_at, deletes the GridFS bytes, marks them abandoned.
    Returns the count cleaned up. Safe to call repeatedly/
    concurrently - abandon_staged_upload()'s delete_file() already
    tolerates a file that's already gone (see app/jobs/storage.py)."""

    expired = await find_expired_staged_uploads(db, limit=limit)
    for upload_id, _upload in expired:
        await abandon_staged_upload(db, upload_id)
    if expired:
        logger.info("Cleaned up %d expired staged upload(s)", len(expired))
    return len(expired)