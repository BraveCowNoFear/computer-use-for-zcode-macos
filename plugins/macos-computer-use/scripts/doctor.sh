#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/runtime-common.sh"
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "Primary background backend:"
  MACOS_CUA_PLUGIN_ROOT="$ROOT" \
  MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
    "$ROOT/scripts/run-cua-driver.sh" --verify-runtime
  echo
fi

echo "Direct native fallback:"
MACOS_CUA_PLUGIN_ROOT="$ROOT" \
MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
  exec "$ROOT/scripts/run-mcp.sh" --self-test
