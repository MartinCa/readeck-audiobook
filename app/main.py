import base64
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import autogen, bookmarks, config, jobs, models, readeck, tts
from app.schemas import (
    AutoExclusionUpdate,
    AutoGenerationStatus,
    Bookmark,
    BookmarkIds,
    BookmarkPage,
    CountResult,
    Health,
    Job,
    JobIds,
    JobPage,
    QueueResult,
    Settings,
    SettingsUpdate,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    autogen.start_scheduler()
    try:
        yield
    finally:
        await autogen.stop_scheduler()
        await jobs.stop_worker()
        await readeck.close_client()
        # After the workers are done, so nothing is mid-synthesis. Tearing an
        # onnxruntime CUDA session down at interpreter exit instead is a known
        # way to hang or crash on the way out.
        tts.release_kokoro()


app = FastAPI(title="Readeck Audiobook", lifespan=lifespan)


# ── Errors: RFC 9457 problem+json everywhere (DESIGN.md section 7) ─────────────


def problem(
    status: int,
    title: str,
    detail: str | None = None,
    errors: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(
        body, status_code=status, media_type="application/problem+json", headers=headers
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    title = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return problem(exc.status_code, title, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors: dict[str, list[str]] = {}
    for err in exc.errors():
        field = ".".join(str(part) for part in err["loc"] if part not in ("body", "query"))
        errors.setdefault(field or "request", []).append(err["msg"])
    return problem(422, "Validation failed", "Some fields are invalid.", errors)


@app.exception_handler(readeck.ReadeckError)
async def readeck_error(request: Request, exc: readeck.ReadeckError):
    return problem(502, "Could not load bookmarks from Readeck", str(exc))


# ── Middleware ─────────────────────────────────────────────────────────────────


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    if config.auth_enabled() and request.url.path not in ("/health",):
        if not _authorized(request):
            return problem(
                401,
                "Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="Readeck Audiobook"'},
            )

    if request.method in UNSAFE_METHODS and not _same_origin(request):
        # The app has no per-user session, so a cross-site request would
        # otherwise be able to delete a visitor's whole job list.
        return problem(403, "Cross-origin request rejected")

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


# ── Bookmarks ──────────────────────────────────────────────────────────────────


@app.get("/api/bookmarks", response_model=BookmarkPage)
async def list_bookmarks(
    page: int = Query(1, ge=1),
    search: str = "",
    audio: bookmarks.AudioFilter = bookmarks.AudioFilter.any,
    auto_generation: bookmarks.ExclusionFilter = Query(
        bookmarks.ExclusionFilter.any, alias="autoGeneration"
    ),
    added_from: date | None = Query(None, alias="addedFrom"),
    added_to: date | None = Query(None, alias="addedTo"),
    published_from: date | None = Query(None, alias="publishedFrom"),
    published_to: date | None = Query(None, alias="publishedTo"),
):
    filters = bookmarks.BookmarkFilters(
        search=search.strip(),
        added_from=added_from,
        added_to=added_to,
        published_from=published_from,
        published_to=published_to,
        audio=audio,
        exclusion=auto_generation,
    )
    data = await bookmarks.list_bookmarks(filters, page, BOOKMARKS_PER_PAGE)
    return BookmarkPage(
        items=[Bookmark.from_item(item) for item in data["items"]],
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        total_pages=data["total_pages"],
    )


@app.post("/api/bookmarks/audio/delete", response_model=CountResult)
async def delete_bookmark_audio(body: BookmarkIds):
    deleted = await models.delete_completed_jobs(body.bookmark_ids)
    jobs.remove_audio_files(deleted)
    return CountResult(count=len(deleted))


@app.put("/api/bookmarks/auto-generation", response_model=CountResult)
async def set_auto_exclusion(body: AutoExclusionUpdate):
    ids = list(dict.fromkeys(body.bookmark_ids))
    await models.set_auto_excluded(ids, body.excluded)
    return CountResult(count=len(ids))


# ── Jobs ───────────────────────────────────────────────────────────────────────


@app.post("/api/jobs", response_model=QueueResult)
async def create_jobs(body: BookmarkIds):
    queued, skipped = await jobs.queue_bookmarks(body.bookmark_ids)
    return QueueResult(queued=queued, skipped=skipped)


@app.get("/api/jobs", response_model=JobPage)
async def list_jobs(page: int = Query(1, ge=1)):
    """Queued, running and failed jobs. A completed job is its bookmark's audio."""
    rows, total = await models.list_jobs(
        limit=JOBS_PER_PAGE, offset=(page - 1) * JOBS_PER_PAGE, statuses=models.OPEN_STATUSES
    )
    return JobPage(
        items=[Job.from_row(r) for r in rows],
        total=total,
        page=page,
        page_size=JOBS_PER_PAGE,
        total_pages=max(1, (total + JOBS_PER_PAGE - 1) // JOBS_PER_PAGE),
    )


@app.get("/api/jobs/ids", response_model=list[str])
async def list_job_ids():
    return await models.list_job_ids(statuses=models.OPEN_STATUSES)


@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str):
    job = await models.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return Job.from_row(job)


@app.post("/api/jobs/{job_id}/retry", response_model=Job)
async def retry_job(job_id: str):
    job = await models.retry_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No failed job with that id")
    return Job.from_row(job)


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str):
    job = await models.delete_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    jobs.remove_audio_files([job])
    return Response(status_code=204)


@app.post("/api/jobs/bulk-delete", response_model=CountResult)
async def bulk_delete_jobs(body: JobIds):
    deleted = [
        job for job_id in dict.fromkeys(body.job_ids) if (job := await models.delete_job(job_id))
    ]
    jobs.remove_audio_files(deleted)
    return CountResult(count=len(deleted))


# ── Settings ───────────────────────────────────────────────────────────────────


async def _settings_response() -> Settings:
    current = await autogen.load_settings()
    state = await autogen.load_run_state()
    next_run = None
    if current.enabled and autogen.valid_cron(current.cron):
        next_run = autogen.scheduled_next_run() or autogen.next_run(current.cron)
    return Settings(
        auto_generation=AutoGenerationStatus(
            enabled=current.enabled,
            since=current.since,
            cron=current.cron,
            next_run=next_run,
            last_run=state.last_run,
            last_queued=state.last_queued,
            last_error=state.last_error,
        )
    )


@app.get("/api/settings", response_model=Settings)
async def get_settings():
    return await _settings_response()


@app.put("/api/settings", response_model=Settings)
async def update_settings(body: SettingsUpdate):
    update = body.auto_generation
    cron = update.cron.strip()
    if not autogen.valid_cron(cron):
        return problem(
            422,
            "Validation failed",
            "The schedule is not a valid cron expression.",
            {"autoGeneration.cron": ["Not a valid cron expression, e.g. 0 * * * *"]},
        )
    await autogen.save_settings(
        autogen.AutoGenSettings(enabled=update.enabled, since=update.since, cron=cron)
    )
    return await _settings_response()


@app.post("/api/auto-generation/run", response_model=QueueResult)
async def run_auto_generation():
    queued = await autogen.run_once()
    return QueueResult(queued=queued, skipped=0)


# ── File download ──────────────────────────────────────────────────────────────


@app.get("/api/audio/{filename}")
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


@app.get("/health", response_model=Health)
async def health():
    return Health(status="ok")


# ── Frontend ───────────────────────────────────────────────────────────────────
# The built single-page app. Registered last so every API route above wins.

if (config.FRONTEND_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIR / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    root = config.FRONTEND_DIR.resolve()
    if path:
        candidate = (root / path).resolve()
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=404,
            detail="The frontend has not been built; run `pnpm build` in frontend/",
        )
    # Client-side routes all resolve to the app shell, which must never be
    # cached: it names the hashed asset files of the current build.
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
