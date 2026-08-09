import os

# Must be set before any app module is imported so module-level env reads pick
# up the test values (e.g. app.config.READECK_BASE_URL).
os.environ.setdefault("READECK_BASE_URL", "http://readeck.test")
os.environ.setdefault("READECK_API_TOKEN", "test-token")

import httpx
import pytest
import pytest_asyncio

from app import models, tts


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    """Isolated audio output directory."""
    path = tmp_path / "audio"
    path.mkdir()
    monkeypatch.setattr(tts, "AUDIO_DIR", path)
    return path


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """Isolated SQLite database for each test."""
    monkeypatch.setattr(models, "DB_PATH", str(tmp_path / "test.db"))
    await models.init_db()


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch, audio_dir):
    """Test HTTP client wired to the FastAPI app with an isolated DB."""
    monkeypatch.setattr(models, "DB_PATH", str(tmp_path / "test.db"))

    # Prevent the background worker from starting in tests.
    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr("app.jobs.start_worker", lambda: None)
    monkeypatch.setattr("app.jobs.stop_worker", _noop_async)

    # ASGITransport does not run the ASGI lifespan, so initialise DB explicitly.
    await models.init_db()

    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        # POST /jobs and /jobs/bulk-delete respond with a redirect (PRG
        # pattern); follow it so callers see the resulting jobs page like a
        # browser would.
        follow_redirects=True,
    ) as c:
        yield c
