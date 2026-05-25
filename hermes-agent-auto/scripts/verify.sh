#!/usr/bin/env bash
set -euo pipefail
python -m compileall app
python - <<'PY'
from app.config import get_settings
settings = get_settings()
print(f"Hermes Agent config loaded: {settings.app_name}")
PY
