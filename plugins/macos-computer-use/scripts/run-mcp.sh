#!/bin/bash
set -euo pipefail

# Foreground Quartz/PyObjC fallback. The primary backend is run-cua-driver.sh.

ROOT="${MACOS_CUA_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}"
source "$ROOT/scripts/runtime-common.sh"

DEV_PYTHON="$ROOT/.venv/bin/python3"
DATA_PYTHON="$DATA_DIR/venv/bin/python3"
VERSION_FILE="$DATA_DIR/runtime-version"
RUNTIME_VERSION="0.3.0"

if [[ "$(uname -s)" != "Darwin" ]]; then
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  exec python3 -m macos_cua.server
fi

if [[ -x "$DEV_PYTHON" ]]; then
  require_supported_python "$DEV_PYTHON"
  PYTHON="$DEV_PYTHON"
else
  mkdir -p "$DATA_DIR"
  if [[ ! -x "$DATA_PYTHON" ]] || [[ ! -f "$VERSION_FILE" ]] || [[ "$(<"$VERSION_FILE")" != "$RUNTIME_VERSION" ]]; then
    LOCK_DIR="$DATA_DIR/install.lock"
    acquire_runtime_lock "$LOCK_DIR" "macOS Computer Use dependency installer" 240 5
    trap 'release_runtime_lock "$LOCK_DIR"' EXIT
    if [[ ! -x "$DATA_PYTHON" ]] || [[ ! -f "$VERSION_FILE" ]] || [[ "$(<"$VERSION_FILE" 2>/dev/null || true)" != "$RUNTIME_VERSION" ]]; then
      echo "Preparing the local macOS Computer Use runtime..." >&2
      require_supported_python python3
      python3 -m venv "$DATA_DIR/venv"
      require_supported_python "$DATA_PYTHON"
      "$DATA_PYTHON" -m pip install --disable-pip-version-check --only-binary=:all: --quiet -r "$ROOT/requirements.txt" >&2
      printf '%s' "$RUNTIME_VERSION" > "$VERSION_FILE"
    fi
    release_runtime_lock "$LOCK_DIR"
    trap - EXIT
  fi
  PYTHON="$DATA_PYTHON"
  require_supported_python "$PYTHON"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m macos_cua.server
