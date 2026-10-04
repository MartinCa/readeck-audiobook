import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import aiosqlite

from app import config

DB_PATH = config.DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    bookmark_id   TEXT NOT NULL,
    bookmark_title TEXT,
    bookmark_url  TEXT,
    lang          TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    tts_engine    TEXT,
    voice         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    audio_path    TEXT,
    error_msg     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_bookmark ON jobs(bookmark_id, status);
-- rowid cannot appear in an index definition; it is implicitly the trailing
-- key of every index entry, which is exactly the tiebreak the listing wants.
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

-- Bookmarks the user has opted out of automatic audio generation.
CREATE TABLE IF NOT EXISTS auto_excluded (
    bookmark_id   TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL
);

-- Every bookmark a job was ever queued for, by hand or automatically. Auto
-- generation only picks up bookmarks that are not in here, so deleting a
-- bookmark's audio (which deletes its job rows) does not make the next
-- scheduled run generate it again, and a failed job is not retried forever.
CREATE TABLE IF NOT EXISTS queued_bookmarks (
    bookmark_id     TEXT PRIMARY KEY,
    first_queued_at TEXT NOT NULL
);

-- The local copy of the Readeck library, kept current by app.sync. Times are
-- UTC ISO 8601 with seconds precision so they compare correctly as text;
-- `readeck_updated` is Readeck's own value, compared verbatim to spot changes.
CREATE TABLE IF NOT EXISTS bookmarks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    site_name       TEXT NOT NULL DEFAULT '',
    authors         TEXT NOT NULL DEFAULT '[]',
    lang            TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT '',
    reading_time    INTEGER,
    description     TEXT NOT NULL DEFAULT '',
    published       TEXT,
    created         TEXT NOT NULL,
    loaded          INTEGER NOT NULL DEFAULT 1,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    readeck_updated TEXT NOT NULL DEFAULT '',
    synced_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created DESC);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the initial release, applied to existing databases on
# startup. Kept as plain ALTERs since SQLite has no "ADD COLUMN IF NOT EXISTS".
_MIGRATIONS = {
    "lang": "ALTER TABLE jobs ADD COLUMN lang TEXT",
    "attempts": "ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
}


class JobStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


TERMINAL_STATUSES = (JobStatus.completed, JobStatus.failed)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open a configured connection.

    `busy_timeout` is per-connection, so it has to be set on reads as well as
    writes; `journal_mode` is stored in the database file and set once by
    init_db. Autocommit (isolation_level=None) keeps each statement atomic on
    its own, which is all the single-statement job claim needs.
    """
    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        db.row_factory = aiosqlite.Row
        yield db


async def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with _connect() as db:
        # WAL matters here: the worker writes job rows while page requests read
        # them, and under the default rollback journal writers block readers.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.executescript(SCHEMA)
        async with db.execute("PRAGMA table_info(jobs)") as cur:
            existing = {row[1] for row in await cur.fetchall()}
        for column, statement in _MIGRATIONS.items():
            if column not in existing:
                await db.execute(statement)
        # Databases from before queued_bookmarks existed: their jobs count.
        await db.execute(
            "INSERT OR IGNORE INTO queued_bookmarks (bookmark_id, first_queued_at) "
            "SELECT bookmark_id, MIN(created_at) FROM jobs GROUP BY bookmark_id"
        )


async def create_job(
    bookmark_id: str,
    bookmark_title: str,
    bookmark_url: str,
    tts_engine: str,
    voice: str,
    lang: str = "",
    only_if_idle: bool = False,
) -> dict | None:
    """Insert a pending job and return it.

    With `only_if_idle`, nothing is inserted (and None returned) when the
    bookmark already has a pending or processing job. The check is part of the
    INSERT, so two callers queueing the same bookmark at once cannot both win.
    """
    job_id = str(uuid.uuid4())
    now = _now()
    values = (job_id, bookmark_id, bookmark_title, bookmark_url, lang, tts_engine, voice, now)
    columns = "(id, bookmark_id, bookmark_title, bookmark_url, lang, tts_engine, voice, created_at)"
    async with _connect() as db:
        if only_if_idle:
            cur = await db.execute(
                f"INSERT INTO jobs {columns} SELECT ?, ?, ?, ?, ?, ?, ?, ? "
                "WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE bookmark_id = ? AND status IN (?, ?))",
                (*values, bookmark_id, JobStatus.pending, JobStatus.processing),
            )
            if cur.rowcount == 0:
                return None
        else:
            await db.execute(f"INSERT INTO jobs {columns} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        await db.execute(
            "INSERT OR IGNORE INTO queued_bookmarks (bookmark_id, first_queued_at) VALUES (?, ?)",
            (bookmark_id, now),
        )
    return await get_job(job_id)


async def get_job(job_id: str) -> dict | None:
    async with _connect() as db:
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_jobs(job_ids: list[str]) -> list[dict]:
    """Fetch several jobs at once (used by the batched status poll)."""
    if not job_ids:
        return []
    placeholders = ", ".join("?" * len(job_ids))
    async with _connect() as db:
        async with db.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders})", tuple(job_ids)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# Timestamps are microsecond-resolution, so a bulk-queued batch no longer ties
# the way it did under SQLite's whole-second `datetime('now')`. The rowid
# tiebreaker covers the remaining exact collisions: on a tie SQLite is free to
# fall back to insertion order, which reverses the intended newest-first
# listing and leaves pagination formally unstable.
_ORDER_BY = "ORDER BY created_at DESC, rowid DESC"


def _status_filter(statuses: tuple[str, ...] | None) -> tuple[str, tuple]:
    if not statuses:
        return "", ()
    return f"WHERE status IN ({', '.join('?' * len(statuses))})", tuple(statuses)


async def list_jobs(
    limit: int = 50, offset: int = 0, statuses: tuple[str, ...] | None = None
) -> tuple[list[dict], int]:
    """Return (jobs, total_count) with pagination, optionally only some statuses."""
    where, params = _status_filter(statuses)
    async with _connect() as db:
        async with db.execute(f"SELECT COUNT(*) FROM jobs {where}", params) as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0
        async with db.execute(
            f"SELECT * FROM jobs {where} {_ORDER_BY} LIMIT ? OFFSET ?",
            (*params, limit, max(0, offset)),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows], total


_UPDATABLE_COLUMNS = frozenset({"status", "audio_path", "error_msg", "attempts"})

# Jobs the Jobs page shows. A completed job is the bookmark's audio, which the
# Bookmarks page shows instead.
OPEN_STATUSES = (JobStatus.pending, JobStatus.processing, JobStatus.failed)


async def update_job(job_id: str, **kwargs):
    invalid = set(kwargs.keys()) - _UPDATABLE_COLUMNS
    if invalid:
        raise ValueError(f"update_job: unknown columns {invalid}")
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    async with _connect() as db:
        await db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)


async def delete_job(job_id: str, statuses: tuple[str, ...] | None = None) -> dict | None:
    """Delete a job, optionally only while it has one of `statuses`; returns the row."""
    where = f" AND status IN ({', '.join('?' * len(statuses))})" if statuses else ""
    async with _connect() as db:
        async with db.execute(
            f"DELETE FROM jobs WHERE id = ?{where} RETURNING *", (job_id, *(statuses or ()))
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def delete_jobs(job_ids: list[str], statuses: tuple[str, ...] | None = None) -> list[dict]:
    """Delete several jobs, optionally only those with one of `statuses`; returns the rows."""
    where = f" AND status IN ({', '.join('?' * len(statuses))})" if statuses else ""
    deleted: list[dict] = []
    async with _connect() as db:
        for chunk in _chunks(list(dict.fromkeys(job_ids))):
            placeholders = ", ".join("?" * len(chunk))
            async with db.execute(
                f"DELETE FROM jobs WHERE id IN ({placeholders}){where} RETURNING *",
                (*chunk, *(statuses or ())),
            ) as cur:
                deleted.extend(dict(r) for r in await cur.fetchall())
    return deleted


async def list_job_ids(statuses: tuple[str, ...] | None = None) -> list[str]:
    """Return every job id, unpaginated (used for "select all" bulk actions)."""
    where, params = _status_filter(statuses)
    async with _connect() as db:
        async with db.execute(f"SELECT id FROM jobs {where} {_ORDER_BY}", params) as cur:
            rows = await cur.fetchall()
            return [row[0] for row in rows]


async def list_audio_paths() -> set[str]:
    """Every audio filename still referenced by a job row."""
    async with _connect() as db:
        async with db.execute(
            "SELECT audio_path FROM jobs WHERE audio_path IS NOT NULL AND audio_path != ''"
        ) as cur:
            return {row[0] for row in await cur.fetchall()}


async def get_active_job_for_bookmark(bookmark_id: str) -> dict | None:
    """Return the most recent pending/processing job for a bookmark, if any."""
    async with _connect() as db:
        async with db.execute(
            f"SELECT * FROM jobs WHERE bookmark_id = ? AND status IN (?, ?) {_ORDER_BY} LIMIT 1",
            (bookmark_id, JobStatus.pending, JobStatus.processing),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def requeue_interrupted_jobs() -> tuple[int, int]:
    """Recover jobs left 'processing' when the process died.

    A job stuck in 'processing' has no worker coroutine left to finish it and
    would otherwise count against MAX_CONCURRENT_JOBS forever, wedging the
    queue. Rather than failing the work outright, jobs that still have attempts
    left go back to 'pending' so they resume on their own; the rest are failed
    so a job that reliably kills the process cannot loop.

    Returns (requeued, failed).
    """
    now = _now()
    async with _connect() as db:
        requeued = await db.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE status = ? AND attempts < ?",
            (JobStatus.pending, now, JobStatus.processing, config.MAX_JOB_ATTEMPTS),
        )
        requeued_count = requeued.rowcount
        failed = await db.execute(
            "UPDATE jobs SET status = ?, error_msg = ?, updated_at = ? WHERE status = ?",
            (
                JobStatus.failed,
                f"Interrupted by server restart {config.MAX_JOB_ATTEMPTS} time(s); giving up",
                now,
                JobStatus.processing,
            ),
        )
        failed_count = failed.rowcount
        return requeued_count, failed_count


async def claim_next_pending_job(max_concurrent: int) -> dict | None:
    """Atomically claim the next pending job if fewer than max_concurrent are running.

    A single UPDATE ... RETURNING does the check, the claim and the read of the
    claimed row in one statement, so there is no TOCTOU window and no need to
    guess which row was taken.
    """
    async with _connect() as db:
        async with db.execute(
            """
            UPDATE jobs
               SET status = 'processing', updated_at = ?, attempts = attempts + 1
             WHERE id = (
                 SELECT id FROM jobs
                  WHERE status = 'pending'
                  ORDER BY created_at, rowid
                  LIMIT 1
             )
               AND (SELECT COUNT(*) FROM jobs WHERE status = 'processing') < ?
            RETURNING *
            """,
            (_now(), max_concurrent),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


# ── Per-bookmark views ─────────────────────────────────────────────────────────

# SQLite caps the number of bound parameters per statement; stay well under it.
_ID_CHUNK = 500


def _chunks(ids: list[str]) -> list[list[str]]:
    unique = list(dict.fromkeys(ids))
    return [unique[i : i + _ID_CHUNK] for i in range(0, len(unique), _ID_CHUNK)]


async def latest_jobs_by_bookmark(bookmark_ids: list[str]) -> dict[str, dict]:
    """The most recent job of any status for each bookmark that has one."""
    latest: dict[str, dict] = {}
    async with _connect() as db:
        for chunk in _chunks(bookmark_ids):
            placeholders = ", ".join("?" * len(chunk))
            async with db.execute(
                f"SELECT * FROM jobs WHERE bookmark_id IN ({placeholders}) "
                "ORDER BY created_at, rowid",
                tuple(chunk),
            ) as cur:
                for row in await cur.fetchall():
                    latest[row["bookmark_id"]] = dict(row)
    return latest


async def audio_by_bookmark(bookmark_ids: list[str] | None = None) -> dict[str, dict]:
    """The newest completed job with audio for each bookmark.

    With no ids, covers every bookmark that has audio.
    """
    base = (
        "SELECT * FROM jobs WHERE status = 'completed' "
        "AND audio_path IS NOT NULL AND audio_path != ''"
    )
    order = " ORDER BY created_at, rowid"
    audio: dict[str, dict] = {}
    async with _connect() as db:
        if bookmark_ids is None:
            async with db.execute(base + order) as cur:
                rows = await cur.fetchall()
        else:
            rows = []
            for chunk in _chunks(bookmark_ids):
                placeholders = ", ".join("?" * len(chunk))
                async with db.execute(
                    f"{base} AND bookmark_id IN ({placeholders}){order}", tuple(chunk)
                ) as cur:
                    rows.extend(await cur.fetchall())
    for row in rows:
        audio[row["bookmark_id"]] = dict(row)
    return audio


async def delete_completed_jobs(bookmark_ids: list[str]) -> list[dict]:
    """Delete the completed jobs (the audio) of these bookmarks; returns the rows."""
    deleted: list[dict] = []
    async with _connect() as db:
        for chunk in _chunks(bookmark_ids):
            placeholders = ", ".join("?" * len(chunk))
            async with db.execute(
                f"DELETE FROM jobs WHERE status = 'completed' "
                f"AND bookmark_id IN ({placeholders}) RETURNING *",
                tuple(chunk),
            ) as cur:
                deleted.extend(dict(r) for r in await cur.fetchall())
    return deleted


async def delete_superseded_audio(bookmark_id: str) -> list[dict]:
    """Keep only the latest completed job of a bookmark; returns the deleted rows.

    The survivor is picked in SQL rather than passed in, so two completions for
    one bookmark finishing together agree on it instead of deleting each other.
    """
    async with _connect() as db:
        async with db.execute(
            "DELETE FROM jobs WHERE status = 'completed' AND bookmark_id = ? AND id != ("
            "  SELECT id FROM jobs WHERE status = 'completed' AND bookmark_id = ?"
            "  ORDER BY updated_at DESC, rowid DESC LIMIT 1"
            ") RETURNING *",
            (bookmark_id, bookmark_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_failed_jobs(bookmark_ids: list[str]) -> int:
    """Drop the failed jobs of bookmarks that are being queued again."""
    deleted = 0
    async with _connect() as db:
        for chunk in _chunks(bookmark_ids):
            placeholders = ", ".join("?" * len(chunk))
            cur = await db.execute(
                f"DELETE FROM jobs WHERE status = 'failed' AND bookmark_id IN ({placeholders})",
                tuple(chunk),
            )
            deleted += cur.rowcount
    return deleted


async def retry_job(job_id: str) -> dict | None:
    """Send a failed job back to the queue with a fresh attempt budget."""
    async with _connect() as db:
        async with db.execute(
            "UPDATE jobs SET status = ?, error_msg = NULL, attempts = 0, updated_at = ? "
            "WHERE id = ? AND status = ? RETURNING *",
            (JobStatus.pending, _now(), job_id, JobStatus.failed),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── Auto generation bookkeeping ────────────────────────────────────────────────


async def auto_excluded_ids() -> set[str]:
    async with _connect() as db:
        async with db.execute("SELECT bookmark_id FROM auto_excluded") as cur:
            return {row[0] for row in await cur.fetchall()}


async def set_auto_excluded(bookmark_ids: list[str], excluded: bool) -> None:
    async with _connect() as db:
        for chunk in _chunks(bookmark_ids):
            if excluded:
                now = _now()
                await db.executemany(
                    "INSERT OR IGNORE INTO auto_excluded (bookmark_id, created_at) VALUES (?, ?)",
                    [(bid, now) for bid in chunk],
                )
            else:
                placeholders = ", ".join("?" * len(chunk))
                await db.execute(
                    f"DELETE FROM auto_excluded WHERE bookmark_id IN ({placeholders})",
                    tuple(chunk),
                )


async def queued_bookmark_ids() -> set[str]:
    """Every bookmark a job was ever queued for."""
    async with _connect() as db:
        async with db.execute("SELECT bookmark_id FROM queued_bookmarks") as cur:
            return {row[0] for row in await cur.fetchall()}


# ── Bookmarks (the local copy of Readeck) ──────────────────────────────────────

_BOOKMARK_COLUMNS = (
    "id",
    "title",
    "url",
    "site_name",
    "authors",
    "lang",
    "type",
    "reading_time",
    "description",
    "published",
    "created",
    "loaded",
    "is_deleted",
    "readeck_updated",
    "synced_at",
)

# A bookmark worth showing: Readeck has finished fetching it and it is not on
# its way to the bin.
_VISIBLE = "b.loaded = 1 AND b.is_deleted = 0"
_HAS_AUDIO = (
    "EXISTS (SELECT 1 FROM jobs j WHERE j.bookmark_id = b.id AND j.status = 'completed' "
    "AND j.audio_path IS NOT NULL AND j.audio_path != '')"
)
_IS_EXCLUDED = "EXISTS (SELECT 1 FROM auto_excluded x WHERE x.bookmark_id = b.id)"


async def bookmark_versions() -> dict[str, str]:
    """Readeck's last-updated value for every stored bookmark, by id."""
    async with _connect() as db:
        async with db.execute("SELECT id, readeck_updated FROM bookmarks") as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}


async def upsert_bookmarks(rows: list[dict]) -> None:
    """Insert or replace bookmarks; each row carries every column."""
    if not rows:
        return
    columns = ", ".join(_BOOKMARK_COLUMNS)
    placeholders = ", ".join("?" * len(_BOOKMARK_COLUMNS))
    async with _connect() as db:
        await db.execute("BEGIN")
        await db.executemany(
            f"INSERT OR REPLACE INTO bookmarks ({columns}) VALUES ({placeholders})",
            [tuple(row[c] for c in _BOOKMARK_COLUMNS) for row in rows],
        )
        await db.execute("COMMIT")


async def delete_bookmarks(bookmark_ids: list[str]) -> list[dict]:
    """Forget bookmarks that are gone from Readeck, with everything kept for them.

    Removes the bookmark rows, their jobs, exclusions and queue history; returns
    the deleted job rows so the caller can remove their audio files.
    """
    deleted_jobs: list[dict] = []
    async with _connect() as db:
        for chunk in _chunks(bookmark_ids):
            placeholders = ", ".join("?" * len(chunk))
            params = tuple(chunk)
            async with db.execute(
                f"DELETE FROM jobs WHERE bookmark_id IN ({placeholders}) RETURNING *", params
            ) as cur:
                deleted_jobs.extend(dict(r) for r in await cur.fetchall())
            for table, column in (
                ("bookmarks", "id"),
                ("auto_excluded", "bookmark_id"),
                ("queued_bookmarks", "bookmark_id"),
            ):
                await db.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", params)
    return deleted_jobs


async def count_bookmarks() -> int:
    async with _connect() as db:
        async with db.execute(f"SELECT COUNT(*) FROM bookmarks b WHERE {_VISIBLE}") as cur:
            return (await cur.fetchone())[0]


async def get_bookmark_rows(bookmark_ids: list[str]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    async with _connect() as db:
        for chunk in _chunks(bookmark_ids):
            placeholders = ", ".join("?" * len(chunk))
            async with db.execute(
                f"SELECT * FROM bookmarks WHERE id IN ({placeholders})", tuple(chunk)
            ) as cur:
                rows.update({row["id"]: dict(row) for row in await cur.fetchall()})
    return rows


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def query_bookmarks(
    *,
    search: str = "",
    created_from: str | None = None,
    created_to: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    has_audio: bool | None = None,
    excluded: bool | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """A page of visible bookmarks, newest first, and the total that match.

    `created_*` compare full timestamps; `published_*` compare the UTC day
    (YYYY-MM-DD) and leave out bookmarks with no publication date.
    """
    where = [_VISIBLE]
    params: list = []
    if search:
        pattern = _like(search)
        columns = ("title", "site_name", "authors", "description", "url")
        where.append("(" + " OR ".join(f"b.{c} LIKE ? ESCAPE '\\'" for c in columns) + ")")
        params += [pattern] * len(columns)
    if created_from:
        where.append("b.created >= ?")
        params.append(created_from)
    if created_to:
        where.append("b.created <= ?")
        params.append(created_to)
    if published_from:
        where.append("substr(b.published, 1, 10) >= ?")
        params.append(published_from)
    if published_to:
        where.append("substr(b.published, 1, 10) <= ?")
        params.append(published_to)
    if has_audio is not None:
        where.append(_HAS_AUDIO if has_audio else f"NOT {_HAS_AUDIO}")
    if excluded is not None:
        where.append(_IS_EXCLUDED if excluded else f"NOT {_IS_EXCLUDED}")
    clause = " AND ".join(where)
    async with _connect() as db:
        async with db.execute(f"SELECT COUNT(*) FROM bookmarks b WHERE {clause}", params) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            f"SELECT b.* FROM bookmarks b WHERE {clause} "
            "ORDER BY b.created DESC, b.id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return rows, total


async def auto_generation_candidates(created_from: str | None = None) -> list[dict]:
    """Articles never queued and not excluded, oldest first."""
    sql = (
        f"SELECT b.* FROM bookmarks b WHERE {_VISIBLE} AND b.type = 'article' "
        f"AND NOT {_IS_EXCLUDED} "
        "AND NOT EXISTS (SELECT 1 FROM queued_bookmarks q WHERE q.bookmark_id = b.id)"
    )
    params: tuple = ()
    if created_from:
        sql += " AND b.created >= ?"
        params = (created_from,)
    async with _connect() as db:
        async with db.execute(sql + " ORDER BY b.created, b.id", params) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Settings ───────────────────────────────────────────────────────────────────


async def get_settings() -> dict[str, str]:
    async with _connect() as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}


async def set_settings(values: dict[str, str | None]) -> None:
    """Upsert settings; a None value removes the key."""
    async with _connect() as db:
        for key, value in values.items():
            if value is None:
                await db.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                await db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
