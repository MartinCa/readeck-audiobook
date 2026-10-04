"""Tests for the cron scheduler shared by the sync and auto generation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from app import scheduler


def _schedule(cron: str | None, run=None) -> scheduler.Schedule:
    return scheduler.Schedule("test", AsyncMock(return_value=cron), run or AsyncMock())


def test_cron_validation():
    assert scheduler.valid_cron("*/10 * * * *")
    assert not scheduler.valid_cron("every hour")
    assert not scheduler.valid_cron("")


def test_next_run_is_utc_and_in_the_future():
    after = datetime(2026, 1, 1, 10, 7, tzinfo=UTC)
    nxt = scheduler.next_run("0 * * * *", after)
    assert nxt.tzinfo == UTC
    assert nxt > after
    assert nxt - after <= timedelta(hours=1)


async def test_off_does_nothing():
    schedule = _schedule(None)
    await schedule.tick()
    schedule.run.assert_not_called()
    assert schedule.next_run() is None


async def test_switching_on_plans_the_next_slot_rather_than_running_now():
    schedule = _schedule("0 3 * * *")
    await schedule.tick()
    schedule.run.assert_not_called()
    assert schedule.next_run() > datetime.now(UTC)


async def test_runs_when_the_slot_arrives_and_plans_the_next():
    schedule = _schedule("0 3 * * *")
    schedule.planned = ("0 3 * * *", datetime.now(UTC) - timedelta(seconds=1))
    await schedule.tick()
    schedule.run.assert_awaited_once()
    assert schedule.next_run() > datetime.now(UTC)


async def test_a_changed_cron_replans():
    schedule = _schedule("0 3 * * *")
    await schedule.tick()
    schedule.cron.return_value = "*/5 * * * *"
    await schedule.tick()
    assert schedule.planned[0] == "*/5 * * * *"


async def test_a_plan_for_an_old_cron_is_not_reported():
    schedule = _schedule("0 3 * * *")
    await schedule.tick()
    assert schedule.next_run("0 3 * * *") is not None
    assert schedule.next_run("*/5 * * * *") is None


async def test_an_invalid_cron_is_treated_as_off():
    schedule = _schedule("nonsense")
    await schedule.tick()
    assert schedule.next_run() is None


async def test_a_failed_run_does_not_stop_the_schedule():
    schedule = _schedule("0 3 * * *", AsyncMock(side_effect=RuntimeError("down")))
    schedule.planned = ("0 3 * * *", datetime.now(UTC) - timedelta(seconds=1))
    await schedule.tick()  # logs, does not raise
    assert schedule.next_run() > datetime.now(UTC)
