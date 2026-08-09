# Readeck Audiobook

A lightweight web app that converts your [Readeck](https://readeck.org) bookmarks into MP3 audiobooks using text-to-speech.

Browse your Readeck library, select articles, queue them for audio generation, and download the resulting MP3 files — all from a simple web UI.

## Features

- Paginated, searchable bookmark browser synced from your Readeck instance
- Background TTS job queue with live status polling
- Two TTS backends:
  - **edge-tts** (default) — Microsoft neural voices, no API key, ~200 MB Docker image
  - **kokoro** (optional) — small local neural model, runs in-process, no API key, English only for now
- Automatic language detection: voice is chosen based on the bookmark's `lang` field
- Readable filenames and ID3 tags — files land as `how-to-build-a-thing-a1b2c3d4.mp3`, tagged with the article title
- Long articles are synthesised in chunks, with retries, so a dropped connection doesn't waste the whole run
- Jobs interrupted by a restart resume automatically
- SQLite persistence — no external database needed
- Fully self-hosted: no CDN or third-party asset requests at runtime

## Quick start

**1. Copy and fill in the environment file**

```sh
cp .env.example .env
```

Edit `.env`:

```sh
READECK_BASE_URL=https://readeck.example.com
READECK_API_TOKEN=your-api-token-here
```

To get an API token: in Readeck go to **Settings → API tokens** and create one.

**2. Start the app**

```sh
docker compose up -d
```

Open [http://localhost:8080](http://localhost:8080).

## Security

**The app is unauthenticated by default and has no concept of users.** Anyone who can reach the port can browse your library and queue or delete jobs. Run it on a trusted network, or behind your own authenticating reverse proxy.

For a simple gate, set `AUTH_USERNAME` and `AUTH_PASSWORD` to enable HTTP basic auth on every route except `/health` (kept open for container healthchecks).

State-changing requests (`POST`, `DELETE`) are rejected when they carry an `Origin`/`Referer` from another site, so a page you visit elsewhere cannot quietly delete your jobs. If you put the app behind a proxy that serves it on a different hostname than the browser sends, list that origin in `TRUSTED_ORIGINS`.

## Configuration

All settings are passed as environment variables (or via `.env`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `READECK_BASE_URL` | Yes | — | Readeck instance URL, e.g. `https://readeck.example.com` |
| `READECK_API_TOKEN` | Yes | — | Readeck Bearer API token |
| `TTS_ENGINE` | No | `edge-tts` | `edge-tts` or `kokoro` |
| `EDGE_TTS_VOICE` | No | `en-US-AriaNeural` | Default voice when language cannot be detected |
| `KOKORO_VOICE` | No | `af_heart` | Kokoro voice name, only used when `TTS_ENGINE=kokoro` |
| `MAX_CONCURRENT_JOBS` | No | `2` | Maximum simultaneous TTS jobs |
| `DATA_DIR` | No | `/app/data` | Where the SQLite database lives |
| `AUDIO_DIR` | No | `/app/audio` | Where generated MP3s are written |
| `TTS_CHUNK_CHARS` | No | `2000` | Characters per synthesis request for long articles |
| `TTS_MAX_RETRIES` | No | `3` | Attempts per chunk before the job fails |
| `WORKER_POLL_SECONDS` | No | `2` | How often the worker looks for pending jobs |
| `MAX_JOB_ATTEMPTS` | No | `3` | Restart-interrupted retries before a job is given up on |
| `SHUTDOWN_GRACE_SECONDS` | No | `30` | How long shutdown waits for in-flight jobs |
| `AUTH_USERNAME` | No | — | Enables HTTP basic auth (with `AUTH_PASSWORD`) |
| `AUTH_PASSWORD` | No | — | Enables HTTP basic auth (with `AUTH_USERNAME`) |
| `TRUSTED_ORIGINS` | No | — | Extra comma-separated origins allowed to POST/DELETE |

### Language-to-voice mapping (edge-tts)

The voice is automatically selected based on the bookmark's `lang` field:

| Language | Voice |
|---|---|
| `en` | en-US-AriaNeural |
| `de` | de-DE-KatjaNeural |
| `fr` | fr-FR-DeniseNeural |
| `es` | es-ES-ElviraNeural |
| `it` | it-IT-ElsaNeural |
| `nl` | nl-NL-ColetteNeural |
| `pt` | pt-PT-RaquelNeural |
| `pl` | pl-PL-ZofiaNeural |
| `sv` | sv-SE-SofieNeural |
| `da` | da-DK-ChristelNeural |
| `nb` | nb-NO-PernilleNeural |
| `fi` | fi-FI-NooraNeural |

Any unlisted language falls back to `EDGE_TTS_VOICE`. The engine and voice are resolved once, when the job is queued, and stored on the job — so the Jobs page always reports what actually ran.

## Output files

Generated MP3s are named after the article, slugified, with a short job-id suffix:

```
how-to-build-a-thing-a1b2c3d4.mp3
```

The suffix keeps two articles with the same title apart and ties the file back to its job. Titles are transliterated to ASCII (`Blåbærgrød` → `blabaergrod`) and truncated to 80 characters on a word boundary; a title with no usable characters falls back to `article-<id>.mp3`. Each file is tagged with the article title, so it shows up properly in a media player rather than as a bare filename.

## Kokoro (optional, higher quality)

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is a small (82M parameter) open-weight neural TTS model that runs directly in-process — no separate service, no multi-GB download, no subprocess orchestration. Quality is noticeably better than edge-tts, and it runs fine on CPU (near-instant on a GPU if one's available — it uses CUDA automatically when present).

**Currently English only.** Kokoro only covers a handful of languages; when `TTS_ENGINE=kokoro`, bookmarks in any other language still fall back to edge-tts automatically (same as today).

CI publishes a dedicated `*-kokoro` image tag (e.g. `ghcr.io/martinca/readeck-audiobook:latest-kokoro`, or pinned like `:1.2.0-kokoro`) built from `Dockerfile.kokoro` — with `kokoro`/`torch`/`soundfile` installed, kept out of the default `requirements.txt` so the base image stays lightweight. Pull and run it directly, no local build needed:

```sh
cp .env.example .env   # fill in READECK_BASE_URL / READECK_API_TOKEN
docker compose -f docker-compose.kokoro.yml up -d
```

`docker-compose.kokoro.yml` is a standalone compose file (not layered on the default `docker-compose.yml`) that pulls `ghcr.io/martinca/readeck-audiobook:latest-kokoro` by default. Pin a specific version instead via `.env`:

```sh
READECK_AUDIOBOOK_IMAGE=ghcr.io/martinca/readeck-audiobook:1.2.0-kokoro
```

It requests GPU access via `deploy.resources.reservations.devices` (NVIDIA Container Toolkit required on the host) — Kokoro uses CUDA automatically when available and falls back to CPU otherwise, so drop that block if you don't have a GPU.

Model weights (a few hundred MB) download from the Hugging Face Hub on first use and are cached in the `kokoro_models` volume.

To build the image yourself instead of pulling: `docker build -f Dockerfile.kokoro -t readeck-audiobook:kokoro .`

**`[Errno 13] Permission denied: '/app/models/hub'`**: the container runs as an unprivileged `appuser` (uid 1001). If `kokoro_models` is a pre-existing volume that was first populated by an older image (or by a container running as root), it can retain root ownership, which blocks writes from `appuser`. Fix it once with:

```sh
docker run --rm -v kokoro_models:/data alpine chown -R 1001:1001 /data
```

## Architecture

```
readeck-audiobook/
├── app/
│   ├── main.py        # FastAPI routes, middleware and lifespan
│   ├── config.py      # Environment-driven settings
│   ├── readeck.py     # Readeck API client (pooled httpx)
│   ├── tts.py         # Text cleaning, filenames, TTS backends
│   ├── jobs.py        # Background worker loop
│   ├── models.py      # SQLite schema and queries (aiosqlite)
│   └── templates/     # Jinja2 HTML templates
│       ├── base.html
│       ├── _pagination.html
│       ├── index.html # Bookmark browser
│       └── jobs.html  # Job list with live status polling
├── static/
│   ├── app.css
│   └── vendor/        # Vendored Alpine.js (no CDN at runtime)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

**Stack:** Python 3.12+ · FastAPI · Jinja2 · Alpine.js · SQLite · edge-tts

The default image builds on Python 3.14; `Dockerfile.kokoro` pins 3.12 for torch compatibility. CI runs the suite on both.

**How a job flows:**

1. User selects bookmarks on the Bookmarks page and clicks **Generate audio**
2. The selected bookmarks are fetched from Readeck concurrently, and a `Job` row is inserted per bookmark with `status=pending`, recording the article's language and the engine/voice resolved from it
3. The background worker atomically claims pending jobs (up to `MAX_CONCURRENT_JOBS` at a time) and marks them `processing`
4. The worker fetches the article text (Markdown preferred, HTML fallback) and strips markup down to speakable prose
5. The recorded TTS engine synthesises the audio in chunks, retrying failures, and writes an MP3 to `AUDIO_DIR` via a temp file so a crash cannot leave a truncated download
6. The job is marked `completed` with an `audio_path`; the Jobs page shows a download link
7. The Jobs page polls all active jobs in one request every 4 seconds and updates them in place

If the process dies mid-job, the interrupted job returns to `pending` on the next boot (up to `MAX_JOB_ATTEMPTS`), and any orphaned audio files are cleaned up.

## Development

Run locally without Docker. `DATA_DIR` and `AUDIO_DIR` default to the container paths, so point them at the working directory:

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

export READECK_BASE_URL=https://readeck.example.com
export READECK_API_TOKEN=your-token
export DATA_DIR=./data AUDIO_DIR=./audio

mkdir -p audio data
uvicorn app.main:app --reload --port 8080
```

Lint and test:

```sh
ruff check .        # lint
ruff format .       # format
pytest              # run all tests
```

## HTTP endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Bookmark browser (paginated, searchable) |
| `GET` | `/jobs` | Job list with status and download links |
| `POST` | `/jobs` | Queue bookmarks for audio generation |
| `DELETE` | `/jobs/{id}` | Delete a job and its audio file |
| `POST` | `/jobs/bulk-delete` | Delete several jobs at once |
| `GET` | `/audio/{filename}` | Download generated MP3 |
| `GET` | `/api/jobs/{id}/status` | Poll a single job's status (JSON) |
| `GET` | `/api/jobs/statuses?ids=…` | Poll many jobs in one request (JSON) |
| `GET` | `/api/jobs` | List jobs, paginated (JSON) |
| `GET` | `/api/jobs/ids` | Every job id, for "select all" (JSON) |
| `GET` | `/health` | Health check |
