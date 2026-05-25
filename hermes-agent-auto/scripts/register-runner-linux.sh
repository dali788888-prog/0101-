#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GITHUB_REPO_URL:-https://github.com/dali788888-prog/0101-}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-hermes}"
RUNNER_LABELS="${RUNNER_LABELS:-hermes-agent}"
RUNNER_NAME="${RUNNER_NAME:-hermes-agent-runner-$(hostname)}"
RUNNER_VERSION="${RUNNER_VERSION:-2.329.0}"
RUNNER_ARCH="${RUNNER_ARCH:-x64}"

if [ -z "$RUNNER_TOKEN" ]; then
  echo "RUNNER_TOKEN is required. Get it from GitHub: Settings -> Actions -> Runners -> New self-hosted runner." >&2
  echo "Example: RUNNER_TOKEN=xxxx bash hermes-agent-auto/scripts/register-runner-linux.sh" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

ARCHIVE="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${ARCHIVE}"

if [ ! -f "$ARCHIVE" ]; then
  echo "Downloading GitHub Actions runner ${RUNNER_VERSION}..."
  curl -L -o "$ARCHIVE" "$URL"
fi

if [ ! -f ./config.sh ]; then
  tar xzf "$ARCHIVE"
fi

if [ ! -f .runner ]; then
  ./config.sh \
    --url "$REPO_URL" \
    --token "$RUNNER_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    --work "_work" \
    --unattended \
    --replace
fi

if [ "${INSTALL_SERVICE:-true}" = "true" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    ./svc.sh install
    ./svc.sh start
    ./svc.sh status
  else
    sudo ./svc.sh install
    sudo ./svc.sh start
    sudo ./svc.sh status
  fi
else
  echo "Runner registered. Start it manually with:"
  echo "cd $RUNNER_DIR && ./run.sh"
fi

echo "Self-hosted runner connected with labels: ${RUNNER_LABELS}"
