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


async def create_job(
    bookmark_id: str,
    bookmark_title: str,
    bookmark_url: str,
    tts_engine: str,
    voice: str,
    lang: str = "",
) -> dict:
    job_id = str(uuid.uuid4())
    async with _connect() as db:
        await db.execute(
            "INSERT INTO jobs "
            "(id, bookmark_id, bookmark_title, bookmark_url, lang, tts_engine, voice, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, bookmark_id, bookmark_title, bookmark_url, lang, tts_engine, voice, _now()),
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


async def list_jobs(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """Return (jobs, total_count) with pagination."""
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) FROM jobs") as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0
        async with db.execute(
            f"SELECT * FROM jobs {_ORDER_BY} LIMIT ? OFFSET ?",
            (limit, max(0, offset)),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows], total


_UPDATABLE_COLUMNS = frozenset({"status", "audio_path", "error_msg", "attempts"})


async def update_job(job_id: str, **kwargs):
    invalid = set(kwargs.keys()) - _UPDATABLE_COLUMNS
    if invalid:
        raise ValueError(f"update_job: unknown columns {invalid}")
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    async with _connect() as db:
        await db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)


async def delete_job(job_id: str) -> dict | None:
    async with _connect() as db:
        async with db.execute("DELETE FROM jobs WHERE id = ? RETURNING *", (job_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def list_job_ids() -> list[str]:
    """Return every job id, unpaginated (used for "select all" bulk actions)."""
    async with _connect() as db:
        async with db.execute(f"SELECT id FROM jobs {_ORDER_BY}") as cur:
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
