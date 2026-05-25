#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_NAME="hermes_agent_auto_selfhosted"
SERVICE_NAME="hermes-agent"
PORT="${HERMES_AGENT_HOST_PORT:-8099}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required on the self-hosted runner machine." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required on the self-hosted runner machine." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Configure it before production use."
fi

mkdir -p storage/reports

docker compose -p "$PROJECT_NAME" up -d --build "$SERVICE_NAME"

for i in {1..60}; do
  if curl -fsS "http://127.0.0.1:${PORT}/health"; then
    echo "Hermes Agent is healthy at http://127.0.0.1:${PORT}"
    exit 0
  fi
  sleep 2
done

docker compose -p "$PROJECT_NAME" logs "$SERVICE_NAME"
exit 1
