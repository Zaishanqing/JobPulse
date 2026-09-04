#!/bin/sh
set -eu

mode="${1:-api}"

case "$mode" in
  migrate)
    exec python -m alembic upgrade head
    ;;
  api)
    python -m alembic current --check-heads
    exec python -m uvicorn app.main:app \
      --host "${MATCHING_API_HOST:-0.0.0.0}" \
      --port "${MATCHING_API_PORT:-8000}" \
      --log-level "${MATCHING_API_LOG_LEVEL:-info}"
    ;;
  worker)
    python -m alembic current --check-heads
    exec python -m app.worker
    ;;
  vector-worker)
    python -m alembic current --check-heads
    exec python -m app.vector_worker
    ;;
  dispatcher)
    python -m alembic current --check-heads
    exec python -m app.dispatcher
    ;;
  *)
    exec "$@"
    ;;
esac
