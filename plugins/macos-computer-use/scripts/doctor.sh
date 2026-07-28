#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "Primary background backend:"
  MACOS_CUA_PLUGIN_ROOT="$ROOT" \
  MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
    "$ROOT/scripts/run-cua-driver.sh" --verify-runtime
  echo
fi

echo "Direct native fallback:"
PYTHON="$ROOT/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" ]]; then
  echo "python3 is not installed." >&2
  exit 1
fi
export PYTHONPATH="$ROOT"
exec "$PYTHON" -m macos_cua.server --self-test
