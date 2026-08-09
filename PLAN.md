# Readeck Audiobook Web App - Implementation Plan

> **Historical document.** This was the original design sketch, kept for
> context. It no longer describes the code: job execution is a database-polling
> worker rather than `BackgroundTasks` + an asyncio queue, `GET /api/bookmarks`
> was never built, and configuration, storage paths and the endpoint list have
> all moved on. See [README.md](README.md) for how the app actually works.

## Overview

A lightweight web app that fetches bookmarks from a Readeck instance and generates audio versions asynchronously using TTS. Users can select bookmarks, queue generation jobs, and download the resulting MP3/WAV files.

## Architecture

### Tech Stack

- **Backend**: Python 3.12 + FastAPI (async, lightweight, built-in background tasks)
- **Frontend**: Jinja2 templates + HTMX + Alpine.js (server-rendered, minimal JS footprint)
- **TTS Engine**: `edge-tts` (Microsoft Edge TTS — free, no API key, high quality neural voices)
  - Optional: Kokoro-82M as a higher-quality local neural backend (English only for now)
- **Persistence**: SQLite via `aiosqlite` (no external DB dependency)
- **Task execution**: FastAPI `BackgroundTasks` + asyncio queue (no Redis/Celery needed)
- **Content fetching**: Readeck `/api/bookmarks/{id}/article` (HTML) → strip tags → TTS

### Why edge-tts over a local neural model by default

`edge-tts` is a thin Python wrapper around Microsoft's neural TTS service (used in Windows Narrator / Edge browser), produces high-quality results, requires no API key, and keeps the Docker image under 200MB. A local neural model (`TTS_ENGINE=kokoro`, using the small 82M-parameter Kokoro model) can be wired in as an optional mode, built on a separate `Dockerfile.kokoro` image variant so the default image stays lightweight.

---

## File Structure

```
readeck-audiobook/
├── app/
│   ├── main.py           # FastAPI app, routes, startup
│   ├── readeck.py        # Readeck API client (httpx)
│   ├── tts.py            # TTS backends (edge-tts, kokoro)
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
| `TTS_ENGINE` | No | `edge-tts` (default) or `kokoro` |
| `EDGE_TTS_VOICE` | No | Voice name, e.g. `en-US-AriaNeural` (default) |
| `KOKORO_VOICE` | No | Kokoro voice name (default `af_heart`), only used when `TTS_ENGINE=kokoro` |
| `KOKORO_PROVIDER` | No | `cpu` (default) or `cuda`; needs the matching image variant |
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

### kokoro (optional)
- Local 82M-parameter neural model, run in-process via sherpa-onnx (onnxruntime)
- Input: plain text string, same as edge-tts — no separate ebook export needed
- English only for now; other languages fall back to edge-tts automatically
- Requires an image built from `Dockerfile.kokoro` (adds `sherpa-onnx`/`soundfile`,
  kept out of the default `requirements.txt` to keep the base image lightweight);
  build with `--build-arg KOKORO_ACCEL=cuda` for the GPU variant

---

## Docker Setup

### Dockerfile (multi-stage, slim)
- Base: `python:3.14-slim`
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
4. **TTS backends** (`tts.py`) — `edge-tts` implementation + optional kokoro backend
5. **Job worker** (`jobs.py`) — async queue, worker coroutine, text extraction from HTML/Markdown
6. **FastAPI app** (`main.py`) — routes, background task startup, static files
7. **Templates** — `base.html`, `index.html` (bookmark list with HTMX), `jobs.html` (status + download)
8. **Commit and push** to branch `claude/plan-bookmark-audio-app-cqKet`
