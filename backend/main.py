"""BookScan FastAPI entrypoint."""
from __future__ import annotations

import config  # noqa: F401  — must come first; sets env vars before torch/surya imports
config.init_env()
config.init_logging()

import asyncio
import io
import json
import logging
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from starlette.requests import ClientDisconnect

import db
import ocr_engine
import pdf_utils
import proofread
import worker
from schemas import (
    IngestRequest,
    InboxItem,
    PageOut,
    PageUpdate,
    ProofreadZipRequest,
    UploadFinish,
    UploadInit,
    UploadItem,
)

# Scratch dir for in-flight chunked uploads.
UPLOAD_TMP = config.OUTPUTS_DIR / "_uploads"

log = logging.getLogger("bookscan")

# Tracks the eager model-load future so /api/health can report readiness.
_model_load_future: asyncio.Future | None = None


def _on_models_loaded(fut: asyncio.Future) -> None:
    try:
        fut.result()
        log.info("Model load complete.")
    except Exception as e:
        log.error("Model load FAILED: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model_load_future
    db.init()
    db.requeue_interrupted_jobs()  # resume any batch a restart/crash left mid-flight
    worker.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(UPLOAD_TMP, ignore_errors=True)  # drop any half-finished chunked uploads
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()
    _model_load_future = loop.run_in_executor(None, ocr_engine.load_models)
    _model_load_future.add_done_callback(_on_models_loaded)

    task = asyncio.create_task(worker.worker_loop())
    await worker.resume_queued_jobs()
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="BookScan", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_origin_regex=config.ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Upload ----------

@app.post("/api/upload", response_model=list[UploadItem])
async def upload(files: list[UploadFile] = File(...)) -> list[UploadItem]:
    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > config.MAX_UPLOAD:
        raise HTTPException(400, f"Upload at most {config.MAX_UPLOAD} PDFs at once")

    created: list[UploadItem] = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"Not a PDF: {f.filename}")
        job_id = uuid.uuid4().hex
        jdir = worker.job_dir(job_id)
        jdir.mkdir(parents=True, exist_ok=True)
        dest = jdir / "source.pdf"
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        db.create_job(job_id, f.filename, ocr_engine.DEFAULT_LANGS)
        await worker.enqueue(job_id)
        created.append(UploadItem(job_id=job_id, filename=f.filename))
        await worker.broadcast_jobs()
    return created


# ---------- Chunked upload (reliable for big files through the proxy) ----------
# The browser slices a file into small parts and uploads them one at a time;
# the proxy handles small requests fine, and a dropped chunk just retries. The
# parts are reassembled into source.pdf server-side, then the job is created.

@app.post("/api/upload/init")
def upload_init(payload: UploadInit):
    if not payload.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Not a PDF")
    upload_id = uuid.uuid4().hex
    (UPLOAD_TMP / upload_id).mkdir(parents=True, exist_ok=True)
    return {"upload_id": upload_id}


@app.put("/api/upload/chunk/{upload_id}/{index}")
async def upload_chunk(upload_id: str, index: int, request: Request):
    d = UPLOAD_TMP / upload_id
    if not d.is_dir():
        raise HTTPException(404, "Unknown upload session")
    try:
        data = await request.body()
    except ClientDisconnect:
        # Connection dropped mid-chunk (slow link / proxy timeout). The browser
        # retries this chunk, so just fail quietly without a scary traceback.
        raise HTTPException(400, "Chunk interrupted; will retry")
    (d / f"{index:06d}.part").write_bytes(data)
    return {"ok": True, "bytes": len(data)}


@app.post("/api/upload/finish/{upload_id}", response_model=UploadItem)
async def upload_finish(upload_id: str, payload: UploadFinish):
    d = UPLOAD_TMP / upload_id
    if not d.is_dir():
        raise HTTPException(404, "Unknown upload session")
    parts = sorted(d.glob("*.part"))
    if len(parts) != payload.total_chunks:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(
            400, f"Incomplete upload: got {len(parts)} of {payload.total_chunks} chunks"
        )

    name = Path(payload.filename).name
    job_id = uuid.uuid4().hex
    jdir = worker.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    dest = jdir / "source.pdf"
    with dest.open("wb") as out:
        for p in parts:
            with p.open("rb") as src:
                shutil.copyfileobj(src, out)
    shutil.rmtree(d, ignore_errors=True)

    db.create_job(job_id, name, ocr_engine.DEFAULT_LANGS)
    await worker.enqueue(job_id)
    await worker.broadcast_jobs()
    return UploadItem(job_id=job_id, filename=name)


# ---------- Pod-side ingest (for big files that can't go through the proxy) ----------

@app.get("/api/inbox", response_model=list[InboxItem])
def list_inbox():
    """List PDFs sitting in the inbox folder on the server (the pod's volume).
    Drop big files there via RunPod's file manager / Jupyter / scp, then ingest
    them without a browser upload."""
    out: list[InboxItem] = []
    if config.INBOX_DIR.exists():
        for p in sorted(config.INBOX_DIR.glob("*.pdf")):
            try:
                out.append(InboxItem(filename=p.name, size_mb=round(p.stat().st_size / 1048576, 1)))
            except OSError:
                continue
    return out


@app.post("/api/ingest", response_model=UploadItem)
async def ingest(payload: IngestRequest):
    name = Path(payload.filename).name  # strip any path components
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Not a PDF")
    src = (config.INBOX_DIR / name)
    if not src.is_file() or src.resolve().parent != config.INBOX_DIR.resolve():
        raise HTTPException(404, f"File not found in inbox: {name}")

    job_id = uuid.uuid4().hex
    jdir = worker.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    dest = jdir / "source.pdf"
    shutil.copyfile(src, dest)
    db.create_job(job_id, name, ocr_engine.DEFAULT_LANGS)
    await worker.enqueue(job_id)
    await worker.broadcast_jobs()
    return UploadItem(job_id=job_id, filename=name)


# ---------- Jobs ----------

@app.get("/api/jobs")
def list_jobs():
    return db.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    return j


@app.get("/api/jobs/{job_id}/error")
def get_job_error(job_id: str):
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    return {"error": j.get("error") or ""}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    db.delete_job(job_id)
    jdir = worker.job_dir(job_id)
    if jdir.exists():
        shutil.rmtree(jdir, ignore_errors=True)
    await worker.broadcast_jobs()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """Re-enqueue an interrupted job. The worker skips pages already in the DB,
    so only the unfinished pages get processed. Safe to call on failed jobs."""
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    if j["status"] in ("running", "queued"):
        return {"ok": True, "status": j["status"]}
    # Invalidate any cached (partial) searchable PDF — resuming adds pages.
    cached = worker.job_dir(job_id) / "searchable.pdf"
    if cached.exists():
        cached.unlink(missing_ok=True)
    db.set_status(job_id, "queued", None)  # clears any stale error
    await worker.enqueue(job_id)
    await worker.broadcast_jobs()
    return {"ok": True, "status": "queued"}


# ---------- Pages ----------

@app.get("/api/jobs/{job_id}/pages", response_model=list[PageOut])
def get_pages(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(404, "Job not found")
    return db.list_pages(job_id)


@app.get("/api/jobs/{job_id}/pages/{page_num}", response_model=PageOut)
def get_page(job_id: str, page_num: int):
    """Fresh single-page text — the viewer fetches per page so it never shows a
    stale cached list."""
    p = db.get_page(job_id, page_num)
    if not p:
        raise HTTPException(404, "Page not found")
    return p


@app.get("/api/jobs/{job_id}/pages/{page_num}/image")
def get_page_image(job_id: str, page_num: int):
    img_path = pdf_utils.page_image_path(worker.job_dir(job_id), page_num)
    if not img_path.exists():
        raise HTTPException(404, "Page image not found")
    return FileResponse(img_path, media_type="image/png")


@app.put("/api/jobs/{job_id}/pages/{page_num}")
def update_page(job_id: str, page_num: int, payload: PageUpdate):
    ok = db.update_page_text(job_id, page_num, payload.text)
    if not ok:
        raise HTTPException(404, "Page not found")
    cached = worker.job_dir(job_id) / "searchable.pdf"
    if cached.exists():
        cached.unlink(missing_ok=True)
    return {"ok": True}


# ---------- Downloads ----------

def _require_downloadable(job_id: str):
    """Allow download if the job has at least one completed page, regardless of
    status. Lets a failed/running job export the pages done so far (partial
    results) — completed work is never stranded behind a 'done' gate."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not db.list_pages(job_id):
        raise HTTPException(400, "No pages processed yet")
    return job


@app.get("/api/jobs/{job_id}/download/json")
def download_json(job_id: str):
    job = _require_downloadable(job_id)
    pages = db.list_pages_with_bboxes(job_id)
    page_objs = []
    for p in pages:
        block_texts = [
            (b.get("text") or "").strip()
            for b in (p.get("bboxes") or [])
            if (b.get("text") or "").strip()
        ]
        if not block_texts:
            block_texts = [
                ln for ln in (p.get("text") or "").splitlines() if ln.strip()
            ]
        page_objs.append(
            {
                "page": p["page_num"],
                "text": p["text"],
                "lines": block_texts,
            }
        )
    envelope = {
        "book": job["filename"],
        "total_pages": job["total_pages"],
        "processed_at": job.get("completed_at") or job.get("created_at"),
        "pages": page_objs,
    }
    data = json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")
    fname = Path(job["filename"]).stem + ".json"
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/jobs/{job_id}/download/txt")
def download_txt(job_id: str):
    job = _require_downloadable(job_id)
    pages = db.list_pages(job_id)
    parts: list[str] = []
    for p in pages:
        parts.append(f"--- Page {p['page_num']} ---\n\n{p['text']}\n")
    data = "\n".join(parts).encode("utf-8")
    fname = Path(job["filename"]).stem + ".txt"
    return Response(
        content=data,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/jobs/{job_id}/download/pdf")
def download_pdf(job_id: str):
    job = _require_downloadable(job_id)
    jdir = worker.job_dir(job_id)
    out = jdir / "searchable.pdf"
    if not out.exists():
        pages = db.list_pages_with_bboxes(job_id)
        pdf_utils.build_searchable_pdf(jdir / "source.pdf", pages, out)
    fname = Path(job["filename"]).stem + ".searchable.pdf"
    return FileResponse(out, media_type="application/pdf", filename=fname)


# ---------- Proofreading reports ----------
# On-demand quality triage. Scores every page of every book from the OCR text
# (Surya's VLM gives no real confidence — see proofread.py) and writes one CSV
# per book, worst pages first. Pure text analysis over the DB: fast, read-only,
# never touches the OCR pipeline — safe to run any time.

REPORTS_DIR = config.OUTPUTS_DIR / "_reports"


def _safe_report(name: str) -> Path:
    """Resolve a report name to a path inside REPORTS_DIR, or 404. Guards against
    path traversal (``..`` / absolute paths) by only accepting a bare filename."""
    safe = Path(name).name
    p = REPORTS_DIR / safe
    if safe.endswith(".csv") and p.is_file() and p.resolve().parent == REPORTS_DIR.resolve():
        return p
    raise HTTPException(404, "Report not found")


@app.post("/api/proofread/run")
def proofread_run(min_confidence: int = 80):
    min_confidence = max(0, min(100, min_confidence))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for old in REPORTS_DIR.glob("*.csv"):       # clear last run so the list is current
        old.unlink(missing_ok=True)

    result = proofread.analyze(db.DB_PATH, min_confidence)
    reports = []
    for book in result["books"]:
        name = proofread.write_book_csv(book, REPORTS_DIR)
        reports.append({
            "csv_name": name,
            "filename": book["filename"],
            "job_id": book["job_id"],
            "total_pages": book["total_pages"],
            "flagged": book["flagged"],
        })

    index = {
        "generated_at": db.now(),
        "native_confidence": result["native"],
        "min_confidence": min_confidence,
        "reports": reports,
    }
    (REPORTS_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index


@app.get("/api/proofread/reports")
def proofread_reports():
    """The most recent run's report list (lets the UI show last results without
    re-running)."""
    idx = REPORTS_DIR / "index.json"
    if idx.exists():
        try:
            return json.loads(idx.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"generated_at": None, "native_confidence": False, "min_confidence": 80, "reports": []}


@app.get("/api/proofread/reports/{name}/download")
def proofread_download_one(name: str):
    p = _safe_report(name)
    return FileResponse(p, media_type="text/csv", filename=p.name)


@app.post("/api/proofread/download-zip")
def proofread_download_zip(payload: ProofreadZipRequest):
    """Bundle selected report CSVs (or all, if no names given) into a zip."""
    if payload.names:
        paths = [_safe_report(n) for n in payload.names]
    else:
        paths = sorted(REPORTS_DIR.glob("*.csv")) if REPORTS_DIR.exists() else []
    if not paths:
        raise HTTPException(400, "No reports to download — run the analysis first")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, arcname=p.name)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="bookscan-proofread.zip"'},
    )


# ---------- WebSockets ----------

@app.websocket("/api/ws/progress/{job_id}")
async def ws_progress(ws: WebSocket, job_id: str):
    await ws.accept()
    await worker.subscribe(job_id, ws)
    try:
        j = db.get_job(job_id)
        if j:
            await ws.send_text(json.dumps({
                "status": j["status"],
                "processed_pages": j["processed_pages"],
                "total_pages": j["total_pages"],
                "error": j["error"],
            }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await worker.unsubscribe(job_id, ws)


@app.websocket("/api/ws/jobs")
async def ws_jobs(ws: WebSocket):
    """Live job-list updates. Pushes the full list on connect and after every
    create / status change / delete. Frontend falls back to polling if this
    drops."""
    await ws.accept()
    await worker.subscribe_jobs(ws)
    try:
        await ws.send_text(json.dumps({"type": "jobs", "jobs": db.list_jobs()}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await worker.unsubscribe_jobs(ws)


# ---------- Health ----------

@app.get("/api/health")
def health():
    models_ready = bool(_model_load_future and _model_load_future.done()
                        and _model_load_future.exception() is None)
    model_error = None
    if _model_load_future and _model_load_future.done():
        exc = _model_load_future.exception()
        if exc is not None:
            model_error = str(exc)
    return {"ok": True, "models_ready": models_ready, "model_error": model_error}


# ---------- Spec-named URL aliases (deployment spec) ----------
# These delegate to the existing /api/* handlers so the canonical routes keep
# working and any external caller using the spec URLs (curl examples, RunPod
# clients) gets the same behavior.

@app.post("/upload", response_model=list[UploadItem])
async def upload_alias(files: list[UploadFile] = File(...)) -> list[UploadItem]:
    return await upload(files)


@app.get("/jobs")
def jobs_alias():
    return list_jobs()


@app.get("/jobs/{job_id}")
def job_alias(job_id: str):
    return get_job(job_id)


@app.delete("/jobs/{job_id}")
async def delete_alias(job_id: str):
    return await delete_job(job_id)


@app.post("/jobs/{job_id}/resume")
async def resume_alias(job_id: str):
    return await resume_job(job_id)


@app.get("/inbox")
def inbox_alias():
    return list_inbox()


@app.post("/ingest", response_model=UploadItem)
async def ingest_alias(payload: IngestRequest):
    return await ingest(payload)


@app.get("/jobs/{job_id}/error")
def job_error_alias(job_id: str):
    return get_job_error(job_id)


@app.get("/pages/{job_id}/{page_num}")
def page_image_alias(job_id: str, page_num: int):
    return get_page_image(job_id, page_num)


@app.put("/pages/{job_id}/{page_num}")
def page_update_alias(job_id: str, page_num: int, payload: PageUpdate):
    return update_page(job_id, page_num, payload)


@app.get("/download/{job_id}/json")
def download_json_alias(job_id: str):
    return download_json(job_id)


@app.get("/download/{job_id}/txt")
def download_txt_alias(job_id: str):
    return download_txt(job_id)


@app.get("/download/{job_id}/pdf")
def download_pdf_alias(job_id: str):
    return download_pdf(job_id)


@app.websocket("/ws/{job_id}")
async def ws_progress_alias(ws: WebSocket, job_id: str):
    await ws_progress(ws, job_id)


@app.get("/health")
def health_alias():
    return health()
