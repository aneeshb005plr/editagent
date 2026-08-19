"""
app/jobs/storage.py

Durable storage for uploaded file bytes, via GridFS - confirmed
current API via direct introspection (gridfs.AsyncGridFSBucket,
NOT under pymongo.gridfs - that import path doesn't exist in the
installed version; gridfs is its own top-level package).

WHY THIS MATTERS, not just "where big files go": this project's job
worker runs IN-PROCESS with the FastAPI app (see worker.py's
docstring for why), not as a separately deployed service. If the
uploaded file bytes only lived in request-scoped memory, a process
crash mid-job would lose them regardless of what the job record in
Mongo says - the heartbeat/stale-job-requeue pattern (borrowed from
knowledge-sync-worker's proven design) would be tracking a job that
can never actually be resumed, since there'd be nothing left to
resume it WITH. Storing the bytes in GridFS means a restarted worker
can genuinely re-fetch and reprocess a stale job, not just report on
one that's silently unrecoverable.
"""

from __future__ import annotations

import logging

from bson import ObjectId
from gridfs import AsyncGridFSBucket
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.jobs.storage")

_BUCKET_NAME = "review_uploads"


def _get_bucket(db: AsyncDatabase) -> AsyncGridFSBucket:
    return AsyncGridFSBucket(db, bucket_name=_BUCKET_NAME)


async def store_file(db: AsyncDatabase, file_bytes: bytes, filename: str) -> str:
    """Stores file_bytes in GridFS, returns the file id as a string
    (for storage on the ReviewJob record - job schema stays plain
    str, not coupled to bson.ObjectId)."""

    bucket = _get_bucket(db)
    file_id = await bucket.upload_from_stream(filename, file_bytes)
    return str(file_id)


async def retrieve_file(db: AsyncDatabase, gridfs_file_id: str) -> bytes:
    """Fetches the full file back out of GridFS. Raises
    gridfs.errors.NoFile if the id doesn't exist (e.g. already
    cleaned up) - callers should let this propagate as a real job
    failure, not swallow it silently."""

    bucket = _get_bucket(db)
    stream = await bucket.open_download_stream(ObjectId(gridfs_file_id))
    return await stream.read()


async def delete_file(db: AsyncDatabase, gridfs_file_id: str) -> None:
    """Cleans up stored bytes once a job no longer needs them.
    RETENTION POLICY IS DELIBERATELY MINIMAL FOR MVP: delete on
    successful completion (no reason to keep the raw upload once
    findings exist), but the caller currently keeps it on FAILURE
    (so a failed job's file can be inspected/reprocessed) - no
    automatic cleanup of old failed-job files exists yet. A real
    retention/cleanup policy is a deferred decision, same family as
    the "detailed audit history" item already flagged as Phase 2 -
    not silently assumed to be handled."""

    bucket = _get_bucket(db)
    try:
        await bucket.delete(ObjectId(gridfs_file_id))
    except Exception:
        logger.warning(
            "Could not delete GridFS file %s (may already be gone)",
            gridfs_file_id,
            exc_info=True,
        )