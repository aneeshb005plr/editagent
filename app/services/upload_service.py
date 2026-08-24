"""
app/services/upload_service.py

FIX for external review point 1: abandon_staged_upload() now only
deletes the GridFS bytes if it ACTUALLY WON the CAS transition
(STAGED->ABANDONED) - if the record was already consumed (or
already abandoned) by something else, this call correctly does
nothing, rather than potentially deleting a file a job still needs.
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
        logger.error("Staged upload record creation failed after GridFS upload succeeded - compensating", exc_info=True)
        await delete_file(db, gridfs_file_id)
        raise

    return upload_id, upload


async def abandon_staged_upload(db: AsyncDatabase, upload_id: str) -> bool:
    """FIX for external review point 1: fetches the record FIRST (to
    know gridfs_file_id), then attempts the CAS transition, and only
    deletes the GridFS bytes if THIS call won it. Returns whether it
    won - callers that need to know (e.g. create_review_job.py
    reacting to a lost race) can check this rather than assuming
    success."""

    upload = await get_staged_upload(db, upload_id)
    if upload is None:
        return False

    won = await mark_abandoned(db, upload_id)
    if not won:
        # Someone else already transitioned this record (consumed it
        # into a real job, or abandoned it first) - the file is
        # theirs to manage now, not ours to delete.
        return False

    await delete_file(db, upload.gridfs_file_id)
    return True


async def cleanup_expired_staged_uploads(db: AsyncDatabase, limit: int = 100) -> int:
    """Uses the now-CAS-safe abandon_staged_upload() - if a job
    creation wins the race for a given upload in the same moment
    cleanup examines it, cleanup correctly does nothing for that
    record instead of deleting a file the new job needs."""

    expired = await find_expired_staged_uploads(db, limit=limit)
    cleaned = 0
    for upload_id, _upload in expired:
        if await abandon_staged_upload(db, upload_id):
            cleaned += 1
    if cleaned:
        logger.info("Cleaned up %d expired staged upload(s)", cleaned)
    return cleaned