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


async def test_list_job_ids():
    ids = []
    for i in range(3):
        job = await models.create_job(
            bookmark_id=f"bm{i}",
            bookmark_title=f"Article {i}",
            bookmark_url="http://example.com",
            tts_engine="edge-tts",
            voice="en-US-AriaNeural",
        )
        ids.append(job["id"])
    assert set(await models.list_job_ids()) == set(ids)


async def test_list_job_ids_empty():
    assert await models.list_job_ids() == []


async def test_get_active_job_for_bookmark_pending():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    active = await models.get_active_job_for_bookmark("bm1")
    assert active["id"] == job["id"]


async def test_get_active_job_for_bookmark_none_when_terminal():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    await models.update_job(job["id"], status=JobStatus.completed, audio_path="abc.mp3")
    assert await models.get_active_job_for_bookmark("bm1") is None


async def test_get_active_job_for_bookmark_not_found():
    assert await models.get_active_job_for_bookmark("does-not-exist") is None


async def test_requeue_interrupted_jobs_puts_them_back_in_the_queue():
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    await models.claim_next_pending_job(max_concurrent=2)

    requeued, failed = await models.requeue_interrupted_jobs()
    assert (requeued, failed) == (1, 0)

    updated = await models.get_job(job["id"])
    assert updated["status"] == JobStatus.pending
    assert updated["attempts"] == 1


async def test_requeue_interrupted_jobs_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(models.config, "MAX_JOB_ATTEMPTS", 2)
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    # Two crashed runs: each claim increments `attempts`.
    for _ in range(2):
        await models.claim_next_pending_job(max_concurrent=2)
        requeued, failed = await models.requeue_interrupted_jobs()

    assert (requeued, failed) == (0, 1)
    updated = await models.get_job(job["id"])
    assert updated["status"] == JobStatus.failed
    assert "giving up" in updated["error_msg"]


async def test_requeue_interrupted_jobs_leaves_others_alone():
    await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    assert await models.requeue_interrupted_jobs() == (0, 0)
    jobs, _ = await models.list_jobs()
    assert jobs[0]["status"] == JobStatus.pending


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


async def test_job_progress_only_moves_forward_while_running():
    job = await models.create_job("bm1", "Test", "http://example.com", "edge-tts", "v")
    # Not running yet: nothing to record.
    await models.set_job_progress(job["id"], 1, 4)
    assert (await models.get_job(job["id"]))["progress_total"] is None

    await models.claim_next_pending_job(max_concurrent=2)
    await models.set_job_progress(job["id"], 2, 4)
    await models.set_job_progress(job["id"], 1, 4)  # arrived late
    row = await models.get_job(job["id"])
    assert (row["progress_done"], row["progress_total"]) == (2, 4)

    await models.update_job(job["id"], status=JobStatus.completed)
    await models.set_job_progress(job["id"], 3, 4)
    assert (await models.get_job(job["id"]))["progress_done"] == 2


async def test_claim_starts_progress_afresh():
    job = await models.create_job("bm1", "Test", "http://example.com", "edge-tts", "v")
    await models.claim_next_pending_job(max_concurrent=2)
    await models.set_job_progress(job["id"], 3, 4)
    # Interrupted and requeued: the next attempt starts from the beginning.
    await models.update_job(job["id"], status=JobStatus.pending)
    claimed = await models.claim_next_pending_job(max_concurrent=2)
    assert (claimed["progress_done"], claimed["progress_total"]) == (None, None)


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


async def _make(bookmark_id: str, title: str = "Test", **kwargs):
    return await models.create_job(
        bookmark_id=bookmark_id,
        bookmark_title=title,
        bookmark_url="http://example.com",
        tts_engine=kwargs.pop("tts_engine", "edge-tts"),
        voice=kwargs.pop("voice", "en-US-AriaNeural"),
        **kwargs,
    )


@pytest.fixture
def tied_timestamps(monkeypatch):
    """Force every row to share one `created_at`.

    The original schema used SQLite's `datetime('now')`, whose whole-second
    resolution made this the normal case for a bulk-queued batch. Timestamps
    are microsecond-resolution now, so the tie has to be staged to prove the
    rowid tiebreaker still orders correctly when one does occur.
    """
    monkeypatch.setattr(models, "_now", lambda: "2026-08-09T12:00:00+00:00")


async def test_jobs_queued_in_one_batch_list_newest_first():
    created = [await _make(f"bm{i}", f"Article {i}") for i in range(10)]

    listed, total = await models.list_jobs(limit=10)
    assert total == 10
    assert [j["id"] for j in listed] == [j["id"] for j in reversed(created)]


async def test_tied_timestamps_still_list_newest_first(tied_timestamps):
    """Without the rowid tiebreaker SQLite falls back to insertion order here,
    silently reversing the intended newest-first listing."""
    created = [await _make(f"bm{i}", f"Article {i}") for i in range(10)]
    assert {j["created_at"] for j in created} == {"2026-08-09T12:00:00+00:00"}

    listed, _ = await models.list_jobs(limit=10)
    assert [j["id"] for j in listed] == [j["id"] for j in reversed(created)]


async def test_pagination_is_stable_across_a_tied_batch(tied_timestamps):
    created = [await _make(f"bm{i}", f"Article {i}") for i in range(10)]
    expected = [j["id"] for j in reversed(created)]

    seen = []
    for offset in range(0, 10, 3):
        page, _ = await models.list_jobs(limit=3, offset=offset)
        seen.extend(j["id"] for j in page)

    assert seen == expected  # no duplicates, nothing skipped


async def test_list_jobs_clamps_negative_offset():
    await _make("bm1")
    jobs, _ = await models.list_jobs(limit=5, offset=-10)
    assert len(jobs) == 1


async def test_create_job_stores_lang():
    job = await _make("bm1", lang="de")
    assert job["lang"] == "de"
    assert job["attempts"] == 0


async def test_get_jobs_batch():
    a = await _make("bm1")
    b = await _make("bm2")
    await _make("bm3")

    found = await models.get_jobs([a["id"], b["id"], "missing"])
    assert {j["id"] for j in found} == {a["id"], b["id"]}


async def test_get_jobs_empty_list_does_not_query():
    assert await models.get_jobs([]) == []


async def test_list_audio_paths_only_returns_referenced_files():
    a = await _make("bm1")
    await _make("bm2")
    await models.update_job(a["id"], status=JobStatus.completed, audio_path="my-article-abc123.mp3")

    assert await models.list_audio_paths() == {"my-article-abc123.mp3"}


async def test_delete_job_returns_the_deleted_row():
    job = await _make("bm1")
    deleted = await models.delete_job(job["id"])
    assert deleted["id"] == job["id"]
    assert await models.get_job(job["id"]) is None


async def test_claim_increments_attempts():
    job = await _make("bm1")
    claimed = await models.claim_next_pending_job(max_concurrent=2)
    assert claimed["attempts"] == 1
    assert claimed["id"] == job["id"]


async def test_claim_takes_oldest_pending_first():
    first = await _make("bm1", "First")
    second = await _make("bm2", "Second")

    assert (await models.claim_next_pending_job(max_concurrent=5))["id"] == first["id"]
    assert (await models.claim_next_pending_job(max_concurrent=5))["id"] == second["id"]


async def test_init_db_migrates_a_database_without_the_new_columns(tmp_path, monkeypatch):
    """A database created before `lang`/`attempts` existed must still open."""
    import aiosqlite

    path = tmp_path / "legacy.db"
    monkeypatch.setattr(models, "DB_PATH", str(path))
    async with aiosqlite.connect(str(path)) as legacy:
        await legacy.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, bookmark_id TEXT NOT NULL, "
            "bookmark_title TEXT, bookmark_url TEXT, status TEXT NOT NULL DEFAULT 'pending', "
            "tts_engine TEXT, voice TEXT, created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT, audio_path TEXT, error_msg TEXT)"
        )
        await legacy.execute(
            "INSERT INTO jobs (id, bookmark_id, bookmark_title) VALUES ('old', 'bm1', 'Legacy')"
        )
        await legacy.commit()

    await models.init_db()

    job = await models.get_job("old")
    assert job["bookmark_title"] == "Legacy"
    assert job["lang"] is None
    assert job["attempts"] == 0


# ── Audio, exclusion and settings ──────────────────────────────────────────────


async def _job(bookmark_id: str, status: JobStatus | None = None, audio_path: str = "") -> dict:
    job = await models.create_job(bookmark_id, "T", "http://x", "edge-tts", "v")
    if status:
        await models.update_job(job["id"], status=status, audio_path=audio_path or None)
    return await models.get_job(job["id"])


async def test_audio_by_bookmark_returns_the_newest_completed_job():
    await _job("a", JobStatus.completed, "old.mp3")
    newest = await _job("a", JobStatus.completed, "new.mp3")
    await _job("a", JobStatus.failed)
    await _job("b")

    audio = await models.audio_by_bookmark(["a", "b"])
    assert set(audio) == {"a"}
    assert audio["a"]["id"] == newest["id"]
    assert set(await models.audio_by_bookmark()) == {"a"}


async def test_latest_jobs_by_bookmark():
    await _job("a", JobStatus.completed, "a.mp3")
    failed = await _job("a", JobStatus.failed)
    latest = await models.latest_jobs_by_bookmark(["a", "missing"])
    assert latest == {"a": failed}


async def test_delete_superseded_audio_keeps_the_latest_completion():
    old = await _job("a", JobStatus.completed, "old.mp3")
    keep = await _job("a", JobStatus.completed, "new.mp3")
    pending = await _job("a")
    other = await _job("b", JobStatus.completed, "b.mp3")

    deleted = await models.delete_superseded_audio("a")
    assert [d["id"] for d in deleted] == [old["id"]]
    assert await models.get_job(keep["id"])
    assert await models.get_job(pending["id"])
    assert await models.get_job(other["id"])
    # Running it again for the other completion agrees on the same survivor.
    assert await models.delete_superseded_audio("a") == []


async def test_open_statuses_leave_out_completed():
    await _job("a", JobStatus.completed, "a.mp3")
    pending = await _job("b")
    jobs, total = await models.list_jobs(statuses=models.OPEN_STATUSES)
    assert total == 1
    assert jobs[0]["id"] == pending["id"]
    assert await models.list_job_ids(statuses=models.OPEN_STATUSES) == [pending["id"]]


async def test_auto_exclusion():
    await models.set_auto_excluded(["a", "b", "a"], True)
    assert await models.auto_excluded_ids() == {"a", "b"}
    await models.set_auto_excluded(["a"], False)
    assert await models.auto_excluded_ids() == {"b"}


async def test_queued_bookmarks_outlive_their_jobs():
    job = await _job("a")
    await models.delete_job(job["id"])
    assert await models.queued_bookmark_ids() == {"a"}


async def test_init_db_backfills_queued_bookmarks_from_existing_jobs():
    await _job("a")
    async with models._connect() as db:
        await db.execute("DELETE FROM queued_bookmarks")
    await models.init_db()
    assert await models.queued_bookmark_ids() == {"a"}


async def test_settings_upsert_and_remove():
    await models.set_settings({"x": "1", "y": "2"})
    await models.set_settings({"x": "3", "y": None})
    assert await models.get_settings() == {"x": "3"}


async def test_retry_job_only_touches_failed_jobs():
    pending = await _job("a")
    assert await models.retry_job(pending["id"]) is None
    failed = await _job("b", JobStatus.failed)
    retried = await models.retry_job(failed["id"])
    assert retried["status"] == JobStatus.pending
