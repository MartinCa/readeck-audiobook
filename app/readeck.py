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


class BookmarkGone(ReadeckError):
    """Raised when Readeck answers 404: the bookmark has been deleted there."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 404:
        raise BookmarkGone("The bookmark no longer exists in Readeck.")
    resp.raise_for_status()


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
# Concurrent page requests when reading many pages at once.
_PARALLEL = 4


async def list_bookmarks(
    limit: int = MAX_PAGE_SIZE, offset: int = 0, ids: tuple[str, ...] | list[str] = ()
) -> dict[str, Any]:
    """One page of bookmarks, newest first, optionally only those in `ids`."""
    params: list[tuple[str, Any]] = [("limit", limit), ("offset", offset), ("sort", "-created")]
    params.extend(("id", bid) for bid in ids)
    try:
        resp = await _client().get(f"{READECK_BASE_URL}/api/bookmarks", params=params)
        resp.raise_for_status()
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc
    return {"items": resp.json(), "total": int(resp.headers.get("Total-Count", 0))}


async def list_all_bookmarks() -> list[dict[str, Any]]:
    """Every bookmark, newest first.

    Readeck caps a page at 100, so this reads the first page to learn the
    total and then fetches the rest a few at a time.
    """
    first = await list_bookmarks(offset=0)
    items = list(first["items"])
    offsets = range(MAX_PAGE_SIZE, first["total"], MAX_PAGE_SIZE)
    limiter = asyncio.Semaphore(_PARALLEL)

    async def fetch(offset: int) -> list[dict[str, Any]]:
        async with limiter:
            return (await list_bookmarks(offset=offset))["items"]

    for page_items in await asyncio.gather(*(fetch(o) for o in offsets)):
        items.extend(page_items)
    # A bookmark added between page reads shifts every later page by one, so
    # the same item can turn up twice.
    return list({bm["id"]: bm for bm in items}.values())


async def fetch_bookmarks(bookmark_ids: list[str]) -> list[dict[str, Any]]:
    """The bookmarks with these ids, 100 to a request; ids Readeck no longer has are absent."""
    chunks = [
        bookmark_ids[i : i + MAX_PAGE_SIZE] for i in range(0, len(bookmark_ids), MAX_PAGE_SIZE)
    ]
    limiter = asyncio.Semaphore(_PARALLEL)

    async def fetch(chunk: list[str]) -> list[dict[str, Any]]:
        async with limiter:
            return (await list_bookmarks(limit=len(chunk), ids=chunk))["items"]

    items: list[dict[str, Any]] = []
    for page_items in await asyncio.gather(*(fetch(c) for c in chunks)):
        items.extend(page_items)
    return items


async def sync_list() -> dict[str, str]:
    """Every bookmark id with its last-updated time, from one request.

    Uses Readeck's sync endpoint; a Readeck too old to have it answers 404,
    and then the full bookmark list stands in.
    """
    try:
        resp = await _client().get(f"{READECK_BASE_URL}/api/bookmarks/sync")
        if resp.status_code == 404:
            logger.info("Readeck has no sync endpoint; reading the full bookmark list instead")
            return {bm["id"]: bm.get("updated") or "" for bm in await list_all_bookmarks()}
        resp.raise_for_status()
    except ReadeckError:
        raise
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc
    return {item["id"]: item["time"] for item in resp.json() if item.get("type") != "delete"}


async def get_bookmark(bookmark_id: str) -> dict[str, Any]:
    try:
        resp = await _client().get(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}")
        _raise_for_status(resp)
    except ReadeckError:
        raise
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc
    return resp.json()


async def archive_bookmark(bookmark_id: str) -> None:
    """Archive a bookmark and mark it read (progress 100), as Readeck's own button pair does."""
    try:
        resp = await _client().patch(
            f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}",
            json={"is_archived": True, "read_progress": 100},
        )
        _raise_for_status(resp)
    except ReadeckError:
        raise
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc


async def delete_bookmark(bookmark_id: str) -> None:
    """Delete a bookmark from Readeck; BookmarkGone if it was already deleted."""
    try:
        resp = await _client().delete(f"{READECK_BASE_URL}/api/bookmarks/{bookmark_id}")
        _raise_for_status(resp)
    except ReadeckError:
        raise
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc


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
        if resp.status_code != 404:
            resp.raise_for_status()
    except Exception as exc:
        raise ReadeckError(_describe(exc)) from exc

    if resp.status_code == 404:
        # Either the bookmark is gone or it simply has no article (a picture,
        # say); only the bookmark itself can tell which. This raises
        # BookmarkGone in the first case.
        await get_bookmark(bookmark_id)
        raise ReadeckError("Readeck has no article text for this bookmark.")

    soup = BeautifulSoup(resp.text, "html.parser")
    return soup.get_text(separator="\n", strip=True)
