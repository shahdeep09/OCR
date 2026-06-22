"""PDF rendering + searchable-PDF smoke tests."""
from __future__ import annotations

from pathlib import Path

import pdf_utils


def test_get_page_count(sample_pdf):
    assert pdf_utils.get_page_count(sample_pdf) == 1


def test_render_page_creates_png(sample_pdf, tmp_path):
    out_dir = tmp_path / "images"
    out_path = out_dir / "page_0001.png"
    img = pdf_utils.render_page(sample_pdf, 1, out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert img.size[0] > 0 and img.size[1] > 0


def test_render_pages_batch(sample_pdf, tmp_path):
    """The fast pypdfium2 batch renderer produces a PNG + image per page."""
    job_dir = tmp_path / "job"
    rendered = pdf_utils.render_pages(sample_pdf, [1], job_dir)
    assert len(rendered) == 1
    page_num, img = rendered[0]
    assert page_num == 1
    assert img is not None and img.size[0] > 0
    assert pdf_utils.page_image_path(job_dir, 1).exists()


def test_build_searchable_pdf_embeds_text(sample_pdf, tmp_path):
    out = tmp_path / "searchable.pdf"
    pages_data = [
        {
            "page_num": 1,
            "text": "Hello world",
            "bboxes": [
                {
                    "text": "BookScan test page",
                    "bbox": [50.0, 50.0, 1500.0, 90.0],
                    "confidence": 1.0,
                },
                {
                    "text": "Hello world for OCR pipeline smoke test.",
                    "bbox": [50.0, 100.0, 1900.0, 140.0],
                    "confidence": 1.0,
                },
            ],
        }
    ]
    pdf_utils.build_searchable_pdf(sample_pdf, pages_data, out)
    assert out.exists()

    # Verify pypdf can extract our text from the page.
    from pypdf import PdfReader
    extracted = PdfReader(str(out)).pages[0].extract_text() or ""
    # Visible source text from the sample PDF (drawn with Helvetica in the
    # fixture) plus our invisible overlay both contribute. The overlay is what
    # we care about.
    assert "BookScan test page" in extracted
    assert "Hello world" in extracted
