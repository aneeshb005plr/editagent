"""
app/jobs/worker.py

FOUR FIXES per external review (second production-hardening pass):

1. str(Exception) bug (point 3/2): the except block previously called
   str(Exception) - the CLASS itself, not the caught instance - which
   produces the literal string "<class 'Exception'>" for every single
   failure, destroying all diagnostic value. Fixed to `except
   Exception as e: ... str(e)`.

2. Lost memory-release lines (point 4/3): explicit `del file_bytes`/
   `del parsed` after each is no longer needed, restored - matters for
   the stated goal of large files (up to 100MB) times
   MAX_CONCURRENT_JOBS concurrent slots times parsed-document/image
   payload memory.

3. Cleanup failure could halt job processing (point 5/4 - flagged P1
   for availability): requeue/cleanup/claim were previously one
   try/except block - a broken maintenance task (cleanup) meant claim
   never even ran, potentially stalling the whole queue indefinitely
   if the failure persisted. Each step now has its own error
   boundary; a maintenance failure is logged but never prevents a
   claim attempt.

4. Cleanup now throttled (point 6, second review round): every slot
   used to run cleanup_expired_staged_uploads() on every idle cycle -
   correct (CAS-safe, per the review) but wasteful repeated database
   work under a busy queue with several slots. Now runs at most once
   per STAGED_UPLOAD_CLEANUP_INTERVAL_SECONDS, tracked via a shared
   app.state timestamp - a small, harmless race between slots at the
   threshold moment is acceptable (worst case: cleanup runs slightly
   more often than intended, never a correctness issue given CAS).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.documents.pipeline import parse_and_extract
from app.jobs import repository
from app.jobs.findings_repository import save_findings
from app.jobs.schema import ReviewJob
from app.jobs.storage import delete_file, retrieve_file
from app.review.engine import review_document
from app.rules.taxonomy import RULE_SET
from app.services.upload_service import cleanup_expired_staged_uploads

logger = logging.getLogger("app.jobs.worker")


async def _process_job(app, job_id: str, job: ReviewJob) -> None:
    db = app.state.mongo_db
    genai_client = app.state.genai_client
    try:
        file_bytes = await retrieve_file(db, job.gridfs_file_id)
        parsed = await parse_and_extract(
            file_bytes=file_bytes, filename=job.filename, max_size_mb=settings.MAX_FILE_SIZE_MB,
            vision_model=genai_client.bind(temperature=0, max_tokens=1000),
        )
        del file_bytes

        await repository.heartbeat_job(db, job_id)
        findings = await review_document(
            parsed=parsed, rule_set=RULE_SET, applies_to=job.applies_to,
            judgment_model=genai_client, english_variant=job.english_variant, is_pcs=job.is_pcs,
        )
        del parsed

        await repository.heartbeat_job(db, job_id)
        await save_findings(db, job_id, findings)
        await repository.complete_job(db, job_id, finding_count=len(findings))
        logger.info("Job %s completed: %s -> %d findings", job_id, job.filename, len(findings))
    except Exception as e:
        logger.error("Job %s failed: %s", job_id, job.filename, exc_info=True)
        await repository.fail_job(db, job_id, error_message=str(e))
        return

    # FIX per explicit review request: source-file cleanup is now a
    # SEPARATE concern, outside the try/except above that calls
    # fail_job(). Previously delete_file() lived inside that same
    # try block - if it failed AFTER complete_job() had already
    # succeeded, the exception fell into the except handler and
    # fail_job() overwrote an already-SUCCEEDED job back to FAILED.
    # A review that genuinely succeeded (findings saved, job marked
    # SUCCEEDED) must stay SUCCEEDED regardless of whether cleanup
    # of the now-unneeded source file works - cleanup failing just
    # means the file needs cleanup later, not that the review failed.
    try:
        await delete_file(db, job.gridfs_file_id)
    except Exception:
        logger.error(
            "Job %s succeeded but source-file cleanup failed - file will need manual/future cleanup",
            job_id, exc_info=True,
        )

async def _worker_slot_loop(app, slot_id: int) -> None:
    db = app.state.mongo_db

    if not hasattr(app.state, "last_staged_upload_cleanup_at"):
        app.state.last_staged_upload_cleanup_at = None

    while True:
        app.state.worker_last_iteration = datetime.now(timezone.utc)

        # FIX for external review point 5/4: each maintenance step now
        # has its OWN error boundary - a failure in one never prevents
        # the others, and critically, never prevents the claim attempt
        # below.
        try:
            requeued = await repository.requeue_stale_jobs(db, settings.STALE_JOB_THRESHOLD_SECONDS)
            if requeued:
                logger.warning("Slot %d: requeued %d stale job(s)", slot_id, requeued)
        except Exception:
            logger.error("Slot %d: stale-job requeue failed", slot_id, exc_info=True)

        try:
            now = datetime.now(timezone.utc)
            last = app.state.last_staged_upload_cleanup_at
            due = last is None or (now - last).total_seconds() >= settings.STAGED_UPLOAD_CLEANUP_INTERVAL_SECONDS
            if due:
                app.state.last_staged_upload_cleanup_at = now  # FIX point 6 - throttled
                cleaned = await cleanup_expired_staged_uploads(db)
                if cleaned:
                    logger.info("Slot %d: cleaned up %d expired staged upload(s)", slot_id, cleaned)
        except Exception:
            logger.error("Slot %d: expired-upload cleanup failed", slot_id, exc_info=True)

        try:
            async with app.state.job_claim_lock:
                claim = await repository.claim_next_pending_job(db)
        except Exception:
            logger.error("Slot %d: job claim failed", slot_id, exc_info=True)
            claim = None

        if claim is None:
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)
            continue

        job_id, job = claim
        logger.info("Slot %d claimed job %s: %s (user=%s)", slot_id, job_id, job.filename, job.user_id)
        await _process_job(app, job_id, job)


async def _supervised_slot_loop(app, slot_id: int) -> None:
    while True:
        try:
            await _worker_slot_loop(app, slot_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Slot %d crashed unexpectedly - restarting in 5s", slot_id, exc_info=True)
            await asyncio.sleep(5)


def start_worker(app) -> list[asyncio.Task]:
    app.state.job_claim_lock = asyncio.Lock()
    tasks = [asyncio.create_task(_supervised_slot_loop(app, slot_id)) for slot_id in range(settings.MAX_CONCURRENT_JOBS)]
    app.state.worker_tasks = tasks
    logger.info("Job worker started (in-process, %d concurrent slots)", len(tasks))
    return tasks


async def stop_worker(app) -> None:
    tasks = getattr(app.state, "worker_tasks", None)
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Job worker stopped (%d slots)", len(tasks))