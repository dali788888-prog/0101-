#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker first." >&2
  exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
fi
mkdir -p storage/reports
docker compose up -d --build
echo "Hermes Agent is starting at http://localhost:8099"
echo "Health: http://localhost:8099/health"
