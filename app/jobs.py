"""Background job worker."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config, models, readeck, tts

logger = logging.getLogger(__name__)

MAX_CONCURRENT = config.MAX_CONCURRENT_JOBS

DEFAULT_MIN_ARTICLE_WORDS = 30

_worker_task: asyncio.Task | None = None
# Strong references to in-flight jobs. asyncio only holds tasks weakly, so a
# task that nothing references can be garbage collected mid-execution.
_inflight: set[asyncio.Task] = set()


class ArticleTooShort(ValueError):
    """Readeck handed over too little text to be worth an audiobook."""


async def load_min_article_words() -> int:
    raw = (await models.get_settings()).get("min_article_words")
    return int(raw) if raw else DEFAULT_MIN_ARTICLE_WORDS


async def save_min_article_words(words: int) -> None:
    await models.set_settings({"min_article_words": str(words)})


async def _process_job(job: dict):
    job_id = job["id"]
    # Job is already marked 'processing' by claim_next_pending_job
    try:
        text = await readeck.get_article_text(job["bookmark_id"])
        # A failed extraction can still come back as a title and a stray
        # character; reading that out would produce a useless audio file.
        words, minimum = tts.count_words(text), await load_min_article_words()
        if words < minimum:
            raise ArticleTooShort(
                f"Readeck extracted only {words} words of text (the minimum is {minimum}). "
                "Re-extract the bookmark in Readeck, then retry."
            )
        audio_path = await tts.generate_audio(
            job_id,
            text,
            title=job.get("bookmark_title") or "",
            url=job.get("bookmark_url") or "",
            engine=job.get("tts_engine") or "",
            voice=job.get("voice") or "",
            lang=job.get("lang") or "",
            on_progress=lambda done, total: models.set_job_progress(job_id, done, total),
        )
        await models.update_job(
            job_id,
            status=models.JobStatus.completed,
            audio_path=audio_path.name,
            duration_seconds=await asyncio.to_thread(tts.audio_duration, audio_path),
            error_msg=None,
        )
        if not await models.get_job(job_id):
            # The bookmark was removed by a sync while this job was running.
            audio_path.unlink(missing_ok=True)
            return
        logger.info("Job %s completed: %s", job_id, audio_path.name)
        # A bookmark has one audio file: a regeneration replaces the old one.
        superseded = await models.delete_superseded_audio(job["bookmark_id"])
        remove_audio_files(superseded)
    except readeck.BookmarkGone:
        # Deleted in Readeck since it was queued: there is nothing to read,
        # and nothing of it worth keeping here either.
        logger.info("Job %s: bookmark %s is gone from Readeck", job_id, job["bookmark_id"])
        await forget_bookmarks([job["bookmark_id"]])
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


async def forget_bookmarks(bookmark_ids: list[str], *, keep_rows: bool = False) -> None:
    """Drop bookmarks deleted, archived or read in Readeck: jobs, audio files and,
    unless `keep_rows`, the bookmark rows."""
    if bookmark_ids:
        remove_audio_files(await models.delete_bookmarks(bookmark_ids, keep_rows=keep_rows))


async def apply_readeck_action(
    action: Callable[[str], Awaitable[None]], bookmark_ids: list[str], *, keep_rows: bool
) -> tuple[list[str], int]:
    """Run a Readeck action on each bookmark, then forget the ones it worked for here.

    A bookmark already gone from Readeck counts as done: that is the state the
    action was after. Returns (ids done, number failed); a failed bookmark is left
    alone.
    """
    ids = list(dict.fromkeys(bookmark_ids))
    limiter = asyncio.Semaphore(4)

    async def attempt(bookmark_id: str) -> bool:
        async with limiter:
            try:
                await action(bookmark_id)
            except readeck.BookmarkGone:
                pass
            except readeck.ReadeckError as exc:
                logger.warning("Readeck action failed for bookmark %s: %s", bookmark_id, exc)
                return False
            return True

    outcomes = await asyncio.gather(*(attempt(bid) for bid in ids))
    done = [bid for bid, ok in zip(ids, outcomes, strict=True) if ok]
    await forget_bookmarks(done, keep_rows=keep_rows)
    return done, len(ids) - len(done)


async def backfill_durations() -> int:
    """Measure audio generated before lengths were recorded; returns how many."""
    measured = 0
    for job_id, filename in await models.audio_missing_duration():
        path = tts.AUDIO_DIR / Path(filename).name
        if not path.is_file():
            # A row whose file is gone can never be measured; leave it quietly
            # rather than warn about it on every start.
            logger.debug("Audio file %s is missing; skipping its length", path.name)
            continue
        seconds = await asyncio.to_thread(tts.audio_duration, path)
        if seconds is not None:
            await models.update_job(job_id, duration_seconds=seconds)
            measured += 1
    return measured


def remove_audio_files(job_rows: list[dict]) -> None:
    """Delete the audio files of job rows that have just been deleted."""
    for row in job_rows:
        if row.get("audio_path"):
            (tts.AUDIO_DIR / Path(row["audio_path"]).name).unlink(missing_ok=True)


@dataclass
class QueueOutcome:
    queued: int = 0
    skipped: int = 0
    # Bookmarks Readeck extracted no article from: nothing to read out.
    no_article: int = 0


async def queue_bookmarks(bookmark_ids: list[str]) -> QueueOutcome:
    """Queue a job per bookmark, skipping any with a job already pending or running.

    Titles and languages come from the local copy of Readeck; a bookmark not
    synced yet is fetched.
    """
    # De-duplicate while preserving order; a double submit can repeat ids.
    unique_ids = list(dict.fromkeys(bookmark_ids))
    queued_ids = [bid for bid in unique_ids if not await models.get_active_job_for_bookmark(bid)]

    bookmarks: dict[str, dict[str, Any]] = await models.get_bookmark_rows(queued_ids)
    # The language is stored on the job so the worker need not look it up.
    bookmarks.update(await readeck.get_bookmarks([b for b in queued_ids if b not in bookmarks]))
    outcome = QueueOutcome()
    readable = [bid for bid in queued_ids if has_article(bookmarks.get(bid, {}))]
    outcome.no_article = len(queued_ids) - len(readable)
    # A new attempt replaces an earlier failure rather than piling up beside it.
    await models.delete_failed_jobs(readable)

    for bid in readable:
        bm = bookmarks.get(bid, {})
        lang = bm.get("lang") or ""
        engine, voice = tts.resolve_engine_and_voice(lang)
        # The check above is a cheap pre-filter; the insert re-checks
        # atomically in case another caller (say the auto generation
        # scheduler) queued the same bookmark in the meantime.
        job = await models.create_job(
            bookmark_id=bid,
            bookmark_title=bm.get("title") or bid,
            bookmark_url=bm.get("url") or "",
            lang=lang,
            tts_engine=engine,
            voice=voice,
            only_if_idle=True,
        )
        outcome.queued += job is not None
    outcome.skipped = len(unique_ids) - outcome.queued - outcome.no_article
    return outcome


def has_article(bookmark: dict[str, Any]) -> bool:
    """Whether Readeck extracted article text; a row or Readeck's own JSON."""
    return bookmark.get("has_article") not in (0, False)


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
