#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must run on macOS." >&2
  exit 1
fi
command -v python3 >/dev/null 2>&1 || {
  echo "python3 3.10+ is required." >&2
  exit 1
}

echo "Preparing the background Cua Driver backend..."
MACOS_CUA_PLUGIN_ROOT="$ROOT" \
MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
  "$ROOT/scripts/run-cua-driver.sh" --prepare-only

echo "Preparing the direct Quartz/PyObjC fallback..."
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python3" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt"
export PYTHONPATH="$ROOT"
"$ROOT/.venv/bin/python3" -m macos_cua.server --self-test

echo
echo "Runtime installed. Enable the plugin, then call check_permissions from the primary MCP."
echo "Grant Accessibility and Screen Recording to CuaDriver.app when macOS asks, then restart ZCode."
