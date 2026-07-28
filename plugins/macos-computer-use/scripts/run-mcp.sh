#!/bin/bash
set -euo pipefail

# Foreground Quartz/PyObjC fallback. The primary backend is run-cua-driver.sh.

ROOT="${MACOS_CUA_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}"
source "$ROOT/scripts/runtime-common.sh"

DEV_PYTHON="$ROOT/.venv/bin/python3"
RUNTIME_VERSION="$MACOS_CUA_RUNTIME_VERSION"
DATA_VENV="$DATA_DIR/venv-$RUNTIME_VERSION"
DATA_PYTHON="$DATA_VENV/bin/python3"

if [[ "$(uname -s)" != "Darwin" ]]; then
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  exec python3 -m macos_cua.server "$@"
fi

if [[ -x "$DEV_PYTHON" ]]; then
  if ! macos_cua_native_runtime_ready "$DEV_PYTHON" "$ROOT"; then
    echo "The checkout .venv cannot import the required native macOS frameworks; reinstall it." >&2
    exit 1
  fi
  PYTHON="$DEV_PYTHON"
else
  mkdir -p "$DATA_DIR"
  if ! macos_cua_native_runtime_ready "$DATA_PYTHON" "$ROOT"; then
    LOCK_DIR="$DATA_DIR/install.lock"
    acquire_runtime_lock "$LOCK_DIR" "macOS Computer Use dependency installer" 240 30
    STAGING_VENV="$DATA_DIR/.venv-$RUNTIME_VERSION.install.$$"
    cleanup_dependency_install() {
      if [[ -n "${STAGING_VENV:-}" ]] && [[ -d "$STAGING_VENV" ]]; then
        rm -rf -- "$STAGING_VENV"
      fi
      release_runtime_lock "$LOCK_DIR"
    }
    trap cleanup_dependency_install EXIT
    if ! macos_cua_native_runtime_ready "$DATA_PYTHON" "$ROOT"; then
      echo "Preparing the local macOS Computer Use runtime..." >&2
      require_supported_python python3
      rm -rf -- "$STAGING_VENV"
      python3 -m venv "$STAGING_VENV"
      STAGING_PYTHON="$STAGING_VENV/bin/python3"
      require_supported_python "$STAGING_PYTHON"
      "$STAGING_PYTHON" -m pip install --disable-pip-version-check --no-cache-dir --require-hashes --no-deps --only-binary=:all: --quiet -r "$ROOT/requirements.txt" >&2
      PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$STAGING_PYTHON" -m macos_cua.server --self-test >&2
      if [[ -e "$DATA_VENV" ]] || [[ -L "$DATA_VENV" ]]; then
        rm -rf -- "$DATA_VENV"
      fi
      mv "$STAGING_VENV" "$DATA_VENV"
      STAGING_VENV=""
    fi
    cleanup_dependency_install
    trap - EXIT
  fi
  PYTHON="$DATA_PYTHON"
  if ! macos_cua_native_runtime_ready "$PYTHON" "$ROOT"; then
    echo "The published direct fallback runtime failed its native import check." >&2
    exit 1
  fi
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m macos_cua.server "$@"
