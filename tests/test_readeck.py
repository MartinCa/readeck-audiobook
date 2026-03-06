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
async def test_get_article_epub():
    epub_data = b"PK\x03\x04fake epub bytes"
    respx.get("http://readeck.test/api/bookmarks/abc123/article.epub").mock(
        return_value=httpx.Response(200, content=epub_data)
    )
    result = await readeck.get_article_epub("abc123")
    assert result == epub_data
