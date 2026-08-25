"""
test_fix2_completion_cleanup_separation.py

Five required tests for app/jobs/worker.py's _process_job() - job
completion must be independent of source-file cleanup success.
"""

import asyncio
from unittest.mock import MagicMock, patch

from app.jobs.worker import _process_job
from app.jobs.schema import JobStatus, ReviewJob
from app.jobs import repository
from fake_mongo import FakeDB


async def _make_app_and_job(db, filename="doc.docx"):
    app = MagicMock()
    app.state.mongo_db = db
    app.state.genai_client = MagicMock()
    job_id = await repository.create_job(db, ReviewJob(user_id="u1", filename=filename, file_size_bytes=10, gridfs_file_id="g1"))
    job = await repository.get_job(db, job_id)
    return app, job_id, job


async def test_review_failure_marks_job_failed():
    db = FakeDB()
    app, job_id, job = await _make_app_and_job(db)

    async def failing_retrieve(db, gridfs_file_id):
        raise RuntimeError("simulated GridFS retrieval failure")

    with patch("app.jobs.worker.retrieve_file", new=failing_retrieve):
        await _process_job(app, job_id, job)

    final = await repository.get_job(db, job_id)
    assert final.status == JobStatus.FAILED
    assert "GridFS retrieval failure" in final.error_message
    print("PASS: review failure -> job becomes FAILED")


async def test_finding_persistence_failure_marks_job_failed():
    db = FakeDB()
    app, job_id, job = await _make_app_and_job(db)

    async def fake_retrieve(db, gridfs_file_id):
        return b"fake bytes"
    async def fake_parse(**kwargs):
        return MagicMock()
    async def fake_review(**kwargs):
        return []
    async def failing_save_findings(db, job_id, findings):
        raise RuntimeError("simulated finding persistence failure")

    with patch("app.jobs.worker.retrieve_file", new=fake_retrieve), \
         patch("app.jobs.worker.parse_and_extract", new=fake_parse), \
         patch("app.jobs.worker.review_document", new=fake_review), \
         patch("app.jobs.worker.save_findings", new=failing_save_findings):
        await _process_job(app, job_id, job)

    final = await repository.get_job(db, job_id)
    assert final.status == JobStatus.FAILED
    assert "finding persistence failure" in final.error_message
    print("PASS: finding persistence failure -> job becomes FAILED")


async def test_complete_and_cleanup_both_succeed():
    db = FakeDB()
    app, job_id, job = await _make_app_and_job(db)
    deleted = []

    async def fake_retrieve(db, gridfs_file_id):
        return b"fake bytes"
    async def fake_parse(**kwargs):
        return MagicMock()
    async def fake_review(**kwargs):
        return []
    async def fake_save_findings(db, job_id, findings):
        pass
    async def fake_delete(db, gridfs_file_id):
        deleted.append(gridfs_file_id)

    with patch("app.jobs.worker.retrieve_file", new=fake_retrieve), \
         patch("app.jobs.worker.parse_and_extract", new=fake_parse), \
         patch("app.jobs.worker.review_document", new=fake_review), \
         patch("app.jobs.worker.save_findings", new=fake_save_findings), \
         patch("app.jobs.worker.delete_file", new=fake_delete):
        await _process_job(app, job_id, job)

    final = await repository.get_job(db, job_id)
    assert final.status == JobStatus.SUCCEEDED
    assert deleted == ["g1"]
    print("PASS: complete_job succeeds + delete_file succeeds -> SUCCEEDED")


async def test_complete_succeeds_cleanup_fails_still_succeeded():
    """THE core fix being verified: a cleanup failure after a
    successful completion must NOT flip the job back to FAILED."""
    db = FakeDB()
    app, job_id, job = await _make_app_and_job(db)

    async def fake_retrieve(db, gridfs_file_id):
        return b"fake bytes"
    async def fake_parse(**kwargs):
        return MagicMock()
    async def fake_review(**kwargs):
        return []
    async def fake_save_findings(db, job_id, findings):
        pass
    async def failing_delete(db, gridfs_file_id):
        raise RuntimeError("simulated GridFS delete failure AFTER job already succeeded")

    with patch("app.jobs.worker.retrieve_file", new=fake_retrieve), \
         patch("app.jobs.worker.parse_and_extract", new=fake_parse), \
         patch("app.jobs.worker.review_document", new=fake_review), \
         patch("app.jobs.worker.save_findings", new=fake_save_findings), \
         patch("app.jobs.worker.delete_file", new=failing_delete):
        await _process_job(app, job_id, job)

    final = await repository.get_job(db, job_id)
    assert final.status == JobStatus.SUCCEEDED, (
        f"Job must remain SUCCEEDED despite cleanup failure - got {final.status}"
    )
    assert final.error_message is None, "fail_job() must NOT have been called - error_message should be untouched"
    print("PASS: complete_job succeeds + delete_file fails -> still SUCCEEDED")


async def test_cleanup_failure_logged_and_does_not_call_fail_job():
    db = FakeDB()
    app, job_id, job = await _make_app_and_job(db)

    async def fake_retrieve(db, gridfs_file_id):
        return b"fake bytes"
    async def fake_parse(**kwargs):
        return MagicMock()
    async def fake_review(**kwargs):
        return []
    async def fake_save_findings(db, job_id, findings):
        pass
    async def failing_delete(db, gridfs_file_id):
        raise RuntimeError("simulated cleanup failure")

    fail_job_calls = []
    real_fail_job = repository.fail_job
    async def tracking_fail_job(db, job_id, error_message):
        fail_job_calls.append(error_message)
        return await real_fail_job(db, job_id, error_message)

    with patch("app.jobs.worker.retrieve_file", new=fake_retrieve), \
         patch("app.jobs.worker.parse_and_extract", new=fake_parse), \
         patch("app.jobs.worker.review_document", new=fake_review), \
         patch("app.jobs.worker.save_findings", new=fake_save_findings), \
         patch("app.jobs.worker.delete_file", new=failing_delete), \
         patch("app.jobs.worker.repository.fail_job", new=tracking_fail_job):
        await _process_job(app, job_id, job)

    assert fail_job_calls == [], f"fail_job() must never be called for a post-success cleanup failure, but was called with: {fail_job_calls}"
    final = await repository.get_job(db, job_id)
    assert final.status == JobStatus.SUCCEEDED
    print("PASS: cleanup failure is logged and does not overwrite successful status (fail_job never called)")


async def main():
    await test_review_failure_marks_job_failed()
    await test_finding_persistence_failure_marks_job_failed()
    await test_complete_and_cleanup_both_succeed()
    await test_complete_succeeds_cleanup_fails_still_succeeded()
    await test_cleanup_failure_logged_and_does_not_call_fail_job()
    print()
    print("ALL FIX-2 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())