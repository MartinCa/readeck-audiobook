"""The bookmark listing: Readeck's bookmarks joined with this app's own state.

Search and the added-date range are filters Readeck applies itself, so with only
those set a page is passed straight through. Audio, auto-exclusion and the
published-date range exist only here (Readeck cannot filter on publication
date), so with any of them set the whole Readeck result is read, filtered
locally and paginated here. That full read is cached briefly so paging through
a filtered list does not re-read the library for every page.
"""

import time
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from app import models, readeck


class AudioFilter(StrEnum):
    any = "any"
    with_audio = "with"
    without_audio = "without"


class ExclusionFilter(StrEnum):
    any = "any"
    excluded = "excluded"
    included = "included"


@dataclass(frozen=True)
class BookmarkFilters:
    search: str = ""
    added_from: date | None = None
    added_to: date | None = None
    published_from: date | None = None
    published_to: date | None = None
    audio: AudioFilter = AudioFilter.any
    exclusion: ExclusionFilter = ExclusionFilter.any

    @property
    def needs_local_filtering(self) -> bool:
        return bool(
            self.published_from
            or self.published_to
            or self.audio != AudioFilter.any
            or self.exclusion != ExclusionFilter.any
        )

    def readeck_params(self) -> dict[str, Any]:
        # Readeck compares against full timestamps, so widen the end date to
        # the last second of that day to make the range inclusive.
        return {
            "search": self.search,
            "range_start": readeck.day_bound(self.added_from) if self.added_from else "",
            "range_end": readeck.day_bound(self.added_to, end=True) if self.added_to else "",
        }


_CACHE_SECONDS = 30
_cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}


async def _all_from_readeck(params: dict[str, Any]) -> list[dict[str, Any]]:
    key = tuple(sorted(params.items()))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    items = await readeck.list_all_bookmarks(**params)
    _cache.clear()  # only the latest query is worth keeping
    _cache[key] = (time.monotonic(), items)
    return items


def clear_cache() -> None:
    _cache.clear()


def _published_day(bm: dict[str, Any]) -> date | None:
    value = bm.get("published")
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _matches(
    bm: dict[str, Any], filters: BookmarkFilters, with_audio: set[str], excluded: set[str]
) -> bool:
    if filters.audio == AudioFilter.with_audio and bm["id"] not in with_audio:
        return False
    if filters.audio == AudioFilter.without_audio and bm["id"] in with_audio:
        return False
    if filters.exclusion == ExclusionFilter.excluded and bm["id"] not in excluded:
        return False
    if filters.exclusion == ExclusionFilter.included and bm["id"] in excluded:
        return False
    if filters.published_from or filters.published_to:
        published = _published_day(bm)
        if published is None:
            return False
        if filters.published_from and published < filters.published_from:
            return False
        if filters.published_to and published > filters.published_to:
            return False
    return True


async def list_bookmarks(filters: BookmarkFilters, page: int, page_size: int) -> dict[str, Any]:
    """A page of bookmarks, each annotated with its audio, job and exclusion."""
    offset = (page - 1) * page_size
    excluded = await models.auto_excluded_ids()

    if filters.needs_local_filtering:
        everything = await _all_from_readeck(filters.readeck_params())
        with_audio = (
            set(await models.audio_by_bookmark()) if filters.audio != AudioFilter.any else set()
        )
        matching = [bm for bm in everything if _matches(bm, filters, with_audio, excluded)]
        total = len(matching)
        items = matching[offset : offset + page_size]
    else:
        data = await readeck.list_bookmarks(
            limit=page_size, offset=offset, **filters.readeck_params()
        )
        total = data["total"]
        items = data["items"]

    ids = [bm["id"] for bm in items]
    audio = await models.audio_by_bookmark(ids)
    latest = await models.latest_jobs_by_bookmark(ids)
    return {
        "items": [
            {
                **bm,
                "audio": audio.get(bm["id"]),
                # A finished job is shown as the audio; only a job still
                # running, or one that failed after it, is worth a badge.
                "job": job
                if (job := latest.get(bm["id"])) and job["status"] != "completed"
                else None,
                "auto_excluded": bm["id"] in excluded,
            }
            for bm in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }
