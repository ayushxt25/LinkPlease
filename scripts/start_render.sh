#!/bin/sh
set -e

celery -A app.worker.celery_app.celery_app worker --loglevel=info --concurrency=1 &
WORKER_PID="$!"

celery -A app.worker.celery_app.celery_app beat --loglevel=info &
BEAT_PID="$!"

cleanup() {
  kill "$WORKER_PID" "$BEAT_PID" 2>/dev/null || true
}

trap cleanup INT TERM

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
