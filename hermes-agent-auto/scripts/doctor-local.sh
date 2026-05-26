#!/usr/bin/env bash
set -euo pipefail

PORT="${HERMES_AGENT_HOST_PORT:-8099}"
BASE_URL="http://127.0.0.1:${PORT}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-hermes_agent_auto_isolated}"
SERVICE_NAME="hermes-agent"

echo "== Hermes Agent local doctor =="
echo "Base URL: ${BASE_URL}"

echo ""
echo "[1/5] Docker status"
if command -v docker >/dev/null 2>&1; then
  docker --version
else
  echo "FAIL: docker command not found"
  exit 1
fi

echo ""
echo "[2/5] Docker Compose status"
if docker compose version >/dev/null 2>&1; then
  docker compose version
else
  echo "FAIL: docker compose plugin not found"
  exit 1
fi

echo ""
echo "[3/5] Container status"
docker compose -p "$PROJECT_NAME" ps || true

echo ""
echo "[4/5] Health endpoint"
if curl -fsS "${BASE_URL}/health"; then
  echo ""
  echo "PASS: health endpoint is reachable"
else
  echo "FAIL: health endpoint is not reachable"
  echo "Try: docker compose -p ${PROJECT_NAME} logs ${SERVICE_NAME}"
  exit 1
fi

echo ""
echo "[5/5] Search configuration"
if [ -f .env ]; then
  PROVIDER="$(grep -E '^SEARCH_PROVIDER=' .env | tail -n1 | cut -d= -f2- || true)"
  echo "SEARCH_PROVIDER=${PROVIDER:-not-set}"
  case "${PROVIDER:-none}" in
    none|"") echo "WARN: live web search is disabled. Set SEARCH_PROVIDER=brave/tavily/serpapi/searxng and configure the matching key." ;;
    brave) grep -qE '^BRAVE_SEARCH_API_KEY=.+$' .env && echo "PASS: Brave key appears configured" || echo "WARN: BRAVE_SEARCH_API_KEY is empty" ;;
    tavily) grep -qE '^TAVILY_API_KEY=.+$' .env && echo "PASS: Tavily key appears configured" || echo "WARN: TAVILY_API_KEY is empty" ;;
    serpapi) grep -qE '^SERPAPI_API_KEY=.+$' .env && echo "PASS: SerpAPI key appears configured" || echo "WARN: SERPAPI_API_KEY is empty" ;;
    searxng) echo "INFO: SearXNG selected. Verify SEARXNG_URL is reachable from the container." ;;
    *) echo "WARN: unknown SEARCH_PROVIDER=${PROVIDER}" ;;
  esac
else
  echo "WARN: .env not found in current directory"
fi

echo ""
echo "Doctor completed."
