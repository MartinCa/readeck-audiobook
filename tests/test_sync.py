"""Tests for keeping the local copy of Readeck in sync."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import models, readeck, sync


@pytest.fixture(autouse=True)
def _use_db(db):
    """Pull in the db fixture for every test in this module."""


def _bm(bid: str, **extra) -> dict:
    return {
        "id": bid,
        "title": f"Title {bid}",
        "url": f"https://example.com/{bid}",
        "type": "article",
        "created": "2026-01-01T10:00:00Z",
        **extra,
    }


@pytest.fixture
def remote(monkeypatch):
    """A fake Readeck: `remote.library` maps id -> (version, bookmark)."""

    class Remote:
        library: dict[str, tuple[str, dict]] = {}
        fetched: list[list[str]] = []

        async def sync_list(self):
            return {bid: version for bid, (version, _) in self.library.items()}

        async def fetch_bookmarks(self, ids):
            self.fetched.append(list(ids))
            return [self.library[bid][1] for bid in ids if bid in self.library]

    fake = Remote()
    fake.library = {}
    fake.fetched = []
    monkeypatch.setattr(readeck, "sync_list", fake.sync_list)
    monkeypatch.setattr(readeck, "fetch_bookmarks", fake.fetch_bookmarks)
    return fake


async def _stored() -> dict[str, dict]:
    return await models.get_bookmark_rows(list(await models.bookmark_versions()))


async def test_first_sync_stores_everything(remote):
    remote.library = {"a": ("1", _bm("a")), "b": ("1", _bm("b"))}
    state = await sync.run_once()
    assert (state.added, state.updated, state.removed) == (2, 0, 0)
    stored = await _stored()
    assert stored["a"]["title"] == "Title a"
    assert stored["a"]["created"] == "2026-01-01T10:00:00+00:00"


async def test_an_unchanged_library_fetches_nothing(remote):
    remote.library = {"a": ("1", _bm("a"))}
    await sync.run_once()
    remote.fetched.clear()
    state = await sync.run_once()
    assert remote.fetched == [[]]
    assert (state.added, state.updated, state.removed) == (0, 0, 0)


async def test_a_changed_bookmark_is_refetched_and_keeps_its_audio(remote):
    remote.library = {"a": ("1", _bm("a"))}
    await sync.run_once()
    job = await models.create_job("a", "Title a", "http://x", "edge-tts", "v")
    await models.update_job(job["id"], status=models.JobStatus.completed, audio_path="a.mp3")

    remote.library = {"a": ("2", _bm("a", title="Renamed"))}
    state = await sync.run_once()
    assert (state.added, state.updated) == (0, 1)
    assert (await _stored())["a"]["title"] == "Renamed"
    assert await models.audio_by_bookmark(["a"])


async def test_a_bookmark_deleted_in_readeck_is_forgotten_with_its_audio(remote, audio_dir):
    remote.library = {"a": ("1", _bm("a")), "b": ("1", _bm("b"))}
    await sync.run_once()
    job = await models.create_job("a", "Title a", "http://x", "edge-tts", "v")
    await models.update_job(job["id"], status=models.JobStatus.completed, audio_path="a.mp3")
    (audio_dir / "a.mp3").write_bytes(b"x")
    pending = await models.create_job("a", "Title a", "http://x", "edge-tts", "v")
    await models.set_auto_excluded(["a"], True)

    del remote.library["a"]
    state = await sync.run_once()
    assert state.removed == 1
    assert set(await _stored()) == {"b"}
    assert not (audio_dir / "a.mp3").exists()
    assert await models.get_job(job["id"]) is None
    assert await models.get_job(pending["id"]) is None
    assert await models.auto_excluded_ids() == set()
    assert "a" not in await models.queued_bookmark_ids()


async def test_a_bookmark_missing_from_the_list_but_still_in_readeck_is_kept(remote, monkeypatch):
    remote.library = {"a": ("1", _bm("a"))}
    await sync.run_once()

    async def listing_without_a():
        return {}

    monkeypatch.setattr(readeck, "sync_list", listing_without_a)
    state = await sync.run_once()
    assert state.removed == 0
    assert set(await _stored()) == {"a"}


async def test_a_failed_sync_is_recorded_and_changes_nothing(remote, monkeypatch):
    remote.library = {"a": ("1", _bm("a"))}
    await sync.run_once()
    monkeypatch.setattr(
        readeck, "sync_list", AsyncMock(side_effect=readeck.ReadeckError("Readeck is down"))
    )
    with pytest.raises(readeck.ReadeckError):
        await sync.run_once()
    state = await sync.load_state()
    assert state.last_error == "Readeck is down"
    assert state.last_run is not None
    assert set(await _stored()) == {"a"}


async def test_only_one_sync_runs_at_a_time(remote, monkeypatch):
    release = asyncio.Event()

    async def slow_list():
        await release.wait()
        return {}

    monkeypatch.setattr(readeck, "sync_list", slow_list)
    first = asyncio.create_task(sync.run_once())
    await asyncio.sleep(0)
    assert sync.is_running()
    assert await sync.run_once() is None
    assert sync.start_background() is False
    release.set()
    assert await first is not None
    assert not sync.is_running()


def test_to_row_normalises_readeck_values():
    row = sync.to_row(
        _bm(
            "a",
            created="2026-01-01T12:00:00.123456+02:00",
            published="2025-06-30",
            authors=["Ada"],
            site="example.com",
            loaded=False,
        ),
        "v",
        "2026-06-01T00:00:00+00:00",
    )
    assert row["created"] == "2026-01-01T10:00:00+00:00"
    assert row["published"] == "2025-06-30T00:00:00+00:00"
    assert row["authors"] == '["Ada"]'
    assert row["site_name"] == "example.com"
    assert row["loaded"] == 0
    assert row["readeck_updated"] == "v"


async def test_the_schedule_uses_the_saved_cron():
    assert await sync.schedule.cron() == sync.DEFAULT_CRON
    await sync.save_cron("0 * * * *")
    assert await sync.schedule.cron() == "0 * * * *"


async def test_a_failed_state_write_does_not_hide_the_sync_error(remote, monkeypatch):
    monkeypatch.setattr(
        readeck, "sync_list", AsyncMock(side_effect=readeck.ReadeckError("Readeck is down"))
    )
    monkeypatch.setattr(sync, "_save_state", AsyncMock(side_effect=RuntimeError("locked")))
    with pytest.raises(readeck.ReadeckError, match="Readeck is down"):
        await sync.run_once()
    assert not sync.is_running()
