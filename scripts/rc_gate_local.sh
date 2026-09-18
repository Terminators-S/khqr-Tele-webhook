#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[RC] validating isolated release environment"
docker compose config >/dev/null

echo "[RC] resetting isolated PostgreSQL gate database"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
docker compose up -d db
docker compose run --rm migrate

echo "[RC] configuration + schema gates"
.venv/bin/python scripts/release_preflight.py
.venv/bin/python -m compileall -q app khqr_sdk scripts tests alembic
.venv/bin/alembic check

echo "[RC] behavior gates"
.venv/bin/pytest -q
.venv/bin/python scripts/postgres_concurrency_gate.py


echo "[RC] packaging gates"
rm -rf dist
mkdir -p dist
.venv/bin/pip wheel --no-deps -q -w dist .
docker build -q -t khqr-self-develop:0.1.0 .
docker run --rm khqr-self-develop:0.1.0 python -c "import app, khqr_sdk; print('IMAGE_IMPORT_OK', app.__version__)"

echo "[RC] cleaning destructive gate data"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "KHQR_CORE_RC_GATE PASS"
