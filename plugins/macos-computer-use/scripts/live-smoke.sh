#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/runtime-common.sh"
DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}"
DEV_PYTHON="$ROOT/.venv/bin/python3"
DATA_PYTHON="$DATA_DIR/venv-$MACOS_CUA_DEPENDENCY_ID/bin/python3"

if macos_cua_native_runtime_ready "$DEV_PYTHON" "$ROOT"; then
  PYTHON="$DEV_PYTHON"
else
  if ! macos_cua_native_runtime_ready "$DATA_PYTHON" "$ROOT"; then
    MACOS_CUA_PLUGIN_ROOT="$ROOT" \
    MACOS_CUA_DATA_DIR="$DATA_DIR" \
      "$ROOT/scripts/run-mcp.sh" --self-test >&2
  fi
  PYTHON="$DATA_PYTHON"
fi
if ! macos_cua_native_runtime_ready "$PYTHON" "$ROOT"; then
  echo "The live-smoke native runtime is unavailable. Run scripts/install.sh and retry." >&2
  exit 1
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export MACOS_CUA_DATA_DIR="$DATA_DIR"
exec "$PYTHON" "$ROOT/scripts/live-smoke.py" "$@"
