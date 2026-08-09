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


async def test_post_jobs_redirects_instead_of_rendering(client, monkeypatch):
    """POST /jobs must redirect (Post/Redirect/Get), not render the jobs page
    directly. Rendering it directly would leave the browser's current
    document POST-derived, so the jobs page's own periodic refresh (used to
    pick up job status changes) would resubmit that POST instead of doing a
    plain GET — silently re-queuing the same bookmarks on every refresh.
    """
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "My Article", "url": "http://example.com"}),
    )
    resp = await client.post("/jobs", data={"bookmark_ids": ["abc123"]}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/jobs?flash=")


async def test_post_jobs_multiple_bookmarks(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "Article", "url": "http://example.com"}),
    )
    resp = await client.post("/jobs", data={"bookmark_ids": ["id1", "id2", "id3"]})
    assert resp.status_code == 200
    jobs, total = await models.list_jobs()
    assert total == 3


async def test_post_jobs_skips_duplicate_active_job(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "My Article", "url": "http://example.com"}),
    )
    resp1 = await client.post("/jobs", data={"bookmark_ids": ["abc123"]})
    assert resp1.status_code == 200

    resp2 = await client.post("/jobs", data={"bookmark_ids": ["abc123"]})
    assert resp2.status_code == 200
    assert b"Skipped 1 already queued" in resp2.content

    jobs, total = await models.list_jobs()
    assert total == 1


async def test_post_jobs_allows_requeue_after_completion(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "My Article", "url": "http://example.com"}),
    )
    await client.post("/jobs", data={"bookmark_ids": ["abc123"]})
    jobs, _ = await models.list_jobs()
    await models.update_job(jobs[0]["id"], status=models.JobStatus.completed, audio_path="a.mp3")

    resp = await client.post("/jobs", data={"bookmark_ids": ["abc123"]})
    assert resp.status_code == 200
    assert b"Queued 1 job" in resp.content

    _, total = await models.list_jobs()
    assert total == 2


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
    # Empty body so htmx's outerHTML swap removes the card instead of
    # inserting a stray JSON blob into the page.
    assert resp.content == b""
    assert await models.get_job(job["id"]) is None


async def test_delete_job_not_found(client):
    resp = await client.delete("/jobs/does-not-exist")
    assert resp.status_code == 404


async def test_bulk_delete_jobs(client):
    job1 = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test 1",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    job2 = await models.create_job(
        bookmark_id="bm2",
        bookmark_title="Test 2",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    job3 = await models.create_job(
        bookmark_id="bm3",
        bookmark_title="Test 3",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )

    resp = await client.post("/jobs/bulk-delete", data={"job_ids": [job1["id"], job2["id"]]})
    assert resp.status_code == 200
    assert b"Deleted 2 job(s)" in resp.content

    assert await models.get_job(job1["id"]) is None
    assert await models.get_job(job2["id"]) is None
    assert await models.get_job(job3["id"]) is not None


async def test_bulk_delete_jobs_redirects_instead_of_rendering(client):
    resp = await client.post(
        "/jobs/bulk-delete", data={"job_ids": ["nope"]}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/jobs?flash=")


async def test_bulk_delete_jobs_ignores_unknown_ids(client):
    resp = await client.post("/jobs/bulk-delete", data={"job_ids": ["nope"]})
    assert resp.status_code == 200
    assert b"Deleted 0 job(s)" in resp.content


async def test_bulk_delete_jobs_no_ids(client):
    resp = await client.post("/jobs/bulk-delete", data={})
    assert resp.status_code == 200
    assert b"Deleted 0 job(s)" in resp.content


async def test_api_job_ids(client):
    job1 = await models.create_job(
        bookmark_id="bm1",
        bookmark_title="Test 1",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    job2 = await models.create_job(
        bookmark_id="bm2",
        bookmark_title="Test 2",
        bookmark_url="http://example.com",
        tts_engine="edge-tts",
        voice="en-US-AriaNeural",
    )
    resp = await client.get("/api/jobs/ids")
    assert resp.status_code == 200
    assert set(resp.json()) == {job1["id"], job2["id"]}


async def test_audio_not_found(client):
    resp = await client.get("/audio/nonexistent.mp3")
    assert resp.status_code == 404


async def test_audio_path_traversal_blocked(client):
    # Path traversal attempt should be safely handled (file won't exist anyway)
    resp = await client.get("/audio/../../etc/passwd")
    assert resp.status_code == 404
