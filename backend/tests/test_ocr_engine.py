"""Adapter tests: ocr_engine builds the right call shape for various Surya
signatures, and parses both new (PageOCRResult.blocks) and old (.text_lines)
response shapes without importing real Surya.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import ocr_engine


# ---------- _html_to_text ----------

def test_html_to_text_strips_tags_and_unescapes():
    s = "<p>Hello&nbsp;<b>world</b></p><p>line 2</p>"
    assert ocr_engine._html_to_text(s) == "Hello\xa0world\nline 2"


def test_html_to_text_br_becomes_newline():
    assert ocr_engine._html_to_text("a<br/>b<br>c") == "a\nb\nc"


def test_html_to_text_drops_empty_lines():
    assert ocr_engine._html_to_text("<p></p><p>hi</p><p>  </p>") == "hi"


# ---------- _build_call (signature adaptation) ----------

def _set_predictor_with_sig(monkeypatch, fn):
    """Patch the global recognition predictor to a Mock and set _rec_params from fn."""
    mock = MagicMock()
    mock.__call__ = MagicMock()
    monkeypatch.setattr(ocr_engine, "_recognition_predictor", mock, raising=True)
    monkeypatch.setattr(ocr_engine, "_rec_params",
                        dict(inspect.signature(fn).parameters), raising=True)


def test_build_call_new_signature(monkeypatch):
    def newsig(images, layout_results=None, *, full_page=None): pass
    _set_predictor_with_sig(monkeypatch, newsig)
    monkeypatch.setattr(ocr_engine, "_detection_predictor", "DET", raising=True)
    args, kwargs = ocr_engine._build_call_for_batch(["IMG"], [["hi", "en"]])  # type: ignore[arg-type]
    assert args == [["IMG"]]
    assert kwargs == {"full_page": True}  # no langs/det in new sig


def test_build_call_old_signature_with_langs_and_det(monkeypatch):
    def oldsig(images, langs=None, det_predictor=None): pass
    _set_predictor_with_sig(monkeypatch, oldsig)
    monkeypatch.setattr(ocr_engine, "_detection_predictor", "DET", raising=True)
    args, kwargs = ocr_engine._build_call_for_batch(["IMG"], [["hi"]])  # type: ignore[arg-type]
    assert args == [["IMG"]]
    assert kwargs == {"langs": [["hi"]], "det_predictor": "DET"}


def test_build_call_skips_det_when_none(monkeypatch):
    def oldsig(images, langs=None, det_predictor=None): pass
    _set_predictor_with_sig(monkeypatch, oldsig)
    monkeypatch.setattr(ocr_engine, "_detection_predictor", None, raising=True)
    args, kwargs = ocr_engine._build_call_for_batch(["IMG"], [["en"]])  # type: ignore[arg-type]
    assert "det_predictor" not in kwargs


def test_build_call_task_names_variant(monkeypatch):
    def mid(images, task_names=None, det_predictor=None): pass
    _set_predictor_with_sig(monkeypatch, mid)
    monkeypatch.setattr(ocr_engine, "_detection_predictor", "DET", raising=True)
    args, kwargs = ocr_engine._build_call_for_batch(["IMG"], [["en"]])  # type: ignore[arg-type]
    assert kwargs["task_names"] == ["ocr_with_boxes"]
    assert kwargs["det_predictor"] == "DET"


def test_build_call_batch_of_three(monkeypatch):
    """New: batched calls pass multiple images and matching task_names."""
    def mid(images, task_names=None, det_predictor=None): pass
    _set_predictor_with_sig(monkeypatch, mid)
    monkeypatch.setattr(ocr_engine, "_detection_predictor", "DET", raising=True)
    imgs = ["A", "B", "C"]
    langs = [["en"], ["hi"], ["gu"]]
    args, kwargs = ocr_engine._build_call_for_batch(imgs, langs)  # type: ignore[arg-type]
    assert args == [["A", "B", "C"]]
    assert kwargs["task_names"] == ["ocr_with_boxes"] * 3


# ---------- _extract_lines (response parsing) ----------

def test_extract_lines_new_shape_blocks():
    blk = SimpleNamespace(
        reading_order=0,
        skipped=False,
        error=False,
        html="<p>Hello world</p>",
        bbox=[1.0, 2.0, 3.0, 4.0],
        confidence=0.9,
    )
    pred = SimpleNamespace(blocks=[blk])
    lines = ocr_engine._extract_lines(pred)
    assert lines == [{"text": "Hello world", "bbox": [1.0, 2.0, 3.0, 4.0], "confidence": 0.9}]


def test_extract_lines_skips_skipped_and_errored_blocks():
    blocks = [
        SimpleNamespace(reading_order=0, skipped=True,  error=False, html="<p>x</p>", bbox=[0,0,1,1], confidence=1.0),
        SimpleNamespace(reading_order=1, skipped=False, error=True,  html="<p>y</p>", bbox=[0,0,1,1], confidence=1.0),
        SimpleNamespace(reading_order=2, skipped=False, error=False, html="<p>z</p>", bbox=[0,0,1,1], confidence=1.0),
    ]
    pred = SimpleNamespace(blocks=blocks)
    lines = ocr_engine._extract_lines(pred)
    assert [l["text"] for l in lines] == ["z"]


def test_extract_lines_respects_reading_order():
    blocks = [
        SimpleNamespace(reading_order=2, skipped=False, error=False, html="<p>second</p>", bbox=[0,0,1,1], confidence=1.0),
        SimpleNamespace(reading_order=1, skipped=False, error=False, html="<p>first</p>",  bbox=[0,0,1,1], confidence=1.0),
    ]
    pred = SimpleNamespace(blocks=blocks)
    lines = ocr_engine._extract_lines(pred)
    assert [l["text"] for l in lines] == ["first", "second"]


def test_extract_lines_legacy_text_lines_shape():
    line = SimpleNamespace(text="legacy text", bbox=[5,6,7,8], confidence=0.7)
    pred = SimpleNamespace(text_lines=[line])
    lines = ocr_engine._extract_lines(pred)
    assert lines == [{"text": "legacy text", "bbox": [5.0, 6.0, 7.0, 8.0], "confidence": 0.7}]
