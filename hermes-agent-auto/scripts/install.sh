#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_NAME="hermes_agent_auto_isolated"
SERVICE_NAME="hermes-agent"
PORT="${HERMES_AGENT_HOST_PORT:-8099}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command docker

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required. Install Docker Desktop or docker-compose-plugin first." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit .env to configure search keys and model settings."
fi

mkdir -p storage/reports

echo "Starting Hermes Agent with Docker Compose..."
docker compose -p "$PROJECT_NAME" up -d --build "$SERVICE_NAME"

echo ""
echo "Hermes Agent Docker service started."
echo "Web console: http://localhost:${PORT}"
echo "Health:      http://localhost:${PORT}/health"
echo "Logs:        docker compose -p ${PROJECT_NAME} logs -f ${SERVICE_NAME}"
echo "Stop:        docker compose -p ${PROJECT_NAME} down"
