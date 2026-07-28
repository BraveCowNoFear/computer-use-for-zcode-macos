#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/runtime-common.sh"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must run on macOS." >&2
  exit 1
fi
echo "Preparing the background Cua Driver backend..."
MACOS_CUA_PLUGIN_ROOT="$ROOT" \
MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
  "$ROOT/scripts/run-cua-driver.sh" --prepare-only

echo "Preparing the direct Quartz/PyObjC fallback..."
MACOS_CUA_PLUGIN_ROOT="$ROOT" \
MACOS_CUA_DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}" \
  "$ROOT/scripts/run-mcp.sh" --self-test

echo
echo "Runtime installed. Enable the plugin, then call check_permissions from the primary MCP."
echo "Grant Accessibility and Screen Recording to the plugin-owned CuaDriver.app when macOS asks, then restart ZCode."
