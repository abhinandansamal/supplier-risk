#!/usr/bin/env bash
# Start the API and UI on http://127.0.0.1:8000 with exactly one worker.
# One worker on purpose (SEC-024, decision 0008): the queue, the live events and the
# model rate limiter live in this one process. Settings come from .env.
set -euo pipefail
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "usage: scripts/run_app.sh [extra uvicorn options]"
  echo "Starts uvicorn with 1 worker on 127.0.0.1:8000 (settings from .env)."
  exit 0
fi
cd "$(dirname "$0")/.."
exec uv run uvicorn --factory --app-dir backend app.main:create \
  --host 127.0.0.1 --port 8000 --workers 1 --no-access-log "$@"
