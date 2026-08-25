"""
test_fix1_reservation_release_race.py

Three required tests for the reservation-release/GridFS-deletion
race fix in app/agent/nodes/create_review_job.py's
_reconcile_stuck_reservation().
"""

import asyncio
from unittest.mock import patch

from bson import ObjectId

from app.agent.nodes.create_review_job import _reconcile_stuck_reservation
from app.jobs.schema import ReviewJob
from app.jobs import repository as jobs_repository
from app.repository.staged_upload_repository import (
    create_staged_upload,
    get_staged_upload,
    mark_consumed,
)
from app.schema.staged_upload import StagedUpload
from fake_mongo import FakeDB


async def _make_reserved_upload(db, gridfs_file_id="gridfs-1", filename="doc.docx"):
    upload = StagedUpload(user_id="u1", gridfs_file_id=gridfs_file_id, filename=filename, size_bytes=100)
    upload_id = await create_staged_upload(db, upload)
    reserved = await mark_consumed(db, upload_id, job_id=None)
    assert reserved
    staged = await get_staged_upload(db, upload_id)
    return upload_id, staged


async def test_successful_release_deletes_gridfs_file():
    db = FakeDB()
    deleted = []
    async def fake_delete(db, gridfs_file_id):
        deleted.append(gridfs_file_id)

    upload_id, staged = await _make_reserved_upload(db)

    with patch("app.agent.nodes.create_review_job.delete_file", new=fake_delete):
        # No concurrent job exists for this upload - release should win cleanly.
        result = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=True)

    assert result is not None
    assert "couldn't be completed" in result["messages"][0].content
    assert deleted == [staged.gridfs_file_id], "GridFS file must be deleted on a WON release"
    print("PASS: successful release -> GridFS file deleted")


async def test_lost_release_cas_does_not_delete_gridfs_file():
    db = FakeDB()
    deleted = []
    async def fake_delete(db, gridfs_file_id):
        deleted.append(gridfs_file_id)

    upload_id, staged = await _make_reserved_upload(db)

    # Simulate a concurrent winner: another invocation already linked
    # a real job to this upload BEFORE our release attempt runs -
    # i.e. consumed_job_id is no longer None, so release_reservation's
    # CAS (which requires consumed_job_id=None) will lose.
    from app.repository.staged_upload_repository import set_consumed_job_id
    concurrent_job_id = await jobs_repository.create_job(
        db, ReviewJob(user_id="u1", filename=staged.filename, file_size_bytes=100,
                       gridfs_file_id=staged.gridfs_file_id, source_upload_id=upload_id),
    )
    await set_consumed_job_id(db, upload_id, concurrent_job_id)

    with patch("app.agent.nodes.create_review_job.delete_file", new=fake_delete):
        result = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=True)

    assert deleted == [], "GridFS file must NOT be deleted when release_reservation() loses the CAS"
    print("PASS: lost release CAS -> GridFS file NOT deleted")
    return result, concurrent_job_id


async def test_job_appearing_during_reconciliation_is_recovered_not_destroyed():
    """Same setup as the lost-CAS test, but explicitly asserts the
    reconciliation path RECOVERS/LINKS the job that appeared, rather
    than just avoiding deletion."""
    db = FakeDB()
    deleted = []
    async def fake_delete(db, gridfs_file_id):
        deleted.append(gridfs_file_id)

    upload_id, staged = await _make_reserved_upload(db, gridfs_file_id="gridfs-live", filename="live.docx")

    from app.repository.staged_upload_repository import set_consumed_job_id
    concurrent_job_id = await jobs_repository.create_job(
        db, ReviewJob(user_id="u1", filename=staged.filename, file_size_bytes=100,
                       gridfs_file_id=staged.gridfs_file_id, source_upload_id=upload_id),
    )
    await set_consumed_job_id(db, upload_id, concurrent_job_id)

    with patch("app.agent.nodes.create_review_job.delete_file", new=fake_delete):
        result = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=True)

    assert result is not None
    assert result.get("active_job_id") == concurrent_job_id, "Must recover/link the job that appeared, not treat it as absent"
    assert "already underway" in result["messages"][0].content
    assert deleted == [], "The live job's file must survive"

    final_staged = await get_staged_upload(db, upload_id)
    assert final_staged.consumed_job_id == concurrent_job_id, "Repair should re-link consumed_job_id"
    print("PASS: job appearing during reconciliation is recovered/linked, not destroyed")


async def main():
    await test_successful_release_deletes_gridfs_file()
    await test_lost_release_cas_does_not_delete_gridfs_file()
    await test_job_appearing_during_reconciliation_is_recovered_not_destroyed()
    print()
    print("ALL FIX-1 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())