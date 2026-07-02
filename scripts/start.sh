#!/usr/bin/env bash
# BookScan — start the backend DETACHED so it survives the web terminal
# closing or your internet dropping. The OCR keeps running on the pod while
# you're offline; reconnect later and it's just done (or resumable).
#
# Usage (on the pod):
#   bash /workspace/bookscan/scripts/start.sh
#
# Then:
#   watch logs:  bash /workspace/bookscan/scripts/logs.sh
#   stop:        bash /workspace/bookscan/scripts/stop.sh
set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"      # /workspace/bookscan
LOG="/workspace/bookscan.log"

if pgrep -f "uvicorn main:app" >/dev/null 2>&1; then
  echo "BookScan is already running."
  echo "  Logs: bash $REPO/scripts/logs.sh"
  echo "  Stop: bash $REPO/scripts/stop.sh"
  exit 0
fi

# --- System packages (container disk is wiped on pod Stop) ---
if ! command -v pdfinfo >/dev/null 2>&1; then
  echo "==> Installing poppler-utils + libcurl4..."
  apt-get update -qq && apt-get install -y -qq poppler-utils libcurl4
fi

# --- Noto fonts (gitignored; reseed if missing) ---
FONT_DIR="$REPO/backend/fonts"
mkdir -p "$FONT_DIR"
for pair in \
  "NotoSans-Regular.ttf|NotoSans/full/ttf/NotoSans-Regular.ttf" \
  "NotoSansDevanagari-Regular.ttf|NotoSansDevanagari/full/ttf/NotoSansDevanagari-Regular.ttf" \
  "NotoSansGujarati-Regular.ttf|NotoSansGujarati/full/ttf/NotoSansGujarati-Regular.ttf"; do
  name=${pair%%|*}; path=${pair##*|}
  [ -f "$FONT_DIR/$name" ] || curl -fsSL -o "$FONT_DIR/$name" \
    "https://cdn.jsdelivr.net/gh/notofonts/notofonts.github.io@main/fonts/$path" || true
done

# --- Inbox drop-folder for big PDFs (pod-side ingest) ---
mkdir -p "$REPO/inbox"

cd "$REPO/backend"
# shellcheck source=/dev/null
source .venv/bin/activate
# shellcheck source=/dev/null
source /workspace/llamacpp/env.sh

# OCR loop guards (DEE-77): cap VLM generation + bound loopy pages so one bad
# page can't hang the book. A manual `export` before running this still wins.
export SURYA_MAX_TOKENS_FULL_PAGE="${SURYA_MAX_TOKENS_FULL_PAGE:-1536}"
export BOOKSCAN_PAGE_TIMEOUT="${BOOKSCAN_PAGE_TIMEOUT:-60}"
export BOOKSCAN_BATCH_TIMEOUT="${BOOKSCAN_BATCH_TIMEOUT:-150}"

echo "==> Starting uvicorn DETACHED (survives terminal/internet drop)..."
# setsid → new session (no controlling terminal); nohup → ignore SIGHUP;
# </dev/null → no stdin tie; & → background.
setsid nohup uvicorn main:app --host 0.0.0.0 --port 8000 > "$LOG" 2>&1 < /dev/null &
sleep 2

if pgrep -f "uvicorn main:app" >/dev/null 2>&1; then
  echo ""
  echo "BookScan is running DETACHED. You can close this terminal or go offline."
  echo "  Watch logs:  bash $REPO/scripts/logs.sh"
  echo "  Stop:        bash $REPO/scripts/stop.sh"
  echo ""
  echo "Wait until the log shows 'Model load complete.' before the first OCR."
else
  echo "ERROR: uvicorn did not start. Last 20 log lines:"
  tail -n 20 "$LOG" 2>/dev/null || true
  exit 1
fi
