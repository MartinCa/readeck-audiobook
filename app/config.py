"""Central configuration, read once from the environment.

Every tunable lives here so that paths are overridable for local development
(the app previously hardcoded the container paths ``/app/data`` and
``/app/audio``, which made running outside Docker impossible) and so that
tests have a single place to patch.
"""

import os
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or default).expanduser()


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


# ── Storage ────────────────────────────────────────────────────────────────────

DATA_DIR = _env_path("DATA_DIR", "/app/data")
AUDIO_DIR = _env_path("AUDIO_DIR", "/app/audio")

# ── Readeck ────────────────────────────────────────────────────────────────────

READECK_BASE_URL = os.environ.get("READECK_BASE_URL", "").rstrip("/")
READECK_API_TOKEN = os.environ.get("READECK_API_TOKEN", "")

# ── TTS ────────────────────────────────────────────────────────────────────────

TTS_ENGINE = os.environ.get("TTS_ENGINE", "edge-tts")
EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "en-US-AriaNeural")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")

# Long articles are synthesised in pieces: edge-tts is a single WebSocket
# session per call, and one multi-hour request is far more likely to fail than
# a sequence of short ones.
TTS_CHUNK_CHARS = _env_int("TTS_CHUNK_CHARS", 2000, minimum=200)
TTS_MAX_RETRIES = _env_int("TTS_MAX_RETRIES", 3)

# ── Worker ─────────────────────────────────────────────────────────────────────

MAX_CONCURRENT_JOBS = _env_int("MAX_CONCURRENT_JOBS", 2)
WORKER_POLL_SECONDS = _env_int("WORKER_POLL_SECONDS", 2)
# Jobs interrupted by a restart are retried automatically, but only so often —
# a job that reliably crashes the process would otherwise loop forever.
MAX_JOB_ATTEMPTS = _env_int("MAX_JOB_ATTEMPTS", 3)
SHUTDOWN_GRACE_SECONDS = _env_int("SHUTDOWN_GRACE_SECONDS", 30)

# ── Security ───────────────────────────────────────────────────────────────────

# Optional HTTP basic auth. Both must be set for the gate to activate; the app
# is unauthenticated by default, which is only appropriate on a trusted network.
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

# Extra origins permitted to make state-changing requests, beyond the request's
# own host. Comma-separated, e.g. "https://audiobook.example.com".
TRUSTED_ORIGINS = tuple(
    o.strip().rstrip("/") for o in os.environ.get("TRUSTED_ORIGINS", "").split(",") if o.strip()
)


def auth_enabled() -> bool:
    return bool(AUTH_USERNAME and AUTH_PASSWORD)


DB_PATH = str(DATA_DIR / "audiobook.db")
