"""Keep the local copy of the Readeck library current.

The Bookmarks page and auto generation read bookmarks from the local database
rather than from Readeck, so filtering and paging stay fast with thousands of
bookmarks. A sync runs at startup, on its own cron schedule, and on demand:

1. Readeck's sync endpoint lists every bookmark id with its last-updated time
   in one request.
2. Ids that are new, or whose time differs from the stored one, are fetched
   (100 to a request) and stored.
3. Stored ids Readeck no longer lists are confirmed gone and forgotten, with
   their jobs and audio files.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app import jobs, models, readeck, scheduler

logger = logging.getLogger(__name__)

DEFAULT_CRON = "*/15 * * * *"


@dataclass
class SyncState:
    last_run: datetime | None = None
    last_error: str | None = None
    added: int = 0
    updated: int = 0
    removed: int = 0


async def load_cron() -> str:
    return (await models.get_settings()).get("sync_cron") or DEFAULT_CRON


async def save_cron(cron: str) -> None:
    await models.set_settings({"sync_cron": cron})
    scheduler.poke()


async def load_state() -> SyncState:
    raw = await models.get_settings()
    last_run = raw.get("sync_last_run")
    return SyncState(
        last_run=datetime.fromisoformat(last_run) if last_run else None,
        last_error=raw.get("sync_last_error"),
        added=int(raw.get("sync_last_added") or 0),
        updated=int(raw.get("sync_last_updated") or 0),
        removed=int(raw.get("sync_last_removed") or 0),
    )


async def _save_state(state: SyncState) -> None:
    await models.set_settings(
        {
            "sync_last_run": state.last_run.isoformat() if state.last_run else None,
            "sync_last_error": state.last_error,
            "sync_last_added": str(state.added),
            "sync_last_updated": str(state.updated),
            "sync_last_removed": str(state.removed),
        }
    )


def _utc(value: str | None) -> str | None:
    """A Readeck timestamp as UTC ISO 8601 to the second, so stored times sort as text."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def to_row(bm: dict[str, Any], readeck_updated: str, synced_at: str) -> dict[str, Any]:
    """A Readeck bookmark as a row of the local `bookmarks` table."""
    return {
        "id": bm["id"],
        "title": bm.get("title") or "",
        "url": bm.get("url") or "",
        "site_name": bm.get("site_name") or bm.get("site") or "",
        "authors": json.dumps(bm.get("authors") or []),
        "lang": bm.get("lang") or "",
        "type": bm.get("type") or "",
        "reading_time": bm.get("reading_time") or None,
        "description": bm.get("description") or "",
        "published": _utc(bm.get("published")),
        "created": _utc(bm.get("created")) or synced_at,
        "loaded": 1 if bm.get("loaded", True) else 0,
        "is_deleted": 1 if bm.get("is_deleted") else 0,
        "readeck_updated": readeck_updated,
        "synced_at": synced_at,
    }


_running = False


def is_running() -> bool:
    return _running


async def run_once() -> SyncState | None:
    """Bring the local copy up to date; returns None if a sync is already running."""
    global _running
    if _running:
        return None
    _running = True
    now = datetime.now(UTC)
    state = SyncState(last_run=now)
    try:
        remote = await readeck.sync_list()
        local = await models.bookmark_versions()

        changed = [bid for bid, version in remote.items() if local.get(bid) != version]
        fetched = await readeck.fetch_bookmarks(changed)
        synced_at = now.isoformat(timespec="seconds")
        await models.upsert_bookmarks(
            [to_row(bm, remote.get(bm["id"], ""), synced_at) for bm in fetched]
        )
        state.added = sum(1 for bm in fetched if bm["id"] not in local)
        state.updated = len(fetched) - state.added

        missing = [bid for bid in local if bid not in remote]
        if missing:
            # Deleting audio is not undoable, so confirm with Readeck first
            # rather than trusting one listing.
            still_there = {bm["id"] for bm in await readeck.fetch_bookmarks(missing)}
            gone = [bid for bid in missing if bid not in still_there]
            await jobs.forget_bookmarks(gone)
            state.removed = len(gone)

        if state.added or state.updated or state.removed:
            logger.info(
                "Readeck sync: %d added, %d updated, %d removed",
                state.added,
                state.updated,
                state.removed,
            )
        return state
    except Exception as exc:
        state.last_error = str(exc)
        raise
    finally:
        _running = False
        try:
            await _save_state(state)
        except Exception:
            # Raising here would replace the sync's own error, if it had one.
            logger.exception("Could not record the Readeck sync result")


async def _run_logged() -> None:
    try:
        await run_once()
    except Exception:
        logger.exception("Readeck sync failed")


_background: set[asyncio.Task] = set()


def start_background() -> bool:
    """Start a sync without waiting for it; False if one is already running."""
    if _running or _background:
        return False
    task = asyncio.create_task(_run_logged(), name="readeck-sync")
    _background.add(task)
    task.add_done_callback(_background.discard)
    return True


async def stop() -> None:
    """Cancel a background sync (on shutdown)."""
    for task in list(_background):
        task.cancel()
    await asyncio.gather(*_background, return_exceptions=True)


schedule = scheduler.Schedule("Readeck sync", load_cron, _run_logged)
