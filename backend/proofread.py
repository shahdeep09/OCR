#!/usr/bin/env python3
"""BookScan proofreading triage — rank OCR pages by how likely they need a human.

WHY THIS EXISTS
---------------
Surya 0.20's VLM backend does NOT emit a usable per-page confidence. The field
exists in our data but the VLM leaves it at 0.0 (see ocr_engine._extract_lines —
it reads ``getattr(blk, "confidence", 0.0)`` because the new HTML blocks have no
such attribute). So instead of a model confidence, this tool derives a *quality
score* from the OCR text itself and ranks pages worst-first, so you proofread
only the suspicious ones instead of all of them.

WHAT IT FLAGS (the signals)
---------------------------
  • error markers   — our own "[page could not be rendered]" / "[OCR timed out]"
  • blank pages     — no text at all (a missed scan, or a genuinely blank page)
  • loops/repeats   — the VLM occasionally repeats a line or a character forever
  • garbage chars   — high ratio of stray symbols (not letters/marks/digits/punct)
  • script mix      — Gujarati bleeding into a Hindi book, or vice-versa
  • sparse pages    — far less text than the book's typical page (possible cut-off)

Each page gets a confidence 0–100 (higher = cleaner). You set the cut-off.

USAGE (pure standard library — no pip installs needed)
------------------------------------------------------
    python proofread.py                       # every book in ./jobs.db
    python proofread.py --job <job_id>        # one specific book
    python proofread.py --min-confidence 85   # stricter — flags more pages
    python proofread.py --top 30              # just the 30 worst pages overall
    python proofread.py --db /path/jobs.db --out /path/reports

OUTPUTS (written next to the DB by default)
-------------------------------------------
    proofread_report.csv    one row per flagged page (open in Excel / Sheets)
    proofread_report.html   grouped by book, links to each page's scan image

It reads the SQLite DB read-only, so it never touches or slows a running job.
Run it any time — after one book, or over the whole batch in the morning.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- constants ---

ERROR_MARKERS = ("[OCR timed out", "[page could not be rendered]")

# Punctuation that is normal in Hindi/Gujarati/English book *prose*. Kept
# deliberately tight: symbols that almost never appear densely in body text
# (@ # $ % ^ & * _ + = ~ ` | < > { } \) are intentionally EXCLUDED so a page
# made of them reads as garbage. Anything that is NOT a letter (L*), combining
# mark (M* — Indic matras live here), digit (N*), whitespace, or one of these
# counts toward the "odd characters" ratio.
COMMON_PUNCT = set(".,;:!?'\"()[]-–—/…“”‘’«»‹›।॥₹")

DEVA = (0x0900, 0x097F)
GUJ = (0x0A80, 0x0AFF)


# --------------------------------------------------------------- text signals -

def _in(o: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= o <= rng[1]


def script_counts(text: str) -> tuple[int, int, int, int]:
    """Return (devanagari, gujarati, latin, other-letters) character counts."""
    deva = guj = latin = other = 0
    for ch in text:
        o = ord(ch)
        if _in(o, DEVA):
            deva += 1
        elif _in(o, GUJ):
            guj += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            latin += 1
        elif ch.isalpha():
            other += 1
    return deva, guj, latin, other


def garbage_ratio(text: str) -> float:
    """Fraction of non-whitespace characters that look like OCR junk."""
    junk = total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "M", "N"):   # letters, combining marks, numbers
            continue
        if ch in COMMON_PUNCT:
            continue
        junk += 1
    return (junk / total) if total else 0.0


def repetition_penalty(text: str) -> tuple[float, list[str]]:
    """Detect decoder loops: repeated lines, repeated chars, runaway tokens."""
    penalty = 0.0
    reasons: list[str] = []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 4:
        counts = Counter(lines)
        top_line, top_freq = counts.most_common(1)[0]
        if top_freq / len(lines) > 0.4:
            penalty += 45 * min(1.0, top_freq / len(lines))
            reasons.append(f"{top_freq} of {len(lines)} lines identical (loop?)")
        else:
            uniq = len(counts) / len(lines)
            if len(lines) >= 6 and uniq < 0.5:
                penalty += 22 * (0.5 - uniq) / 0.5
                reasons.append(f"only {len(counts)} unique of {len(lines)} lines")

    run = re.search(r"([^\s])\1{14,}", text)
    if run:
        penalty += 28
        reasons.append(f"{len(run.group(0))}× repeated '{run.group(1)}'")

    longest = max((len(w) for w in text.split()), default=0)
    if longest > 40:
        penalty += min(18, (longest - 40) / 2)
        reasons.append(f"{longest}-char unbroken run")

    return min(65.0, penalty), reasons


# --------------------------------------------------------------- page scoring -

def score_page(text: str, dominant_script: str, book_median_chars: float) -> dict[str, Any]:
    """Return {confidence, reasons, chars} for one page.

    confidence is 0 (definitely review) .. 100 (looks clean). dominant_script and
    book_median_chars come from a first pass over the whole book so the script-mix
    and sparseness checks are relative to *this* book, not absolute.
    """
    t = (text or "").strip()
    chars = len(t)

    # Hard cases first.
    for marker in ERROR_MARKERS:
        if marker in (text or ""):
            return {"confidence": 0, "reasons": ["OCR error marker — must re-run"], "chars": chars}
    if chars == 0:
        return {"confidence": 8, "reasons": ["no text — blank page or missed scan"], "chars": 0}

    reasons: list[str] = []
    penalty = 0.0

    rep_pen, rep_reasons = repetition_penalty(t)
    penalty += rep_pen
    reasons += rep_reasons

    gr = garbage_ratio(t)
    if gr > 0.12:
        penalty += min(40.0, 8 + (gr - 0.12) * 70)
        reasons.append(f"{gr * 100:.0f}% odd characters")

    deva, guj, latin, other = script_counts(t)
    letters = deva + guj + latin + other
    if letters >= 40:
        if dominant_script == "deva" and guj / letters > 0.35:
            penalty += min(40.0, guj / letters * 45)
            reasons.append(f"{guj / letters * 100:.0f}% Gujarati in a Hindi book — check script")
        elif dominant_script == "guj" and deva / letters > 0.35:
            penalty += min(40.0, deva / letters * 45)
            reasons.append(f"{deva / letters * 100:.0f}% Hindi in a Gujarati book — check script")
        elif dominant_script in ("deva", "guj") and latin / letters > 0.6:
            penalty += 10.0
            reasons.append(f"{latin / letters * 100:.0f}% Latin — verify (English insert?)")

    if book_median_chars > 0 and chars > 0:
        if chars < 0.05 * book_median_chars:
            penalty += 25.0
            reasons.append(f"very sparse: {chars} chars vs ~{int(book_median_chars)} typical")
        elif chars < 0.2 * book_median_chars:
            penalty += 12.0
            reasons.append(f"sparse: {chars} chars vs ~{int(book_median_chars)} typical")

    confidence = max(0, 100 - int(round(min(100.0, penalty))))
    return {"confidence": confidence, "reasons": reasons, "chars": chars}


# ------------------------------------------------------------------ DB access -

def load_jobs(db_path: Path, job_filter: str | None) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if job_filter:
            jrows = conn.execute("SELECT * FROM jobs WHERE id=?", (job_filter,)).fetchall()
        else:
            jrows = conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        jobs = []
        for j in jrows:
            prows = conn.execute(
                "SELECT page_num, text, bboxes_json FROM pages WHERE job_id=? ORDER BY page_num",
                (j["id"],),
            ).fetchall()
            if not prows:
                continue
            jobs.append({"job": dict(j), "pages": [dict(p) for p in prows]})
        return jobs
    finally:
        conn.close()


def native_confidence_present(jobs: list[dict[str, Any]]) -> bool:
    """True only if Surya actually populated block confidences (any value > 0)."""
    for jb in jobs:
        for p in jb["pages"]:
            try:
                for blk in json.loads(p["bboxes_json"] or "[]"):
                    if float(blk.get("confidence", 0) or 0) > 0:
                        return True
            except Exception:
                pass
    return False


def book_profile(pages: list[dict[str, Any]]) -> tuple[str, float]:
    """Dominant script ('deva'/'guj'/'latin') and median char count for a book."""
    deva = guj = latin = 0
    counts = []
    for p in pages:
        t = (p["text"] or "").strip()
        if t and not any(m in t for m in ERROR_MARKERS):
            counts.append(len(t))
        d, g, l, _ = script_counts(t)
        deva += d
        guj += g
        latin += l
    dominant = max((("deva", deva), ("guj", guj), ("latin", latin)), key=lambda x: x[1])[0]
    counts.sort()
    median = counts[len(counts) // 2] if counts else 0.0
    return dominant, float(median)


# --------------------------------------------------------------------- report -

def analyze(db_path: Path, min_confidence: int = 80) -> dict[str, Any]:
    """Score every page of every book. Shared core for the CLI and the API.

    Returns::

        {"native": bool,              # did Surya provide a real confidence?
         "min_confidence": int,
         "books": [{"job_id", "filename", "total_pages", "flagged", "rows": [...]}]}

    ``rows`` are ALL pages for that book, worst-confidence first; each row is
    ``{page, confidence, chars, reasons, snippet}``.
    """
    jobs = load_jobs(db_path, None)
    native = native_confidence_present(jobs)
    books: list[dict[str, Any]] = []
    for jb in jobs:
        job, pages = jb["job"], jb["pages"]
        dominant, median = book_profile(pages)
        rows: list[dict[str, Any]] = []
        for p in pages:
            res = score_page(p["text"], dominant, median)
            rows.append({
                "page": p["page_num"],
                "confidence": res["confidence"],
                "chars": res["chars"],
                "reasons": "; ".join(res["reasons"]) or "—",
                "snippet": (p["text"] or "").strip().replace("\n", " ")[:160],
            })
        rows.sort(key=lambda r: (r["confidence"], r["page"]))
        books.append({
            "job_id": job["id"],
            "filename": job.get("filename") or job["id"],
            "total_pages": len(pages),
            "flagged": sum(1 for r in rows if r["confidence"] < min_confidence),
            "rows": rows,
        })
    books.sort(key=lambda b: b["filename"].lower())
    return {"native": native, "min_confidence": min_confidence, "books": books}


def safe_csv_name(filename: str, job_id: str) -> str:
    """Filesystem-safe, collision-proof CSV name for a book: '<stem>__<shortid>.csv'."""
    stem = Path(filename).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "book"
    return f"{safe}__{job_id[:8]}.csv"


def write_book_csv(book: dict[str, Any], out_dir: Path) -> str:
    """Write one book's full page-by-page confidence data to its own CSV.

    Returns the file name written (not the full path). utf-8-sig so Excel opens
    the Indic text correctly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    name = safe_csv_name(book["filename"], book["job_id"])
    with (out_dir / name).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["confidence", "page", "chars", "reasons", "snippet"])
        for r in book["rows"]:
            w.writerow([r["confidence"], r["page"], r["chars"], r["reasons"], r["snippet"]])
    return name


def _flatten(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten per-book rows into one list tagged with filename/job_id (for the
    combined console view + HTML)."""
    flat: list[dict[str, Any]] = []
    for b in books:
        for r in b["rows"]:
            flat.append({**r, "filename": b["filename"], "job_id": b["job_id"]})
    flat.sort(key=lambda r: (r["confidence"], r["filename"], r["page"]))
    return flat


def write_html(rows: list[dict[str, Any]], out: Path, db_path: Path) -> None:
    outputs_dir = db_path.parent.parent / "outputs"  # backend/jobs.db -> ../outputs
    by_book: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_book.setdefault(r["filename"] or r["job_id"], []).append(r)

    def color(c: int) -> str:
        if c == 0:
            return "#b00020"
        if c < 50:
            return "#c75300"
        if c < 80:
            return "#9a7d00"
        return "#3a7d00"

    parts = [
        "<!doctype html><meta charset='utf-8'><title>BookScan proofreading</title>",
        "<style>body{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:24px;color:#222}"
        "h2{margin-top:28px}table{border-collapse:collapse;width:100%}"
        "td,th{border-bottom:1px solid #eee;padding:6px 10px;vertical-align:top;text-align:left}"
        "th{background:#fafafa}.c{font-weight:700}.s{color:#555}a{color:#06c}</style>",
        f"<h1>Pages to proofread — {len(rows)} flagged</h1>",
        "<p class='s'>Lowest confidence first. Open each flagged page in the viewer and fix it.</p>",
    ]
    for book, items in by_book.items():
        parts.append(f"<h2>{html.escape(book)} <span class='s'>({len(items)} pages)</span></h2>")
        parts.append("<table><tr><th>Conf</th><th>Page</th><th>Reasons</th><th>Chars</th><th>Text preview</th></tr>")
        for r in items:
            img = outputs_dir / r["job_id"] / "images" / f"page_{r['page']:04d}.png"
            try:
                rel = img.relative_to(db_path.parent)
                page_cell = f"<a href='{html.escape(str(rel))}'>{r['page']}</a>"
            except ValueError:
                page_cell = str(r["page"])
            parts.append(
                f"<tr><td class='c' style='color:{color(r['confidence'])}'>{r['confidence']}</td>"
                f"<td>{page_cell}</td><td>{html.escape(r['reasons'])}</td>"
                f"<td class='s'>{r['chars']}</td>"
                f"<td class='s'>{html.escape(r['snippet'])}</td></tr>"
            )
        parts.append("</table>")
    out.write_text("\n".join(parts), encoding="utf-8")


# ----------------------------------------------------------------------- main -

def main() -> None:
    # Indic text + em-dashes in the console need UTF-8; Windows defaults to cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Rank OCR pages by how likely they need proofreading.")
    ap.add_argument("--db", type=Path, default=Path(__file__).parent / "jobs.db",
                    help="Path to jobs.db (default: alongside this script).")
    ap.add_argument("--job", help="Only analyze this job id (default: all books).")
    ap.add_argument("--min-confidence", type=int, default=80,
                    help="Flag pages below this confidence (0-100, default 80).")
    ap.add_argument("--top", type=int,
                    help="Ignore the threshold; just list the N worst pages overall.")
    ap.add_argument("--out", type=Path, help="Output folder (default: next to the DB).")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB not found: {args.db}")
    out_dir = args.out or args.db.parent
    by_book_dir = out_dir / "by_book"

    result = analyze(args.db, args.min_confidence)
    books = result["books"]
    if args.job:
        books = [b for b in books if b["job_id"] == args.job]
    if not books:
        raise SystemExit("No jobs with pages found in the DB.")

    # One CSV per book — the whole point: each book's confidence data on its own.
    written = [write_book_csv(b, by_book_dir) for b in books]

    # Combined view (console + HTML): flagged pages, or the N worst with --top.
    flat = _flatten(books)
    view = flat[:args.top] if args.top is not None else [
        r for r in flat if r["confidence"] < args.min_confidence
    ]
    html_path = out_dir / "proofread_report.html"
    write_html(view, html_path, args.db)

    total_pages = sum(b["total_pages"] for b in books)
    print("=" * 64)
    print("BookScan proofreading triage")
    print("=" * 64)
    print(f"Books analyzed     : {len(books)}")
    print(f"Pages analyzed     : {total_pages}")
    if result["native"]:
        print("Surya confidence   : present — (note: heuristics still applied)")
    else:
        print("Surya confidence   : NOT provided by this build (all 0.0) — using text heuristics")
    if args.top is not None:
        print(f"Showing            : {len(view)} worst pages (--top {args.top})")
    else:
        pct = (len(view) / total_pages * 100) if total_pages else 0.0
        print(f"Flagged (< {args.min_confidence:>3})      : {len(view)} of {total_pages} pages ({pct:.1f}%)")
    print("-" * 64)
    for r in view[:40]:
        book = (r["filename"] or r["job_id"])[:26]
        print(f"  conf {r['confidence']:>3}  p{r['page']:<5} {book:<28} {r['reasons']}")
    if len(view) > 40:
        print(f"  … and {len(view) - 40} more — see the report files.")
    print("-" * 64)
    print(f"Per-book CSVs ({len(written)}) in: {by_book_dir}")
    for name in written:
        print(f"    {name}")
    print(f"HTML overview        : {html_path}")


if __name__ == "__main__":
    main()
