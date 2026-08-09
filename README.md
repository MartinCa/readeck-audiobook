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

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is a small (82M parameter) open-weight neural TTS model that runs directly in-process — no separate service, no subprocess orchestration. Quality is noticeably better than edge-tts, and it runs fine on CPU.

It runs on [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (onnxruntime), not PyTorch. That keeps the variant image close in size to the default one: no torch, no transformers, no spaCy, and no espeak-ng system package — the model archive bundles its own `espeak-ng-data`.

**Currently English only.** Kokoro only covers a handful of languages; when `TTS_ENGINE=kokoro`, bookmarks in any other language still fall back to edge-tts automatically (same as today).

CI publishes two image tags built from `Dockerfile.kokoro`:

| Tag | Build arg | For |
| --- | --- | --- |
| `latest-kokoro` | `KOKORO_ACCEL=cpu` | CPU-only hosts |
| `latest-kokoro-cuda` | `KOKORO_ACCEL=cuda` | NVIDIA GPU hosts |

Both bake the model in at build time, so there is nothing to download on first run. Pull and run directly, no local build needed:

```sh
cp .env.example .env   # fill in READECK_BASE_URL / READECK_API_TOKEN
docker compose -f docker-compose.kokoro.yml up -d
```

`docker-compose.kokoro.yml` is a standalone compose file (not layered on the default `docker-compose.yml`) that pulls `ghcr.io/martinca/readeck-audiobook:latest-kokoro-cuda` by default and requests GPU access via `deploy.resources.reservations.devices` (NVIDIA Container Toolkit required on the host). Pin a specific version instead via `.env`:

```sh
READECK_AUDIOBOOK_IMAGE=ghcr.io/martinca/readeck-audiobook:1.2.0-kokoro-cuda
```

**On a host without a GPU**, switch to the CPU tag and set `KOKORO_PROVIDER=cpu`, then drop the `deploy` block. Unlike the old torch build, the CUDA image cannot fall back to CPU — the execution provider is compiled into the wheel, so the tag has to change too.

To build the images yourself instead of pulling:

```sh
docker build -f Dockerfile.kokoro -t readeck-audiobook:kokoro .
docker build -f Dockerfile.kokoro --build-arg KOKORO_ACCEL=cuda -t readeck-audiobook:kokoro-cuda .
```

**Verifying the GPU is actually in use.** sherpa-onnx falls back to CPU silently when the CUDA execution provider fails to register — there is no `torch.cuda.is_available()` equivalent to log. The image therefore turns on sherpa-onnx's own debug output whenever `KOKORO_PROVIDER=cuda`; check the container logs on the first synthesis for the list of providers that registered.

**Voices.** `KOKORO_VOICE` still takes names (`af_heart`, `am_michael`, `bf_emma`, …). sherpa-onnx selects speakers by integer id internally, and `app/tts.py` holds the name→id table for the bundled `kokoro-multi-lang-v1_0` model. An unrecognised name fails the job with the list of valid English voices rather than quietly synthesising in another voice.

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

Both images build on Python 3.14. CI runs the suite on 3.12 (the declared minimum) and 3.14.

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
