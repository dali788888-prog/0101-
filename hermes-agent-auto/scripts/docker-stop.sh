#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_NAME="hermes_agent_auto_isolated"

docker compose -p "$PROJECT_NAME" down

echo "Hermes Agent Docker service stopped."
