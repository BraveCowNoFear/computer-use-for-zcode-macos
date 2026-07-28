#!/bin/bash
set -euo pipefail

# Foreground Quartz/PyObjC fallback. The primary backend is run-cua-driver.sh.

ROOT="${MACOS_CUA_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}"
DEV_PYTHON="$ROOT/.venv/bin/python3"
DATA_PYTHON="$DATA_DIR/venv/bin/python3"
VERSION_FILE="$DATA_DIR/runtime-version"
RUNTIME_VERSION="0.1.0"

if [[ "$(uname -s)" != "Darwin" ]]; then
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  exec python3 -m macos_cua.server
fi

if [[ -x "$DEV_PYTHON" ]]; then
  PYTHON="$DEV_PYTHON"
else
  mkdir -p "$DATA_DIR"
  if [[ ! -x "$DATA_PYTHON" ]] || [[ ! -f "$VERSION_FILE" ]] || [[ "$(<"$VERSION_FILE")" != "$RUNTIME_VERSION" ]]; then
    LOCK_DIR="$DATA_DIR/install.lock"
    WAITED=0
    until mkdir "$LOCK_DIR" 2>/dev/null; do
      sleep 1
      WAITED=$((WAITED + 1))
      if [[ "$WAITED" -ge 240 ]]; then
        echo "Timed out waiting for the macOS Computer Use dependency installer." >&2
        exit 1
      fi
    done
    cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
    trap cleanup_lock EXIT
    if [[ ! -x "$DATA_PYTHON" ]] || [[ ! -f "$VERSION_FILE" ]] || [[ "$(<"$VERSION_FILE" 2>/dev/null || true)" != "$RUNTIME_VERSION" ]]; then
      echo "Preparing the local macOS Computer Use runtime..." >&2
      command -v python3 >/dev/null 2>&1 || {
        echo "python3 is required. Install Python 3.10+ and re-enable the plugin." >&2
        exit 1
      }
      python3 -m venv "$DATA_DIR/venv"
      "$DATA_PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements.txt" >&2
      printf '%s' "$RUNTIME_VERSION" > "$VERSION_FILE"
    fi
    cleanup_lock
    trap - EXIT
  fi
  PYTHON="$DATA_PYTHON"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m macos_cua.server
