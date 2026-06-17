#!/usr/bin/env bash
# BookScan — RunPod / Ubuntu setup.
#
# Run once on a fresh pod:
#   bash setup.sh
#
# Idempotent: re-running is safe.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BACKEND_DIR"

echo "==> System packages (poppler-utils, build deps)"
apt-get update -y
apt-get install -y --no-install-recommends \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    git \
    ca-certificates

echo "==> Python virtualenv"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
python -m pip install --upgrade pip

echo "==> Python deps"
# Install from requirements.txt (lets you pin versions). The list below mirrors
# the deployment spec for reference / ad-hoc installs.
pip install -r requirements.txt
# If you need to add anything ad-hoc, do it here without editing requirements.txt:
#   pip install <pkg>

echo "==> Make sure outputs/ exists at repo root"
mkdir -p "$BACKEND_DIR/../outputs"

echo "==> Done. Start the server with:"
echo "    cd $BACKEND_DIR && source .venv/bin/activate && \\"
echo "    uvicorn main:app --host 0.0.0.0 --port 8000"
echo ""
echo "On RunPod, expose port 8000 (HTTP) and use the public URL as VITE_BACKEND_URL."
