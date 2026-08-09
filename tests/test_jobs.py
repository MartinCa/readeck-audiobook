"""Tests for the background worker in app.jobs."""

import asyncio

import pytest
import pytest_asyncio

from app import jobs, models, tts


@pytest.fixture(autouse=True)
def _use_db(db):
    """Pull in the db fixture for every test in this module."""


@pytest_asyncio.fixture(autouse=True)
async def _clear_inflight():
    jobs._inflight.clear()
    yield
    for task in list(jobs._inflight):
        task.cancel()
    await asyncio.gather(*jobs._inflight, return_exceptions=True)
    jobs._inflight.clear()


async def _queue(bookmark_id: str = "bm1", title: str = "An Article", **kwargs) -> dict:
    return await models.create_job(
        bookmark_id=bookmark_id,
        bookmark_title=title,
        bookmark_url="http://example.com/post",
        tts_engine=kwargs.pop("tts_engine", "edge-tts"),
        voice=kwargs.pop("voice", "en-US-AriaNeural"),
        **kwargs,
    )


class TestProcessJob:
    async def test_completes_and_records_the_audio_filename(self, monkeypatch, audio_dir):
        job = await _queue()
        claimed = await models.claim_next_pending_job(2)

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("# Title\n\nSome words."))

        async def fake_generate(job_id, text, **kwargs):
            path = audio_dir / "an-article-abc123.mp3"
            path.write_bytes(b"audio")
            return path

        monkeypatch.setattr(jobs.tts, "generate_audio", fake_generate)
        await jobs._process_job(claimed)

        updated = await models.get_job(job["id"])
        assert updated["status"] == models.JobStatus.completed
        assert updated["audio_path"] == "an-article-abc123.mp3"
        assert updated["error_msg"] is None

    async def test_passes_the_stored_engine_and_voice_through(self, monkeypatch, audio_dir):
        """The worker used to ignore the job's columns and re-read TTS_ENGINE,
        so the badge on the Jobs page could disagree with what actually ran."""
        await _queue(tts_engine="kokoro", voice="af_heart", lang="en")
        claimed = await models.claim_next_pending_job(2)
        seen = {}

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))

        async def fake_generate(job_id, text, **kwargs):
            seen.update(kwargs)
            path = audio_dir / "x.mp3"
            path.write_bytes(b"a")
            return path

        monkeypatch.setattr(jobs.tts, "generate_audio", fake_generate)
        await jobs._process_job(claimed)

        assert seen["engine"] == "kokoro"
        assert seen["voice"] == "af_heart"
        assert seen["lang"] == "en"
        assert seen["title"] == "An Article"
        assert seen["url"] == "http://example.com/post"

    async def test_does_not_refetch_the_bookmark(self, monkeypatch, audio_dir):
        """`lang` is stored at queue time, so the worker needs one Readeck call."""
        await _queue(lang="de")
        claimed = await models.claim_next_pending_job(2)

        async def explode(*args, **kwargs):
            raise AssertionError("worker should not call get_bookmark")

        monkeypatch.setattr(jobs.readeck, "get_bookmark", explode)
        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))
        monkeypatch.setattr(jobs.tts, "generate_audio", _writes(audio_dir / "x.mp3"))

        await jobs._process_job(claimed)
        assert (await models.get_job(claimed["id"]))["status"] == models.JobStatus.completed

    async def test_records_the_error_when_synthesis_fails(self, monkeypatch):
        job = await _queue()
        claimed = await models.claim_next_pending_job(2)

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))

        async def boom(*args, **kwargs):
            raise RuntimeError("edge-tts websocket closed")

        monkeypatch.setattr(jobs.tts, "generate_audio", boom)
        await jobs._process_job(claimed)

        updated = await models.get_job(job["id"])
        assert updated["status"] == models.JobStatus.failed
        assert "websocket closed" in updated["error_msg"]

    async def test_cancellation_returns_the_job_to_the_queue(self, monkeypatch):
        job = await _queue()
        claimed = await models.claim_next_pending_job(2)

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))

        async def never_finishes(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(jobs.tts, "generate_audio", never_finishes)

        task = asyncio.create_task(jobs._process_job(claimed))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await models.get_job(job["id"]))["status"] == models.JobStatus.pending


class TestWorkerLoop:
    async def test_fills_every_free_slot_in_one_pass(self, monkeypatch):
        """Claiming one job per tick meant a high MAX_CONCURRENT_JOBS took many
        seconds to reach full occupancy."""
        monkeypatch.setattr(jobs, "MAX_CONCURRENT", 3)
        for i in range(5):
            await _queue(f"bm{i}")

        started = []
        monkeypatch.setattr(jobs, "_spawn", lambda job: _fake_task(started, job))

        claimed = await jobs._claim_available_slots()
        assert claimed == 3
        assert len(started) == 3

    async def test_stops_when_the_queue_is_empty(self, monkeypatch):
        monkeypatch.setattr(jobs, "MAX_CONCURRENT", 5)
        await _queue("bm1")
        started = []
        monkeypatch.setattr(jobs, "_spawn", lambda job: _fake_task(started, job))

        assert await jobs._claim_available_slots() == 1

    async def test_keeps_a_strong_reference_to_running_jobs(self, monkeypatch, audio_dir):
        """asyncio only holds tasks weakly; an unreferenced task can be
        garbage collected mid-synthesis."""
        await _queue()
        claimed = await models.claim_next_pending_job(2)

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))
        monkeypatch.setattr(jobs.tts, "generate_audio", _writes(audio_dir / "x.mp3"))

        task = jobs._spawn(claimed)
        assert task in jobs._inflight
        await task
        assert task not in jobs._inflight  # discarded on completion


class TestStopWorker:
    async def test_waits_for_an_in_flight_job_to_finish(self, monkeypatch, audio_dir):
        job = await _queue()
        claimed = await models.claim_next_pending_job(2)

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))

        async def slow_generate(job_id, text, **kwargs):
            await asyncio.sleep(0.05)
            path = audio_dir / "x.mp3"
            path.write_bytes(b"a")
            return path

        monkeypatch.setattr(jobs.tts, "generate_audio", slow_generate)
        jobs._spawn(claimed)

        await jobs.stop_worker(timeout=5)

        assert (await models.get_job(job["id"]))["status"] == models.JobStatus.completed
        assert not jobs._inflight

    async def test_requeues_a_job_that_outlives_the_grace_period(self, monkeypatch):
        job = await _queue()
        claimed = await models.claim_next_pending_job(2)

        monkeypatch.setattr(jobs.readeck, "get_article_text", _returns("Words."))

        async def never_finishes(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(jobs.tts, "generate_audio", never_finishes)
        jobs._spawn(claimed)

        await jobs.stop_worker(timeout=0.05)

        # Requeued rather than lost, so the next boot picks it up.
        assert (await models.get_job(job["id"]))["status"] == models.JobStatus.pending

    async def test_is_safe_with_nothing_running(self):
        await jobs.stop_worker(timeout=1)


class TestCleanupOrphanedAudio:
    async def test_removes_unreferenced_and_partial_files(self, monkeypatch, audio_dir):
        job = await _queue()
        await models.update_job(
            job["id"], status=models.JobStatus.completed, audio_path="keep-me-abc123.mp3"
        )
        (audio_dir / "keep-me-abc123.mp3").write_bytes(b"a")
        (audio_dir / "orphan-def456.mp3").write_bytes(b"b")
        (audio_dir / "half-written.mp3.part").write_bytes(b"c")

        removed = await jobs.cleanup_orphaned_audio()

        assert removed == 2
        assert {p.name for p in audio_dir.iterdir()} == {"keep-me-abc123.mp3"}

    async def test_leaves_unrelated_files_alone(self, audio_dir):
        (audio_dir / "notes.txt").write_bytes(b"keep")
        assert await jobs.cleanup_orphaned_audio() == 0
        assert (audio_dir / "notes.txt").exists()

    async def test_handles_a_missing_audio_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path / "nope")
        assert await jobs.cleanup_orphaned_audio() == 0


# ── helpers ────────────────────────────────────────────────────────────────────


def _returns(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _writes(path):
    async def _inner(job_id, text, **kwargs):
        path.write_bytes(b"audio")
        return path

    return _inner


def _fake_task(collector, job):
    """Stand-in for _spawn that records the job and occupies a slot.

    The task blocks forever so the slot stays taken for the whole claim loop;
    the _clear_inflight fixture cancels whatever is left over.
    """
    collector.append(job)
    task = asyncio.create_task(asyncio.Event().wait())
    jobs._inflight.add(task)
    task.add_done_callback(jobs._inflight.discard)
    return task
