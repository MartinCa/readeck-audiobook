# Readeck Audiobook — Claude Code Guide

## Project overview

A lightweight FastAPI web app that converts [Readeck](https://readeck.org) bookmarks into MP3 audiobooks via text-to-speech. See [README.md](README.md) for full documentation.

**Stack:** Python 3.12+ · FastAPI · SQLite · edge-tts, with a React + TypeScript web UI in `frontend/` built on [MartinCa/frontend-kit](https://github.com/MartinCa/frontend-kit) (shadcn/ui on Base UI, Tailwind, TanStack Router + Query).

## Layout notes

- `app/config.py` is the single place environment variables are read. Add new settings there rather than calling `os.environ` from feature modules.
- Storage paths (`DATA_DIR`, `AUDIO_DIR`) are configurable and default to the container paths — never hardcode `/app/...`.
- The engine and voice for a job are resolved once at queue time and stored on the row. The worker uses the stored values, so the UI always reports what actually ran.
- Kokoro runs on sherpa-onnx (onnxruntime), not PyTorch. sherpa-onnx picks a speaker by integer id, so `KOKORO_VOICES` in `app/tts.py` maps the familiar names — that table is specific to the `kokoro-multi-lang-v1_0` model `Dockerfile.kokoro` bundles, so changing the model means changing the table.
- Before writing UI code, read `frontend/DESIGN.md` (shared frontend-kit rules; section 9 is this project's) — `.claude/skills/frontend-conventions/` carries the same rules. Never hand-edit `frontend/src/components/ui/**`, `frontend/src/lib/api-types.ts` or `frontend/src/routeTree.gen.ts`.
- The backend is a JSON API under `/api`: camelCase on the wire (pydantic models in `app/schemas.py`), errors as `application/problem+json`. FastAPI serves the built UI from `FRONTEND_DIR` and falls back to `index.html` for client routes.
- After changing an endpoint or schema, run `python scripts/export_openapi.py` and `pnpm generate:api-types` in `frontend/`; `tests/test_openapi.py` fails until the checked-in spec matches.
- The app makes no CDN requests at runtime (fonts and scripts are bundled by Vite); keep it that way.
- A completed job *is* the bookmark's audio: the Bookmarks page shows it, and the Jobs page lists only pending, processing and failed jobs. A new completion for a bookmark replaces its earlier audio.
- Bookmarks are read from the local `bookmarks` table, which `app/sync.py` keeps current from Readeck's `/api/bookmarks/sync` (ids and update times, then only changed bookmarks are fetched). The bookmark listing never calls Readeck; a bookmark gone from Readeck is removed with its jobs and audio via `jobs.forget_bookmarks`. One archived or read there is kept as a row with `finished = 1` (hidden, no jobs or audio) so unchanged ones are not re-fetched; `has_article = 0` means Readeck extracted no text, so it cannot be queued or auto generated.
- Readeck actions (archive + read, delete) go through `jobs.apply_readeck_action`: Readeck first, then forget locally only the bookmarks it worked for. The minimum-words guard reads the `min_article_words` setting.
- Sync and auto generation settings live in the `settings` table (edited in the UI), not the environment; both crons run on the loop in `app/scheduler.py`. An auto generation run only queues bookmarks absent from `queued_bookmarks` and `auto_excluded`, so each bookmark is picked up once.
- Lists poll with TanStack Query's `refetchInterval`, one request for the whole page and only while a job on it is queued or generating. Avoid per-card polling.

## MCP servers

### Vuetify (project scope)

The Vuetify MCP server is configured at project scope in [`.mcp.json`](.mcp.json).
It provides Vuetify component documentation and usage guidance directly inside Claude Code.

```json
{
  "mcpServers": {
    "vuetify": {
      "command": "npx",
      "args": ["-y", "@vuetify/mcp@latest"]
    }
  }
}
```

Claude Code automatically loads `.mcp.json` from the project root when you open this repository, so the `vuetify` MCP server is available to all contributors without any per-user configuration.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

export READECK_BASE_URL=https://readeck.example.com
export READECK_API_TOKEN=your-token
export DATA_DIR=./data AUDIO_DIR=./audio

mkdir -p audio data
uvicorn app.main:app --reload --port 8080
```

Frontend (Node 24, pnpm), in a second terminal — the Vite dev server proxies `/api` to port 8080:

```sh
cd frontend && pnpm install && pnpm dev
```

## Linting & tests

```sh
ruff check .        # lint
ruff format .       # format
pytest              # run all tests

cd frontend
pnpm run lint       # ESLint, --max-warnings 0
pnpm run format-check
pnpm exec tsc -b    # type check
pnpm test           # vitest
```
