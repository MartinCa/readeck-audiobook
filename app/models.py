import uuid
from datetime import datetime, timezone
from enum import Enum

import aiosqlite

DB_PATH = "/app/data/audiobook.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    bookmark_id   TEXT NOT NULL,
    bookmark_title TEXT,
    bookmark_url  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    tts_engine    TEXT,
    voice         TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT,
    audio_path    TEXT,
    error_msg     TEXT
);
"""


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def create_job(bookmark_id: str, bookmark_title: str, bookmark_url: str,
                     tts_engine: str, voice: str) -> dict:
    job_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO jobs (id, bookmark_id, bookmark_title, bookmark_url, tts_engine, voice) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, bookmark_id, bookmark_title, bookmark_url, tts_engine, voice),
        )
        await db.commit()
    return await get_job(job_id)


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_jobs(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """Return (jobs, total_count) with pagination."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM jobs") as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0
        async with db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows], total


_UPDATABLE_COLUMNS = frozenset({"status", "audio_path", "error_msg"})


async def update_job(job_id: str, **kwargs):
    invalid = set(kwargs.keys()) - _UPDATABLE_COLUMNS
    if invalid:
        raise ValueError(f"update_job: unknown columns {invalid}")
    kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)
        await db.commit()


async def delete_job(job_id: str) -> dict | None:
    job = await get_job(job_id)
    if job:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            await db.commit()
    return job


async def claim_next_pending_job(max_concurrent: int) -> dict | None:
    """Atomically claim the next pending job if fewer than max_concurrent are running.

    Uses a single UPDATE so there is no TOCTOU window between checking the
    active count and marking a job as processing.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            UPDATE jobs
               SET status = 'processing', updated_at = ?
             WHERE id = (
                 SELECT id FROM jobs
                  WHERE status = 'pending'
                  ORDER BY created_at
                  LIMIT 1
             )
               AND (SELECT COUNT(*) FROM jobs WHERE status = 'processing') < ?
            """,
            (now, max_concurrent),
        )
        await db.commit()
        # Return the row we just claimed, if any
        async with db.execute(
            "SELECT * FROM jobs WHERE status = 'processing' AND updated_at = ? LIMIT 1",
            (now,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
