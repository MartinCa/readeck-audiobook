import base64
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, jobs, models, readeck, tts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"

BOOKMARKS_PER_PAGE = 30
JOBS_PER_PAGE = 50
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [
        name
        for name, value in (
            ("READECK_BASE_URL", config.READECK_BASE_URL),
            ("READECK_API_TOKEN", config.READECK_API_TOKEN),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    if not config.auth_enabled():
        logger.warning(
            "No AUTH_USERNAME/AUTH_PASSWORD set — the app is unauthenticated. "
            "Only expose it on a trusted network."
        )

    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    await models.init_db()

    requeued, failed = await models.requeue_interrupted_jobs()
    if requeued or failed:
        logger.warning(
            "Recovered %d job(s) interrupted by a previous run (%d requeued, %d given up on)",
            requeued + failed,
            requeued,
            failed,
        )
    orphans = await jobs.cleanup_orphaned_audio()
    if orphans:
        logger.info("Removed %d orphaned audio file(s)", orphans)

    jobs.start_worker()
    try:
        yield
    finally:
        await jobs.stop_worker()
        await readeck.close_client()
        # After the workers are done, so nothing is mid-synthesis. Tearing an
        # onnxruntime CUDA session down at interpreter exit instead is a known
        # way to hang or crash on the way out.
        tts.release_kokoro()


app = FastAPI(title="Readeck Audiobook", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def safe_url(value: str | None) -> str:
    """Return `value` only if it is a plain web link.

    Bookmark URLs come from saved pages, so a `javascript:` or `data:` URL must
    never reach an `href`.
    """
    if not value:
        return ""
    try:
        scheme = urlparse(value).scheme.lower()
    except ValueError:
        return ""
    return value if scheme in ("http", "https") else ""


templates.env.filters["safe_url"] = safe_url


# ── Middleware ─────────────────────────────────────────────────────────────────


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    if config.auth_enabled() and request.url.path not in ("/health",):
        if not _authorized(request):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Readeck Audiobook"'},
            )

    if request.method in UNSAFE_METHODS and not _same_origin(request):
        # The app has no per-user session, so a cross-site form post would
        # otherwise be able to delete a visitor's whole job list.
        return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)

    return await call_next(request)


def _authorized(request: Request) -> bool:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        username, _, password = base64.b64decode(token).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(username, config.AUTH_USERNAME) and hmac.compare_digest(
        password, config.AUTH_PASSWORD
    )


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        # Non-browser clients (curl, scripts) send neither; browsers always
        # attach Origin to cross-site state-changing requests, so this stays
        # an effective CSRF guard without breaking API use.
        return True
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        return False
    if f"{parsed.scheme}://{parsed.netloc}" in config.TRUSTED_ORIGINS:
        return True
    return parsed.netloc == (request.headers.get("host") or request.url.netloc)


# ── Pages ──────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, page: int = Query(1, ge=1), search: str = ""):
    offset = (page - 1) * BOOKMARKS_PER_PAGE
    error = ""
    try:
        data = await readeck.list_bookmarks(limit=BOOKMARKS_PER_PAGE, offset=offset, search=search)
    except readeck.ReadeckError as exc:
        logger.error("Failed to fetch bookmarks: %s", exc)
        error = str(exc)
        data = {"items": [], "total": 0, "total_pages": 1, "current_page": page}
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "bookmarks": data["items"],
            "total": data["total"],
            "total_pages": data["total_pages"],
            "current_page": page,
            "search": search,
            "error": error,
        },
    )


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request, page: int = Query(1, ge=1), flash: str = ""):
    offset = (page - 1) * JOBS_PER_PAGE
    job_list, total = await models.list_jobs(limit=JOBS_PER_PAGE, offset=offset)
    total_pages = max(1, (total + JOBS_PER_PAGE - 1) // JOBS_PER_PAGE)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": job_list,
            "current_page": page,
            "total_pages": total_pages,
            "total": total,
            "flash": flash[:200],
        },
    )


# ── Actions ────────────────────────────────────────────────────────────────────


@app.post("/jobs")
async def create_jobs(bookmark_ids: list[str] = Form(...)):
    # De-duplicate while preserving order; a double-submitted form can repeat ids.
    unique_ids = list(dict.fromkeys(bookmark_ids))
    queued_ids = [bid for bid in unique_ids if not await models.get_active_job_for_bookmark(bid)]
    skipped = len(unique_ids) - len(queued_ids)

    # One concurrent fetch for the whole batch rather than a sequential round
    # trip per bookmark, and the language is stored so the worker need not
    # fetch the bookmark a second time.
    bookmarks = await readeck.get_bookmarks(queued_ids)

    created = 0
    for bid in queued_ids:
        bm = bookmarks.get(bid, {})
        lang = bm.get("lang") or ""
        engine, voice = tts.resolve_engine_and_voice(lang)
        await models.create_job(
            bookmark_id=bid,
            bookmark_title=bm.get("title") or bid,
            bookmark_url=bm.get("url") or "",
            lang=lang,
            tts_engine=engine,
            voice=voice,
        )
        created += 1

    flash = f"Queued {created} job(s)."
    if skipped:
        flash += f" Skipped {skipped} already queued or in progress."
    # Redirect (Post/Redirect/Get) rather than rendering the jobs page
    # directly: if we rendered it here, the browser's current document
    # would be POST-derived, and a later refresh would resubmit that same
    # POST — silently re-queuing the same bookmarks.
    return RedirectResponse(url=f"/jobs?flash={quote(flash)}", status_code=303)


async def _delete_job_and_audio(job_id: str) -> dict | None:
    job = await models.delete_job(job_id)
    if job and job.get("audio_path"):
        audio_file = tts.AUDIO_DIR / Path(job["audio_path"]).name
        audio_file.unlink(missing_ok=True)
    return job


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    job = await _delete_job_and_audio(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Empty body: hx-swap="outerHTML" on the caller removes the job card
    # cleanly instead of swapping in a JSON blob.
    return HTMLResponse(content="")


@app.post("/jobs/bulk-delete")
async def bulk_delete_jobs(request: Request, job_ids: list[str] = Form(default=[])):
    deleted = 0
    for job_id in dict.fromkeys(job_ids):
        if await _delete_job_and_audio(job_id):
            deleted += 1

    # The page deletes over fetch and patches its own DOM; only a plain form
    # post needs the Post/Redirect/Get round trip.
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": deleted})

    flash = quote(f"Deleted {deleted} job(s).")
    return RedirectResponse(url=f"/jobs?flash={flash}", status_code=303)


# ── API endpoints ──────────────────────────────────────────────────────────────


@app.get("/api/jobs/{job_id}/status")
async def job_status(job_id: str):
    job = await models.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/statuses")
async def job_statuses(ids: str = ""):
    """Status for many jobs in one request.

    The Jobs page polls this once per interval instead of giving every card its
    own timer, which previously meant one request per visible job every 4s.
    """
    requested = [i for i in (ids.split(",") if ids else []) if i][:JOBS_PER_PAGE]
    found = await models.get_jobs(requested)
    return {
        "jobs": {
            job["id"]: {
                "status": job["status"],
                "audio_path": job["audio_path"],
                "error_msg": job["error_msg"],
                "tts_engine": job["tts_engine"],
            }
            for job in found
        },
        "missing": sorted(set(requested) - {job["id"] for job in found}),
    }


@app.get("/api/jobs")
async def api_jobs(limit: int = Query(JOBS_PER_PAGE, ge=1, le=500), offset: int = Query(0, ge=0)):
    job_list, total = await models.list_jobs(limit=limit, offset=offset)
    return {"items": job_list, "total": total, "limit": limit, "offset": offset}


@app.get("/api/jobs/ids")
async def api_job_ids():
    return await models.list_job_ids()


# ── File download ──────────────────────────────────────────────────────────────


@app.get("/audio/{filename}")
async def download_audio(filename: str):
    # Resolve inside AUDIO_DIR and verify containment: `Path.name` alone would
    # still allow a symlink planted in the audio directory to escape it.
    safe = Path(filename).name
    audio_dir = tts.AUDIO_DIR.resolve()
    path = (audio_dir / safe).resolve()
    if not safe.endswith(".mp3") or audio_dir not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(path=str(path), media_type="audio/mpeg", filename=safe)


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}
