# Readeck Audiobook — Claude Code Guide

## Project overview

A lightweight FastAPI web app that converts [Readeck](https://readeck.org) bookmarks into MP3 audiobooks via text-to-speech. See [README.md](README.md) for full documentation.

**Stack:** Python 3.12 · FastAPI · Jinja2 · HTMX · Alpine.js · SQLite · edge-tts

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

mkdir -p audio data
uvicorn app.main:app --reload --port 8080
```

## Linting & tests

```sh
ruff check .        # lint
ruff format .       # format
pytest              # run all tests
```
