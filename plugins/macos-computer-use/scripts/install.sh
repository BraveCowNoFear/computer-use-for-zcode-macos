#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/runtime-common.sh"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must run on macOS." >&2
  exit 1
fi
require_supported_python python3

echo "Preparing the background Cua Driver backend..."
MACOS_CUA_PLUGIN_ROOT="$ROOT" \
MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
  "$ROOT/scripts/run-cua-driver.sh" --prepare-only

echo "Preparing the direct Quartz/PyObjC fallback..."
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python3" -m pip install --disable-pip-version-check --require-hashes --no-deps --only-binary=:all: -r "$ROOT/requirements.txt"
export PYTHONPATH="$ROOT"
"$ROOT/.venv/bin/python3" -m macos_cua.server --self-test

echo
echo "Runtime installed. Enable the plugin, then call check_permissions from the primary MCP."
echo "Grant Accessibility and Screen Recording to CuaDriver.app when macOS asks, then restart ZCode."
