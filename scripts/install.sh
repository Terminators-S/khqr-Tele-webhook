#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "No .env found. Generating secure local defaults..."
  python3 scripts/bootstrap_open_source.py
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but was not found." >&2
  exit 1
fi

echo "Building and starting safe core services..."
docker compose up -d --build db api settlement webhook

echo "Waiting for the API health gate..."
for _ in $(seq 1 40); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=2).read()" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=2).read()" >/dev/null 2>&1; then
  echo "API did not become healthy. Run: docker compose ps" >&2
  exit 1
fi
echo
echo "KHQR Self-Develop is running."
echo "Dashboard: http://127.0.0.1:8088/dashboard"
echo "Core API:   http://127.0.0.1:8088"
echo
echo "Dashboard login secret (same as INTERNAL_SECRET):"
awk -F= '$1=="INTERNAL_SECRET"{print substr($0,index($0,"=")+1)}' .env
echo
echo "Safety defaults preserved:"
echo "  TELEGRAM_SHADOW_ONLY=true"
echo "  ALLOW_LIVE_TELEGRAM=false"
echo "  ALLOW_SHADOW_PROMOTION=false"
echo
echo "The Telegram collector is NOT started by install.sh."
echo "Complete Setup in the dashboard before any activation."
