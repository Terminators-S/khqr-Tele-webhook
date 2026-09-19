#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

TEST_PROJECT="khqr-self-develop-test"
TEST_COMPOSE=(docker compose -p "$TEST_PROJECT" -f docker-compose.test.yml)
export KHQR_TEST_DATABASE_URL="${KHQR_TEST_DATABASE_URL:-postgresql+psycopg2://khqr_test:khqr_test@127.0.0.1:55433/khqr_test}"
export DATABASE_URL="$KHQR_TEST_DATABASE_URL"
export INTERNAL_SECRET="test-internal-secret-32-characters"
cleanup() {
  "${TEST_COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
"${TEST_COMPOSE[@]}" up -d db-test
for _ in $(seq 1 30); do
  state=$("${TEST_COMPOSE[@]}" ps --format json 2>/dev/null | grep -q 'healthy' && echo healthy || true)
  [ "$state" = healthy ] && break
  sleep 1
done

.venv/bin/alembic upgrade head >/dev/null
.venv/bin/python -m pytest "$@"
