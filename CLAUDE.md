# Readeck Audiobook — Claude Code Guide

## Project overview

A lightweight FastAPI web app that converts [Readeck](https://readeck.org) bookmarks into MP3 audiobooks via text-to-speech. See [README.md](README.md) for full documentation.

**Stack:** Python 3.12+ · FastAPI · Jinja2 · Alpine.js · SQLite · edge-tts

## Layout notes

- `app/config.py` is the single place environment variables are read. Add new settings there rather than calling `os.environ` from feature modules.
- Storage paths (`DATA_DIR`, `AUDIO_DIR`) are configurable and default to the container paths — never hardcode `/app/...`.
- The engine and voice for a job are resolved once at queue time and stored on the row. The worker uses the stored values, so the UI always reports what actually ran.
- Kokoro runs on sherpa-onnx (onnxruntime), not PyTorch. sherpa-onnx picks a speaker by integer id, so `KOKORO_VOICES` in `app/tts.py` maps the familiar names — that table is specific to the `kokoro-multi-lang-v1_0` model `Dockerfile.kokoro` bundles, so changing the model means changing the table.
- Front-end assets are vendored in `static/vendor/`. The app makes no CDN requests at runtime; keep it that way.
- The Jobs page polls `/api/jobs/statuses` once per interval for all active jobs and patches the DOM in place. Avoid reintroducing per-card polling or full-page reloads.

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

## Linting & tests

```sh
ruff check .        # lint
ruff format .       # format
pytest              # run all tests
```
