"""Pytest config: make ``backend/`` importable and provide a temp-DB fixture
that fully isolates ``db.py``'s module-level connection from the real jobs.db.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Reimport ``db`` against a temporary DB_PATH and yield the module."""
    import db as _db
    fake_db_path = tmp_path / "test_jobs.db"
    monkeypatch.setattr(_db, "DB_PATH", fake_db_path, raising=True)
    # Rebuild the module-level connection against the new path.
    _db._db.close()  # type: ignore[attr-defined]
    monkeypatch.setattr(_db, "_db", _db._conn(), raising=True)
    _db.init()
    yield _db
    _db._db.close()  # type: ignore[attr-defined]


@pytest.fixture
def sample_pdf(tmp_path):
    """A tiny 1-page PDF written to disk via reportlab. Returns Path."""
    from reportlab.pdfgen import canvas
    p = tmp_path / "sample.pdf"
    c = canvas.Canvas(str(p), pagesize=(612, 792))
    c.setFont("Helvetica", 16)
    c.drawString(80, 720, "BookScan test page")
    c.drawString(80, 690, "Hello world for OCR pipeline smoke test.")
    c.showPage()
    c.save()
    return p
