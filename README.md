# Readeck Audiobook

A lightweight web app that converts your [Readeck](https://readeck.org) bookmarks into MP3 audiobooks using text-to-speech.

Browse your Readeck library, select articles, queue them for audio generation, and listen to or download the resulting MP3 right on the bookmark — or let a schedule generate audio for new articles automatically.

## Features

- Paginated, searchable bookmark browser for your Readeck instance, showing each article's publish date and the date it was added to Readeck
- A local copy of the Readeck library, synced on its own cron schedule, so filtering stays fast with thousands of bookmarks; bookmarks deleted in Readeck are removed here too, audio included
- Filters: has audio or not, excluded from auto generation or not, and optional start/end dates for when an article was published and when it was added to Readeck
- Finished audio lives on its bookmark, with a player and a download link; bulk-delete audio or bulk-exclude bookmarks from auto generation
- Optional **auto audio generation** on a cron schedule (see below)
- Background TTS job queue; the Jobs page shows what is queued, generating or failed, updated live
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
| `KOKORO_IDLE_UNLOAD_SECONDS` | No | `300` | Unload the Kokoro model after this long without a job; `0` keeps it loaded |
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
| `TRUSTED_ORIGINS` | No | — | Extra comma-separated origins allowed to POST/PUT/DELETE |
| `TZ` | No | `UTC` | Time zone the auto generation cron schedule is read in, e.g. `Europe/Copenhagen` |
| `FRONTEND_DIR` | No | `/app/static` | Where the built web UI is served from; only needed when running outside Docker |

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

## Readeck sync

The Bookmarks page and auto generation work from a local copy of your Readeck library in the app's database. The app syncs when it starts and then on a cron schedule you set under **Settings** (default `*/15 * * * *`, every 15 minutes, in the container's time zone). **Sync now** on the Settings page runs one immediately.

A sync asks Readeck's sync endpoint for every bookmark id with its last-updated time in one request, then fetches only the bookmarks that are new or changed. A bookmark Readeck no longer has is checked once more and then removed here, along with its jobs and audio files. An older Readeck without the sync endpoint works too, at the cost of reading the full bookmark list each time.

Search on the Bookmarks page matches the title, site, authors, description and URL of the local copy, not the article text.

If a bookmark is deleted in Readeck while its audio is queued or generating, the job is dropped quietly rather than reported as failed.

## Auto audio generation

Turn it on under **Settings** in the web UI (it is off by default; the settings live in the database, not the environment):

- **Schedule** — a cron expression (default `0 * * * *`, hourly), read in the container's time zone (`TZ`). On each run the app looks through the local copy of Readeck (see above) for articles and queues the ones that need audio; the worker then fetches each article's text from Readeck.
- **Only articles added on or after** — optional. Leave it empty to generate audio for every existing article as well as new ones; set a date to leave older articles alone.

A run queues an article only if Readeck has finished loading it, it has never had a job before and it is not excluded, so each article is picked up once: deleting its audio, or a failed job, does not make the next run try again (use **Generate audio** or **Retry** for that). Videos and pictures are skipped. To keep particular bookmarks out, select them on the Bookmarks page and choose **Exclude from auto generation**. **Run now** on the Settings page does a run immediately.

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

**Verifying the GPU is actually in use.** sherpa-onnx falls back to CPU silently when the CUDA execution provider fails to register — there is no `torch.cuda.is_available()` equivalent to log. Set `KOKORO_DEBUG=1` and check the container logs on the first synthesis for the list of providers that registered, then turn it back off: that flag also makes sherpa-onnx dump the full text of every article to the log, twice (once as hex).

**GPU memory while idle.** The model is loaded on the first Kokoro job and cached, because reloading it per job would add several seconds to every one. An onnxruntime session holds its weights and its allocator arena for as long as it exists, so on CUDA the app sat on roughly 2.3 GB of VRAM between jobs — `nvidia-smi` shows the uvicorn process holding it at 0% utilisation. `KOKORO_IDLE_UNLOAD_SECONDS` (default 300) drops the model once that long passes with no Kokoro job, and the next job loads it again; set it to `0` on a machine dedicated to this app, where holding the memory costs nothing. Note that a few hundred MB of CUDA context stays attached to the process after the unload — the driver keeps it until the process exits — so expect the figure to fall to a few hundred MB rather than to zero.

**`Failed to load library libonnxruntime_providers_cuda.so with error: lib*.so: cannot open shared object file`**: the CUDA build of sherpa-onnx does not bundle the CUDA runtime — unlike the old torch wheels, which did. Those libraries come from the `nvidia-*-cu12` wheels in `requirements-kokoro-cuda.txt`, whose directories `Dockerfile.kokoro` registers with `ldconfig`. If a job fails this way, a library is missing from that list: add the matching wheel (the name follows the library, e.g. `libcurand.so.10` → `nvidia-curand-cu12`) and rebuild. The image build runs `ldd` over the provider and fails if anything is unresolved, so this should be caught at build time rather than on the first job.

**Voices.** `KOKORO_VOICE` still takes names (`af_heart`, `am_michael`, `bf_emma`, …). sherpa-onnx selects speakers by integer id internally, and `app/tts.py` holds the name→id table for the bundled `kokoro-multi-lang-v1_0` model. An unrecognised name fails the job with the list of valid English voices rather than quietly synthesising in another voice.

## Architecture

```
readeck-audiobook/
├── app/
│   ├── main.py        # FastAPI JSON API, middleware, lifespan, serves the web UI
│   ├── config.py      # Environment-driven settings
│   ├── schemas.py     # API request/response models (camelCase on the wire)
│   ├── bookmarks.py   # Bookmark listing and filters over the local copy
│   ├── sync.py        # Keeps the local copy of Readeck current
│   ├── autogen.py     # Auto audio generation settings and runs
│   ├── scheduler.py   # Cron loop shared by the sync and auto generation
│   ├── readeck.py     # Readeck API client (pooled httpx)
│   ├── tts.py         # Text cleaning, filenames, TTS backends
│   ├── jobs.py        # Queueing and the background worker loop
│   └── models.py      # SQLite schema and queries (aiosqlite)
├── frontend/          # React + TypeScript web UI (MartinCa/frontend-kit conventions)
│   ├── DESIGN.md      # Frontend rules; section 9 is this project's
│   ├── openapi.json   # API spec the TypeScript types are generated from
│   └── src/
├── scripts/export_openapi.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

**Stack:** Python 3.12+ · FastAPI · SQLite · edge-tts — React · TypeScript · Vite · TanStack Router/Query · shadcn/ui (Base UI) · Tailwind, following [MartinCa/frontend-kit](https://github.com/MartinCa/frontend-kit). The Docker images build the web UI in a Node stage and serve it from FastAPI; nothing is fetched from a CDN at runtime.

Both images build on Python 3.14. CI runs the suite on 3.12 (the declared minimum) and 3.14.

**How a job flows:**

1. User selects bookmarks on the Bookmarks page and clicks **Generate audio** (or the auto generation schedule picks them up)
2. The selected bookmarks' title and language are read from the local copy of Readeck, and a `Job` row is inserted per bookmark with `status=pending`, recording the article's language and the engine/voice resolved from it
3. The background worker atomically claims pending jobs (up to `MAX_CONCURRENT_JOBS` at a time) and marks them `processing`
4. The worker fetches the article text (Markdown preferred, HTML fallback) and strips markup down to speakable prose
5. The recorded TTS engine synthesises the audio in chunks, retrying failures, and writes an MP3 to `AUDIO_DIR` via a temp file so a crash cannot leave a truncated download
6. The job is marked `completed` with an `audio_path`, replacing any earlier audio for that bookmark; the bookmark now shows a player and a download link, and the job leaves the Jobs page
7. While a job is queued or generating, the Bookmarks and Jobs pages refresh their list in one request every 4 seconds

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

The web UI lives in `frontend/` (Node 24, pnpm). In a second terminal, run the Vite dev server, which proxies `/api` to the backend on port 8080, and open http://localhost:5173:

```sh
cd frontend
pnpm install
pnpm dev
```

To serve a production build from FastAPI instead, run `pnpm build` and start uvicorn with `FRONTEND_DIR=frontend/dist`.

After changing an API endpoint or schema, regenerate the spec and the frontend's types (a test fails until you do):

```sh
python scripts/export_openapi.py
cd frontend && pnpm generate:api-types
```

Lint and test:

```sh
ruff check .        # lint
ruff format .       # format
pytest              # run all tests

cd frontend
pnpm run lint && pnpm run format-check && pnpm exec tsc -b && pnpm test
```

### Git hooks

Local hooks are managed by [lefthook](https://lefthook.dev/) from the shared
[`MartinCa/lefthook-configs`](https://github.com/MartinCa/lefthook-configs)
fragments pinned at `v2.1.0` in `lefthook.yml`. Human contributors install it
once per clone:

```bash
uv tool install lefthook@2.1.12  # standalone binary into uv's tool bin dir (default ~/.local/bin)
lefthook install                 # idempotent, safe to re-run
```

AI agents must not install lefthook themselves — it is included in the OpenCode
image; if `lefthook` is not on `PATH`, they should report this to the user (see
`AGENTS.md`).

Pre-commit runs `uvx ruff check --fix` and `uvx ruff format` on staged Python
and ESLint + Prettier on staged frontend files (re-staging fixes), a
`betterleaks` secret scan of the staged diff, and a `zizmor` audit of staged
workflow files; commit-msg enforces Conventional Commits; pre-push runs both
test suites (`uv run pytest`, `pnpm test`). `lefthook`, `pnpm`,
`betterleaks`, and `zizmor` must be on `PATH`, and
`LEFTHOOK=0 git commit` skips the hooks as a last resort. See `AGENTS.md` for
the exact hooks-vs-CI enforcement split.

## HTTP endpoints

The API is JSON with camelCase fields; errors are `application/problem+json`. The full spec is at `/docs` on a running instance and in `frontend/openapi.json`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/bookmarks` | Bookmarks with their audio and job state; `page`, `search`, `audio` (`with`/`without`), `autoGeneration` (`excluded`/`included`), `addedFrom`, `addedTo`, `publishedFrom`, `publishedTo` (`YYYY-MM-DD`) |
| `POST` | `/api/bookmarks/audio/delete` | Delete the audio of several bookmarks |
| `PUT` | `/api/bookmarks/auto-generation` | Exclude bookmarks from, or include them in, auto generation |
| `POST` | `/api/jobs` | Queue bookmarks for audio generation |
| `GET` | `/api/jobs` | Queued, generating and failed jobs, paginated |
| `GET` | `/api/jobs/ids` | Every such job id, for "select all" |
| `GET` | `/api/jobs/{id}` | One job |
| `POST` | `/api/jobs/{id}/retry` | Queue a failed job again |
| `DELETE` | `/api/jobs/{id}` | Delete a job and its audio file |
| `POST` | `/api/jobs/bulk-delete` | Delete several jobs at once |
| `GET` / `PUT` | `/api/settings` | Auto generation settings and its last/next run |
| `POST` | `/api/auto-generation/run` | Run auto generation now |
| `GET` | `/api/audio/{filename}` | Download generated MP3 |
| `GET` | `/health` | Health check |
