"""Surya OCR wrapper. Loads models once at process start, runs per page.

Targets Surya's current schema (PageOCRResult -> blocks[].html / .bbox /
.reading_order). Falls back gracefully to older shapes (text_lines[]) if a
different Surya version is installed.
"""
from __future__ import annotations

import html
import inspect
import logging
import re
from typing import Any

from PIL import Image

log = logging.getLogger("bookscan.ocr")

DEFAULT_LANGS = ["hi", "gu", "en"]

_recognition_predictor = None
_detection_predictor = None
_rec_params: dict[str, inspect.Parameter] = {}

# Crude but reliable HTML -> plain text for Surya block.html output.
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_BLOCK_END_RE = re.compile(
    r"</\s*(p|div|h[1-6]|li|tr|table|ul|ol|blockquote)\s*>", re.IGNORECASE
)
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(s: str) -> str:
    if not s:
        return ""
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_END_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    # Collapse runs of spaces/tabs but preserve newlines
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def load_models() -> None:
    """Eagerly load Surya predictors.

    Recognition is required. Detection is best-effort — current Surya hits
    a PyTorch meta-tensor bug on some torch versions when initializing the
    detection model. Since we run with ``full_page=True`` (recognition handles
    everything internally), losing the detection predictor is non-fatal.
    """
    global _recognition_predictor, _detection_predictor, _rec_params
    if _recognition_predictor is not None:
        return

    from surya.recognition import RecognitionPredictor

    _recognition_predictor = RecognitionPredictor()

    try:
        from surya.detection import DetectionPredictor
        _detection_predictor = DetectionPredictor()
        log.info("DetectionPredictor loaded.")
    except Exception as e:
        log.warning(
            "DetectionPredictor failed to load (%s). Continuing without it — "
            "full_page recognition does not require detection.", e
        )
        _detection_predictor = None

    sig = inspect.signature(_recognition_predictor.__call__)
    _rec_params = dict(sig.parameters)
    log.info("RecognitionPredictor.__call__ signature: %s", sig)
    log.info("Detected parameter names: %s", list(_rec_params.keys()))


def _ensure_loaded() -> None:
    if _recognition_predictor is None:
        load_models()


def _build_call(image: Image.Image, langs: list[str]) -> tuple[list, dict]:
    """Build (args, kwargs) for the recognition predictor based on its real signature."""
    args: list[Any] = [[image]]
    kwargs: dict[str, Any] = {}
    params = _rec_params

    if "langs" in params:
        kwargs["langs"] = [langs]
    elif "languages" in params:
        kwargs["languages"] = [langs]

    if "task_names" in params:
        kwargs["task_names"] = ["ocr_with_boxes"]

    if _detection_predictor is not None:
        for cand in ("det_predictor", "detection_predictor", "det", "detector"):
            if cand in params:
                kwargs[cand] = _detection_predictor
                break

    if "full_page" in params:
        kwargs["full_page"] = True  # most accurate path in current Surya

    return args, kwargs


def _extract_lines(pred: Any) -> list[dict[str, Any]]:
    """Return list of {text, bbox, confidence} from whatever shape Surya returned.

    Handles two shapes:
      - New (PageOCRResult): pred.blocks[] with .html, .bbox, .reading_order, .skipped, .error
      - Old (OCRResult):      pred.text_lines[] with .text, .bbox, .confidence
    """
    lines_out: list[dict[str, Any]] = []

    blocks = getattr(pred, "blocks", None)
    if blocks is not None:
        ordered = sorted(
            blocks,
            key=lambda b: getattr(b, "reading_order", 0),
        )
        for blk in ordered:
            if getattr(blk, "skipped", False) or getattr(blk, "error", False):
                continue
            html_str = getattr(blk, "html", "") or ""
            text = _html_to_text(html_str)
            if not text:
                continue
            bbox = getattr(blk, "bbox", None) or [0, 0, 0, 0]
            lines_out.append(
                {
                    "text": text,
                    "bbox": [float(x) for x in bbox],
                    "confidence": float(getattr(blk, "confidence", 0.0) or 0.0),
                }
            )
        return lines_out

    # Legacy shape
    for line in getattr(pred, "text_lines", []) or []:
        bbox = getattr(line, "bbox", None) or [0, 0, 0, 0]
        lines_out.append(
            {
                "text": getattr(line, "text", "") or "",
                "bbox": [float(x) for x in bbox],
                "confidence": float(getattr(line, "confidence", 0.0) or 0.0),
            }
        )
    return lines_out


def run_page(image: Image.Image, langs: list[str] | None = None) -> dict[str, Any]:
    """Run Surya OCR on a single PIL image."""
    _ensure_loaded()
    langs = langs or DEFAULT_LANGS

    args, kwargs = _build_call(image, langs)
    try:
        predictions = _recognition_predictor(*args, **kwargs)
    except TypeError as e:
        log.warning("Primary call failed (%s); falling back to images-only call", e)
        predictions = _recognition_predictor([image])

    pred = predictions[0]
    lines_out = _extract_lines(pred)
    text = "\n".join(l["text"] for l in lines_out if l["text"].strip())
    log.info("Page OCR: %d blocks, %d chars", len(lines_out), len(text))

    w, h = image.size
    return {"text": text, "lines": lines_out, "image_size": [w, h]}
