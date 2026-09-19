#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

TEST_PROJECT="khqr-self-develop-test"
TEST_DB="postgresql+psycopg2://khqr_test:khqr_test@127.0.0.1:55433/khqr_test"
TEST_COMPOSE=(docker compose -p "$TEST_PROJECT" -f docker-compose.test.yml)
cleanup() {
  "${TEST_COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[RC] validating runtime + isolated-test compose configs"
docker compose config >/dev/null
"${TEST_COMPOSE[@]}" config >/dev/null

echo "[RC] resetting dedicated test PostgreSQL only"
cleanup
"${TEST_COMPOSE[@]}" up -d db-test >/dev/null
for _ in $(seq 1 30); do
  cid=$("${TEST_COMPOSE[@]}" ps -q db-test)
  state=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || true)
  [ "$state" = "healthy" ] && break
  sleep 1
done
[ "${state:-}" = "healthy" ] || { echo "test PostgreSQL failed health gate"; exit 1; }

export DATABASE_URL="$TEST_DB"
.venv/bin/alembic upgrade head >/dev/null
echo "[RC] configuration + schema gates"
.venv/bin/python scripts/release_preflight.py
.venv/bin/python -m compileall -q app khqr_sdk scripts tests alembic
.venv/bin/alembic check

echo "[RC] behavior gates"
KHQR_TEST_DATABASE_URL="$TEST_DB" .venv/bin/python -m pytest -q
.venv/bin/python scripts/postgres_concurrency_gate.py

echo "[RC] packaging gates"
rm -rf dist
mkdir -p dist
.venv/bin/pip wheel --no-deps -q -w dist .
docker build -q -t khqr-self-develop:0.1.0 . >/dev/null
docker run --rm khqr-self-develop:0.1.0 python -c "import app, khqr_sdk; print('IMAGE_IMPORT_OK', app.__version__)"

cleanup
trap - EXIT
echo "KHQR_CORE_RC_GATE PASS"
