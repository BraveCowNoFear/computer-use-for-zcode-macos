#!/bin/bash

# Shared crash-safe runtime locks. A directory is used so acquisition stays
# atomic on macOS without requiring flock. The PID marker lets a later ZCode
# process recover a lock left behind by a killed installer.

MACOS_CUA_RUNTIME_VERSION="0.16.0"
MACOS_CUA_DEPENDENCY_ID="pyobjc-12.2.1-f76ce5003027"

python_is_supported() {
  local python="$1"
  [[ -x "$(command -v "$python" 2>/dev/null || true)" ]] || return 1
  "$python" -c 'import platform, sys; raise SystemExit(0 if platform.python_implementation() == "CPython" and (3, 10) <= sys.version_info < (3, 16) else 1)' \
    >/dev/null 2>&1
}

require_supported_python() {
  local python="$1"
  if python_is_supported "$python"; then
    return 0
  fi
  local detected="not found"
  if command -v "$python" >/dev/null 2>&1; then
    detected="$("$python" -c 'import platform, sys; print(f"{platform.python_implementation()} {sys.version.split()[0]}")' 2>/dev/null || printf 'unreadable')"
  fi
  echo "The direct fallback requires CPython 3.10 through 3.15; $python is $detected." >&2
  return 1
}

macos_cua_native_runtime_ready() {
  local python="$1"
  local plugin_root="$2"
  python_is_supported "$python" || return 1
  PYTHONPATH="$plugin_root${PYTHONPATH:+:$PYTHONPATH}" "$python" -c \
    'import AppKit, ApplicationServices, Quartz; from macos_cua.macos import MacOSBackend; b=MacOSBackend(); raise SystemExit(0 if b.native_error is None else 1)' \
    >/dev/null 2>&1
}

runtime_lock_mtime() {
  local lock_dir="$1"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    /usr/bin/stat -f '%m' "$lock_dir" 2>/dev/null || printf '0\n'
  else
    stat -c '%Y' "$lock_dir" 2>/dev/null || printf '0\n'
  fi
}

runtime_process_start() {
  local pid="$1"
  /bin/ps -o lstart= -p "$pid" 2>/dev/null | /usr/bin/awk '{$1=$1; print}'
}

acquire_runtime_lock() {
  local lock_dir="$1"
  local label="$2"
  local timeout_seconds="${3:-240}"
  local orphan_grace_seconds="${4:-30}"
  local waited=0

  mkdir -p "$(dirname "$lock_dir")"
  while ! mkdir "$lock_dir" 2>/dev/null; do
    local owner=""
    local now
    local modified
    local age
    local recorded_start=""
    local actual_start=""
    if [[ -f "$lock_dir/pid" ]]; then
      IFS= read -r owner < "$lock_dir/pid" || owner=""
    fi
    if [[ -f "$lock_dir/started" ]]; then
      IFS= read -r recorded_start < "$lock_dir/started" || recorded_start=""
    fi
    if [[ "$owner" =~ ^[0-9]+$ ]]; then
      actual_start="$(runtime_process_start "$owner")"
    fi
    now="$(date +%s)"
    modified="$(runtime_lock_mtime "$lock_dir")"
    age=$((now - modified))

    if { [[ ! "$owner" =~ ^[0-9]+$ ]] || ! kill -0 "$owner" 2>/dev/null \
      || [[ -z "$recorded_start" ]] || [[ "$recorded_start" != "$actual_start" ]]; } \
      && [[ "$age" -ge "$orphan_grace_seconds" ]]; then
      rm -f -- "$lock_dir/pid" "$lock_dir/started" "$lock_dir/token" "$lock_dir/created"
      if rmdir "$lock_dir" 2>/dev/null; then
        continue
      fi
    fi

    if [[ "$waited" -ge "$timeout_seconds" ]]; then
      echo "Timed out waiting for the $label lock at $lock_dir." >&2
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done

  RUNTIME_LOCK_TOKEN="$$-$(date +%s)-${RANDOM:-0}"
  export RUNTIME_LOCK_TOKEN
  printf '%s\n' "$$" > "$lock_dir/pid"
  runtime_process_start "$$" > "$lock_dir/started"
  printf '%s\n' "$RUNTIME_LOCK_TOKEN" > "$lock_dir/token"
  date +%s > "$lock_dir/created"
}

release_runtime_lock() {
  local lock_dir="$1"
  local owner=""
  local recorded_start=""
  local token=""
  if [[ -f "$lock_dir/pid" ]]; then
    IFS= read -r owner < "$lock_dir/pid" || owner=""
  fi
  if [[ -f "$lock_dir/started" ]]; then
    IFS= read -r recorded_start < "$lock_dir/started" || recorded_start=""
  fi
  if [[ -f "$lock_dir/token" ]]; then
    IFS= read -r token < "$lock_dir/token" || token=""
  fi
  if [[ "$owner" == "$$" ]] && [[ "$recorded_start" == "$(runtime_process_start "$$")" ]] \
    && [[ -n "${RUNTIME_LOCK_TOKEN:-}" ]] && [[ "$token" == "$RUNTIME_LOCK_TOKEN" ]]; then
    rm -f -- "$lock_dir/pid" "$lock_dir/started" "$lock_dir/token" "$lock_dir/created"
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}

driver_reports_unrestricted() {
  local binary="$1"
  local socket="$2"
  local status
  status="$("$binary" status --socket "$socket" 2>/dev/null)" || return 1
  grep -Fq "permission mode: unrestricted" <<< "$status" || return 1
  grep -Fq "user policy: configured=false, active=false, valid=true" <<< "$status" || return 1
  grep -Fq "managed policy: configured=false, active=false, valid=true" <<< "$status" || return 1
  grep -Fq "session policy: configured=false, approved_at_startup=false, valid=true" <<< "$status"
}
