# Readeck Audiobook Web App - Implementation Plan

## Overview

A lightweight web app that fetches bookmarks from a Readeck instance and generates audio versions asynchronously using TTS. Users can select bookmarks, queue generation jobs, and download the resulting MP3/WAV files.

## Architecture

### Tech Stack

- **Backend**: Python 3.12 + FastAPI (async, lightweight, built-in background tasks)
- **Frontend**: Jinja2 templates + HTMX + Alpine.js (server-rendered, minimal JS footprint)
- **TTS Engine**: `edge-tts` (Microsoft Edge TTS — free, no API key, high quality neural voices)
  - Optional: ebook2audiobook as an alternative backend (heavier, supports voice cloning)
- **Persistence**: SQLite via `aiosqlite` (no external DB dependency)
- **Task execution**: FastAPI `BackgroundTasks` + asyncio queue (no Redis/Celery needed)
- **Content fetching**: Readeck `/api/bookmarks/{id}/article` (HTML) → strip tags → TTS
  - Or `/api/bookmarks/{id}/article.epub` if ebook2audiobook is selected

### Why edge-tts over ebook2audiobook directly

ebook2audiobook requires PyTorch + multi-GB model downloads, making the Docker image impractical (~10GB+). `edge-tts` is a thin Python wrapper around Microsoft's neural TTS service (used in Windows Narrator / Edge browser), produces high-quality results, requires no API key, and keeps the Docker image under 200MB. ebook2audiobook can be wired in as an optional `TTS_ENGINE=ebook2audiobook` mode pointing to a separately-hosted container.

---

## File Structure

```
readeck-audiobook/
├── app/
│   ├── main.py           # FastAPI app, routes, startup
│   ├── readeck.py        # Readeck API client (httpx)
│   ├── tts.py            # TTS backends (edge-tts, ebook2audiobook stub)
│   ├── jobs.py           # Job queue, worker loop, SQLite helpers
│   ├── models.py         # Pydantic models + DB schema
│   └── templates/
│       ├── base.html     # Base layout
│       ├── index.html    # Bookmark list + selection UI
│       └── jobs.html     # Job status + download UI
├── static/
│   └── app.css           # Minimal styles (Tailwind CDN or plain CSS)
├── audio/                # Generated audio files (Docker volume)
├── data/                 # SQLite DB (Docker volume)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `READECK_BASE_URL` | Yes | Readeck instance base URL (e.g. `https://readeck.example.com`) |
| `READECK_API_TOKEN` | Yes | Readeck Bearer API token |
| `TTS_ENGINE` | No | `edge-tts` (default) or `ebook2audiobook` |
| `EDGE_TTS_VOICE` | No | Voice name, e.g. `en-US-AriaNeural` (default) |
| `EBOOK2AUDIOBOOK_TIMEOUT_SECONDS` | No | Max conversion time when `TTS_ENGINE=ebook2audiobook` (default `7200`) |
| `MAX_CONCURRENT_JOBS` | No | Max simultaneous TTS jobs (default: `2`) |
| `PORT` | No | HTTP port (default: `8080`) |

---

## Data Model

```sql
CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,
    bookmark_id TEXT NOT NULL,
    bookmark_title TEXT,
    bookmark_url TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    -- pending | processing | completed | failed
    tts_engine  TEXT,
    voice       TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME,
    audio_path  TEXT,   -- relative path under /audio/
    error_msg   TEXT
);
```

---

## Internal API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Bookmark list page (HTML, paginated, searchable) |
| `GET` | `/jobs` | Job list page with status + download links |
| `POST` | `/jobs` | Submit one or more bookmark IDs for audio generation |
| `DELETE` | `/jobs/{id}` | Delete a job and its audio file |
| `GET` | `/audio/{filename}` | Stream/download the generated audio file |
| `GET` | `/api/bookmarks` | Proxy: fetch bookmark list from Readeck (JSON, for HTMX) |
| `GET` | `/api/jobs/{id}/status` | Poll job status (JSON, for HTMX polling) |
| `GET` | `/health` | Health check endpoint |

---

## Audio Generation Flow

1. User visits `/` → app calls Readeck `GET /api/bookmarks` → renders paginated list with checkboxes
2. User selects bookmarks → submits form → `POST /jobs` with list of bookmark IDs
3. For each ID, a `Job` row is inserted with `status=pending`
4. A background worker picks up pending jobs (up to `MAX_CONCURRENT_JOBS` at a time):
   - Fetches article content: `GET /api/bookmarks/{id}/article` (HTML) or `.md` (Markdown)
   - Strips HTML tags / cleans text
   - Calls TTS engine → writes to `audio/{job_id}.mp3`
   - Updates job `status=completed`, stores `audio_path`
5. User visits `/jobs` to see status; completed jobs have a download link
6. HTMX polling (`hx-trigger="every 3s"`) auto-refreshes in-progress job rows

---

## TTS Backend Details

### edge-tts (default)
- Python package `edge-tts` calls Microsoft's TTS endpoint
- Input: plain text string
- Output: MP3 file streamed to disk
- Voice auto-selected based on bookmark `lang` field if available, else falls back to `EDGE_TTS_VOICE`
- Language-to-voice mapping: `en→en-US-AriaNeural`, `de→de-DE-KatjaNeural`, `fr→fr-FR-DeniseNeural`, etc.

### ebook2audiobook (optional)
- Fetches the EPUB export (`GET /api/bookmarks/{id}/article.epub`)
- ebook2audiobook exposes no stable HTTP API for conversion, so the app runs its `--headless`
  CLI as a local subprocess instead, on an image built from `Dockerfile.ebook2audiobook`
  (bundles the ebook2audiobook runtime on top of this app's image)

---

## Docker Setup

### Dockerfile (multi-stage, slim)
- Base: `python:3.12-slim`
- Install system deps: `ffmpeg` (for audio processing), `ca-certificates`
- Copy `requirements.txt`, install Python deps
- Copy app code
- Run as non-root user
- Expose port 8080
- Entrypoint: `uvicorn app.main:app --host 0.0.0.0 --port 8080`

### docker-compose.yml
```yaml
services:
  readeck-audiobook:
    build: .
    ports:
      - "8080:8080"
    environment:
      READECK_BASE_URL: ${READECK_BASE_URL}
      READECK_API_TOKEN: ${READECK_API_TOKEN}
      EDGE_TTS_VOICE: ${EDGE_TTS_VOICE:-en-US-AriaNeural}
      TTS_ENGINE: ${TTS_ENGINE:-edge-tts}
    volumes:
      - audio_data:/app/audio
      - db_data:/app/data
    restart: unless-stopped

volumes:
  audio_data:
  db_data:
```

---

## Implementation Steps

1. **Project scaffolding** — `requirements.txt`, `Dockerfile`, `docker-compose.yml`
2. **Database layer** (`models.py`, `jobs.py`) — SQLite schema, CRUD helpers using `aiosqlite`
3. **Readeck client** (`readeck.py`) — `httpx.AsyncClient` wrapper for bookmark list + article fetch
4. **TTS backends** (`tts.py`) — `edge-tts` implementation + ebook2audiobook stub
5. **Job worker** (`jobs.py`) — async queue, worker coroutine, text extraction from HTML/Markdown
6. **FastAPI app** (`main.py`) — routes, background task startup, static files
7. **Templates** — `base.html`, `index.html` (bookmark list with HTMX), `jobs.html` (status + download)
8. **Commit and push** to branch `claude/plan-bookmark-audio-app-cqKet`
