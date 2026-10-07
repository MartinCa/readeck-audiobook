"""Request and response bodies for the JSON API.

Field names are snake_case here and camelCase on the wire (DESIGN.md section 7);
the frontend's types are generated from the OpenAPI spec these produce.
"""

from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app import config


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def safe_url(value: str | None) -> str:
    """Return `value` only if it is a plain web link.

    Bookmark URLs come from saved pages, so a `javascript:` or `data:` URL must
    never reach an `href`.
    """
    if not value:
        return ""
    try:
        scheme = urlparse(value).scheme.lower()
    except ValueError:
        return ""
    return value if scheme in ("http", "https") else ""


def audio_url(filename: str) -> str:
    return f"/api/audio/{quote(filename)}"


def _utc(value: str | None) -> datetime | None:
    """Parse a stored timestamp; rows written before they carried an offset are UTC."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


JobStatusName = Literal["pending", "processing", "completed", "failed"]


class JobProgress(ApiModel):
    """Text chunks synthesized so far out of the article's total."""

    done: int
    total: int

    @classmethod
    def from_row(cls, row: dict) -> "JobProgress | None":
        if row.get("status") != "processing" or row.get("progress_total") is None:
            return None
        return cls(done=row["progress_done"] or 0, total=row["progress_total"])


class Job(ApiModel):
    id: str
    bookmark_id: str
    bookmark_title: str
    bookmark_url: str
    status: JobStatusName
    tts_engine: str
    voice: str
    lang: str
    error_msg: str | None
    progress: JobProgress | None
    created_at: datetime
    updated_at: datetime | None

    @classmethod
    def from_row(cls, row: dict) -> "Job":
        return cls(
            id=row["id"],
            bookmark_id=row["bookmark_id"],
            bookmark_title=row["bookmark_title"] or row["bookmark_id"],
            bookmark_url=safe_url(row["bookmark_url"]),
            status=row["status"],
            tts_engine=row["tts_engine"] or "",
            voice=row["voice"] or "",
            lang=row["lang"] or "",
            error_msg=row["error_msg"],
            progress=JobProgress.from_row(row),
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
        )


class JobPage(ApiModel):
    items: list[Job]
    total: int
    page: int
    page_size: int
    total_pages: int


class Audio(ApiModel):
    job_id: str
    filename: str
    url: str
    tts_engine: str
    voice: str
    generated_at: datetime
    # Length of the finished file; None until it has been measured.
    duration_seconds: float | None

    @classmethod
    def from_row(cls, row: dict) -> "Audio":
        return cls(
            job_id=row["id"],
            filename=row["audio_path"],
            url=audio_url(row["audio_path"]),
            tts_engine=row["tts_engine"] or "",
            voice=row["voice"] or "",
            generated_at=_utc(row["updated_at"] or row["created_at"]),
            duration_seconds=row.get("duration_seconds"),
        )


class BookmarkJob(ApiModel):
    id: str
    status: JobStatusName
    error_msg: str | None
    progress: JobProgress | None


class Bookmark(ApiModel):
    id: str
    title: str
    url: str
    site_name: str
    authors: list[str]
    lang: str
    type: str
    reading_time: int | None
    description: str
    published: datetime | None
    added: datetime
    audio: Audio | None
    job: BookmarkJob | None
    auto_excluded: bool
    # Whether Readeck extracted article text; without it there is nothing to read out.
    has_article: bool
    # The bookmark's page in Readeck, empty when READECK_BASE_URL is unset.
    readeck_url: str

    @classmethod
    def from_item(cls, item: dict) -> "Bookmark":
        job = item.get("job")
        return cls(
            id=item["id"],
            title=item.get("title") or item.get("url") or item["id"],
            url=safe_url(item.get("url")),
            site_name=item.get("site_name") or item.get("site") or "",
            authors=item.get("authors") or [],
            lang=item.get("lang") or "",
            type=item.get("type") or "",
            reading_time=item.get("reading_time") or None,
            description=item.get("description") or "",
            published=item.get("published") or None,
            added=item["created"],
            audio=Audio.from_row(item["audio"]) if item.get("audio") else None,
            job=(
                BookmarkJob(
                    id=job["id"],
                    status=job["status"],
                    error_msg=job["error_msg"],
                    progress=JobProgress.from_row(job),
                )
                if job
                else None
            ),
            auto_excluded=item["auto_excluded"],
            has_article=item.get("has_article", 1) != 0,
            readeck_url=(
                f"{config.READECK_BASE_URL}/bookmarks/{quote(item['id'])}"
                if config.READECK_BASE_URL
                else ""
            ),
        )


class BookmarkPage(ApiModel):
    items: list[Bookmark]
    total: int
    page: int
    page_size: int
    total_pages: int


class BookmarkIds(ApiModel):
    bookmark_ids: list[str] = Field(min_length=1)


class JobIds(ApiModel):
    job_ids: list[str] = Field(min_length=1)


class AutoExclusionUpdate(ApiModel):
    bookmark_ids: list[str] = Field(min_length=1)
    excluded: bool


class QueueResult(ApiModel):
    queued: int
    skipped: int
    # Bookmarks Readeck extracted no article text from, which cannot be generated.
    no_article: int = 0


class ReadeckActionResult(ApiModel):
    """How a bulk Readeck action went; a failed bookmark is left as it was."""

    count: int
    failed: int


class CountResult(ApiModel):
    count: int


class AutoGenerationSettings(ApiModel):
    enabled: bool
    since: date | None = None
    cron: str


class AutoGenerationStatus(AutoGenerationSettings):
    next_run: datetime | None
    last_run: datetime | None
    last_queued: int | None
    last_error: str | None


class SyncSettings(ApiModel):
    cron: str


class SyncStatus(SyncSettings):
    running: bool
    bookmark_count: int
    next_run: datetime | None
    last_run: datetime | None
    last_error: str | None
    added: int
    updated: int
    removed: int


class GenerationSettings(ApiModel):
    # An article with fewer words than this fails instead of becoming an
    # audio file; it guards against a failed extraction.
    min_article_words: int = Field(ge=0, le=10000)


class Settings(ApiModel):
    sync: SyncStatus
    auto_generation: AutoGenerationStatus
    generation: GenerationSettings


class SettingsUpdate(ApiModel):
    """Either section, or both; a section left out is unchanged."""

    sync: SyncSettings | None = None
    auto_generation: AutoGenerationSettings | None = None
    generation: GenerationSettings | None = None


class Health(ApiModel):
    status: str
