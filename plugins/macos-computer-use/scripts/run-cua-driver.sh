#!/bin/bash
set -euo pipefail

# Primary ZCode MCP launcher. It installs the signed CuaDriver.app once, starts
# a plugin-owned unrestricted daemon, then exposes its native MCP surface.

ROOT="${MACOS_CUA_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}"
CUA_VERSION="0.12.6"
CUA_TAG="cua-driver-rs-v${CUA_VERSION}"
INSTALLER_URL="https://raw.githubusercontent.com/trycua/cua/${CUA_TAG}/libs/cua-driver/scripts/_install-rust.sh"
INSTALLER_SHA256="351878e9d7ac1b915b77572ba906102be10a6d93293073ad3e98544817984069"
BIN_DIR="$DATA_DIR/cua-driver-bin"
PLUGIN_BIN="$BIN_DIR/cua-driver"
INSTALLER="$DATA_DIR/installers/cua-driver-${CUA_VERSION}.sh"
LOCK_DIR="$DATA_DIR/cua-driver-install.lock"
SOCKET="/tmp/zcode-cua-${UID}.sock"

export CUA_DRIVER_RS_TELEMETRY_ENABLED=0
export CUA_TELEMETRY_ENABLED=0

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The background Cua Driver backend requires macOS. The fallback MCP remains available." >&2
  exit 1
fi

has_required_surface() {
  local candidate="$1"
  [[ -x "$candidate" ]] || return 1
  local tools
  tools="$($candidate list-tools 2>/dev/null)" || return 1
  for required in start_session get_window_state click type_text end_session; do
    [[ "$tools" == *"$required"* ]] || return 1
  done
}

resolve_existing_binary() {
  local candidate
  for candidate in \
    "${CUA_DRIVER_BIN:-}" \
    "$PLUGIN_BIN" \
    "/Applications/CuaDriver.app/Contents/MacOS/cua-driver" \
    "$(command -v cua-driver 2>/dev/null || true)"; do
    if [[ -n "$candidate" ]] && has_required_surface "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

verify_installer() {
  local actual
  actual="$(/usr/bin/shasum -a 256 "$INSTALLER" | /usr/bin/awk '{print $1}')"
  if [[ "$actual" != "$INSTALLER_SHA256" ]]; then
    echo "Refusing to run the Cua Driver installer: SHA-256 mismatch." >&2
    echo "Expected $INSTALLER_SHA256 but received $actual." >&2
    exit 1
  fi
}

install_driver() {
  mkdir -p "$DATA_DIR/installers" "$BIN_DIR"
  local waited=0
  until mkdir "$LOCK_DIR" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if [[ "$waited" -ge 240 ]]; then
      echo "Timed out waiting for the Cua Driver installer lock." >&2
      exit 1
    fi
  done
  cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
  trap cleanup_lock EXIT

  if ! resolve_existing_binary >/dev/null; then
    echo "Installing signed Cua Driver ${CUA_VERSION} for background macOS control..." >&2
    command -v curl >/dev/null 2>&1 || {
      echo "curl is required for the one-time Cua Driver download." >&2
      exit 1
    }
    curl -fsSL "$INSTALLER_URL" -o "$INSTALLER"
    verify_installer
    CUA_DRIVER_RS_VERSION="$CUA_VERSION" \
    CUA_DRIVER_RS_INSTALL_DIR="$BIN_DIR" \
    CUA_DRIVER_RS_NO_MODIFY_PATH=1 \
      /bin/bash "$INSTALLER" --bin-dir "$BIN_DIR" --no-modify-path >&2
  fi

  cleanup_lock
  trap - EXIT
}

BIN="$(resolve_existing_binary || true)"
if [[ -z "$BIN" ]]; then
  install_driver
  BIN="$(resolve_existing_binary || true)"
fi
if [[ -z "$BIN" ]]; then
  echo "Cua Driver did not expose the required tool surface after installation." >&2
  echo "Use the macos-computer-use-fallback tools or run scripts/doctor.sh." >&2
  exit 1
fi

# Persist the opt-out as well as setting it in this process, so a LaunchServices
# daemon cannot re-enable content-free upstream telemetry.
"$BIN" telemetry disable >/dev/null 2>&1 || true

if [[ "${1:-}" == "--prepare-only" ]]; then
  echo "$BIN"
  "$BIN" --version
  exit 0
fi

# A dedicated short socket keeps this daemon independent from any Cua Driver a
# user already runs. Permission mode is immutable for the daemon lifetime.
if ! "$BIN" status --socket "$SOCKET" >/dev/null 2>&1; then
  rm -f "$SOCKET"
  /usr/bin/open -n -g -a CuaDriver --args \
    serve \
    --socket "$SOCKET" \
    --permission-mode unrestricted \
    --dangerously-bypass-approvals

  ready=0
  attempt=0
  while [[ "$attempt" -lt 150 ]]; do
    if "$BIN" status --socket "$SOCKET" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 0.2
    attempt=$((attempt + 1))
  done
  if [[ "$ready" != "1" ]]; then
    echo "Cua Driver did not become ready within 30 seconds." >&2
    echo "Grant Accessibility and Screen Recording to CuaDriver.app, then restart ZCode." >&2
    exit 1
  fi
fi

exec "$BIN" mcp --socket "$SOCKET"
