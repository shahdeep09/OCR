"""Per-page concurrent job worker + WebSocket progress fan-out.

Each job renders + OCRs its pages concurrently (asyncio.Semaphore(PARALLEL_PAGES)),
saving every page to SQLite the moment it finishes. This makes progress
incremental from page 1 and — combined with resume — crash-resilient:

  Resume:
    On (re)start of a job, pages already present in the DB are skipped. A page
    row exists iff OCR completed for it, so an interrupted 500-page job that
    reached page 400 re-runs only pages 401-500. Works across pod migration
    because jobs.db + outputs/ live on the network volume.

Jobs are processed strictly one at a time (single-PDF queue). Pages within a
job run up to PARALLEL_PAGES at once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

from fastapi import WebSocket

import db
import ocr_engine
import pdf_utils

log = logging.getLogger("bookscan.worker")

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

# Pages OCR'd together in one Surya call. Matches llama-server's --parallel
# slots (Surya default = 8) so the GPU is fed a full batch at once — the batched
# call is markedly faster than firing single-image calls concurrently. Override
# via BOOKSCAN_BATCH_SIZE.
BATCH_SIZE = max(1, int(os.environ.get("BOOKSCAN_BATCH_SIZE", "8")))


_queue: asyncio.Queue[str] = asyncio.Queue()

# Per-job subscribers (for /api/ws/progress/{job_id}).
_subscribers: dict[str, set[WebSocket]] = {}
_subs_lock = asyncio.Lock()

# Global jobs-list subscribers (for /api/ws/jobs).
_job_subs: set[WebSocket] = set()
_job_subs_lock = asyncio.Lock()


# ---------- Queue ----------

async def enqueue(job_id: str) -> None:
    await _queue.put(job_id)


# ---------- Per-job WS ----------

async def subscribe(job_id: str, ws: WebSocket) -> None:
    async with _subs_lock:
        _subscribers.setdefault(job_id, set()).add(ws)


async def unsubscribe(job_id: str, ws: WebSocket) -> None:
    async with _subs_lock:
        if job_id in _subscribers:
            _subscribers[job_id].discard(ws)
            if not _subscribers[job_id]:
                _subscribers.pop(job_id, None)


async def _broadcast(job_id: str, payload: dict[str, Any]) -> None:
    async with _subs_lock:
        targets = list(_subscribers.get(job_id, set()))
    dead: list[WebSocket] = []
    for ws in targets:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.append(ws)
    if dead:
        async with _subs_lock:
            for ws in dead:
                _subscribers.get(job_id, set()).discard(ws)


# ---------- Jobs-list WS ----------

async def subscribe_jobs(ws: WebSocket) -> None:
    async with _job_subs_lock:
        _job_subs.add(ws)


async def unsubscribe_jobs(ws: WebSocket) -> None:
    async with _job_subs_lock:
        _job_subs.discard(ws)


async def broadcast_jobs() -> None:
    """Push the current job list to all /api/ws/jobs subscribers."""
    payload = json.dumps({"type": "jobs", "jobs": db.list_jobs()})
    async with _job_subs_lock:
        targets = list(_job_subs)
    dead: list[WebSocket] = []
    for ws in targets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    if dead:
        async with _job_subs_lock:
            for ws in dead:
                _job_subs.discard(ws)


# ---------- Job processing ----------

def job_dir(job_id: str) -> Path:
    return OUTPUTS_DIR / job_id


async def _process_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        log.warning("Job %s missing from DB; skipping", job_id)
        return

    jdir = job_dir(job_id)
    source_pdf = jdir / "source.pdf"
    if not source_pdf.exists():
        db.set_status(job_id, "failed", "Source PDF missing")
        await _broadcast(job_id, {"status": "failed", "error": "Source PDF missing"})
        await broadcast_jobs()
        return

    try:
        db.set_status(job_id, "running")

        total = pdf_utils.get_page_count(source_pdf)
        db.set_total_pages(job_id, total)

        if total == 0:
            db.set_status(job_id, "done")
            await _broadcast(job_id, {"status": "done", "processed_pages": 0, "total_pages": 0})
            await broadcast_jobs()
            return

        # ---- Resume: skip pages already OCR'd (a page row == completed) ----
        existing = {p["page_num"] for p in db.list_pages(job_id)}
        done_count = len(existing)
        db.set_processed_pages(job_id, done_count)
        todo = [p for p in range(1, total + 1) if p not in existing]

        await _broadcast(
            job_id,
            {"status": "running", "processed_pages": done_count, "total_pages": total},
        )
        await broadcast_jobs()

        if not todo:
            db.set_status(job_id, "done")
            await _broadcast(
                job_id, {"status": "done", "processed_pages": total, "total_pages": total}
            )
            await broadcast_jobs()
            return

        n_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
        log.info(
            "Job %s: %d/%d already done, OCR'ing %d remaining in %d batch(es) of %d",
            job_id, done_count, total, len(todo), n_batches, BATCH_SIZE,
        )

        langs = [s for s in (job["languages"] or "").split(",") if s] or ocr_engine.DEFAULT_LANGS

        loop = asyncio.get_running_loop()

        async def render_one(page_num: int):
            img_path = pdf_utils.page_image_path(jdir, page_num)
            img = await loop.run_in_executor(
                None, pdf_utils.render_page, source_pdf, page_num, img_path
            )
            return page_num, img

        # Process the remaining pages in batches of BATCH_SIZE. Each batch:
        # render its pages (concurrently), OCR all of them in ONE Surya call
        # (8 images -> 8 llama slots), then persist + broadcast per page. A
        # crash loses at most the current batch; everything before it is saved.
        for start in range(0, len(todo), BATCH_SIZE):
            batch_nums = todo[start:start + BATCH_SIZE]
            rendered = await asyncio.gather(*[render_one(p) for p in batch_nums])
            page_nums = [pn for pn, _ in rendered]
            images = [img for _, img in rendered]

            results = await loop.run_in_executor(
                None, ocr_engine.run_batch, images, [langs] * len(images)
            )

            for page_num, result in zip(page_nums, results):
                db.upsert_page(job_id, page_num, result["text"], result["lines"])
                processed = db.bump_processed(job_id)
                await _broadcast(
                    job_id,
                    {
                        "status": "running",
                        "processed_pages": processed,
                        "total_pages": total,
                    },
                )
            await broadcast_jobs()

        db.set_status(job_id, "done")
        await _broadcast(
            job_id, {"status": "done", "processed_pages": total, "total_pages": total}
        )
        await broadcast_jobs()
    except Exception as e:
        tb = traceback.format_exc()
        log.error("Job %s failed: %s\n%s", job_id, e, tb)
        db.set_status(job_id, "failed", f"{e}\n\n{tb}")
        await _broadcast(job_id, {"status": "failed", "error": str(e)})
        await broadcast_jobs()


async def worker_loop() -> None:
    log.info("Worker loop started (batch size = %d)", BATCH_SIZE)
    while True:
        job_id = await _queue.get()
        try:
            await _process_job(job_id)
        finally:
            _queue.task_done()


async def resume_queued_jobs() -> None:
    for j in db.list_jobs():
        if j["status"] == "queued":
            await _queue.put(j["id"])
