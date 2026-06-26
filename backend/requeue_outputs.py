"""Recovery: re-queue books whose source PDF is still in outputs/ but whose DB
rows were lost.

For each ``outputs/<job_id>/source.pdf`` that has no matching job in the DB, this
recreates a 'queued' job reusing the SAME id — so the existing PDF (and any
already-rendered page images) are picked up in place, no re-uploading. After
running with --run, restart the backend; startup re-enqueues queued jobs and OCR
resumes.

    python requeue_outputs.py            # dry run — show what would be re-queued
    python requeue_outputs.py --run      # actually create the queued jobs

The original filenames lived in the lost DB, so re-queued books get a name from
the PDF's title metadata where present, else 'recovered-<shortid>.pdf'.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
import db

DEFAULT_LANGS = ["hi", "gu", "en"]
SKIP = {"_reports", "_uploads", "_db_backups"}     # bookkeeping dirs, not jobs


def _title(pdf: Path) -> str | None:
    try:
        import pikepdf
        with pikepdf.open(str(pdf)) as p:
            t = p.docinfo.get("/Title")
            t = str(t).strip() if t else ""
            return t or None
    except Exception:
        return None


def find_orphans() -> list[tuple[str, Path]]:
    """outputs/<id>/source.pdf folders that have no job row in the DB."""
    out: list[tuple[str, Path]] = []
    if not config.OUTPUTS_DIR.exists():
        return out
    for d in sorted(config.OUTPUTS_DIR.iterdir()):
        if not d.is_dir() or d.name in SKIP:
            continue
        src = d / "source.pdf"
        if src.exists() and db.get_job(d.name) is None:
            out.append((d.name, src))
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Re-queue books still on disk in outputs/.")
    ap.add_argument("--run", action="store_true",
                    help="actually create the queued jobs (default is a dry run)")
    args = ap.parse_args()

    db.init()
    orphans = find_orphans()
    if not orphans:
        print("Nothing to re-queue — no orphaned source PDFs in outputs/.")
        return

    print(f"{len(orphans)} book(s) found in outputs/ with no DB record:")
    for job_id, src in orphans:
        name = _title(src) or f"recovered-{job_id[:8]}.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        size_mb = src.stat().st_size / 1048576
        print(f"  {job_id[:8]}…  {size_mb:6.1f} MB  ->  {name}")
        if args.run:
            db.create_job(job_id, name, DEFAULT_LANGS)

    if args.run:
        print(f"\nCreated {len(orphans)} queued job(s). Now restart the backend to start OCR:")
        print("  bash /workspace/bookscan/scripts/stop.sh && bash /workspace/bookscan/scripts/start.sh")
    else:
        print("\nDry run — nothing changed. Re-run with --run to queue these.")


if __name__ == "__main__":
    main()
