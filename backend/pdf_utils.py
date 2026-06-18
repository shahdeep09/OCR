"""PDF rendering (pdf2image + bundled poppler) and searchable-PDF building."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import pikepdf
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

log = logging.getLogger("bookscan.pdf")

BACKEND_DIR = Path(__file__).parent
BUNDLED_POPPLER_BIN = BACKEND_DIR / "poppler" / "bin"
FONT_DIR = BACKEND_DIR / "fonts"

# 200 DPI is a sweet spot for vision-language OCR: a US-letter page becomes
# ~1700 x 2200 px which sits at the ideal input size for Surya's encoder.
# Higher DPIs (300+) waste GPU time on detail the model down-tokenizes anyway.
RENDER_DPI = 200

# Hard cap on image width before OCR. Unusually large input PDFs (legal/A3 or
# very high-DPI source) still get downscaled to this size to keep inference
# time bounded. Mirrored in ocr_engine.MAX_IMAGE_WIDTH for belt-and-suspenders.
MAX_IMAGE_WIDTH = 1800

# Font registration is idempotent and lazy.
_FONTS_REGISTERED = False
_FONT_LATIN = "BookScanNotoSans"
_FONT_DEVA = "BookScanNotoSansDeva"
_FONT_GUJ = "BookScanNotoSansGuj"
_FONT_FALLBACK = "Helvetica"  # built-in; used only if font files are missing


def _register_fonts() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    candidates = [
        (_FONT_LATIN, "NotoSans-Regular.ttf"),
        (_FONT_DEVA, "NotoSansDevanagari-Regular.ttf"),
        (_FONT_GUJ, "NotoSansGujarati-Regular.ttf"),
    ]
    for name, fname in candidates:
        path = FONT_DIR / fname
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
            except Exception as e:
                log.warning("Failed to register font %s: %s", fname, e)
        else:
            log.warning("Font file missing: %s", path)
    _FONTS_REGISTERED = True


def _font_registered(name: str) -> bool:
    try:
        pdfmetrics.getFont(name)
        return True
    except Exception:
        return False


def _pick_font(text: str) -> str:
    """Return a registered font name suitable for the dominant script in ``text``."""
    deva = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    guj = sum(1 for c in text if "઀" <= c <= "૿")
    if deva and deva >= guj and _font_registered(_FONT_DEVA):
        return _FONT_DEVA
    if guj and _font_registered(_FONT_GUJ):
        return _FONT_GUJ
    if _font_registered(_FONT_LATIN):
        return _FONT_LATIN
    return _FONT_FALLBACK


def _poppler_path() -> str | None:
    if BUNDLED_POPPLER_BIN.exists():
        return str(BUNDLED_POPPLER_BIN)
    return None


def get_page_count(pdf_path: Path) -> int:
    info = pdfinfo_from_path(str(pdf_path), poppler_path=_poppler_path())
    return int(info.get("Pages", 0))


def render_page(pdf_path: Path, page_num: int, out_path: Path) -> Image.Image:
    """Render a single PDF page (1-indexed) to a PNG file. Returns the PIL image."""
    images = convert_from_path(
        str(pdf_path),
        dpi=RENDER_DPI,
        first_page=page_num,
        last_page=page_num,
        poppler_path=_poppler_path(),
        fmt="png",
    )
    if not images:
        raise RuntimeError(f"Failed to render page {page_num} of {pdf_path}")
    img = images[0]
    if img.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / img.width
        new_size = (MAX_IMAGE_WIDTH, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return img


def page_image_path(job_dir: Path, page_num: int) -> Path:
    return job_dir / "images" / f"page_{page_num:04d}.png"


def build_searchable_pdf(
    source_pdf: Path,
    pages_data: list[dict[str, Any]],
    out_path: Path,
    image_dpi: int = RENDER_DPI,
) -> Path:
    """Overlay invisible Unicode text on the original PDF using Surya bbox coords.

    pages_data items:
        {"page_num": int, "text": str,
         "bboxes": [{"text": str, "bbox":[x0,y0,x1,y1], "confidence": float}, ...]}
    bbox coords are in image pixel space at ``image_dpi``; we scale to PDF points.
    """
    _register_fonts()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_in = pikepdf.open(str(source_pdf))
    try:
        scale = 72.0 / image_dpi
        by_page = {p["page_num"]: p for p in pages_data}
        overlay_count = 0

        for idx, page in enumerate(pdf_in.pages, start=1):
            entry = by_page.get(idx)
            if not entry:
                continue
            lines = entry.get("bboxes") or []
            if not lines:
                continue

            mediabox = page.mediabox
            page_w = float(mediabox[2]) - float(mediabox[0])
            page_h = float(mediabox[3]) - float(mediabox[1])

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(page_w, page_h))
            try:
                for line in lines:
                    text = (line.get("text") or "").strip()
                    if not text:
                        continue
                    # text may contain embedded newlines (multi-line block).
                    # Emit each visual line at the same baseline to keep things
                    # simple — the goal is searchability, not visible layout.
                    bbox = line.get("bbox") or [0, 0, 0, 0]
                    x0, y0, x1, y1 = [float(v) for v in bbox]
                    px0 = x0 * scale
                    px1 = x1 * scale
                    py_top = page_h - (y0 * scale)
                    py_bot = page_h - (y1 * scale)
                    box_h = max(1.0, py_top - py_bot)
                    box_w = max(1.0, px1 - px0)

                    sublines = [s for s in text.splitlines() if s.strip()] or [text]
                    per_line_h = box_h / max(1, len(sublines))

                    for i, subline in enumerate(sublines):
                        font_name = _pick_font(subline)
                        font_size = max(2.0, per_line_h * 0.85)
                        try:
                            string_w = c.stringWidth(subline, font_name, font_size)
                        except Exception:
                            # font can't measure this string; use fallback
                            font_name = _FONT_FALLBACK
                            string_w = c.stringWidth(subline, font_name, font_size)

                        text_obj = c.beginText()
                        text_obj.setTextRenderMode(3)  # invisible
                        text_obj.setFont(font_name, font_size)
                        if string_w > 0:
                            text_obj.setHorizScale(100.0 * box_w / string_w)
                        baseline_y = py_top - per_line_h * (i + 1) + per_line_h * 0.15
                        text_obj.setTextOrigin(px0, baseline_y)
                        try:
                            text_obj.textOut(subline)
                            c.drawText(text_obj)
                            overlay_count += 1
                        except Exception as e:
                            log.warning("Skipped overlay line: %s", e)
            finally:
                c.save()

            buf.seek(0)
            overlay_pdf = pikepdf.open(buf)
            try:
                page.add_overlay(overlay_pdf.pages[0])
            finally:
                overlay_pdf.close()

        log.info("Searchable PDF: wrote %d overlay text runs across %d page(s)",
                 overlay_count, len(pdf_in.pages))
        pdf_in.save(str(out_path))
    finally:
        pdf_in.close()

    return out_path
