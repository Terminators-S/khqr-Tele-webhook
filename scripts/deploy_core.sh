#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[deploy] validating compose"
docker compose config >/dev/null

echo "[deploy] starting database"
docker compose up -d db

echo "[deploy] applying migrations"
docker compose run --rm migrate

echo "[deploy] starting core services"
docker compose up -d --build api settlement webhook

echo "[deploy] waiting for health"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8088/healthz >/dev/null; then
    echo "KHQR_CORE_DEPLOY PASS"
    docker compose ps
    exit 0
  fi
  sleep 1
done

echo "KHQR_CORE_DEPLOY FAIL: health endpoint did not become ready" >&2
docker compose logs --no-color --tail=80 api settlement webhook >&2 || true
exit 1
