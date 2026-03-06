import logging
import os
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

READECK_BASE_URL = os.environ.get("READECK_BASE_URL", "").rstrip("/")
READECK_API_TOKEN = os.environ.get("READECK_API_TOKEN", "")

_headers = {"Authorization": f"Bearer {READECK_API_TOKEN}", "Accept": "application/json"}


async def list_bookmarks(limit: int = 50, offset: int = 0, search: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset, "is_loaded": True}
    if search:
        params["search"] = search
    async with httpx.AsyncClient(headers=_headers, timeout=30) as client:
        resp = await client.get(f"{READECK_BASE_URL}/api/bookmarks", params=params)
        resp.raise_for_status()
        return {
            "items": resp.json(),
            "total": int(resp.headers.get("Total-Count", 0)),
            "total_pages": int(resp.headers.get("Total-Pages", 1)),
            "current_page": int(resp.headers.get("Current-Page", 1)),
        }


async def get_bookmark(bookmark_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(headers=_headers, timeout=30) as client:
        resp = await client.get(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}")
        resp.raise_for_status()
        return resp.json()


async def get_article_text(bookmark_id: str) -> str:
    """Fetch the article as Markdown (plain text-friendly, no HTML stripping needed)."""
    md_headers = {**_headers, "Accept": "text/markdown"}
    async with httpx.AsyncClient(headers=md_headers, timeout=60) as client:
        resp = await client.get(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}/article.md")
        if resp.status_code == 200 and resp.text.strip():
            return resp.text
        logger.debug(
            "Markdown endpoint returned %s for bookmark %s; falling back to HTML",
            resp.status_code,
            bookmark_id,
        )

    # Fallback: fetch HTML and strip tags
    html_headers = {**_headers, "Accept": "text/html"}
    async with httpx.AsyncClient(headers=html_headers, timeout=60) as client:
        resp = await client.get(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}/article")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup.get_text(separator="\n", strip=True)


async def get_article_epub(bookmark_id: str) -> bytes:
    epub_headers = {**_headers, "Accept": "application/epub+zip"}
    async with httpx.AsyncClient(headers=epub_headers, timeout=120) as client:
        resp = await client.get(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}/article.epub")
        resp.raise_for_status()
        return resp.content
