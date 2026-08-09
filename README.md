# Readeck Audiobook

A lightweight web app that converts your [Readeck](https://readeck.org) bookmarks into MP3 audiobooks using text-to-speech.

Browse your Readeck library, select articles, queue them for audio generation, and download the resulting MP3 files — all from a simple web UI.

## Features

- Paginated, searchable bookmark browser synced from your Readeck instance
- Background TTS job queue with live status polling
- Two TTS backends:
  - **edge-tts** (default) — Microsoft neural voices, no API key, ~200 MB Docker image
  - **ebook2audiobook** (optional) — high-quality neural TTS with voice cloning, runs as a separate container
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
| `TTS_ENGINE` | No | `edge-tts` | `edge-tts` or `ebook2audiobook` |
| `EDGE_TTS_VOICE` | No | `en-US-AriaNeural` | Default voice when language cannot be detected |
| `EBOOK2AUDIOBOOK_TIMEOUT_SECONDS` | No | `7200` | Max time to wait for an ebook2audiobook conversion before failing the job |
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

## ebook2audiobook (optional)

[ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) produces higher-quality audio using large neural TTS models and supports voice cloning. It is significantly heavier (multi-GB model downloads on first run).

Its Gradio UI does not expose a stable HTTP API for conversion — the only supported non-interactive path is its `--headless` CLI mode. Because of that, `TTS_ENGINE=ebook2audiobook` does **not** talk to a separate service over the network: instead, `Dockerfile.ebook2audiobook` builds a variant of this image on top of `athomasson2/ebook2audiobook`, and the app shells out to the bundled headless CLI locally for each conversion job.

**Enable it:**

```sh
# In your .env
TTS_ENGINE=ebook2audiobook
```

```sh
docker compose -f docker-compose.yml -f docker-compose.ebook2audiobook.yml up -d --build
```

The default base image is the CUDA build (`athomasson2/ebook2audiobook:v26.7.27-cu130`), which requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on the host. For CPU or ROCm hosts, build with a different base image and drop the `deploy.resources.reservations.devices` GPU block from `docker-compose.ebook2audiobook.yml`:

```sh
docker compose -f docker-compose.yml -f docker-compose.ebook2audiobook.yml build \
  --build-arg E2A_BASE_IMAGE=docker.io/athomasson2/ebook2audiobook:cpu
```

| Hardware | Tag |
|---|---|
| NVIDIA CUDA (default) | `athomasson2/ebook2audiobook:v26.7.27-cu130` |
| CPU only | `athomasson2/ebook2audiobook:cpu` |
| AMD ROCm 6.4 | `athomasson2/ebook2audiobook:rocm6.4` |

**Notes:**
- This image variant runs as root, unlike the default `Dockerfile` — the bundled ebook2audiobook runtime itself runs as root, and recursively `chown`-ing multi-GB model-cache volumes on every start isn't practical.
- Each conversion spawns its own `ebook2audiobook` process with its own model load — there's no shared server-side queue anymore. Keep `MAX_CONCURRENT_JOBS=1` unless the host has enough RAM/VRAM headroom for multiple concurrent model loads.

## Architecture

```
readeck-audiobook/
├── app/
│   ├── main.py        # FastAPI routes and lifespan
│   ├── readeck.py     # Readeck API client (httpx)
│   ├── tts.py         # TTS backends (edge-tts, ebook2audiobook)
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
