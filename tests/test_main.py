"""Integration tests for the JSON API and the frontend fallback."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import bookmarks, config, models, readeck


async def _completed(bookmark_id: str, audio_path: str, title: str = "T") -> dict:
    job = await models.create_job(bookmark_id, title, "http://x", "edge-tts", "v")
    await models.update_job(job["id"], status=models.JobStatus.completed, audio_path=audio_path)
    return job


def _bookmark(bid: str, **extra) -> dict:
    return {
        "id": bid,
        "title": f"Article {bid}",
        "url": f"https://example.com/{bid}",
        "site_name": "Example",
        "authors": [],
        "lang": "en",
        "type": "article",
        "created": "2026-05-01T10:00:00Z",
        "published": None,
        **extra,
    }


@pytest.fixture
def readeck_page(monkeypatch):
    """Stub the Readeck listing; returns the mock so tests can inspect calls."""

    def install(items: list[dict]):
        mock = AsyncMock(
            return_value={"items": items, "total": len(items), "total_pages": 1, "current_page": 1}
        )
        monkeypatch.setattr("app.readeck.list_bookmarks", mock)
        monkeypatch.setattr("app.readeck.list_all_bookmarks", AsyncMock(return_value=items))
        bookmarks.clear_cache()
        return mock

    return install


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Bookmarks ──────────────────────────────────────────────────────────────────


async def test_bookmarks_are_camel_case_with_both_dates(client, readeck_page):
    readeck_page([_bookmark("a", published="2025-12-24T00:00:00Z", reading_time=4)])
    resp = await client.get("/api/bookmarks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["siteName"] == "Example"
    assert item["readingTime"] == 4
    assert item["added"].startswith("2026-05-01T10:00:00")
    assert item["published"].startswith("2025-12-24")
    assert item["audio"] is None
    assert item["job"] is None
    assert item["autoExcluded"] is False


async def test_bookmark_shows_its_audio(client, readeck_page):
    readeck_page([_bookmark("a")])
    job = await _completed("a", "article-a-123.mp3")
    item = (await client.get("/api/bookmarks")).json()["items"][0]
    assert item["audio"]["jobId"] == job["id"]
    assert item["audio"]["url"] == "/api/audio/article-a-123.mp3"


async def test_bookmark_shows_a_running_job_but_not_a_finished_one(client, readeck_page):
    readeck_page([_bookmark("a"), _bookmark("b")])
    await models.create_job("a", "A", "http://x", "edge-tts", "v")
    await _completed("b", "b.mp3")
    items = {i["id"]: i for i in (await client.get("/api/bookmarks")).json()["items"]}
    assert items["a"]["job"]["status"] == "pending"
    assert items["b"]["job"] is None


async def test_javascript_bookmark_url_is_not_passed_through(client, readeck_page):
    readeck_page([_bookmark("a", url="javascript:alert(1)")])
    item = (await client.get("/api/bookmarks")).json()["items"][0]
    assert item["url"] == ""


async def test_search_and_added_range_go_to_readeck(client, readeck_page):
    mock = readeck_page([])
    await client.get(
        "/api/bookmarks",
        params={"search": "go", "addedFrom": "2026-01-01", "addedTo": "2026-01-31", "page": 2},
    )
    kwargs = mock.call_args.kwargs
    assert kwargs["search"] == "go"
    assert kwargs["range_start"] == "2026-01-01 00:00:00"
    assert kwargs["range_end"] == "2026-01-31 23:59:59"
    assert kwargs["offset"] == 30


async def test_filter_by_audio(client, readeck_page):
    readeck_page([_bookmark("a"), _bookmark("b")])
    await _completed("a", "a.mp3")

    with_audio = (await client.get("/api/bookmarks", params={"audio": "with"})).json()
    without = (await client.get("/api/bookmarks", params={"audio": "without"})).json()
    assert [i["id"] for i in with_audio["items"]] == ["a"]
    assert [i["id"] for i in without["items"]] == ["b"]
    assert without["total"] == 1


async def test_filter_by_auto_generation_exclusion(client, readeck_page):
    readeck_page([_bookmark("a"), _bookmark("b")])
    await models.set_auto_excluded(["b"], True)

    excluded = (await client.get("/api/bookmarks", params={"autoGeneration": "excluded"})).json()
    included = (await client.get("/api/bookmarks", params={"autoGeneration": "included"})).json()
    assert [i["id"] for i in excluded["items"]] == ["b"]
    assert excluded["items"][0]["autoExcluded"] is True
    assert [i["id"] for i in included["items"]] == ["a"]


async def test_filter_by_published_range_is_inclusive_and_drops_unknown(client, readeck_page):
    readeck_page(
        [
            _bookmark("old", published="2025-01-01T08:00:00Z"),
            _bookmark("edge", published="2025-06-30T22:00:00Z"),
            _bookmark("new", published="2026-01-01T00:00:00Z"),
            _bookmark("unknown"),
        ]
    )
    resp = await client.get(
        "/api/bookmarks", params={"publishedFrom": "2025-02-01", "publishedTo": "2025-06-30"}
    )
    assert [i["id"] for i in resp.json()["items"]] == ["edge"]

    only_start = await client.get("/api/bookmarks", params={"publishedFrom": "2025-02-01"})
    assert [i["id"] for i in only_start.json()["items"]] == ["edge", "new"]


async def test_local_filters_paginate_locally(client, readeck_page):
    readeck_page([_bookmark(f"b{i}") for i in range(35)])
    page2 = (await client.get("/api/bookmarks", params={"audio": "without", "page": 2})).json()
    assert page2["total"] == 35
    assert page2["totalPages"] == 2
    assert [i["id"] for i in page2["items"]] == [f"b{i}" for i in range(30, 35)]


async def test_invalid_filter_is_a_problem_response(client, readeck_page):
    readeck_page([])
    resp = await client.get("/api/bookmarks", params={"audio": "maybe"})
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert "audio" in resp.json()["errors"]


async def test_readeck_outage_is_a_502_problem(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.list_bookmarks",
        AsyncMock(side_effect=readeck.ReadeckError("Readeck did not respond in time.")),
    )
    resp = await client.get("/api/bookmarks")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Readeck did not respond in time."


async def test_delete_bookmark_audio(client, audio_dir):
    await _completed("a", "a.mp3")
    (audio_dir / "a.mp3").write_bytes(b"x")
    failed = await models.create_job("a", "A", "http://x", "edge-tts", "v")
    await models.update_job(failed["id"], status=models.JobStatus.failed)

    resp = await client.post("/api/bookmarks/audio/delete", json={"bookmarkIds": ["a", "zzz"]})
    assert resp.json() == {"count": 1}
    assert not (audio_dir / "a.mp3").exists()
    assert await models.audio_by_bookmark(["a"]) == {}
    # Only the audio goes; the failed job stays on the Jobs page.
    assert await models.get_job(failed["id"])


async def test_set_and_clear_auto_exclusion(client):
    resp = await client.put(
        "/api/bookmarks/auto-generation", json={"bookmarkIds": ["a", "b"], "excluded": True}
    )
    assert resp.json() == {"count": 2}
    assert await models.auto_excluded_ids() == {"a", "b"}

    await client.put(
        "/api/bookmarks/auto-generation", json={"bookmarkIds": ["a"], "excluded": False}
    )
    assert await models.auto_excluded_ids() == {"b"}


# ── Jobs ───────────────────────────────────────────────────────────────────────


async def test_jobs_empty(client):
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "page": 1, "pageSize": 50, "totalPages": 1}


async def test_jobs_list_leaves_out_completed(client):
    pending = await models.create_job("a", "A", "http://x", "edge-tts", "v")
    failed = await models.create_job("b", "B", "http://x", "edge-tts", "v")
    await models.update_job(failed["id"], status=models.JobStatus.failed, error_msg="boom")
    await _completed("c", "c.mp3")

    body = (await client.get("/api/jobs")).json()
    assert {j["id"] for j in body["items"]} == {pending["id"], failed["id"]}
    assert body["total"] == 2
    by_id = {j["id"]: j for j in body["items"]}
    assert by_id[failed["id"]]["errorMsg"] == "boom"
    assert by_id[pending["id"]]["createdAt"].endswith("Z")

    ids = (await client.get("/api/jobs/ids")).json()
    assert set(ids) == {pending["id"], failed["id"]}


async def test_get_job(client):
    job = await models.create_job("bm1", "Test", "http://example.com", "edge-tts", "v")
    resp = await client.get(f"/api/jobs/{job['id']}")
    assert resp.status_code == 200
    assert resp.json()["bookmarkId"] == "bm1"
    assert (await client.get("/api/jobs/nope")).status_code == 404


async def test_post_jobs_queues(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "My Article", "url": "http://example.com"}),
    )
    resp = await client.post("/api/jobs", json={"bookmarkIds": ["abc123", "def456"]})
    assert resp.status_code == 200
    assert resp.json() == {"queued": 2, "skipped": 0}
    jobs, total = await models.list_jobs()
    assert total == 2
    assert jobs[0]["bookmark_title"] == "My Article"


async def test_post_jobs_requires_ids(client):
    resp = await client.post("/api/jobs", json={"bookmarkIds": []})
    assert resp.status_code == 422


async def test_post_jobs_skips_duplicate_active_job(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark", AsyncMock(return_value={"title": "A", "url": "http://x"})
    )
    await client.post("/api/jobs", json={"bookmarkIds": ["abc"]})
    resp = await client.post("/api/jobs", json={"bookmarkIds": ["abc"]})
    assert resp.json() == {"queued": 0, "skipped": 1}


async def test_post_jobs_allows_requeue_after_completion(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark", AsyncMock(return_value={"title": "A", "url": "http://x"})
    )
    await _completed("abc", "a.mp3")
    resp = await client.post("/api/jobs", json={"bookmarkIds": ["abc"]})
    assert resp.json() == {"queued": 1, "skipped": 0}


async def test_post_jobs_replaces_an_earlier_failure(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark", AsyncMock(return_value={"title": "A", "url": "http://x"})
    )
    failed = await models.create_job("abc", "A", "http://x", "edge-tts", "v")
    await models.update_job(failed["id"], status=models.JobStatus.failed, error_msg="boom")
    other = await models.create_job("other", "B", "http://x", "edge-tts", "v")
    await models.update_job(other["id"], status=models.JobStatus.failed, error_msg="boom")

    await client.post("/api/jobs", json={"bookmarkIds": ["abc"]})
    jobs, total = await models.list_jobs()
    assert total == 2
    assert await models.get_job(failed["id"]) is None
    assert await models.get_job(other["id"])


async def test_post_jobs_stores_language_and_resolved_engine(client, monkeypatch):
    """The engine and voice are resolved once, at queue time, and stored, so the
    UI reports what actually ran rather than the current TTS_ENGINE."""
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(return_value={"title": "Ein Artikel", "url": "http://example.com", "lang": "de"}),
    )
    await client.post("/api/jobs", json={"bookmarkIds": ["abc123"]})

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
    resp = await client.post("/api/jobs", json={"bookmarkIds": [f"id{i}" for i in range(5)]})

    assert resp.status_code == 200
    assert in_flight["peak"] > 1
    _, total = await models.list_jobs()
    assert total == 5


async def test_post_jobs_survives_a_failing_bookmark_lookup(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark",
        AsyncMock(side_effect=readeck.ReadeckError("Readeck returned HTTP 500.")),
    )
    resp = await client.post("/api/jobs", json={"bookmarkIds": ["abc123"]})

    assert resp.status_code == 200
    jobs, total = await models.list_jobs()
    assert total == 1
    # Falls back to the id so the job is still queued and identifiable.
    assert jobs[0]["bookmark_title"] == "abc123"


async def test_post_jobs_deduplicates_repeated_ids(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.get_bookmark", AsyncMock(return_value={"title": "A", "url": "http://x"})
    )
    await client.post("/api/jobs", json={"bookmarkIds": ["same", "same", "same"]})
    _, total = await models.list_jobs()
    assert total == 1


async def test_retry_failed_job(client):
    job = await models.create_job("a", "A", "http://x", "edge-tts", "v")
    await models.update_job(job["id"], status=models.JobStatus.failed, error_msg="x", attempts=3)

    resp = await client.post(f"/api/jobs/{job['id']}/retry")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["errorMsg"] is None
    assert (await models.get_job(job["id"]))["attempts"] == 0
    # Only a failed job can be retried.
    assert (await client.post(f"/api/jobs/{job['id']}/retry")).status_code == 404


async def test_delete_job(client):
    job = await models.create_job("bm1", "Test", "http://x", "edge-tts", "v")
    resp = await client.delete(f"/api/jobs/{job['id']}")
    assert resp.status_code == 204
    assert await models.get_job(job["id"]) is None
    assert (await client.delete(f"/api/jobs/{job['id']}")).status_code == 404


async def test_delete_job_leaves_completed_audio_alone(client):
    job = await models.create_job("bm1", "Test", "http://x", "edge-tts", "v")
    await models.update_job(job["id"], status=models.JobStatus.completed, audio_path="a.mp3")
    assert (await client.delete(f"/api/jobs/{job['id']}")).status_code == 404
    assert await models.get_job(job["id"])


async def test_bulk_delete_jobs(client):
    a = await models.create_job("a", "A", "http://x", "edge-tts", "v")
    b = await models.create_job("b", "B", "http://x", "edge-tts", "v")
    resp = await client.post("/api/jobs/bulk-delete", json={"jobIds": [a["id"], b["id"], "nope"]})
    assert resp.json() == {"count": 2}
    assert (await models.list_jobs())[1] == 0


async def test_bulk_delete_jobs_leaves_completed_audio_alone(client):
    done = await models.create_job("a", "A", "http://x", "edge-tts", "v")
    await models.update_job(done["id"], status=models.JobStatus.completed, audio_path="a.mp3")
    resp = await client.post("/api/jobs/bulk-delete", json={"jobIds": [done["id"]]})
    assert resp.json() == {"count": 0}
    assert await models.get_job(done["id"])


# ── Settings and auto generation ───────────────────────────────────────────────


async def test_settings_default_to_disabled(client):
    body = (await client.get("/api/settings")).json()["autoGeneration"]
    assert body == {
        "enabled": False,
        "since": None,
        "cron": "0 * * * *",
        "nextRun": None,
        "lastRun": None,
        "lastQueued": None,
        "lastError": None,
    }


async def test_update_settings(client):
    resp = await client.put(
        "/api/settings",
        json={"autoGeneration": {"enabled": True, "since": "2026-01-01", "cron": "*/15 * * * *"}},
    )
    assert resp.status_code == 200
    body = resp.json()["autoGeneration"]
    assert body["enabled"] is True
    assert body["since"] == "2026-01-01"
    assert body["cron"] == "*/15 * * * *"
    assert body["nextRun"] is not None

    again = (await client.get("/api/settings")).json()["autoGeneration"]
    assert again["since"] == "2026-01-01"


async def test_update_settings_rejects_a_bad_cron(client):
    resp = await client.put(
        "/api/settings", json={"autoGeneration": {"enabled": True, "cron": "every hour"}}
    )
    assert resp.status_code == 422
    assert "autoGeneration.cron" in resp.json()["errors"]


async def test_run_auto_generation_now(client, monkeypatch):
    monkeypatch.setattr(
        "app.readeck.list_all_bookmarks", AsyncMock(return_value=[_bookmark("a"), _bookmark("b")])
    )
    monkeypatch.setattr("app.readeck.get_bookmarks", AsyncMock(return_value={}))
    resp = await client.post("/api/auto-generation/run")
    assert resp.json() == {"queued": 2, "skipped": 0}
    status = (await client.get("/api/settings")).json()["autoGeneration"]
    assert status["lastQueued"] == 2
    assert status["lastRun"] is not None


# ── Audio download ─────────────────────────────────────────────────────────────


async def test_audio_not_found(client):
    assert (await client.get("/api/audio/nonexistent.mp3")).status_code == 404


async def test_audio_path_traversal_blocked(client):
    assert (await client.get("/api/audio/../../etc/passwd")).status_code == 404


async def test_audio_download_serves_a_real_file(client, audio_dir):
    (audio_dir / "my-article-abc123.mp3").write_bytes(b"ID3audio")
    resp = await client.get("/api/audio/my-article-abc123.mp3")
    assert resp.status_code == 200
    assert resp.content == b"ID3audio"


async def test_audio_download_rejects_non_mp3(client, audio_dir):
    (audio_dir / "secrets.env").write_bytes(b"token")
    assert (await client.get("/api/audio/secrets.env")).status_code == 404


async def test_audio_download_rejects_a_symlink_escaping_the_directory(client, audio_dir, tmp_path):
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"private")
    (audio_dir / "link.mp3").symlink_to(outside)
    assert (await client.get("/api/audio/link.mp3")).status_code == 404


# ── Frontend ───────────────────────────────────────────────────────────────────


@pytest.fixture
def built_frontend(tmp_path, monkeypatch):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<div id=root></div>")
    (root / "favicon.svg").write_text("<svg/>")
    monkeypatch.setattr(config, "FRONTEND_DIR", root)
    return root


async def test_client_routes_serve_the_app_shell(client, built_frontend):
    for path in ("/", "/jobs", "/settings", "/index.html"):
        resp = await client.get(path)
        assert resp.status_code == 200
        assert "id=root" in resp.text
        assert resp.headers["cache-control"] == "no-cache"


async def test_frontend_serves_its_own_files(client, built_frontend):
    assert (await client.get("/favicon.svg")).text == "<svg/>"


async def test_frontend_does_not_escape_its_directory(client, built_frontend, tmp_path):
    (tmp_path / "secret.txt").write_text("private")
    resp = await client.get("/..%2Fsecret.txt")
    assert "private" not in resp.text


async def test_unknown_api_path_is_a_404_not_the_app_shell(client, built_frontend):
    resp = await client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"


async def test_missing_build_is_explained(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_DIR", tmp_path / "missing")
    resp = await client.get("/")
    assert resp.status_code == 404
    assert "pnpm build" in resp.json()["title"]


# ── Cross-origin protection ────────────────────────────────────────────────────


async def test_cross_origin_post_is_rejected(client):
    """Without a session there is nothing else stopping a drive-by request
    from another site from deleting the visitor's whole job list."""
    resp = await client.post(
        "/api/jobs/bulk-delete", json={"jobIds": ["x"]}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert resp.headers["content-type"] == "application/problem+json"


async def test_same_origin_post_is_allowed(client):
    resp = await client.post(
        "/api/jobs/bulk-delete", json={"jobIds": ["x"]}, headers={"Origin": "http://test"}
    )
    assert resp.status_code == 200


async def test_cross_origin_get_is_unaffected(client):
    resp = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200


async def test_trusted_origin_is_allowed(client, monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_ORIGINS", ("https://audiobook.example.com",))
    resp = await client.post(
        "/api/jobs/bulk-delete",
        json={"jobIds": ["x"]},
        headers={"Origin": "https://audiobook.example.com"},
    )
    assert resp.status_code == 200


# ── Optional basic auth ────────────────────────────────────────────────────────


@pytest.fixture
def with_auth(monkeypatch):
    monkeypatch.setattr(config, "AUTH_USERNAME", "alice")
    monkeypatch.setattr(config, "AUTH_PASSWORD", "s3cret")


async def test_auth_is_off_by_default(client):
    assert (await client.get("/api/jobs")).status_code == 200


async def test_auth_challenges_when_configured(client, with_auth):
    resp = await client.get("/api/jobs")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


async def test_auth_accepts_correct_credentials(client, with_auth):
    resp = await client.get("/api/jobs", auth=("alice", "s3cret"))
    assert resp.status_code == 200


async def test_auth_rejects_wrong_credentials(client, with_auth):
    assert (await client.get("/api/jobs", auth=("alice", "wrong"))).status_code == 401


async def test_health_stays_open_for_container_healthchecks(client, with_auth):
    assert (await client.get("/health")).status_code == 200
