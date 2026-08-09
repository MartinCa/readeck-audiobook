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
- MP3 download links for completed jobs
- SQLite persistence — no external database needed

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

Any unlisted language falls back to `EDGE_TTS_VOICE`.

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

## Architecture

```
readeck-audiobook/
├── app/
│   ├── main.py        # FastAPI routes and lifespan
│   ├── readeck.py     # Readeck API client (httpx)
│   ├── tts.py         # TTS backends (edge-tts, kokoro)
│   ├── jobs.py        # Background worker loop
│   ├── models.py      # SQLite schema and queries (aiosqlite)
│   └── templates/     # Jinja2 HTML templates
│       ├── base.html
│       ├── index.html # Bookmark browser
│       └── jobs.html  # Job list with live status polling
├── static/
│   └── app.css
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

**Stack:** Python 3.12 · FastAPI · Jinja2 · HTMX · Alpine.js · SQLite · edge-tts

**How a job flows:**

1. User selects bookmarks on the Bookmarks page and clicks **Generate audio**
2. A `Job` row is inserted per bookmark with `status=pending`
3. The background worker atomically claims pending jobs (up to `MAX_CONCURRENT_JOBS` at a time) and marks them `processing`
4. The worker fetches the article text (Markdown preferred, HTML fallback) or EPUB from Readeck
5. The chosen TTS engine synthesises the audio and writes an MP3 to `/app/audio/`
6. The job is marked `completed` with an `audio_path`; the Jobs page shows a download link
7. The Jobs page polls active jobs every 4 seconds and reloads automatically on completion

## Development

Run locally without Docker:

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export READECK_BASE_URL=https://readeck.example.com
export READECK_API_TOKEN=your-token

mkdir -p audio data
uvicorn app.main:app --reload --port 8080
```

## HTTP endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Bookmark browser (paginated, searchable) |
| `GET` | `/jobs` | Job list with status and download links |
| `POST` | `/jobs` | Queue bookmarks for audio generation |
| `DELETE` | `/jobs/{id}` | Delete a job and its audio file |
| `GET` | `/audio/{filename}` | Download generated MP3 |
| `GET` | `/api/jobs/{id}/status` | Poll job status (JSON) |
| `GET` | `/api/jobs` | List all jobs (JSON) |
| `GET` | `/health` | Health check |
