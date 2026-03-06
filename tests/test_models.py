"""Tests for the SQLite model layer."""

import pytest

from app import models
from app.models import JobStatus


@pytest.fixture(autouse=True)
def _use_db(db):
    """Pull in the db fixture for every test in this module."""


async def test_create_and_get_job():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Hello World",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    assert job["id"]
    assert job["bookmark_id"] == "bm1"
    assert job["status"] == JobStatus.pending

    fetched = await models.get_job(job["id"])
    assert fetched == job


async def test_get_job_not_found():
    result = await models.get_job("does-not-exist")
    assert result is None


async def test_list_jobs_empty():
    jobs, total = await models.list_jobs()
    assert jobs == []
    assert total == 0


async def test_list_jobs_returns_all():
    for i in range(3):
        await models.create_job(
            bookmark_id=f"bm{i}",
            bookmark_title=f"Article {i}",
            bookmark_url="http://example.com",
            tts_engine="edge-tts",
            voice="en-US-AriaNeural",
        )
    jobs, total = await models.list_jobs()
    assert total == 3
    assert len(jobs) == 3


async def test_list_jobs_pagination():
    for i in range(5):
        await models.create_job(
            bookmark_id=f"bm{i}",
            bookmark_title=f"Article {i}",
            bookmark_url="http://example.com",
            tts_engine="edge-tts",
            voice="en-US-AriaNeural",
        )
    page1, total = await models.list_jobs(limit=2, offset=0)
    assert total == 5
    assert len(page1) == 2

    page2, _ = await models.list_jobs(limit=2, offset=2)
    assert len(page2) == 2

    # IDs should not overlap
    page1_ids = {j["id"] for j in page1}
    page2_ids = {j["id"] for j in page2}
    assert page1_ids.isdisjoint(page2_ids)


async def test_update_job_status():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    await models.update_job(job["id"], status=JobStatus.completed, audio_path="abc.mp3")
    updated = await models.get_job(job["id"])
    assert updated["status"] == JobStatus.completed
    assert updated["audio_path"] == "abc.mp3"
    assert updated["updated_at"] is not None


async def test_update_job_error():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    await models.update_job(job["id"], status=JobStatus.failed, error_msg="TTS crashed")
    updated = await models.get_job(job["id"])
    assert updated["status"] == JobStatus.failed
    assert updated["error_msg"] == "TTS crashed"


async def test_update_job_invalid_column():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    with pytest.raises(ValueError, match="unknown columns"):
        await models.update_job(job["id"], nonexistent_col="oops")


async def test_delete_job():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    deleted = await models.delete_job(job["id"])
    assert deleted["id"] == job["id"]
    assert await models.get_job(job["id"]) is None


async def test_delete_job_not_found():
    result = await models.delete_job("does-not-exist")
    assert result is None


async def test_claim_next_pending_job():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    claimed = await models.claim_next_pending_job(max_concurrent=2)
    assert claimed is not None
    assert claimed["id"] == job["id"]
    assert claimed["status"] == JobStatus.processing


async def test_claim_returns_none_when_queue_empty():
    result = await models.claim_next_pending_job(max_concurrent=2)
    assert result is None


async def test_claim_respects_max_concurrent():
    # Create 3 pending jobs
    for i in range(3):
        await models.create_job(
            bookmark_id=f"bm{i}",
            bookmark_title=f"Article {i}",
            bookmark_url="http://example.com",
            tts_engine="edge-tts",
            voice="en-US-AriaNeural",
        )

    # Claim up to max_concurrent=2; the 3rd claim should be blocked
    first = await models.claim_next_pending_job(max_concurrent=2)
    second = await models.claim_next_pending_job(max_concurrent=2)
    third = await models.claim_next_pending_job(max_concurrent=2)

    assert first is not None
    assert second is not None
    assert third is None  # concurrency cap reached

    _, total_processing = await models.list_jobs()
    jobs, _ = await models.list_jobs()
    processing = [j for j in jobs if j["status"] == JobStatus.processing]
    assert len(processing) == 2
