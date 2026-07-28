#!/bin/bash

# Shared crash-safe runtime locks. A directory is used so acquisition stays
# atomic on macOS without requiring flock. The PID marker lets a later ZCode
# process recover a lock left behind by a killed installer.

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

runtime_lock_mtime() {
  local lock_dir="$1"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    /usr/bin/stat -f '%m' "$lock_dir" 2>/dev/null || printf '0\n'
  else
    stat -c '%Y' "$lock_dir" 2>/dev/null || printf '0\n'
  fi
}

acquire_runtime_lock() {
  local lock_dir="$1"
  local label="$2"
  local timeout_seconds="${3:-240}"
  local orphan_grace_seconds="${4:-5}"
  local waited=0

  mkdir -p "$(dirname "$lock_dir")"
  while ! mkdir "$lock_dir" 2>/dev/null; do
    local owner=""
    local now
    local modified
    local age
    if [[ -f "$lock_dir/pid" ]]; then
      IFS= read -r owner < "$lock_dir/pid" || owner=""
    fi
    now="$(date +%s)"
    modified="$(runtime_lock_mtime "$lock_dir")"
    age=$((now - modified))

    if { [[ ! "$owner" =~ ^[0-9]+$ ]] || ! kill -0 "$owner" 2>/dev/null; } \
      && [[ "$age" -ge "$orphan_grace_seconds" ]]; then
      rm -f -- "$lock_dir/pid" "$lock_dir/created"
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

  printf '%s\n' "$$" > "$lock_dir/pid"
  date +%s > "$lock_dir/created"
}

release_runtime_lock() {
  local lock_dir="$1"
  local owner=""
  if [[ -f "$lock_dir/pid" ]]; then
    IFS= read -r owner < "$lock_dir/pid" || owner=""
  fi
  if [[ "$owner" == "$$" ]]; then
    rm -f -- "$lock_dir/pid" "$lock_dir/created"
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
