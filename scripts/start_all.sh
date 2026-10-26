#!/bin/sh
# Run API + transcription worker inside ONE container/machine sharing one
# disk. Needed on PaaS hosts (Render, Fly) where separate services get
# separate disks: the worker must see the same SQLite jobs DB and audio
# files the API wrote, or jobs queue forever.
set -eu

PORT="${PORT:-8000}"
POLL="${PIANO_SCRIBE_WORKER_POLL_SEC:-2}"

python -m piano_scribe.worker --poll "$POLL" &
WORKER_PID=$!

cleanup() {
  kill "$WORKER_PID" 2>/dev/null || true
}
trap cleanup TERM INT

# Foreground web server (PID 1 via exec so signals arrive here).
exec uvicorn piano_scribe.api:app --host 0.0.0.0 --port "$PORT" --workers 2
