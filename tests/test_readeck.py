"""Tests for the Readeck API client using respx to mock httpx."""

import httpx
import pytest
import respx

from app import readeck


@respx.mock
async def test_list_bookmarks():
    respx.get("http://readeck.test/api/bookmarks").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "abc", "title": "Test Article"}],
            headers={"Total-Count": "1", "Total-Pages": "1", "Current-Page": "1"},
        )
    )
    result = await readeck.list_bookmarks(limit=10, offset=0)
    assert result["total"] == 1
    assert result["items"][0]["id"] == "abc"


@respx.mock
async def test_list_bookmarks_by_id():
    route = respx.get("http://readeck.test/api/bookmarks").mock(
        return_value=httpx.Response(200, json=[], headers={"Total-Count": "0"})
    )
    await readeck.list_bookmarks(limit=2, ids=["a", "b"])
    params = route.calls[0].request.url.params
    assert params.get_list("id") == ["a", "b"]
    assert params["limit"] == "2"


@respx.mock
async def test_get_bookmark():
    respx.get("http://readeck.test/api/bookmarks/abc123").mock(
        return_value=httpx.Response(200, json={"id": "abc123", "title": "Hello", "lang": "en"})
    )
    bm = await readeck.get_bookmark("abc123")
    assert bm["id"] == "abc123"
    assert bm["lang"] == "en"


@respx.mock
async def test_get_article_text_markdown_path():
    respx.get("http://readeck.test/api/bookmarks/abc123/article.md").mock(
        return_value=httpx.Response(200, text="# Hello\n\nWorld")
    )
    text = await readeck.get_article_text("abc123")
    assert "Hello" in text
    assert "World" in text


@respx.mock
async def test_get_article_text_html_fallback():
    # Markdown endpoint returns empty → fall back to HTML
    respx.get("http://readeck.test/api/bookmarks/abc123/article.md").mock(
        return_value=httpx.Response(200, text="   ")
    )
    respx.get("http://readeck.test/api/bookmarks/abc123/article").mock(
        return_value=httpx.Response(200, text="<html><body><p>Fallback text</p></body></html>")
    )
    text = await readeck.get_article_text("abc123")
    assert "Fallback text" in text


@respx.mock
async def test_get_article_text_markdown_404_falls_back():
    respx.get("http://readeck.test/api/bookmarks/abc123/article.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://readeck.test/api/bookmarks/abc123/article").mock(
        return_value=httpx.Response(200, text="<p>HTML content</p>")
    )
    text = await readeck.get_article_text("abc123")
    assert "HTML content" in text


@respx.mock
async def test_get_bookmarks_fetches_concurrently():
    for i in range(3):
        respx.get(f"http://readeck.test/api/bookmarks/id{i}").mock(
            return_value=httpx.Response(200, json={"id": f"id{i}", "title": f"T{i}"})
        )
    result = await readeck.get_bookmarks(["id0", "id1", "id2"])
    assert set(result) == {"id0", "id1", "id2"}
    assert result["id1"]["title"] == "T1"


@respx.mock
async def test_get_bookmarks_omits_ids_that_fail():
    """One bad bookmark must not sink the whole batch."""
    respx.get("http://readeck.test/api/bookmarks/good").mock(
        return_value=httpx.Response(200, json={"id": "good", "title": "Fine"})
    )
    respx.get("http://readeck.test/api/bookmarks/bad").mock(return_value=httpx.Response(500))
    result = await readeck.get_bookmarks(["good", "bad"])
    assert set(result) == {"good"}


async def test_get_bookmarks_with_no_ids():
    assert await readeck.get_bookmarks([]) == {}


@respx.mock
async def test_reuses_one_pooled_connection():
    """A fresh AsyncClient per call meant a new TCP + TLS handshake each time."""
    respx.get("http://readeck.test/api/bookmarks/abc").mock(
        return_value=httpx.Response(200, json={"id": "abc"})
    )
    await readeck.get_bookmark("abc")
    first = readeck._client()
    await readeck.get_bookmark("abc")
    assert readeck._client() is first


@respx.mock
async def test_unauthorised_raises_a_readeck_error_naming_the_token():
    respx.get("http://readeck.test/api/bookmarks").mock(return_value=httpx.Response(401))
    with pytest.raises(readeck.ReadeckError, match="READECK_API_TOKEN"):
        await readeck.list_bookmarks()


@respx.mock
async def test_connection_failure_raises_a_readeck_error():
    respx.get("http://readeck.test/api/bookmarks").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(readeck.ReadeckError, match="Could not reach Readeck"):
        await readeck.list_bookmarks()


@respx.mock
async def test_article_fetch_failure_raises_a_readeck_error():
    respx.get("http://readeck.test/api/bookmarks/abc/article.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://readeck.test/api/bookmarks/abc/article").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(readeck.ReadeckError, match="HTTP 503"):
        await readeck.get_article_text("abc")


async def test_close_client_is_safe_when_none_was_created():
    await readeck.close_client()


@respx.mock
async def test_fetch_bookmarks_asks_for_100_ids_at_a_time():
    def page(request):
        ids = request.url.params.get_list("id")
        assert len(ids) <= 100
        # Readeck leaves out ids it no longer has.
        return httpx.Response(200, json=[{"id": i} for i in ids if i != "gone"])

    route = respx.get("http://readeck.test/api/bookmarks").mock(side_effect=page)
    ids = [f"b{i}" for i in range(250)] + ["gone"]
    items = await readeck.fetch_bookmarks(ids)
    assert sorted(b["id"] for b in items) == sorted(ids[:-1])
    assert route.call_count == 3


async def test_fetch_bookmarks_with_no_ids():
    assert await readeck.fetch_bookmarks([]) == []


@respx.mock
async def test_sync_list_maps_ids_to_update_times():
    respx.get("http://readeck.test/api/bookmarks/sync").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "a", "time": "2026-01-02T10:00:00.5Z", "type": "update"},
                {"id": "b", "time": "2026-01-01T10:00:00Z", "type": "update"},
                {"id": "c", "time": "2026-01-03T00:00:00Z", "type": "delete"},
            ],
        )
    )
    assert await readeck.sync_list() == {
        "a": "2026-01-02T10:00:00.5Z",
        "b": "2026-01-01T10:00:00Z",
    }


@respx.mock
async def test_sync_list_falls_back_to_the_full_list_on_an_older_readeck():
    respx.get("http://readeck.test/api/bookmarks/sync").mock(return_value=httpx.Response(404))
    respx.get("http://readeck.test/api/bookmarks").mock(
        return_value=httpx.Response(
            200, json=[{"id": "a", "updated": "2026-01-01T00:00:00Z"}], headers={"Total-Count": "1"}
        )
    )
    assert await readeck.sync_list() == {"a": "2026-01-01T00:00:00Z"}


@respx.mock
async def test_sync_list_failure_raises_a_readeck_error():
    respx.get("http://readeck.test/api/bookmarks/sync").mock(return_value=httpx.Response(500))
    with pytest.raises(readeck.ReadeckError, match="HTTP 500"):
        await readeck.sync_list()


@respx.mock
async def test_a_deleted_bookmark_raises_bookmark_gone():
    respx.get("http://readeck.test/api/bookmarks/abc").mock(return_value=httpx.Response(404))
    with pytest.raises(readeck.BookmarkGone):
        await readeck.get_bookmark("abc")


@respx.mock
async def test_article_text_of_a_deleted_bookmark_raises_bookmark_gone():
    respx.get("http://readeck.test/api/bookmarks/abc/article.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://readeck.test/api/bookmarks/abc/article").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://readeck.test/api/bookmarks/abc").mock(return_value=httpx.Response(404))
    with pytest.raises(readeck.BookmarkGone):
        await readeck.get_article_text("abc")


@respx.mock
async def test_a_bookmark_with_no_article_is_not_mistaken_for_a_deleted_one():
    respx.get("http://readeck.test/api/bookmarks/abc/article.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://readeck.test/api/bookmarks/abc/article").mock(
        return_value=httpx.Response(404)
    )
    respx.get("http://readeck.test/api/bookmarks/abc").mock(
        return_value=httpx.Response(200, json={"id": "abc", "type": "photo"})
    )
    with pytest.raises(readeck.ReadeckError, match="no article text") as raised:
        await readeck.get_article_text("abc")
    assert not isinstance(raised.value, readeck.BookmarkGone)


@respx.mock
async def test_list_all_bookmarks_reads_every_page():
    def page(request):
        offset = int(request.url.params["offset"])
        assert request.url.params["limit"] == "100"
        items = [{"id": f"b{i}"} for i in range(offset, min(offset + 100, 250))]
        return httpx.Response(200, json=items, headers={"Total-Count": "250"})

    route = respx.get("http://readeck.test/api/bookmarks").mock(side_effect=page)
    items = await readeck.list_all_bookmarks()
    assert [b["id"] for b in items] == [f"b{i}" for i in range(250)]
    assert route.call_count == 3


@respx.mock
async def test_list_all_bookmarks_drops_a_duplicate_from_a_shifted_page():
    pages = {0: [{"id": "a"}, {"id": "b"}], 2: [{"id": "b"}, {"id": "c"}]}

    def page(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=pages[offset // 50], headers={"Total-Count": "150"})

    respx.get("http://readeck.test/api/bookmarks").mock(side_effect=page)
    items = await readeck.list_all_bookmarks()
    assert [b["id"] for b in items] == ["a", "b", "c"]
