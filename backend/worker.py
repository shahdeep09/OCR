"""Two-stage job worker + WebSocket progress fan-out.

Each job is processed in two stages with a small cleanup phase:

  Stage A — Pre-render
    Render every page of the PDF into a job-local tmp dir on local SSD,
    concurrently across CPU cores (asyncio.Semaphore(RENDER_CONCURRENCY=12)).

  Stage B — Batched OCR
    Load pages from tmp in groups of BATCH_SIZE, send each group as a single
    ``ocr_engine.run_batch`` call, persist results to SQLite and copy the
    rendered PNG to the network-volume outputs dir so the viewer can serve
    it. Broadcast progress to the per-job and global WS subscribers after
    each page.

  Stage C — Cleanup
    Best-effort ``rmtree`` of the tmp dir.

Jobs are still queued strictly one at a time. Pages within a job batch up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import WebSocket

import db
import ocr_engine
import pdf_utils

log = logging.getLogger("bookscan.worker")

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

# Local SSD scratch for pre-rendered page PNGs. /tmp on Linux is RAM-backed or
# fast local; on Windows we fall back to the system temp dir.
LOCAL_TMP_ROOT = Path(os.environ.get("BOOKSCAN_LOCAL_TMP", "/tmp" if os.name != "nt" else os.environ.get("TEMP", ".")))

# CPU concurrency for the render stage. 12 matches a typical RunPod L4 pod's
# vCPU count; pdf2image -> pdftoppm is per-page subprocess and scales linearly.
RENDER_CONCURRENCY = max(1, int(os.environ.get("BOOKSCAN_RENDER_CONCURRENCY", "12")))

# Batch size for the OCR stage. Should match llama-server's --parallel slots
# (Surya default = 8) so the predictor batch fits cleanly into available
# inference parallelism. Override with BOOKSCAN_BATCH_SIZE.
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


def _tmp_dir(job_id: str) -> Path:
    return LOCAL_TMP_ROOT / f"bookscan_{job_id}"


def _copy_to_volume(src: Path, dst: Path) -> None:
    """Move-or-copy a PNG from local SSD to the network volume outputs dir."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    # /tmp -> /workspace is cross-filesystem on RunPod; shutil.move falls back
    # to copy+delete. Use copyfile + unlink for explicit error scoping.
    shutil.copyfile(src, dst)


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

    tmp = _tmp_dir(job_id)
    try:
        db.set_status(job_id, "running")
        await _broadcast(job_id, {"status": "running", "processed_pages": 0, "total_pages": 0})
        await broadcast_jobs()

        total = pdf_utils.get_page_count(source_pdf)
        db.set_total_pages(job_id, total)
        await _broadcast(job_id, {"status": "running", "processed_pages": 0, "total_pages": total})
        await broadcast_jobs()

        if total == 0:
            db.set_status(job_id, "done")
            await _broadcast(job_id, {"status": "done", "processed_pages": 0, "total_pages": 0})
            await broadcast_jobs()
            return

        langs = [s for s in (job["languages"] or "").split(",") if s] or ocr_engine.DEFAULT_LANGS

        loop = asyncio.get_running_loop()
        tmp.mkdir(parents=True, exist_ok=True)

        # ============ STAGE A: PRE-RENDER ============
        log.info(
            "Job %s: Stage A — pre-rendering %d page(s) to %s (concurrency=%d)",
            job_id, total, tmp, RENDER_CONCURRENCY,
        )
        t_render_start = time.monotonic()

        render_sem = asyncio.Semaphore(RENDER_CONCURRENCY)

        async def render_one(page_num: int) -> tuple[int, Path]:
            async with render_sem:
                tmp_path = tmp / f"page_{page_num:04d}.png"
                await loop.run_in_executor(
                    None, pdf_utils.render_page, source_pdf, page_num, tmp_path
                )
                return page_num, tmp_path

        render_tasks = [
            asyncio.create_task(render_one(p), name=f"render-{job_id[:8]}-{p}")
            for p in range(1, total + 1)
        ]
        try:
            render_results = await asyncio.gather(*render_tasks)
        except BaseException:
            for t in render_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*render_tasks, return_exceptions=True)
            raise

        render_results.sort(key=lambda x: x[0])
        t_render = time.monotonic() - t_render_start
        log.info(
            "Job %s: Stage A complete — %d page(s) rendered in %.2fs (%.2f pages/sec)",
            job_id, total, t_render, total / max(t_render, 1e-3),
        )

        # ============ STAGE B: BATCHED OCR ============
        log.info(
            "Job %s: Stage B — batched OCR, batch_size=%d, %d batch(es)",
            job_id, BATCH_SIZE, (total + BATCH_SIZE - 1) // BATCH_SIZE,
        )
        t_ocr_start = time.monotonic()

        def _ocr_batch_sync(image_paths: list[Path]) -> list[dict[str, Any]]:
            from PIL import Image as PILImage
            images = [PILImage.open(p).convert("RGB") for p in image_paths]
            return ocr_engine.run_batch(images, [langs] * len(images))

        batches_total = (total + BATCH_SIZE - 1) // BATCH_SIZE
        batch_idx = 0
        for start in range(0, total, BATCH_SIZE):
            batch_idx += 1
            end = min(start + BATCH_SIZE, total)
            batch_pages = render_results[start:end]
            paths = [p for _, p in batch_pages]

            t_batch_start = time.monotonic()
            try:
                results = await loop.run_in_executor(None, _ocr_batch_sync, paths)
            except Exception:
                # Any batch failure aborts the whole job. /tmp will be cleaned
                # by the outer finally.
                raise
            t_batch = time.monotonic() - t_batch_start

            for (page_num, tmp_path), result in zip(batch_pages, results):
                permanent_path = pdf_utils.page_image_path(jdir, page_num)
                await loop.run_in_executor(None, _copy_to_volume, tmp_path, permanent_path)

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

                # Free local SSD as we go.
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            log.info(
                "Job %s: Stage B batch %d/%d complete — %d page(s) in %.2fs (%.2f pages/sec)",
                job_id, batch_idx, batches_total, len(paths), t_batch,
                len(paths) / max(t_batch, 1e-3),
            )

        t_ocr = time.monotonic() - t_ocr_start
        log.info(
            "Job %s: Stage B complete — %d page(s) OCR'd in %.2fs (%.2f pages/sec)",
            job_id, total, t_ocr, total / max(t_ocr, 1e-3),
        )
        log.info(
            "Job %s: TOTAL %d page(s) in %.2fs (%.2f pages/sec end-to-end)",
            job_id, total, t_render + t_ocr, total / max(t_render + t_ocr, 1e-3),
        )

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
    finally:
        # ============ STAGE C: CLEANUP ============
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


async def worker_loop() -> None:
    log.info(
        "Worker loop started (render_concurrency=%d, batch_size=%d, tmp_root=%s)",
        RENDER_CONCURRENCY, BATCH_SIZE, LOCAL_TMP_ROOT,
    )
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
