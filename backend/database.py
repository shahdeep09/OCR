"""Spec-named alias for ``db.py``.

The deployment spec references ``database.py``; the canonical implementation
lives in ``db.py``. This module re-exports the entire public surface so both
``import db`` and ``import database`` work.
"""
from __future__ import annotations

from db import (  # noqa: F401  (re-exports)
    DB_PATH,
    bump_processed,
    create_job,
    delete_job,
    get_job,
    init,
    list_jobs,
    list_pages,
    list_pages_with_bboxes,
    now,
    requeue_interrupted_jobs,
    reset_running_to_failed,
    set_status,
    set_total_pages,
    update_page_text,
    upsert_page,
)
