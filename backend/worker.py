"""Per-job page-pipelined worker + WebSocket progress fan-out.

Jobs are still processed strictly one at a time (single-PDF queue), but pages
within a job are pipelined through ``PARALLEL_PAGES`` concurrent worker
coroutines. This matches Surya's llama-server ``--parallel 8`` default, so the
GPU is kept fed instead of idling between sequential page calls.
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

# How many pages to process concurrently per job. Should match the number of
# parallel slots that llama-server is started with (Surya's default is 8).
# Override with the env var BOOKSCAN_PARALLEL_PAGES if needed.
PARALLEL_PAGES = max(1, int(os.environ.get("BOOKSCAN_PARALLEL_PAGES", "8")))

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
        await _broadcast(job_id, {"status": "running", "processed_pages": 0, "total_pages": 0})
        await broadcast_jobs()

        total = pdf_utils.get_page_count(source_pdf)
        db.set_total_pages(job_id, total)
        await _broadcast(job_id, {"status": "running", "processed_pages": 0, "total_pages": total})
        await broadcast_jobs()

        langs = [s for s in (job["languages"] or "").split(",") if s] or ocr_engine.DEFAULT_LANGS

        if total == 0:
            db.set_status(job_id, "done")
            await _broadcast(job_id, {"status": "done", "processed_pages": 0, "total_pages": 0})
            await broadcast_jobs()
            return

        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(PARALLEL_PAGES)

        log.info(
            "Job %s: pipelining %d page(s), up to %d in flight at once",
            job_id, total, PARALLEL_PAGES,
        )

        async def process_one(page_num: int) -> tuple[int, dict[str, Any]]:
            async with sem:
                img_path = pdf_utils.page_image_path(jdir, page_num)
                img = await loop.run_in_executor(
                    None, pdf_utils.render_page, source_pdf, page_num, img_path
                )
                result = await loop.run_in_executor(
                    None, ocr_engine.run_page, img, langs
                )
                return page_num, result

        tasks = [
            asyncio.create_task(process_one(p), name=f"job-{job_id[:8]}-page-{p}")
            for p in range(1, total + 1)
        ]

        try:
            for fut in asyncio.as_completed(tasks):
                page_num, result = await fut
                db.upsert_page(job_id, page_num, result["text"], result["lines"])
                processed = db.bump_processed(job_id)
                await _broadcast(
                    job_id,
                    {
                        "status": "running",
                        "processed_pages": processed,
                        "total_pages": total,
                        "page_just_done": page_num,
                    },
                )
                await broadcast_jobs()
        except BaseException:
            # Any failure: cancel the remaining page tasks so they don't keep
            # burning GPU time while the job is already marked failed.
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        db.set_status(job_id, "done")
        await _broadcast(
            job_id,
            {"status": "done", "processed_pages": total, "total_pages": total},
        )
        await broadcast_jobs()
    except Exception as e:
        tb = traceback.format_exc()
        log.error("Job %s failed: %s\n%s", job_id, e, tb)
        db.set_status(job_id, "failed", f"{e}\n\n{tb}")
        await _broadcast(job_id, {"status": "failed", "error": str(e)})
        await broadcast_jobs()


async def worker_loop() -> None:
    log.info("Worker loop started (parallel pages = %d)", PARALLEL_PAGES)
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
