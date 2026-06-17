# BookScan

Local OCR web tool for batch-scanning books in **Hindi · Gujarati · English** using [Surya OCR](https://github.com/VikParuchuri/surya). Runs entirely on your machine — no auth, no cloud.

```
D:\Local OCR\
├── backend/      # FastAPI + Surya + pdf2image (bundled poppler)
├── frontend/     # Vite + React (light mode)
└── outputs/      # per-job folder: source PDF, page images, searchable PDF
```

## Features

- Upload up to **5 PDFs** at a time, processed sequentially.
- **Live page-by-page progress** over WebSocket.
- **Side-by-side viewer**: scanned image on the left, editable OCR text on the right (auto-saves).
- Per-page **Copy** + per-book downloads: **JSON / TXT / Searchable PDF**.
- Persistent **job history** panel with per-row delete.
- JSON output is just `{page, text}` per page.
- Windows-friendly: poppler is bundled inside the repo.

---

## Setup

### 1. Backend

Requirements: **Python 3.10+** on Windows.

```powershell
cd "D:\Local OCR\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The first time you upload a PDF, Surya will download its model weights from Hugging Face (a few hundred MB). Subsequent runs are instant.

Poppler is already bundled at `backend/poppler/bin/` — nothing to install.

### 2. Frontend

Requirements: **Node 18+**.

```powershell
cd "D:\Local OCR\frontend"
npm install
```

---

## Run

Open two terminals.

**Terminal 1 — backend:**
```powershell
cd "D:\Local OCR\backend"
.\.venv\Scripts\Activate.ps1
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — frontend:**
```powershell
cd "D:\Local OCR\frontend"
npm run dev
```

Open <http://127.0.0.1:5173>.

---

## How it works

- `POST /api/upload` accepts up to 5 PDFs, creates a job row per file in `jobs.db`, copies the file to `outputs/{job_id}/source.pdf`, and enqueues it.
- A single async worker processes jobs one at a time: renders pages with `pdf2image` (300 DPI PNG), runs Surya OCR per page, stores text + bounding boxes in SQLite, and broadcasts progress to any connected `WS /api/ws/progress/{job_id}` client.
- Editing a page in the viewer sends a debounced `PUT` to update the DB and invalidates any cached searchable PDF.
- Downloads:
  - **JSON** — `[{page, text}]` (from current DB state).
  - **TXT** — concatenated text with `--- Page N ---` separators.
  - **Searchable PDF** — original PDF with an invisible text layer overlaid using Surya bbox coords (`pikepdf` + `reportlab`). Cached at `outputs/{job_id}/searchable.pdf` until the text is edited.

---

## Notes

- Surya runs on **CPU** here. A bundled config in [backend/config.py](backend/config.py) forces `TORCH_DEVICE=cpu` to dodge a `meta-tensor` init bug seen on some torch+accelerate combos. For GPU OCR we'd run a remote worker (see the cloud-worker note below).
- All data is local. `outputs/` and `backend/jobs.db` are the only mutable state.
- Backend restart: in-flight jobs are marked `failed` and a "Restarted mid-job" message is shown in the error card. Completed jobs and their downloads are preserved.
- Rotating logs at `backend/logs/bookscan.log` (5 MB × 5 files). Console mirrors them.

---

## Development

### Run the test suite

```powershell
cd "D:\Local OCR\backend"
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt   # one-time
pytest tests/ -v
```

Coverage at a glance:
- `tests/test_db.py` — SQLite CRUD + FK-cascade + status transitions.
- `tests/test_pdf_utils.py` — pdf2image rendering, searchable-PDF round-trip with `pypdf`.
- `tests/test_ocr_engine.py` — Surya signature adaptation + response parsing (mocked, no model load).

### Logs

- File: `backend/logs/bookscan.log` (rotated). Inspect during debugging:
  ```powershell
  Get-Content 'D:\Local OCR\backend\logs\bookscan.log' -Tail 50 -Wait
  ```
- Console: whatever's currently in your uvicorn terminal.

### Health endpoint

```powershell
curl http://127.0.0.1:8000/api/health
# -> {"ok": true, "models_ready": true, "model_error": null}
```

`models_ready` is `false` while Surya weights are still loading; flips to `true` once the eager load completes. `model_error` is set if the load failed — surface it in the UI from there.

### Endpoints quick reference

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/upload` | multipart `files` (≤ 5 PDFs) |
| `GET` | `/api/jobs` | full list (also pushed live via `/api/ws/jobs`) |
| `GET` | `/api/jobs/{id}` | single job row |
| `GET` | `/api/jobs/{id}/error` | full traceback string |
| `DELETE` | `/api/jobs/{id}` | row + `outputs/{id}/` |
| `GET` | `/api/jobs/{id}/pages` | `[{page_num, text}]` |
| `GET` | `/api/jobs/{id}/pages/{n}/image` | PNG at 300 DPI |
| `PUT` | `/api/jobs/{id}/pages/{n}` | body `{text}` |
| `GET` | `/api/jobs/{id}/download/{json\|txt\|pdf}` | export |
| `WS` | `/api/ws/progress/{id}` | per-job updates |
| `WS` | `/api/ws/jobs` | global jobs-list updates |
| `GET` | `/api/health` | `{ok, models_ready, model_error}` |

### Keyboard shortcuts (in viewer)

| Key | Action |
|---|---|
| `←` / `→` | prev / next page (when not typing) |
| `Ctrl+S` | force-save current edits |
| `Esc` | close viewer, return to history |

### Regenerating `requirements.txt`

```powershell
cd "D:\Local OCR\backend"
.\.venv\Scripts\Activate.ps1
pip freeze > requirements.lock.txt   # if you need an exact pin
```

`requirements.txt` itself is hand-maintained; `requirements-dev.txt` only adds test tooling.

---

## Deployment — RunPod GPU backend, Windows frontend

You can keep the frontend on your Windows PC and host the backend on a RunPod GPU pod (or any Linux box with CUDA). OCR speeds up roughly 20–60×.

### 1) Spin up a RunPod pod

1. Sign in at <https://runpod.io>, click **Deploy → Pods**.
2. Pick a **GPU pod**. Cheap option: any 24 GB GPU (RTX 3090 / A5000 / L4) — Surya's recognition model is small.
3. **Container image**: `runpod/pytorch:2.4.0-py3.12-cuda12.1.1` (or any CUDA-enabled Python 3.10+ image).
4. **Expose HTTP port 8000** (Pod Configuration → "Expose HTTP Ports" → `8000`).
5. **Volume**: at least 30 GB to fit weights + your scanned outputs.
6. Start the pod.

### 2) Connect and install

Open the pod's **Web Terminal**, then:

```bash
cd /workspace
git clone <your repo URL> bookscan
cd bookscan/backend
bash setup.sh
```

`setup.sh` installs `poppler-utils`, creates a venv, and installs the Python deps. About 5 minutes.

### 3) Start the backend

```bash
cd /workspace/bookscan/backend
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

You'll see Surya download its weights on first request (~1.5 GB; one-time, persists on the volume). After that, the recognition model loads in a couple of seconds per cold start.

### 4) Get the public URL

In the RunPod dashboard, your pod's **Connect** panel shows an HTTPS URL like:

```
https://abc123-8000.proxy.runpod.net
```

Copy it.

### 5) Point your local frontend at it

On your Windows PC, in `frontend/`:

```powershell
cp .env.example .env.local
# Edit .env.local — replace VITE_BACKEND_URL:
# VITE_BACKEND_URL=https://abc123-8000.proxy.runpod.net
npm run dev
```

Open <http://127.0.0.1:5173>. Uploads, WebSocket progress, and downloads all flow over the public RunPod URL.

### 6) Stop the pod to save money

RunPod bills per minute the pod is running. To pause without losing data:

- **Pod → Stop** — pauses the container, keeps the volume. You pay only for storage (~$0.10/GB/month). Restart any time and your work is intact.
- **Pod → Terminate** — destroys everything including the volume. Only do this if you exported what you need.

Stop the pod the moment you're done scanning. A 24 GB GPU pod left running 24/7 is ~$300/month; stopped, it's a few dollars/month.

### URL aliases

Both URL shapes work on the backend. The frontend uses the `/api/*` form; curl examples and direct HTTP clients can use either:

| Spec form | Canonical form |
|---|---|
| `POST /upload` | `POST /api/upload` |
| `GET /jobs` | `GET /api/jobs` |
| `GET /jobs/{id}` | `GET /api/jobs/{id}` |
| `DELETE /jobs/{id}` | `DELETE /api/jobs/{id}` |
| `GET /pages/{id}/{n}` | `GET /api/jobs/{id}/pages/{n}/image` |
| `PUT /pages/{id}/{n}` | `PUT /api/jobs/{id}/pages/{n}` |
| `GET /download/{id}/json\|txt\|pdf` | `GET /api/jobs/{id}/download/json\|txt\|pdf` |
| `WS /ws/{id}` | `WS /api/ws/progress/{id}` |
| `GET /health` | `GET /api/health` |

### JSON output shape

`GET /download/{id}/json` returns:

```json
{
  "book": "Adobe Scan Jun 16, 2026.pdf",
  "total_pages": 1,
  "processed_at": "2026-06-16T20:18:22",
  "pages": [
    {
      "page": 1,
      "text": "... full page text ...",
      "lines": ["block 1 text", "block 2 text", "..."]
    }
  ]
}
```

`lines[]` is a bonus convenience for indexing / RAG — each OCR'd block as its own string. The minimum required `{page, text}` is always present.

### Output folder layout

```
outputs/{job_id}/
├── source.pdf            # uploaded original
├── images/               # rendered page PNGs at 300 DPI  (named "pages/" in the spec)
│   └── page_0001.png
└── searchable.pdf        # built on demand, cached
```

Folder is named `images/` in this build; the deployment spec calls it `pages/`. Same content either way — both are 300-DPI PNGs.

### Device policy

- **Windows (local dev)**: forced to CPU. Sidesteps a meta-tensor init bug seen on some `torch + accelerate` combos.
- **Linux (RunPod)**: torch decides automatically — uses CUDA if present.
- Override with `BOOKSCAN_FORCE_CPU=1` or `BOOKSCAN_FORCE_GPU=1` in `backend/.env`.
