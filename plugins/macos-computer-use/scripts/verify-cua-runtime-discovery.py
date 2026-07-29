#!/usr/bin/env python3
"""Exercise permission-free discovery over the real Cua daemon socket."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"primary runtime discovery verification failed: {message}")


def call(binary: Path, socket: str, name: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "0"
    env["CUA_DRIVER_RS_UPDATE_CHECK"] = "false"
    completed = subprocess.run(
        [str(binary), "call", name, "--socket", socket],
        input="{}",
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
        env=env,
    )
    if completed.returncode != 0:
        fail(f"{name} exited {completed.returncode}: {completed.stderr.strip()}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(f"{name} did not return structured JSON: {error}")
    if not isinstance(result, dict):
        fail(f"{name} returned {type(result).__name__}, not an object")
    return result


def exact_keys(name: str, result: dict[str, Any], expected: set[str]) -> None:
    actual = set(result)
    if actual != expected:
        fail(f"{name} fields drifted: expected {sorted(expected)}, got {sorted(actual)}")


def main() -> int:
    if len(sys.argv) != 3:
        fail("usage: verify-cua-runtime-discovery.py /path/to/cua-driver /path/to/socket")
    binary = Path(sys.argv[1]).resolve()
    socket = sys.argv[2]
    if not binary.is_file():
        fail(f"binary does not exist: {binary}")
    if not socket:
        fail("socket path is empty")

    apps = call(binary, socket, "list_apps")
    exact_keys("list_apps", apps, {"apps"})
    if not isinstance(apps["apps"], list):
        fail("list_apps.apps is not an array")

    windows = call(binary, socket, "list_windows")
    exact_keys("list_windows", windows, {"windows", "current_space_id"})
    if not isinstance(windows["windows"], list):
        fail("list_windows.windows is not an array")
    if windows["current_space_id"] is not None:
        fail("list_windows.current_space_id is no longer null on macOS")

    screen = call(binary, socket, "get_screen_size")
    exact_keys("get_screen_size", screen, {"width", "height", "scale_factor"})
    if type(screen["width"]) is not int or screen["width"] <= 0:
        fail("get_screen_size.width is not a positive integer")
    if type(screen["height"]) is not int or screen["height"] <= 0:
        fail("get_screen_size.height is not a positive integer")
    if not isinstance(screen["scale_factor"], (int, float)) or isinstance(
        screen["scale_factor"], bool
    ) or screen["scale_factor"] <= 0:
        fail("get_screen_size.scale_factor is not positive")

    cursor = call(binary, socket, "get_cursor_position")
    exact_keys("get_cursor_position", cursor, {"x", "y"})
    for coordinate in ("x", "y"):
        if not isinstance(cursor[coordinate], (int, float)) or isinstance(
            cursor[coordinate], bool
        ):
            fail(f"get_cursor_position.{coordinate} is not numeric")

    print("Verified permission-free primary discovery over the live Cua daemon.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
