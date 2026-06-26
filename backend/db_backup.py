"""Automatic, rotating snapshots of jobs.db — the safety net the database lacked.

The OCR text lives ONLY in jobs.db; a restart once left it empty and there was
no copy to fall back on. This takes a consistent snapshot (SQLite online-backup
API) to the network volume every few minutes, on startup, and on clean shutdown,
keeping the last N. It NEVER snapshots an empty DB — so a wipe can't rotate your
good backups away; they simply stop being added to and the last good ones remain.

Restore (STOP the backend first):

    python db_backup.py --list
    python db_backup.py --restore latest
    python db_backup.py --restore jobs-20260626-153000-auto.db
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

import config
import db

log = logging.getLogger("bookscan.backup")

BACKUP_DIR = config.OUTPUTS_DIR / "_db_backups"          # on the volume, gitignored
KEEP = int(os.environ.get("BOOKSCAN_DB_BACKUPS_KEEP", "12"))
INTERVAL_S = int(os.environ.get("BOOKSCAN_DB_BACKUP_INTERVAL", "300"))   # 5 minutes


def _stamp() -> str:
    # db.now() -> '2026-06-26T15:30:00'; make it filesystem-safe + sortable.
    return db.now().replace("-", "").replace(":", "").replace("T", "-")


def list_backups() -> list[Path]:
    """All snapshots, oldest first (the timestamped names sort chronologically)."""
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("jobs-*.db"))


def latest() -> Path | None:
    backups = list_backups()
    return backups[-1] if backups else None


def _rotate(keep: int) -> None:
    if keep <= 0:
        return
    for old in list_backups()[:-keep]:
        old.unlink(missing_ok=True)


def snapshot(reason: str = "auto") -> Path | None:
    """Take one snapshot — UNLESS the DB is empty (we never overwrite or rotate
    away good backups with nothing). Returns the path written, or None if skipped."""
    n = db.job_count()
    if n == 0:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"jobs-{_stamp()}-{reason}.db"
    db.snapshot_to(path)
    _rotate(KEEP)
    log.info("DB snapshot: %s (%d jobs)", path.name, n)
    return path


def startup_check() -> None:
    """At boot: snapshot immediately if we have data, so a fresh backup always
    exists before the periodic loop's first tick. If the DB is empty but good
    backups exist, log a LOUD warning — a wipe happened and is recoverable."""
    if db.job_count() > 0:
        try:
            snapshot("startup")
        except Exception:
            log.warning("Startup snapshot failed", exc_info=True)
        return
    last = latest()
    if last is not None:
        log.error(
            "jobs.db is EMPTY but a backup exists (%s). If this is unexpected, stop "
            "the backend and run:  python db_backup.py --restore latest", last.name
        )


async def backup_loop(interval_s: int = INTERVAL_S) -> None:
    """Background task: snapshot every ``interval_s`` seconds."""
    loop = asyncio.get_running_loop()
    log.info("DB backup loop started (every %ds, keep %d, dir=%s)", interval_s, KEEP, BACKUP_DIR)
    while True:
        await asyncio.sleep(interval_s)
        try:
            await loop.run_in_executor(None, snapshot, "auto")
        except Exception as e:
            log.warning("DB snapshot failed: %s", e)


def restore(which: str = "latest") -> Path:
    """Copy a snapshot over the live jobs.db. STOP the backend before calling."""
    if which in ("latest", "", None):
        src = latest()
        if not src:
            raise SystemExit(f"No snapshots found in {BACKUP_DIR}")
    else:
        src = BACKUP_DIR / which
        if not src.exists():
            src = Path(which)          # allow an explicit path too
        if not src.exists():
            raise SystemExit(f"Snapshot not found: {which}")

    db.close()                          # release our handle before overwriting
    dest = db.DB_PATH
    # Drop stale WAL/SHM so SQLite reads the restored file cleanly.
    for side in (dest.parent / (dest.name + "-wal"), dest.parent / (dest.name + "-shm")):
        side.unlink(missing_ok=True)
    shutil.copyfile(src, dest)
    return dest


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="BookScan jobs.db snapshot / restore.")
    ap.add_argument("--list", action="store_true", help="list available snapshots")
    ap.add_argument("--snapshot", action="store_true", help="take one snapshot now")
    ap.add_argument("--restore", metavar="NAME",
                    help="restore a snapshot ('latest' or a filename) — STOP the backend first")
    args = ap.parse_args()

    if args.list:
        backups = list_backups()
        if not backups:
            print(f"No snapshots in {BACKUP_DIR}")
        for p in backups:
            print(f"{p.stat().st_size / 1024:9.0f} KB  {p.name}")
        return
    if args.snapshot:
        p = snapshot("manual")
        print(f"Wrote {p}" if p else "Skipped — DB is empty.")
        return
    if args.restore:
        dest = restore(args.restore)
        print(f"Restored -> {dest}. Now start the backend.")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
