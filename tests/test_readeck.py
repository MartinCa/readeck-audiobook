"""Tests for the Readeck API client using respx to mock httpx."""

from datetime import date

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
    assert result["total_pages"] == 1
    assert result["current_page"] == 1
    assert result["items"][0]["id"] == "abc"


@respx.mock
async def test_list_bookmarks_with_search():
    route = respx.get("http://readeck.test/api/bookmarks").mock(
        return_value=httpx.Response(
            200,
            json=[],
            headers={"Total-Count": "0", "Total-Pages": "1", "Current-Page": "1"},
        )
    )
    result = await readeck.list_bookmarks(limit=10, offset=0, search="python")
    assert result["total"] == 0
    # Verify the search param was forwarded
    assert "search=python" in str(route.calls[0].request.url)


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


def test_day_bound_survives_readecks_lower_casing():
    # Readeck lower-cases range values before parsing; "2026-01-01t00:00:00z"
    # no longer parses, so the bound must carry no letters at all.
    start = readeck.day_bound(date(2026, 1, 1))
    end = readeck.day_bound(date(2026, 1, 1), end=True)
    assert (start, end) == ("2026-01-01 00:00:00", "2026-01-01 23:59:59")
    assert start == start.lower() and end == end.lower()


@respx.mock
async def test_list_bookmarks_forwards_filters():
    route = respx.get("http://readeck.test/api/bookmarks").mock(
        return_value=httpx.Response(200, json=[], headers={"Total-Count": "0"})
    )
    await readeck.list_bookmarks(
        range_start="2026-01-01 00:00:00", range_end="2026-02-01 23:59:59", types=("article",)
    )
    params = route.calls[0].request.url.params
    assert params["range_start"] == "2026-01-01 00:00:00"
    assert params["range_end"] == "2026-02-01 23:59:59"
    assert params["type"] == "article"
    assert params["sort"] == "-created"
    assert "search" not in params


@respx.mock
async def test_list_all_bookmarks_reads_every_page():
    def page(request):
        offset = int(request.url.params["offset"])
        assert request.url.params["limit"] == "100"
        items = [{"id": f"b{i}"} for i in range(offset, min(offset + 100, 250))]
        return httpx.Response(200, json=items, headers={"Total-Count": "250"})

    route = respx.get("http://readeck.test/api/bookmarks").mock(side_effect=page)
    items = await readeck.list_all_bookmarks(search="x")
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
