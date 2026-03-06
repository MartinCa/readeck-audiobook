import os

# Must be set before any app module is imported so module-level env reads pick
# up the test values (e.g. app.readeck.READECK_BASE_URL).
os.environ.setdefault("READECK_BASE_URL", "http://readeck.test")
os.environ.setdefault("READECK_API_TOKEN", "test-token")

import httpx
import pytest
import pytest_asyncio

from app import models


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """Isolated in-memory-ish SQLite DB for each test."""
    monkeypatch.setattr("app.models.DB_PATH", str(tmp_path / "test.db"))
    await models.init_db()


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Test HTTP client wired to the FastAPI app with an isolated DB."""
    monkeypatch.setattr("app.models.DB_PATH", str(tmp_path / "test.db"))
    # Prevent the background worker task from starting in tests.
    monkeypatch.setattr("app.jobs.start_worker", lambda: None)
    monkeypatch.setattr("app.jobs.stop_worker", lambda: None)

    # ASGITransport does not run the ASGI lifespan, so initialise DB explicitly.
    await models.init_db()

    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
