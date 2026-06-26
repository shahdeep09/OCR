"""DB round-trip tests."""
from __future__ import annotations


def test_create_and_list_job(tmp_db):
    tmp_db.create_job("j1", "book.pdf", ["hi", "gu", "en"])
    jobs = tmp_db.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "j1"
    assert jobs[0]["filename"] == "book.pdf"
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["languages"] == "hi,gu,en"


def test_status_transitions(tmp_db):
    tmp_db.create_job("j1", "book.pdf", ["en"])
    tmp_db.set_status("j1", "running")
    assert tmp_db.get_job("j1")["status"] == "running"
    tmp_db.set_status("j1", "done")
    j = tmp_db.get_job("j1")
    assert j["status"] == "done"
    assert j["completed_at"] is not None


def test_total_and_processed(tmp_db):
    tmp_db.create_job("j1", "book.pdf", ["en"])
    tmp_db.set_total_pages("j1", 3)
    assert tmp_db.bump_processed("j1") == 1
    assert tmp_db.bump_processed("j1") == 2
    j = tmp_db.get_job("j1")
    assert j["total_pages"] == 3
    assert j["processed_pages"] == 2


def test_page_upsert_and_update(tmp_db):
    tmp_db.create_job("j1", "book.pdf", ["en"])
    tmp_db.upsert_page("j1", 1, "hello", [{"text": "hello", "bbox": [0, 0, 10, 10]}])
    tmp_db.upsert_page("j1", 2, "world", [{"text": "world", "bbox": [0, 0, 10, 10]}])
    pages = tmp_db.list_pages("j1")
    assert [p["page_num"] for p in pages] == [1, 2]
    assert pages[0]["text"] == "hello"

    assert tmp_db.update_page_text("j1", 1, "HELLO") is True
    assert tmp_db.list_pages("j1")[0]["text"] == "HELLO"
    assert tmp_db.update_page_text("j1", 99, "x") is False


def test_pages_with_bboxes_roundtrip(tmp_db):
    tmp_db.create_job("j1", "book.pdf", ["en"])
    bb = [{"text": "abc", "bbox": [1.0, 2.0, 3.0, 4.0], "confidence": 0.9}]
    tmp_db.upsert_page("j1", 1, "abc", bb)
    rows = tmp_db.list_pages_with_bboxes("j1")
    assert rows == [{"page_num": 1, "text": "abc", "bboxes": bb}]


def test_delete_cascades(tmp_db):
    tmp_db.create_job("j1", "a.pdf", ["en"])
    tmp_db.upsert_page("j1", 1, "x", [])
    assert tmp_db.delete_job("j1") is True
    assert tmp_db.get_job("j1") is None
    assert tmp_db.list_pages("j1") == []


def test_reset_running_to_failed(tmp_db):
    tmp_db.create_job("j1", "a.pdf", ["en"])
    tmp_db.create_job("j2", "b.pdf", ["en"])
    tmp_db.set_status("j1", "running")
    tmp_db.reset_running_to_failed()
    j1 = tmp_db.get_job("j1")
    j2 = tmp_db.get_job("j2")
    assert j1["status"] == "failed"
    assert "restart" in (j1["error"] or "").lower()
    # j2 was 'queued', which we also reset (workers re-pick up via resume_queued_jobs).
    assert j2["status"] == "failed"


def test_requeue_interrupted_jobs(tmp_db):
    """A restart auto-resumes interrupted work: 'running' -> 'queued', while a job
    already 'queued' is left alone (resume_queued_jobs re-enqueues it)."""
    tmp_db.create_job("j1", "a.pdf", ["en"])      # stays queued
    tmp_db.create_job("j2", "b.pdf", ["en"])
    tmp_db.set_status("j2", "running")            # interrupted mid-run
    tmp_db.requeue_interrupted_jobs()
    assert tmp_db.get_job("j1")["status"] == "queued"
    j2 = tmp_db.get_job("j2")
    assert j2["status"] == "queued"
    assert j2["error"] is None
