#!/usr/bin/env bash
set -euo pipefail

echo "Running database migrations..."
retries=10
delay_seconds=3

for attempt in $(seq 1 "$retries"); do
  if alembic upgrade head; then
    echo "Migrations applied."
    exit 0
  fi

  if [ "$attempt" -eq "$retries" ]; then
    echo "Migration failed after ${retries} attempts."
    exit 1
  fi

  echo "Migration attempt ${attempt}/${retries} failed. Retrying in ${delay_seconds}s..."
  sleep "$delay_seconds"
done
