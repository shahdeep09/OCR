"""SQLite helpers for BookScan job + page persistence."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "jobs.db"

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_db = _conn()


def init() -> None:
    with _lock:
        _db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL,
                total_pages INTEGER NOT NULL DEFAULT 0,
                processed_pages INTEGER NOT NULL DEFAULT 0,
                languages TEXT NOT NULL DEFAULT 'hi,gu,en',
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pages (
                job_id TEXT NOT NULL,
                page_num INTEGER NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                bboxes_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (job_id, page_num),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            """
        )


def now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def create_job(job_id: str, filename: str, languages: list[str]) -> None:
    with _lock:
        _db.execute(
            "INSERT INTO jobs (id, filename, status, languages, created_at) VALUES (?, ?, 'queued', ?, ?)",
            (job_id, filename, ",".join(languages), now()),
        )


def set_total_pages(job_id: str, total: int) -> None:
    with _lock:
        _db.execute("UPDATE jobs SET total_pages=? WHERE id=?", (total, job_id))


def set_status(job_id: str, status: str, error: Optional[str] = None) -> None:
    with _lock:
        if status in ("done", "failed"):
            _db.execute(
                "UPDATE jobs SET status=?, error=?, completed_at=? WHERE id=?",
                (status, error, now(), job_id),
            )
        else:
            _db.execute(
                "UPDATE jobs SET status=?, error=? WHERE id=?",
                (status, error, job_id),
            )


def bump_processed(job_id: str) -> int:
    with _lock:
        cur = _db.execute(
            "UPDATE jobs SET processed_pages = processed_pages + 1 WHERE id=? RETURNING processed_pages",
            (job_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def set_processed_pages(job_id: str, n: int) -> None:
    """Set the processed-page count explicitly (used when resuming a job so the
    count reflects pages already in the DB)."""
    with _lock:
        _db.execute("UPDATE jobs SET processed_pages=? WHERE id=?", (n, job_id))


def upsert_page(job_id: str, page_num: int, text: str, bboxes: list[dict[str, Any]]) -> None:
    with _lock:
        _db.execute(
            """
            INSERT INTO pages (job_id, page_num, text, bboxes_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, page_num) DO UPDATE SET
                text=excluded.text,
                bboxes_json=excluded.bboxes_json
            """,
            (job_id, page_num, text, json.dumps(bboxes, ensure_ascii=False)),
        )


def update_page_text(job_id: str, page_num: int, text: str) -> bool:
    with _lock:
        cur = _db.execute(
            "UPDATE pages SET text=? WHERE job_id=? AND page_num=?",
            (text, job_id, page_num),
        )
        return cur.rowcount > 0


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs() -> list[dict[str, Any]]:
    with _lock:
        rows = _db.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def list_pages(job_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _db.execute(
            "SELECT page_num, text FROM pages WHERE job_id=? ORDER BY page_num", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_pages_with_bboxes(job_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _db.execute(
            "SELECT page_num, text, bboxes_json FROM pages WHERE job_id=? ORDER BY page_num",
            (job_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "page_num": r["page_num"],
                "text": r["text"],
                "bboxes": json.loads(r["bboxes_json"] or "[]"),
            }
        )
    return out


def delete_job(job_id: str) -> bool:
    with _lock:
        cur = _db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cur.rowcount > 0


def reset_running_to_failed() -> None:
    """On startup, mark any 'running' jobs as failed (worker died mid-job)."""
    with _lock:
        _db.execute(
            "UPDATE jobs SET status='failed', error='Backend restarted mid-job', completed_at=? WHERE status IN ('running','queued')",
            (now(),),
        )
