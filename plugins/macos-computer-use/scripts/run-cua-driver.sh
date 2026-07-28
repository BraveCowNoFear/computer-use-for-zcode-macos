#!/bin/bash
set -euo pipefail

# Primary ZCode MCP launcher. It installs the signed CuaDriver.app once, starts
# a plugin-owned unrestricted daemon, then exposes its native MCP surface.

ROOT="${MACOS_CUA_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${MACOS_CUA_DATA_DIR:-$ROOT/.local-data}"
source "$ROOT/scripts/runtime-common.sh"

CUA_VERSION="0.12.6"
CUA_TAG="cua-driver-rs-v${CUA_VERSION}"
ASSET_NAME="cua-driver-rs-${CUA_VERSION}-darwin-universal.tar.gz"
ASSET_URL="https://github.com/trycua/cua/releases/download/${CUA_TAG}/${ASSET_NAME}"
ASSET_SHA256="c86d6a9ccb074e6e3bc17292adc31b9c76933c646cb2b52a7d8813429a5a6e6f"
EXPECTED_TEAM_ID="YCK386LBJ7"
EXPECTED_AUTHORITY="Developer ID Application: Cua AI, Inc. (YCK386LBJ7)"
APP_PARENT="$DATA_DIR/cua-driver-app"
APP_ROOT="$APP_PARENT/v${CUA_VERSION}"
APP_BUNDLE="$APP_ROOT/CuaDriver.app"
APP_BIN="$APP_BUNDLE/Contents/MacOS/cua-driver"
ASSET="$DATA_DIR/installers/$ASSET_NAME"
LOCK_DIR="$DATA_DIR/cua-driver-install.lock"
SOCKET_DIR="/tmp/zcode-cua-${UID}"
SOCKET="$SOCKET_DIR/v${CUA_VERSION}.sock"
START_LOCK="$SOCKET_DIR/v${CUA_VERSION}.start.lock"
TELEMETRY_HOME="$DATA_DIR/cua-telemetry"

export CUA_DRIVER_RS_TELEMETRY_ENABLED=0
export CUA_TELEMETRY_ENABLED=0
export CUA_DRIVER_RS_UPDATE_CHECK=false
export CUA_DRIVER_TELEMETRY_HOME="$TELEMETRY_HOME"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The background Cua Driver backend requires macOS. The fallback MCP remains available." >&2
  exit 1
fi

if [[ -L "$TELEMETRY_HOME" ]] || { [[ -e "$TELEMETRY_HOME" ]] && [[ ! -d "$TELEMETRY_HOME" ]]; }; then
  echo "Refusing unsafe plugin telemetry directory: $TELEMETRY_HOME" >&2
  exit 1
fi
mkdir -p "$TELEMETRY_HOME"
telemetry_owner="$(/usr/bin/stat -f '%u' "$TELEMETRY_HOME" 2>/dev/null || true)"
if [[ "$telemetry_owner" != "$UID" ]]; then
  echo "Refusing plugin telemetry directory not owned by uid $UID: $TELEMETRY_HOME" >&2
  exit 1
fi
chmod 700 "$TELEMETRY_HOME"

has_required_surface() {
  local candidate="$1"
  local bundle="${2:-$APP_BUNDLE}"
  [[ -x "$candidate" ]] || return 1
  [[ "$candidate" -ef "$bundle/Contents/MacOS/cua-driver" ]] || return 1
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$bundle/Contents/Info.plist" 2>/dev/null || true)" == "com.trycua.driver" ]] || return 1
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$bundle/Contents/Info.plist" 2>/dev/null || true)" == "$CUA_VERSION" ]] || return 1
  /usr/bin/codesign --verify --deep --strict "$bundle" >/dev/null 2>&1 || return 1
  /usr/sbin/spctl --assess --type execute "$bundle" >/dev/null 2>&1 || return 1
  local signing_info
  signing_info="$(/usr/bin/codesign -dv --verbose=4 "$bundle" 2>&1)" || return 1
  grep -Fxq "TeamIdentifier=$EXPECTED_TEAM_ID" <<< "$signing_info" || return 1
  grep -Fxq "Authority=$EXPECTED_AUTHORITY" <<< "$signing_info" || return 1
  local version
  version="$($candidate --version 2>/dev/null | tail -n 1)" || return 1
  version="${version##* }"
  [[ "$version" == "$CUA_VERSION" ]] || return 1
  local tools
  tools="$($candidate list-tools 2>/dev/null)" || return 1
  for required in \
    check_permissions start_session end_session list_apps list_windows \
    launch_app get_window_state get_desktop_state click press_key hotkey \
    type_text scroll set_value drag; do
    grep -Eq "^${required}(:|$)" <<< "$tools" || return 1
  done
}

resolve_existing_binary() {
  local candidate
  for candidate in \
    "${CUA_DRIVER_BIN:-}" \
    "$APP_BIN"; do
    # The daemon is launched through the signed app for correct TCC
    # attribution. Never pair it with an unrelated CLI binary.
    if [[ -n "$candidate" ]] && has_required_surface "$candidate" "$APP_BUNDLE"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

verify_sha256() {
  local path="$1" expected="$2" label="$3" actual
  actual="$(/usr/bin/shasum -a 256 "$path" | /usr/bin/awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    echo "Refusing to use the Cua Driver $label: SHA-256 mismatch." >&2
    echo "Expected $expected but received $actual." >&2
    return 1
  fi
}

install_driver() {
  mkdir -p "$DATA_DIR/installers" "$APP_PARENT"
  acquire_runtime_lock "$LOCK_DIR" "Cua Driver installer" 240 30
  local staging="$APP_PARENT/.v${CUA_VERSION}.install.$$"
  local download="$ASSET.download.$$"
  cleanup_driver_install() {
    rm -rf -- "$staging"
    rm -f -- "$download"
    release_runtime_lock "$LOCK_DIR"
  }
  trap cleanup_driver_install EXIT

  if ! resolve_existing_binary >/dev/null; then
    echo "Installing signed Cua Driver ${CUA_VERSION} in the plugin data directory..." >&2
    if [[ -f "$ASSET" ]] && ! verify_sha256 "$ASSET" "$ASSET_SHA256" "cached release archive"; then
      echo "Discarding the incomplete or corrupted cached release archive." >&2
      rm -f -- "$ASSET"
    fi
    if [[ ! -f "$ASSET" ]]; then
      /usr/bin/curl -fsSL "$ASSET_URL" -o "$download"
      verify_sha256 "$download" "$ASSET_SHA256" "downloaded release archive"
      mv "$download" "$ASSET"
    fi
    verify_sha256 "$ASSET" "$ASSET_SHA256" "release archive"
    rm -rf -- "$staging"
    mkdir -p "$staging"
    /usr/bin/tar -xzf "$ASSET" -C "$staging"
    local extracted="$staging/cua-driver-rs-${CUA_VERSION}-darwin-universal"
    local extracted_app="$extracted/CuaDriver.app"
    local extracted_bin="$extracted_app/Contents/MacOS/cua-driver"
    if ! has_required_surface "$extracted_bin" "$extracted_app"; then
      echo "Refusing the Cua Driver release: signer identity or required surface mismatch." >&2
      exit 1
    fi
    rm -rf -- "$APP_ROOT"
    mv "$extracted" "$APP_ROOT"
  fi

  cleanup_driver_install
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

# Persist and prove the opt-out inside plugin data as well as setting it in this
# process. Never change the user's ~/.cua-driver preference for unrelated Cua
# installations, and never let LaunchServices re-enable upstream telemetry.
if ! "$BIN" telemetry disable >/dev/null 2>&1; then
  echo "Cua Driver could not persist its plugin-private telemetry opt-out; refusing the primary backend." >&2
  exit 1
fi
telemetry_status="$(/usr/bin/env -u CUA_DRIVER_RS_TELEMETRY_ENABLED -u CUA_TELEMETRY_ENABLED "$BIN" telemetry status --json 2>/dev/null || true)"
grep -Eq '"enabled"[[:space:]]*:[[:space:]]*false' <<< "$telemetry_status" || {
  echo "Cua Driver telemetry did not remain disabled without the environment override." >&2
  exit 1
}
grep -Eq '"source"[[:space:]]*:[[:space:]]*"persisted"' <<< "$telemetry_status" || {
  echo "Cua Driver did not report the persisted telemetry preference." >&2
  exit 1
}

if [[ "${1:-}" == "--prepare-only" ]]; then
  echo "$BIN"
  "$BIN" --version
  exit 0
fi

# A dedicated short socket keeps this daemon independent from any Cua Driver a
# user already runs. Permission mode is immutable for the daemon lifetime.
if [[ -L "$SOCKET_DIR" ]] || { [[ -e "$SOCKET_DIR" ]] && [[ ! -d "$SOCKET_DIR" ]]; }; then
  echo "Refusing unsafe Cua Driver socket directory: $SOCKET_DIR" >&2
  exit 1
fi
mkdir -p "$SOCKET_DIR"
socket_owner="$(/usr/bin/stat -f '%u' "$SOCKET_DIR" 2>/dev/null || true)"
if [[ "$socket_owner" != "$UID" ]]; then
  echo "Refusing Cua Driver socket directory not owned by uid $UID: $SOCKET_DIR" >&2
  exit 1
fi
chmod 700 "$SOCKET_DIR"

daemon_is_verified() {
  driver_reports_unrestricted "$BIN" "$SOCKET"
}

stop_plugin_daemon_bounded() {
  local stop_pid attempt=0
  "$BIN" stop --socket "$SOCKET" >/dev/null 2>&1 &
  stop_pid=$!
  while kill -0 "$stop_pid" 2>/dev/null && [[ "$attempt" -lt 20 ]]; do
    sleep 0.1
    attempt=$((attempt + 1))
  done
  if kill -0 "$stop_pid" 2>/dev/null; then
    kill "$stop_pid" 2>/dev/null || true
    sleep 0.1
    kill -9 "$stop_pid" 2>/dev/null || true
  fi
  wait "$stop_pid" 2>/dev/null || true
  rm -f -- "$SOCKET"
}

acquire_runtime_lock "$START_LOCK" "Cua Driver startup" 60 30
trap 'release_runtime_lock "$START_LOCK"' EXIT

if ! daemon_is_verified; then
  if "$BIN" status --socket "$SOCKET" >/dev/null 2>&1; then
    stop_plugin_daemon_bounded
  fi
  rm -f -- "$SOCKET"
  # This is a plugin-owned full-access daemon. Do not silently inherit a Cua
  # policy ceiling from another tool or shell. The status gate below proves
  # that no user, managed, or bounded-session policy remained active.
  /usr/bin/env \
    -u CUA_DRIVER_POLICY_FILE \
    -u CUA_DRIVER_MANAGED_POLICY_FILE \
    -u CUA_DRIVER_DISABLE_UNRESTRICTED \
    -u CUA_DRIVER_ALLOW_LEGACY_EXISTING_PROFILE_APPROVAL \
    -u CUA_DRIVER_SESSION_POLICY_FILE \
    -u CUA_DRIVER_SESSION_POLICY_APPROVED \
    -u CUA_DRIVER_PERMISSION_MODE \
    -u CUA_DRIVER_DANGEROUSLY_BYPASS_APPROVALS \
    /usr/bin/open -n -g \
    --env CUA_DRIVER_RS_TELEMETRY_ENABLED=0 \
    --env CUA_TELEMETRY_ENABLED=0 \
    --env CUA_DRIVER_RS_UPDATE_CHECK=false \
    --env "CUA_DRIVER_TELEMETRY_HOME=$TELEMETRY_HOME" \
    "$APP_BUNDLE" --args \
    serve \
    --socket "$SOCKET" \
    --permission-mode unrestricted \
    --dangerously-bypass-approvals

  ready=0
  attempt=0
  while [[ "$attempt" -lt 150 ]]; do
    if daemon_is_verified; then
      ready=1
      break
    fi
    if "$BIN" status --socket "$SOCKET" >/dev/null 2>&1; then
      ready=2
      break
    fi
    sleep 0.2
    attempt=$((attempt + 1))
  done
  if [[ "$ready" != "1" ]]; then
    if [[ "$ready" == "2" ]]; then
      echo "Cua Driver started, but it is not the policy-free unrestricted daemon this plugin requires:" >&2
      "$BIN" status --socket "$SOCKET" >&2 || true
      echo "The plugin will not mislabel a policy-constrained daemon as full access." >&2
    else
      echo "Cua Driver did not become ready within 30 seconds." >&2
      echo "Grant Accessibility and Screen Recording to CuaDriver.app, then restart ZCode." >&2
    fi
    stop_plugin_daemon_bounded
    exit 1
  fi
fi

release_runtime_lock "$START_LOCK"
trap - EXIT

if [[ "${1:-}" == "--verify-runtime" ]]; then
  "$BIN" --version
  "$BIN" status --socket "$SOCKET"
  exit 0
fi

exec "$BIN" mcp --socket "$SOCKET"
