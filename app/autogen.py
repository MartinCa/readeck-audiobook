"""Automatic audio generation: on a cron schedule, queue every new article.

Settings live in the database (they are edited from the UI), not in the
environment. A run reads the Readeck articles added on or after the optional
start date and queues each one that has never had a job and is not excluded,
so every bookmark is picked up at most once: deleting its audio or a failed job
does not make the next run try again.
"""

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from croniter import croniter

from app import jobs, models, readeck

logger = logging.getLogger(__name__)

DEFAULT_CRON = "0 * * * *"
# How often the scheduler wakes to check the clock and pick up edited settings.
TICK_SECONDS = 30


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


def valid_cron(expr: str) -> bool:
    return croniter.is_valid(expr)


def next_run(expr: str, after: datetime | None = None) -> datetime:
    # Cron expressions are read in the server's local time zone (TZ), which is
    # what anyone writing "0 3 * * *" means; the result goes out as UTC.
    base = (after or datetime.now(UTC)).astimezone()
    return croniter(expr, base).get_next(datetime).astimezone(UTC)


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
    _poke.set()


async def load_run_state() -> RunState:
    raw = await models.get_settings()
    last_run = raw.get("auto_generation_last_run")
    last_queued = raw.get("auto_generation_last_queued")
    return RunState(
        last_run=datetime.fromisoformat(last_run) if last_run else None,
        last_queued=int(last_queued) if last_queued else None,
        last_error=raw.get("auto_generation_last_error"),
    )


async def run_once(settings: AutoGenSettings | None = None) -> int:
    """Queue every eligible article; returns how many were queued."""
    settings = settings or await load_settings()
    state = RunState(last_run=datetime.now(UTC))
    try:
        candidates = await readeck.list_all_bookmarks(
            range_start=f"{settings.since.isoformat()}T00:00:00Z" if settings.since else "",
            # Videos and pictures have next to no text to read out.
            types=("article",),
        )
        skip = await models.queued_bookmark_ids() | await models.auto_excluded_ids()
        eligible = {bm["id"]: bm for bm in candidates if bm["id"] not in skip}
        # Oldest first, so the queue works through the backlog in order.
        queued, _ = await jobs.queue_bookmarks(list(reversed(eligible)), prefetched=eligible)
        state.last_queued = queued
        if queued:
            logger.info("Auto generation queued %d bookmark(s)", queued)
        return queued
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


# ── Scheduler ──────────────────────────────────────────────────────────────────

_task: asyncio.Task | None = None
_poke = asyncio.Event()
_next: tuple[str, datetime] | None = None


def scheduled_next_run() -> datetime | None:
    """When the scheduler will next run, if it is enabled and has planned one."""
    return _next[1] if _next else None


async def _tick() -> None:
    global _next
    settings = await load_settings()
    if not settings.enabled or not valid_cron(settings.cron):
        _next = None
        return
    if _next is None or _next[0] != settings.cron:
        # Newly enabled or rescheduled: the first run is the next cron slot,
        # not right now.
        _next = (settings.cron, next_run(settings.cron))
    if datetime.now(UTC) >= _next[1]:
        _next = (settings.cron, next_run(settings.cron))
        try:
            await run_once(settings)
        except Exception:
            logger.exception("Auto generation run failed")


async def scheduler_loop() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auto generation scheduler error")
        _poke.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_poke.wait(), timeout=TICK_SECONDS)


def start_scheduler() -> None:
    global _task, _poke
    # The event must belong to the running loop.
    _poke = asyncio.Event()
    _task = asyncio.create_task(scheduler_loop(), name="auto-generation")


async def stop_scheduler() -> None:
    global _task, _next
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
    _next = None
