"""Tests for automatic audio generation and its scheduler."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app import autogen, models


@pytest.fixture(autouse=True)
def _use_db(db):
    """Pull in the db fixture for every test in this module."""


@pytest.fixture(autouse=True)
def _reset_schedule():
    autogen._next = None
    yield
    autogen._next = None


@pytest.fixture
def readeck_articles(monkeypatch):
    def install(*ids: str):
        listing = AsyncMock(return_value=[{"id": i, "title": i, "url": "http://x"} for i in ids])
        monkeypatch.setattr("app.readeck.list_all_bookmarks", listing)
        monkeypatch.setattr("app.readeck.get_bookmarks", AsyncMock(return_value={}))
        return listing

    return install


async def test_settings_round_trip():
    assert await autogen.load_settings() == autogen.AutoGenSettings()
    await autogen.save_settings(
        autogen.AutoGenSettings(enabled=True, since=date(2026, 3, 1), cron="5 4 * * *")
    )
    assert await autogen.load_settings() == autogen.AutoGenSettings(
        enabled=True, since=date(2026, 3, 1), cron="5 4 * * *"
    )
    await autogen.save_settings(autogen.AutoGenSettings(enabled=True, since=None))
    assert (await autogen.load_settings()).since is None


async def test_run_queues_new_articles_oldest_first(readeck_articles):
    # Readeck lists newest first.
    readeck_articles("newest", "older", "oldest")
    assert await autogen.run_once() == (3, 0)
    jobs, _ = await models.list_jobs()
    queued_order = [j["bookmark_id"] for j in reversed(jobs)]
    assert queued_order == ["oldest", "older", "newest"]


async def test_run_limits_to_articles_added_since_the_start_date(readeck_articles):
    listing = readeck_articles()
    await autogen.run_once(autogen.AutoGenSettings(enabled=True, since=date(2026, 2, 1)))
    assert listing.call_args.kwargs == {
        "range_start": "2026-02-01T00:00:00Z",
        "types": ("article",),
    }


async def test_run_without_a_start_date_covers_every_existing_article(readeck_articles):
    listing = readeck_articles()
    await autogen.run_once(autogen.AutoGenSettings(enabled=True))
    assert listing.call_args.kwargs["range_start"] == ""


async def test_run_skips_excluded_and_previously_queued_bookmarks(readeck_articles):
    readeck_articles("excluded", "had-a-job", "deleted-audio", "fresh")
    await models.set_auto_excluded(["excluded"], True)
    await models.create_job("had-a-job", "T", "http://x", "edge-tts", "v")
    deleted = await models.create_job("deleted-audio", "T", "http://x", "edge-tts", "v")
    await models.delete_job(deleted["id"])

    assert await autogen.run_once() == (1, 0)
    jobs, _ = await models.list_jobs()
    assert {j["bookmark_id"] for j in jobs} == {"had-a-job", "fresh"}


async def test_run_is_idempotent(readeck_articles):
    readeck_articles("a")
    assert await autogen.run_once() == (1, 0)
    assert await autogen.run_once() == (0, 0)


async def test_run_records_its_outcome(readeck_articles, monkeypatch):
    readeck_articles("a")
    await autogen.run_once()
    state = await autogen.load_run_state()
    assert state.last_queued == 1
    assert state.last_error is None
    assert state.last_run is not None

    monkeypatch.setattr(
        "app.readeck.list_all_bookmarks", AsyncMock(side_effect=RuntimeError("Readeck is down"))
    )
    with pytest.raises(RuntimeError):
        await autogen.run_once()
    state = await autogen.load_run_state()
    assert state.last_error == "Readeck is down"
    assert state.last_queued is None


def test_cron_validation():
    assert autogen.valid_cron("*/10 * * * *")
    assert not autogen.valid_cron("every hour")
    assert not autogen.valid_cron("")


def test_next_run_is_utc_and_in_the_future():
    after = datetime(2026, 1, 1, 10, 7, tzinfo=UTC)
    nxt = autogen.next_run("0 * * * *", after)
    assert nxt.tzinfo == UTC
    assert nxt > after
    assert nxt - after <= timedelta(hours=1)


class TestScheduler:
    async def test_disabled_does_nothing(self, monkeypatch):
        run = AsyncMock()
        monkeypatch.setattr(autogen, "run_once", run)
        await autogen._tick()
        run.assert_not_called()
        assert autogen.scheduled_next_run() is None

    async def test_enabling_plans_the_next_slot_rather_than_running_now(self, monkeypatch):
        run = AsyncMock()
        monkeypatch.setattr(autogen, "run_once", run)
        await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="0 3 * * *"))
        await autogen._tick()
        run.assert_not_called()
        assert autogen.scheduled_next_run() > datetime.now(UTC)

    async def test_runs_when_the_slot_arrives_and_plans_the_next(self, monkeypatch):
        run = AsyncMock()
        monkeypatch.setattr(autogen, "run_once", run)
        await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="0 3 * * *"))
        autogen._next = ("0 3 * * *", datetime.now(UTC) - timedelta(seconds=1))
        await autogen._tick()
        run.assert_awaited_once()
        assert autogen.scheduled_next_run() > datetime.now(UTC)

    async def test_a_changed_cron_replans(self, monkeypatch):
        monkeypatch.setattr(autogen, "run_once", AsyncMock())
        await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="0 3 * * *"))
        await autogen._tick()
        await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="*/5 * * * *"))
        await autogen._tick()
        assert autogen._next[0] == "*/5 * * * *"

    async def test_a_plan_for_an_old_cron_is_not_reported(self, monkeypatch):
        monkeypatch.setattr(autogen, "run_once", AsyncMock())
        await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="0 3 * * *"))
        await autogen._tick()
        assert autogen.scheduled_next_run("0 3 * * *") is not None
        assert autogen.scheduled_next_run("*/5 * * * *") is None

    async def test_a_failed_run_does_not_stop_the_scheduler(self, monkeypatch):
        monkeypatch.setattr(autogen, "run_once", AsyncMock(side_effect=RuntimeError("down")))
        await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="0 3 * * *"))
        autogen._next = ("0 3 * * *", datetime.now(UTC) - timedelta(seconds=1))
        await autogen._tick()  # logs, does not raise
        assert autogen.scheduled_next_run() > datetime.now(UTC)
