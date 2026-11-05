#!/bin/sh
# Run database migrations before starting the API. Retries tolerate a database
# that is still accepting connections when the container starts.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  attempt=1
  until alembic upgrade head; do
    if [ "$attempt" -ge 30 ]; then
      echo "alembic upgrade failed after ${attempt} attempts" >&2
      exit 1
    fi
    echo "alembic upgrade failed (attempt ${attempt}); retrying in 2s" >&2
    attempt=$((attempt + 1))
    sleep 2
  done
fi

exec "$@"
