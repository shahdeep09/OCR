#!/usr/bin/env bash
# Stop the detached BookScan backend (and its llama-server child).
echo "Stopping BookScan..."
if pkill -f "uvicorn main:app" 2>/dev/null; then
  echo "  uvicorn stopped."
else
  echo "  uvicorn was not running."
fi
# Surya spawns llama-server as a child; make sure it's gone too.
pkill -f "llama-server" 2>/dev/null && echo "  llama-server stopped." || true
echo "Done."
