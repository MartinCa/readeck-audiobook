"""Background job worker."""
import asyncio
import logging
import os

from app import models, readeck, tts

logger = logging.getLogger(__name__)
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))

_worker_task: asyncio.Task | None = None


async def _process_job(job: dict):
    job_id = job["id"]
    # Job is already marked 'processing' by claim_next_pending_job
    try:
        text = await readeck.get_article_text(job["bookmark_id"])
        bookmark = await readeck.get_bookmark(job["bookmark_id"])
        lang = bookmark.get("lang", "")

        epub_bytes = None
        if tts.TTS_ENGINE == "ebook2audiobook":
            epub_bytes = await readeck.get_article_epub(job["bookmark_id"])

        audio_path = await tts.generate_audio(job_id, text, lang, epub_bytes)
        await models.update_job(
            job_id,
            status=models.JobStatus.completed,
            audio_path=audio_path.name,
        )
        logger.info("Job %s completed: %s", job_id, audio_path)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        await models.update_job(
            job_id,
            status=models.JobStatus.failed,
            error_msg=str(exc),
        )


async def worker_loop():
    """Continuously pick up pending jobs up to MAX_CONCURRENT at a time."""
    while True:
        try:
            job = await models.claim_next_pending_job(MAX_CONCURRENT)
            if job:
                asyncio.create_task(_process_job(job))
        except Exception:
            logger.exception("Worker loop error")
        await asyncio.sleep(2)


def start_worker():
    global _worker_task
    _worker_task = asyncio.create_task(worker_loop())


def stop_worker():
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        _worker_task = None
