"""The bookmark listing: the local copy of Readeck joined with this app's own state.

Every filter runs in SQL against the `bookmarks` table that app.sync keeps
current, so paging and filtering never wait on Readeck.
"""

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from app import models


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

    def query(self) -> dict[str, Any]:
        """Keyword arguments for models.query_bookmarks.

        Both date ranges are inclusive UTC days: the added range compares full
        timestamps against the first and last second of those days.
        """
        return {
            "search": self.search,
            "created_from": f"{self.added_from.isoformat()}T00:00:00+00:00"
            if self.added_from
            else None,
            "created_to": f"{self.added_to.isoformat()}T23:59:59+00:00" if self.added_to else None,
            "published_from": self.published_from.isoformat() if self.published_from else None,
            "published_to": self.published_to.isoformat() if self.published_to else None,
            "has_audio": None
            if self.audio == AudioFilter.any
            else self.audio == AudioFilter.with_audio,
            "excluded": None
            if self.exclusion == ExclusionFilter.any
            else self.exclusion == ExclusionFilter.excluded,
        }


async def list_bookmarks(filters: BookmarkFilters, page: int, page_size: int) -> dict[str, Any]:
    """A page of bookmarks, each annotated with its audio, job and exclusion."""
    items, total = await models.query_bookmarks(
        **filters.query(), limit=page_size, offset=(page - 1) * page_size
    )
    ids = [bm["id"] for bm in items]
    audio = await models.audio_by_bookmark(ids)
    latest = await models.latest_jobs_by_bookmark(ids)
    excluded = await models.auto_excluded_ids()
    return {
        "items": [
            {
                **bm,
                "authors": json.loads(bm["authors"] or "[]"),
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
