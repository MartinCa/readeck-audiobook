"""Automatic audio generation: on a cron schedule, queue every new article.

Settings live in the database (they are edited from the UI), not in the
environment. A run looks through the local copy of Readeck (see app.sync) for
articles added on or after the optional start date and queues each one that has
never had a job and is not excluded, so every bookmark is picked up at most
once: deleting its audio or a failed job does not make the next run try again.
The worker fetches each article's text from Readeck when it gets to it.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from app import jobs, models, scheduler

logger = logging.getLogger(__name__)

DEFAULT_CRON = "0 * * * *"


@dataclass
class AutoGenSettings:
    enabled: bool = False
    since: date | None = None
    cron: str = DEFAULT_CRON


@dataclass
class RunState:
    last_run: datetime | None = None
    last_queued: int | None = None
    last_error: str | None = None


async def load_settings() -> AutoGenSettings:
    raw = await models.get_settings()
    since = raw.get("auto_generation_since")
    return AutoGenSettings(
        enabled=raw.get("auto_generation_enabled") == "1",
        since=date.fromisoformat(since) if since else None,
        cron=raw.get("auto_generation_cron") or DEFAULT_CRON,
    )


async def save_settings(settings: AutoGenSettings) -> None:
    await models.set_settings(
        {
            "auto_generation_enabled": "1" if settings.enabled else "0",
            "auto_generation_since": settings.since.isoformat() if settings.since else None,
            "auto_generation_cron": settings.cron,
        }
    )
    scheduler.poke()


async def load_run_state() -> RunState:
    raw = await models.get_settings()
    last_run = raw.get("auto_generation_last_run")
    last_queued = raw.get("auto_generation_last_queued")
    return RunState(
        last_run=datetime.fromisoformat(last_run) if last_run else None,
        last_queued=int(last_queued) if last_queued else None,
        last_error=raw.get("auto_generation_last_error"),
    )


async def run_once(settings: AutoGenSettings | None = None) -> tuple[int, int]:
    """Queue every eligible article; returns (queued, skipped).

    Skipped are eligible articles that turned out to have a job running
    already, say one queued by hand while this run was looking.
    """
    settings = settings or await load_settings()
    state = RunState(last_run=datetime.now(UTC))
    try:
        # Videos and pictures have next to no text to read out, so only
        # articles qualify; the oldest go first so the backlog is worked
        # through in order.
        eligible = await models.auto_generation_candidates(
            f"{settings.since.isoformat()}T00:00:00+00:00" if settings.since else None
        )
        queued, skipped = await jobs.queue_bookmarks([bm["id"] for bm in eligible])
        state.last_queued = queued
        if queued:
            logger.info("Auto generation queued %d bookmark(s)", queued)
        return queued, skipped
    except Exception as exc:
        state.last_error = str(exc)
        raise
    finally:
        await _save_run_state(state)


async def _save_run_state(state: RunState) -> None:
    values = asdict(state)
    await models.set_settings(
        {
            "auto_generation_last_run": values["last_run"].isoformat(),
            "auto_generation_last_queued": (
                str(values["last_queued"]) if values["last_queued"] is not None else None
            ),
            "auto_generation_last_error": values["last_error"],
        }
    )


async def _scheduled_cron() -> str | None:
    settings = await load_settings()
    return settings.cron if settings.enabled else None


schedule = scheduler.Schedule("auto generation", _scheduled_cron, run_once)
