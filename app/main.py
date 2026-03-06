import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import jobs, models, readeck
from app.tts import AUDIO_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await models.init_db()
    jobs.start_worker()
    yield


app = FastAPI(title="Readeck Audiobook", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="/app/static"), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, page: int = 1, search: str = ""):
    limit = 30
    offset = (page - 1) * limit
    try:
        data = await readeck.list_bookmarks(limit=limit, offset=offset, search=search)
    except Exception as exc:
        logger.error("Failed to fetch bookmarks: %s", exc)
        data = {"items": [], "total": 0, "total_pages": 1, "current_page": page}
    return templates.TemplateResponse("index.html", {
        "request": request,
        "bookmarks": data["items"],
        "total": data["total"],
        "total_pages": data["total_pages"],
        "current_page": page,
        "search": search,
    })


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    all_jobs = await models.list_jobs()
    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": all_jobs,
    })


# ── Actions ────────────────────────────────────────────────────────────────────

@app.post("/jobs", response_class=HTMLResponse)
async def create_jobs(request: Request, bookmark_ids: list[str] = Form(...)):
    created = []
    for bid in bookmark_ids:
        try:
            bm = await readeck.get_bookmark(bid)
            title = bm.get("title", bid)
            url = bm.get("url", "")
        except Exception:
            title = bid
            url = ""
        job = await models.create_job(
            bookmark_id=bid,
            bookmark_title=title,
            bookmark_url=url,
            tts_engine=os.environ.get("TTS_ENGINE", "edge-tts"),
            voice=os.environ.get("EDGE_TTS_VOICE", "en-US-AriaNeural"),
        )
        created.append(job)

    all_jobs = await models.list_jobs()
    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": all_jobs,
        "flash": f"Queued {len(created)} job(s).",
    })


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    job = await models.delete_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("audio_path"):
        audio_file = AUDIO_DIR / job["audio_path"]
        if audio_file.exists():
            audio_file.unlink()
    return JSONResponse({"ok": True})


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/status")
async def job_status(job_id: str):
    job = await models.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs")
async def api_jobs():
    return await models.list_jobs()


# ── File download ──────────────────────────────────────────────────────────────

@app.get("/audio/{filename}")
async def download_audio(filename: str):
    # Prevent path traversal
    safe = Path(filename).name
    path = AUDIO_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(
        path=str(path),
        media_type="audio/mpeg",
        filename=safe,
    )


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
