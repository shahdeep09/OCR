#!/usr/bin/env bash
# Tail the detached BookScan backend log. Ctrl+C to stop watching (does NOT
# stop the server — it keeps running detached).
LOG="/workspace/bookscan.log"
if [ ! -f "$LOG" ]; then
  echo "No log yet at $LOG. Start the server with: bash $(cd "$(dirname "$0")/.." && pwd)/scripts/start.sh"
  exit 1
fi
tail -n 60 -f "$LOG"
