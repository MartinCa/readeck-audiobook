"""Tests for automatic audio generation."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from app import autogen, models


@pytest.fixture(autouse=True)
def _use_db(db):
    """Pull in the db fixture for every test in this module."""


@pytest.fixture
def articles(store_bookmarks, monkeypatch):
    """Store articles locally, added one day apart in the order given."""
    monkeypatch.setattr("app.readeck.get_bookmarks", AsyncMock(return_value={}))

    async def install(*ids: str, **extra):
        await store_bookmarks(
            [
                {
                    "id": bid,
                    "title": bid,
                    "url": "http://x",
                    "type": "article",
                    "created": f"2026-01-{day:02d}T12:00:00Z",
                    **extra,
                }
                for day, bid in enumerate(ids, start=1)
            ]
        )

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


async def test_run_queues_new_articles_oldest_first(articles):
    await articles("oldest", "older", "newest")
    assert await autogen.run_once() == (3, 0)
    jobs, _ = await models.list_jobs()
    queued_order = [j["bookmark_id"] for j in reversed(jobs)]
    assert queued_order == ["oldest", "older", "newest"]


async def test_run_uses_the_local_copy_for_titles(articles):
    await articles("a", lang="de")
    await autogen.run_once()
    jobs, _ = await models.list_jobs()
    assert (jobs[0]["bookmark_title"], jobs[0]["lang"]) == ("a", "de")


async def test_run_limits_to_articles_added_since_the_start_date(articles):
    await articles("jan-1", "jan-2", "jan-3")
    await autogen.run_once(autogen.AutoGenSettings(enabled=True, since=date(2026, 1, 2)))
    jobs, _ = await models.list_jobs()
    assert {j["bookmark_id"] for j in jobs} == {"jan-2", "jan-3"}


async def test_run_only_takes_loaded_articles(articles, store_bookmarks):
    await articles("article")
    await store_bookmarks(
        [
            {"id": "video", "type": "video", "created": "2026-01-05T00:00:00Z"},
            {"id": "loading", "type": "article", "loaded": False, "created": "2026-01-05"},
            {"id": "bin", "type": "article", "is_deleted": True, "created": "2026-01-05"},
        ]
    )
    assert await autogen.run_once() == (1, 0)


async def test_run_skips_excluded_and_previously_queued_bookmarks(articles):
    await articles("excluded", "had-a-job", "deleted-audio", "fresh")
    await models.set_auto_excluded(["excluded"], True)
    await models.create_job("had-a-job", "T", "http://x", "edge-tts", "v")
    deleted = await models.create_job("deleted-audio", "T", "http://x", "edge-tts", "v")
    await models.delete_job(deleted["id"])

    assert await autogen.run_once() == (1, 0)
    jobs, _ = await models.list_jobs()
    assert {j["bookmark_id"] for j in jobs} == {"had-a-job", "fresh"}


async def test_run_is_idempotent(articles):
    await articles("a")
    assert await autogen.run_once() == (1, 0)
    assert await autogen.run_once() == (0, 0)


async def test_run_records_its_outcome(articles, monkeypatch):
    await articles("a")
    await autogen.run_once()
    state = await autogen.load_run_state()
    assert state.last_queued == 1
    assert state.last_error is None
    assert state.last_run is not None

    monkeypatch.setattr(
        "app.models.auto_generation_candidates",
        AsyncMock(side_effect=RuntimeError("database is locked")),
    )
    with pytest.raises(RuntimeError):
        await autogen.run_once()
    state = await autogen.load_run_state()
    assert state.last_error == "database is locked"
    assert state.last_queued is None


async def test_the_schedule_follows_the_enabled_setting():
    assert await autogen.schedule.cron() is None
    await autogen.save_settings(autogen.AutoGenSettings(enabled=True, cron="5 4 * * *"))
    assert await autogen.schedule.cron() == "5 4 * * *"


async def test_run_waits_for_readeck_to_extract_an_article(articles, store_bookmarks):
    await articles("a")
    await store_bookmarks(
        [
            {
                "id": "empty",
                "title": "empty",
                "url": "http://x",
                "type": "article",
                "created": "2026-01-09T12:00:00Z",
                "has_article": False,
            }
        ]
    )
    assert await autogen.run_once() == (1, 0)
    # Not recorded as queued, so it is eligible once Readeck extracts text.
    await store_bookmarks(
        [
            {
                "id": "empty",
                "title": "empty",
                "url": "http://x",
                "type": "article",
                "created": "2026-01-09T12:00:00Z",
                "has_article": True,
            }
        ],
        version="v2",
    )
    assert await autogen.run_once() == (1, 0)
