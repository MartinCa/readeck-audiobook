"""Integration tests for FastAPI routes."""

from unittest.mock import AsyncMock

from app import models


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_api_jobs_empty(client):
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_api_job_status_not_found(client):
    resp = await client.get("/api/jobs/does-not-exist/status")
    assert resp.status_code == 404


async def test_api_job_status_found(client):
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    resp = await client.get(f"/api/jobs/{job['id']}/status")
    assert resp.status_code == 200
    assert resp.json()["id"] == job["id"]


async def test_jobs_page(client):
    resp = await client.get("/jobs")
    assert resp.status_code == 200
    assert b"jobs" in resp.content.lower()


async def test_index_page(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.list_bookmarks",
        AsyncMock(return_value={"items": [], "total": 0, "total_pages": 1, "current_page": 1}),
    )
    resp = await client.get("/")
    assert resp.status_code == 200


async def test_post_jobs_queues_and_returns_jobs_page(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "My Article", "url": "http://example.com"}),
    )
    resp = await client.post("/jobs", data={"bookmark_ids": ["abc123"]})
    assert resp.status_code == 200
    # Response is the jobs HTML page
    assert b"My Article" in resp.content


async def test_post_jobs_multiple_bookmarks(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "Article", "url": "http://example.com"}),
    )
    resp = await client.post("/jobs", data={"bookmark_ids": ["id1", "id2", "id3"]})
    assert resp.status_code == 200
    jobs, total = await models.list_jobs()
    assert total == 3


async def test_delete_job(client):
    job = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    resp = await client.delete(f"/jobs/{job['id']}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert await models.get_job(job["id"]) is None


async def test_delete_job_not_found(client):
    resp = await client.delete("/jobs/does-not-exist")
    assert resp.status_code == 404


async def test_audio_not_found(client):
    resp = await client.get("/audio/nonexistent.mp3")
    assert resp.status_code == 404


async def test_audio_path_traversal_blocked(client):
    # Path traversal attempt should be safely handled (file won't exist anyway)
    resp = await client.get("/audio/../../etc/passwd")
    assert resp.status_code == 404
