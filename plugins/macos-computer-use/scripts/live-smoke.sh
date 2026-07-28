#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/runtime-common.sh"
PYTHON="$ROOT/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  echo "The source-checkout runtime is not installed. Run scripts/install.sh first." >&2
  exit 1
fi
require_supported_python "$PYTHON"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$ROOT/scripts/live-smoke.py"
