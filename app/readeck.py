import asyncio
import logging
import weakref
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app import config

logger = logging.getLogger(__name__)

READECK_BASE_URL = config.READECK_BASE_URL
READECK_API_TOKEN = config.READECK_API_TOKEN

_headers = {"Authorization": f"Bearer {READECK_API_TOKEN}", "Accept": "application/json"}

# One pooled client per event loop, instead of a fresh connection (and TLS
# handshake) for every call. Keyed by loop so tests, which run each case on
# their own loop, never reuse a client bound to a closed one.
_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class ReadeckError(RuntimeError):
    """Raised when the Readeck API cannot be reached or refuses the request."""


def _client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            headers=_headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        _clients[loop] = client
    return client


async def close_client() -> None:
    """Close the pooled client for the running loop (called on shutdown)."""
    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return "Readeck rejected the API token (check READECK_API_TOKEN)."
        return f"Readeck returned HTTP {status}."
    if isinstance(exc, httpx.TimeoutException):
        return "Readeck did not respond in time."
    if isinstance(exc, httpx.RequestError):
        return f"Could not reach Readeck at {READECK_BASE_URL or '(READECK_BASE_URL unset)'}."
    return str(exc)


# Readeck refuses a page size above this.
MAX_PAGE_SIZE = 100


async def list_bookmarks(
    limit: int = 50,
    offset: int = 0,
    search: str = "",
    range_start: str = "",
    range_end: str = "",
    types: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One page of bookmarks, newest first.

    `range_start` / `range_end` filter on the date the bookmark was added to
    Readeck (its `created` field), inclusive; Readeck reads an empty end as
    "now" and an empty start as the beginning of time.
    """
    params: list[tuple[str, Any]] = [
        ("limit", limit),
        ("offset", offset),
        ("is_loaded", "true"),
        ("sort", "-created"),
    ]
    if search:
        params.append(("search", search))
    if range_start:
        params.append(("range_start", range_start))
    if range_end:
        params.append(("range_end", range_end))
    params.extend(("type", t) for t in types)
    try:
        resp = await _client().get(f"{READECK_BASE_URL}/api/bookmarks", params=params)
        resp.raise_for_status()
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc
    return {
        "items": resp.json(),
        "total": int(resp.headers.get("Total-Count", 0)),
        "total_pages": int(resp.headers.get("Total-Pages", 1)),
        "current_page": int(resp.headers.get("Current-Page", 1)),
    }


async def list_all_bookmarks(**filters: Any) -> list[dict[str, Any]]:
    """Every bookmark matching `filters` (see list_bookmarks), newest first.

    Readeck caps a page at 100, so this reads the first page to learn the
    total and then fetches the rest a few at a time.
    """
    first = await list_bookmarks(limit=MAX_PAGE_SIZE, offset=0, **filters)
    items = list(first["items"])
    offsets = range(MAX_PAGE_SIZE, first["total"], MAX_PAGE_SIZE)
    if not offsets:
        return items

    limiter = asyncio.Semaphore(4)

    async def fetch(offset: int) -> list[dict[str, Any]]:
        async with limiter:
            page = await list_bookmarks(limit=MAX_PAGE_SIZE, offset=offset, **filters)
            return page["items"]

    for page_items in await asyncio.gather(*(fetch(o) for o in offsets)):
        items.extend(page_items)
    # A bookmark added between page reads shifts every later page by one, so
    # the same item can turn up twice.
    return list({bm["id"]: bm for bm in items}.values())


async def get_bookmark(bookmark_id: str) -> dict[str, Any]:
    try:
        resp = await _client().get(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}")
        resp.raise_for_status()
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc
    return resp.json()


async def get_bookmarks(bookmark_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch several bookmarks concurrently; ids that fail are simply omitted."""
    if not bookmark_ids:
        return {}
    results = await asyncio.gather(
        *(get_bookmark(bid) for bid in bookmark_ids), return_exceptions=True
    )
    fetched: dict[str, dict[str, Any]] = {}
    for bid, result in zip(bookmark_ids, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Could not fetch bookmark %s: %s", bid, result)
        else:
            fetched[bid] = result
    return fetched


async def get_article_text(bookmark_id: str) -> str:
    """Fetch the article as Markdown (plain text-friendly, no HTML stripping needed)."""
    client = _client()
    try:
        resp = await client.get(
            f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}/article.md",
            headers={"Accept": "text/markdown"},
        )
        if resp.status_code == 200 and resp.text.strip():
            return resp.text
        logger.debug(
            "Markdown endpoint returned %s for bookmark %s; falling back to HTML",
            resp.status_code,
            bookmark_id,
        )

        # Fallback: fetch HTML and strip tags
        resp = await client.get(
            f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}/article",
            headers={"Accept": "text/html"},
        )
        resp.raise_for_status()
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc

    soup = BeautifulSoup(resp.text, "html.parser")
    return soup.get_text(separator="\n", strip=True)
