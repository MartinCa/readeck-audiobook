"""Background job worker."""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from app import config, models, readeck, tts

logger = logging.getLogger(__name__)

MAX_CONCURRENT = config.MAX_CONCURRENT_JOBS

_worker_task: asyncio.Task | None = None
# Strong references to in-flight jobs. asyncio only holds tasks weakly, so a
# task that nothing references can be garbage collected mid-execution.
_inflight: set[asyncio.Task] = set()


async def _process_job(job: dict):
    job_id = job["id"]
    # Job is already marked 'processing' by claim_next_pending_job
    try:
        text = await readeck.get_article_text(job["bookmark_id"])
        audio_path = await tts.generate_audio(
            job_id,
            text,
            title=job.get("bookmark_title") or "",
            url=job.get("bookmark_url") or "",
            engine=job.get("tts_engine") or "",
            voice=job.get("voice") or "",
            lang=job.get("lang") or "",
        )
        await models.update_job(
            job_id,
            status=models.JobStatus.completed,
            audio_path=audio_path.name,
            error_msg=None,
        )
        logger.info("Job %s completed: %s", job_id, audio_path.name)
        # A bookmark has one audio file: a regeneration replaces the old one.
        superseded = await models.delete_completed_jobs([job["bookmark_id"]], keep_job_id=job_id)
        remove_audio_files(superseded)
    except asyncio.CancelledError:
        # Shutdown: hand the job back to the queue so the next boot resumes it.
        logger.info("Job %s cancelled during shutdown; returning it to the queue", job_id)
        with contextlib.suppress(Exception):
            await models.update_job(job_id, status=models.JobStatus.pending)
        raise
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        await models.update_job(
            job_id,
            status=models.JobStatus.failed,
            error_msg=str(exc),
        )


def remove_audio_files(job_rows: list[dict]) -> None:
    """Delete the audio files of job rows that have just been deleted."""
    for row in job_rows:
        if row.get("audio_path"):
            (tts.AUDIO_DIR / Path(row["audio_path"]).name).unlink(missing_ok=True)


async def queue_bookmarks(
    bookmark_ids: list[str], prefetched: dict[str, dict[str, Any]] | None = None
) -> tuple[int, int]:
    """Queue a job per bookmark, skipping any with a job already pending or running.

    `prefetched` maps ids to Readeck bookmarks the caller already has; the rest
    are fetched. Returns (queued, skipped).
    """
    # De-duplicate while preserving order; a double submit can repeat ids.
    unique_ids = list(dict.fromkeys(bookmark_ids))
    queued_ids = [bid for bid in unique_ids if not await models.get_active_job_for_bookmark(bid)]
    skipped = len(unique_ids) - len(queued_ids)

    bookmarks = dict(prefetched or {})
    # One concurrent fetch for the whole batch rather than a sequential round
    # trip per bookmark, and the language is stored so the worker need not
    # fetch the bookmark a second time.
    bookmarks.update(await readeck.get_bookmarks([b for b in queued_ids if b not in bookmarks]))

    for bid in queued_ids:
        bm = bookmarks.get(bid, {})
        lang = bm.get("lang") or ""
        engine, voice = tts.resolve_engine_and_voice(lang)
        await models.create_job(
            bookmark_id=bid,
            bookmark_title=bm.get("title") or bid,
            bookmark_url=bm.get("url") or "",
            lang=lang,
            tts_engine=engine,
            voice=voice,
        )
    return len(queued_ids), skipped


def _spawn(job: dict) -> asyncio.Task:
    task = asyncio.create_task(_process_job(job), name=f"job-{job['id']}")
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    return task


async def _claim_available_slots() -> int:
    """Claim pending jobs until the concurrency limit or the queue is reached."""
    started = 0
    while len(_inflight) < MAX_CONCURRENT:
        job = await models.claim_next_pending_job(MAX_CONCURRENT)
        if not job:
            break
        _spawn(job)
        started += 1
    return started


async def worker_loop():
    """Continuously pick up pending jobs up to MAX_CONCURRENT at a time."""
    while True:
        try:
            await _claim_available_slots()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker loop error")
        await asyncio.sleep(config.WORKER_POLL_SECONDS)


def start_worker():
    global _worker_task
    _worker_task = asyncio.create_task(worker_loop(), name="job-worker")


async def stop_worker(timeout: float | None = None):
    """Stop accepting work and let in-flight jobs finish within the grace period."""
    global _worker_task
    if timeout is None:
        timeout = config.SHUTDOWN_GRACE_SECONDS

    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _worker_task
    _worker_task = None

    if not _inflight:
        return

    pending = list(_inflight)
    logger.info("Waiting up to %ss for %d in-flight job(s)", timeout, len(pending))
    done, still_running = await asyncio.wait(pending, timeout=timeout)
    for task in still_running:
        task.cancel()
    if still_running:
        # _process_job requeues on cancellation, so these resume after restart.
        await asyncio.gather(*still_running, return_exceptions=True)
    logger.info("Shutdown complete: %d finished, %d requeued", len(done), len(still_running))


async def cleanup_orphaned_audio() -> int:
    """Delete audio files no job row refers to any more.

    Files can be orphaned by a crash between writing the MP3 and updating the
    job row, by a database restored from an older backup, or by an interrupted
    synthesis leaving a `.part` file behind.
    """
    audio_dir = tts.AUDIO_DIR
    if not audio_dir.exists():
        return 0
    referenced = await models.list_audio_paths()
    removed = 0
    for path in audio_dir.iterdir():
        if not path.is_file():
            continue
        # `.part` files are always leftovers: synthesis renames into place.
        if path.suffix == ".part" or (path.suffix == ".mp3" and path.name not in referenced):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not remove orphaned audio file %s: %s", path.name, exc)
    return removed
