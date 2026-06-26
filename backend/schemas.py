"""Pydantic models for request/response payloads."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JobOut(BaseModel):
    id: str
    filename: str
    status: str
    total_pages: int
    processed_pages: int
    languages: str
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class PageOut(BaseModel):
    page_num: int
    text: str


class UploadItem(BaseModel):
    job_id: str
    filename: str


class PageUpdate(BaseModel):
    text: str


class IngestRequest(BaseModel):
    filename: str


class UploadInit(BaseModel):
    filename: str


class UploadFinish(BaseModel):
    filename: str
    total_chunks: int


class InboxItem(BaseModel):
    filename: str
    size_mb: float


class ProofreadZipRequest(BaseModel):
    # Report CSV names to bundle; empty list = all reports from the last run.
    names: list[str] = []
