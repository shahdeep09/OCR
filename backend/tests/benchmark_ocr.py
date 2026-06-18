"""Benchmark the BookScan OCR pipeline on a real PDF.

Designed to run on the RunPod L4 (or any GPU pod) against an arbitrary PDF.
Replicates the two-stage worker flow (pre-render → batched OCR) so the
numbers reported here are directly comparable to production throughput.

Usage:
    cd /workspace/bookscan/backend
    source .venv/bin/activate
    source /workspace/llamacpp/env.sh
    python tests/benchmark_ocr.py /workspace/bookscan/outputs/<job_id>/source.pdf --pages 50

Reports:
    - Pre-render time + pages/sec (CPU stage)
    - OCR time + pages/sec (GPU stage)
    - End-to-end total
    - L4 cost (USD, ₹) at default $0.39/hr
    - First-page OCR sample for accuracy eyeball
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Make backend/ importable when run as a script.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402
config.init_env()
config.init_logging()

import ocr_engine  # noqa: E402
import pdf_utils  # noqa: E402


def _gpu_snapshot() -> str:
    """Return a single nvidia-smi line summarising current GPU state, or ''."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
        return out.splitlines()[0]
    except Exception:
        return ""


def benchmark(pdf_path: Path, max_pages: int, batch_size: int, hourly_usd: float, inr_per_usd: float) -> None:
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(2)

    print("=" * 70)
    print(f"BookScan OCR benchmark")
    print("=" * 70)
    print(f"PDF:                {pdf_path}")
    print(f"Backend:            SURYA_INFERENCE_BACKEND={os.environ.get('SURYA_INFERENCE_BACKEND', '(auto)')}")
    print(f"DPI:                {pdf_utils.RENDER_DPI}")
    print(f"Max image width:    {pdf_utils.MAX_IMAGE_WIDTH}px")
    print(f"Batch size:         {batch_size}")
    print(f"GPU at start:       {_gpu_snapshot() or 'nvidia-smi unavailable'}")

    print("\nLoading Surya models...")
    t0 = time.monotonic()
    ocr_engine.load_models()
    print(f"  Models loaded in {time.monotonic() - t0:.1f}s")
    print(f"GPU after load:     {_gpu_snapshot() or 'n/a'}")

    total_in_pdf = pdf_utils.get_page_count(pdf_path)
    n = min(max_pages, total_in_pdf) if max_pages > 0 else total_in_pdf
    print(f"\nProcessing {n} of {total_in_pdf} page(s)")

    tmp_root = Path(os.environ.get("BOOKSCAN_LOCAL_TMP", "/tmp" if os.name != "nt" else os.environ.get("TEMP", ".")))
    tmp = tmp_root / f"bench_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        # ============ Stage A: pre-render ============
        print(f"\nStage A — pre-rendering to {tmp}")
        t_render_start = time.monotonic()
        paths: list[Path] = []
        for p in range(1, n + 1):
            out_path = tmp / f"page_{p:04d}.png"
            pdf_utils.render_page(pdf_path, p, out_path)
            paths.append(out_path)
        t_render = time.monotonic() - t_render_start
        print(f"  Rendered {n} page(s) in {t_render:.2f}s  =>  {n / max(t_render, 1e-3):.2f} pages/sec")

        # ============ Stage B: batched OCR ============
        print(f"\nStage B — OCR (batch size {batch_size})")
        from PIL import Image

        langs = ocr_engine.DEFAULT_LANGS
        all_results: list[dict] = []
        t_ocr_start = time.monotonic()
        batch_num = 0
        for start in range(0, n, batch_size):
            batch_num += 1
            end = min(start + batch_size, n)
            batch_paths = paths[start:end]
            images = [Image.open(p).convert("RGB") for p in batch_paths]
            t_b = time.monotonic()
            results = ocr_engine.run_batch(images, [langs] * len(images))
            dt = time.monotonic() - t_b
            print(
                f"  Batch {batch_num}: pages {start + 1}-{end} "
                f"in {dt:.2f}s  =>  {len(images) / max(dt, 1e-3):.2f} pages/sec "
                f"| GPU: {_gpu_snapshot() or 'n/a'}"
            )
            all_results.extend(results)
        t_ocr = time.monotonic() - t_ocr_start

        # ============ Report ============
        total = t_render + t_ocr
        pps = n / max(total, 1e-3)
        cost_usd = (total / 3600.0) * hourly_usd
        cost_per_page_usd = cost_usd / n
        cost_per_page_inr = cost_per_page_usd * inr_per_usd

        print("\n" + "=" * 70)
        print("RESULTS")
        print("=" * 70)
        print(f"Pages processed:      {n}")
        print(f"Render time:          {t_render:7.2f}s   ({n / max(t_render, 1e-3):6.2f} pages/sec)")
        print(f"OCR time:             {t_ocr:7.2f}s   ({n / max(t_ocr, 1e-3):6.2f} pages/sec)")
        print(f"End-to-end total:     {total:7.2f}s   ({pps:6.2f} pages/sec)")
        print()
        print(f"Cost @ ${hourly_usd}/hr:")
        print(f"  Total this run:     ${cost_usd:.4f}  (₹{cost_usd * inr_per_usd:.3f})")
        print(f"  Per page:           ${cost_per_page_usd:.5f}  (₹{cost_per_page_inr:.4f})")
        print()
        print(f"GPU at end:           {_gpu_snapshot() or 'n/a'}")

        # ============ Accuracy eyeball ============
        if all_results:
            sample = all_results[0]
            print("\n" + "=" * 70)
            print("PAGE 1 OCR SAMPLE  (first 800 chars, for eyeball check)")
            print("=" * 70)
            try:
                sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            except Exception:
                pass
            text = sample["text"]
            print(text[:800] + ("…" if len(text) > 800 else ""))
            print()
            print(f"Page 1 stats: {len(sample['lines'])} block(s), {len(sample['text'])} char(s)")

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="BookScan OCR throughput benchmark")
    ap.add_argument("pdf", type=Path, help="Path to a multi-page PDF")
    ap.add_argument("--pages", type=int, default=50, help="Max pages to OCR (default: 50)")
    ap.add_argument("--batch-size", type=int, default=8, help="Batch size (default: 8)")
    ap.add_argument("--hourly-usd", type=float, default=0.39, help="GPU $/hr for cost calc")
    ap.add_argument("--inr-per-usd", type=float, default=84.0, help="FX rate for ₹ cost calc")
    args = ap.parse_args()

    benchmark(
        pdf_path=args.pdf,
        max_pages=args.pages,
        batch_size=args.batch_size,
        hourly_usd=args.hourly_usd,
        inr_per_usd=args.inr_per_usd,
    )


if __name__ == "__main__":
    main()
