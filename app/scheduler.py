"""Cron schedules for the background tasks: the Readeck sync and auto generation.

One loop wakes every TICK_SECONDS (or sooner when settings change), asks each
schedule for its current cron expression, and runs the ones that are due. A
schedule whose cron changes, or that is newly enabled, plans its first run for
the next cron slot rather than running straight away.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from croniter import croniter

logger = logging.getLogger(__name__)

# How often the loop wakes to check the clock and pick up edited settings.
TICK_SECONDS = 30


def valid_cron(expr: str) -> bool:
    return croniter.is_valid(expr)


def next_run(expr: str, after: datetime | None = None) -> datetime:
    # Cron expressions are read in the server's local time zone (TZ), which is
    # what anyone writing "0 3 * * *" means; the result goes out as UTC.
    base = (after or datetime.now(UTC)).astimezone()
    return croniter(expr, base).get_next(datetime).astimezone(UTC)


@dataclass
class Schedule:
    name: str
    # The cron expression to follow right now, or None while the task is off.
    cron: Callable[[], Awaitable[str | None]]
    run: Callable[[], Awaitable[object]]
    planned: tuple[str, datetime] | None = field(default=None, init=False)

    def next_run(self, cron: str | None = None) -> datetime | None:
        """When this schedule will next run, if it is on and has planned one.

        With `cron`, a plan made for a different expression is ignored: just
        after the schedule is changed the loop has not replanned yet.
        """
        if not self.planned or (cron is not None and self.planned[0] != cron):
            return None
        return self.planned[1]

    async def tick(self) -> None:
        cron = await self.cron()
        if not cron or not valid_cron(cron):
            self.planned = None
            return
        if self.planned is None or self.planned[0] != cron:
            self.planned = (cron, next_run(cron))
        if datetime.now(UTC) >= self.planned[1]:
            self.planned = (cron, next_run(cron))
            try:
                await self.run()
            except Exception:
                logger.exception("Scheduled %s failed", self.name)


_task: asyncio.Task | None = None
_poke = asyncio.Event()
_schedules: list[Schedule] = []


def poke() -> None:
    """Wake the loop now, e.g. after a schedule was edited."""
    _poke.set()


async def _loop() -> None:
    while True:
        for schedule in _schedules:
            try:
                await schedule.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler error in %s", schedule.name)
        _poke.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_poke.wait(), timeout=TICK_SECONDS)


def start(schedules: list[Schedule]) -> None:
    global _task, _poke
    # The event must belong to the running loop.
    _poke = asyncio.Event()
    _schedules[:] = schedules
    _task = asyncio.create_task(_loop(), name="scheduler")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
    for schedule in _schedules:
        schedule.planned = None
    _schedules.clear()
