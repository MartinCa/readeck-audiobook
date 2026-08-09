"""Integration tests for FastAPI routes."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import config, models, readeck


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_api_jobs_empty(client):
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


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
    # Empty body: the page removes the card itself rather than swapping in a
    # JSON blob.
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


# ── Job creation details ───────────────────────────────────────────────────────


async def test_post_jobs_stores_language_and_resolved_engine(client, monkeypatch):
    """The engine and voice are resolved once, at queue time, and stored, so the
    Jobs page reports what actually ran rather than the current TTS_ENGINE."""
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "Ein Artikel", "url": "http://example.com", "lang": "de"}),
    )
    await client.post("/jobs", data={"bookmark_ids": ["abc123"]})

    jobs, _ = await models.list_jobs()
    assert jobs[0]["lang"] == "de"
    assert jobs[0]["tts_engine"] == "edge-tts"
    assert jobs[0]["voice"] == "de-DE-KatjaNeural"


async def test_post_jobs_fetches_bookmarks_concurrently(client, monkeypatch):
    """Queueing N bookmarks used to mean N sequential round trips to Readeck."""
    in_flight = {"now": 0, "peak": 0}

    async def slow_get_bookmark(bookmark_id):
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        return {"title": f"Article {bookmark_id}", "url": "http://example.com", "lang": "en"}

    monkeypatch.setattr("app.readeck.get_bookmark", slow_get_bookmark)
    resp = await client.post("/jobs", data={"bookmark_ids": [f"id{i}" for i in range(5)]})

    assert resp.status_code == 200
    assert in_flight["peak"] > 1
    _, total = await models.list_jobs()
    assert total == 5


async def test_post_jobs_survives_a_failing_bookmark_lookup(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(side_effect=readeck.ReadeckError("Readeck returned HTTP 500.")),
    )
    resp = await client.post("/jobs", data={"bookmark_ids": ["abc123"]})

    assert resp.status_code == 200
    jobs, total = await models.list_jobs()
    assert total == 1
    # Falls back to the id so the job is still queued and identifiable.
    assert jobs[0]["bookmark_title"] == "abc123"


async def test_post_jobs_deduplicates_repeated_ids(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "A", "url": "http://example.com"}),
    )
    await client.post("/jobs", data={"bookmark_ids": ["same", "same", "same"]})
    _, total = await models.list_jobs()
    assert total == 1


# ── Batched status polling ─────────────────────────────────────────────────────


async def test_job_statuses_returns_many_at_once(client):
    a = await models.create_job("bm1", "A", "http://x", "edge-tts", "v")
    b = await models.create_job("bm2", "B", "http://x", "edge-tts", "v")
    await models.update_job(b["id"], status=models.JobStatus.completed, audio_path="b-123.mp3")

    resp = await client.get(f"/api/jobs/statuses?ids={a['id']},{b['id']},ghost")
    assert resp.status_code == 200
    body = resp.json()

    assert body["jobs"][a["id"]]["status"] == "pending"
    assert body["jobs"][b["id"]]["audio_path"] == "b-123.mp3"
    assert body["missing"] == ["ghost"]


async def test_job_statuses_with_no_ids(client):
    resp = await client.get("/api/jobs/statuses")
    assert resp.status_code == 200
    assert resp.json() == {"jobs": {}, "missing": []}


# ── Pagination and input validation ────────────────────────────────────────────


async def test_jobs_page_rejects_a_non_positive_page(client):
    assert (await client.get("/jobs?page=0")).status_code == 422
    assert (await client.get("/jobs?page=-5")).status_code == 422


async def test_search_term_is_url_encoded_in_pagination_links(client, monkeypatch):
    """Unencoded, a term containing "&" splits into a second query parameter
    and page 2 silently searches for something else."""
    monkeypatch.setattr(
        "app.readeck.list_bookmarks",
        AsyncMock(
            return_value={
                "items": [{"id": "a", "title": "T", "url": "http://x"}],
                "total": 100,
                "total_pages": 5,
                "current_page": 1,
            }
        ),
    )
    resp = await client.get("/", params={"search": "rust & go"})
    assert "search=rust%20%26%20go" in resp.text or "search=rust+%26+go" in resp.text
    assert "search=rust &amp; go" not in resp.text


async def test_index_surfaces_a_readeck_outage(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.list_bookmarks",
        AsyncMock(side_effect=readeck.ReadeckError("Could not reach Readeck at http://x.")),
    )
    resp = await client.get("/")
    assert resp.status_code == 200
    # Not the misleading "check your configuration" empty state.
    assert "Could not load bookmarks" in resp.text
    assert "Could not reach Readeck" in resp.text


# ── Output escaping ────────────────────────────────────────────────────────────


async def test_javascript_bookmark_url_is_not_linked(client):
    await models.create_job("bm1", "Sketchy", "javascript:alert(document.domain)", "edge-tts", "v")
    resp = await client.get("/jobs")
    assert "javascript:alert" not in resp.text


async def test_http_bookmark_url_is_linked(client):
    await models.create_job("bm1", "Fine", "https://example.com/post", "edge-tts", "v")
    resp = await client.get("/jobs")
    assert 'href="https://example.com/post"' in resp.text


async def test_flash_is_escaped(client):
    resp = await client.get("/jobs", params={"flash": "<img src=x onerror=alert(1)>"})
    assert "<img src=x" not in resp.text
    assert "&lt;img" in resp.text


# ── Audio download ─────────────────────────────────────────────────────────────


async def test_audio_download_serves_a_real_file(client, audio_dir):
    (audio_dir / "my-article-abc123.mp3").write_bytes(b"ID3audio")
    resp = await client.get("/audio/my-article-abc123.mp3")
    assert resp.status_code == 200
    assert resp.content == b"ID3audio"


async def test_audio_download_rejects_non_mp3(client, audio_dir):
    (audio_dir / "secrets.env").write_bytes(b"token")
    assert (await client.get("/audio/secrets.env")).status_code == 404


async def test_audio_download_rejects_a_symlink_escaping_the_directory(client, audio_dir, tmp_path):
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"private")
    (audio_dir / "link.mp3").symlink_to(outside)
    assert (await client.get("/audio/link.mp3")).status_code == 404


async def test_delete_job_removes_its_audio_file(client, audio_dir):
    job = await models.create_job("bm1", "T", "http://x", "edge-tts", "v")
    audio = audio_dir / "t-abc.mp3"
    audio.write_bytes(b"x")
    await models.update_job(job["id"], status=models.JobStatus.completed, audio_path=audio.name)

    resp = await client.delete(f"/jobs/{job['id']}")
    assert resp.status_code == 200
    assert not audio.exists()


async def test_bulk_delete_returns_json_when_asked(client):
    job = await models.create_job("bm1", "T", "http://x", "edge-tts", "v")
    resp = await client.post(
        "/jobs/bulk-delete",
        data={"job_ids": [job["id"]]},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}


# ── Cross-origin protection ────────────────────────────────────────────────────


async def test_cross_origin_post_is_rejected(client):
    """Without a session there is nothing else stopping a drive-by form post
    on another site from deleting the visitor's whole job list."""
    resp = await client.post(
        "/jobs/bulk-delete",
        data={"job_ids": ["x"]},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


async def test_same_origin_post_is_allowed(client):
    resp = await client.post(
        "/jobs/bulk-delete",
        data={"job_ids": ["x"]},
        headers={"Origin": "http://test", "Accept": "application/json"},
    )
    assert resp.status_code == 200


async def test_cross_origin_get_is_unaffected(client):
    resp = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200


async def test_trusted_origin_is_allowed(client, monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_ORIGINS", ("https://audiobook.example.com",))
    resp = await client.post(
        "/jobs/bulk-delete",
        data={"job_ids": ["x"]},
        headers={"Origin": "https://audiobook.example.com", "Accept": "application/json"},
    )
    assert resp.status_code == 200


# ── Optional basic auth ────────────────────────────────────────────────────────


@pytest.fixture
def with_auth(monkeypatch):
    monkeypatch.setattr(config, "AUTH_USERNAME", "alice")
    monkeypatch.setattr(config, "AUTH_PASSWORD", "s3cret")


async def test_auth_is_off_by_default(client):
    assert (await client.get("/jobs")).status_code == 200


async def test_auth_challenges_when_configured(client, with_auth):
    resp = await client.get("/jobs")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


async def test_auth_accepts_correct_credentials(client, with_auth):
    resp = await client.get("/jobs", auth=("alice", "s3cret"))
    assert resp.status_code == 200


async def test_auth_rejects_wrong_credentials(client, with_auth):
    assert (await client.get("/jobs", auth=("alice", "wrong"))).status_code == 401


async def test_health_stays_open_for_container_healthchecks(client, with_auth):
    assert (await client.get("/health")).status_code == 200
