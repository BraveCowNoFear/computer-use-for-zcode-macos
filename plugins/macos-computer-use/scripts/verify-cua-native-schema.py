#!/usr/bin/env python3
"""Fail closed when pinned Cua Driver native request schemas drift."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


EXPECTED_VERSION = "0.13.1"

CONTRACTS: dict[str, dict[str, Any]] = {
    "check_for_update": {
        "additional_properties": False,
        "properties": set(),
        "required": set(),
    },
    "health_report": {
        "additional_properties": False,
        "properties": {"include", "skip"},
        "required": set(),
        "types": {"include": "array", "skip": "array"},
        "item_types": {"include": "string", "skip": "string"},
    },
    "check_permissions": {
        "additional_properties": False,
        "properties": {"prompt", "probe_direct_capture"},
        "required": set(),
        "types": {"prompt": "boolean", "probe_direct_capture": "boolean"},
        "defaults": {"prompt": False},
    },
    "get_accessibility_tree": {
        "additional_properties": False,
        "properties": set(),
        "required": set(),
    },
    "get_window_state": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "window_id",
            "query",
            "capture_mode",
            "include_screenshot",
            "screenshot_out_file",
            "max_elements",
            "max_depth",
        },
        "required": {"pid", "window_id"},
        "types": {
            "session": "string",
            "pid": "integer",
            "window_id": "integer",
            "query": "string",
            "capture_mode": "string",
            "include_screenshot": "boolean",
            "screenshot_out_file": "string",
            "max_elements": "integer",
            "max_depth": "integer",
        },
        "enums": {"capture_mode": {"ax", "vision"}},
        "limits": {
            "max_elements": {"minimum": 1},
            "max_depth": {"minimum": 1},
        },
    },
    "get_desktop_state": {
        "additional_properties": False,
        "properties": {"session", "screenshot_out_file"},
        "required": set(),
        "types": {"session": "string", "screenshot_out_file": "string"},
    },
    "get_screen_size": {
        "additional_properties": False,
        "properties": {"session"},
        "required": set(),
        "types": {"session": "string"},
    },
    "get_cursor_position": {
        "additional_properties": False,
        "properties": {"session"},
        "required": set(),
        "types": {"session": "string"},
    },
    "move_cursor": {
        "additional_properties": False,
        "properties": {"session", "x", "y", "scope", "cursor_id"},
        "required": {"x", "y"},
        "types": {
            "session": "string",
            "x": "number",
            "y": "number",
            "scope": "string",
            "cursor_id": "string",
        },
        "enums": {"scope": {"window", "desktop"}},
        "defaults": {"scope": "window"},
    },
    "click": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "window_id",
            "element_index",
            "element_token",
            "x",
            "y",
            "action",
            "button",
            "count",
            "modifier",
            "from_zoom",
            "debug_image_out",
            "delivery_mode",
            "scope",
        },
        "required": set(),
        "types": {
            "session": "string",
            "pid": "integer",
            "window_id": "integer",
            "element_index": "integer",
            "element_token": "string",
            "x": "number",
            "y": "number",
            "action": "string",
            "button": "string",
            "count": "integer",
            "modifier": "array",
            "from_zoom": "boolean",
            "debug_image_out": "string",
            "delivery_mode": "string",
            "scope": "string",
        },
        "item_types": {"modifier": "string"},
        "enums": {
            "button": {"left", "right", "middle"},
            "delivery_mode": {"background", "foreground"},
            "scope": {"window", "desktop"},
        },
    },
    "double_click": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "x",
            "y",
            "window_id",
            "element_index",
            "element_token",
            "delivery_mode",
        },
        "required": {"pid"},
        "types": {
            "session": "string",
            "pid": "integer",
            "x": "number",
            "y": "number",
            "window_id": "integer",
            "element_index": "integer",
            "element_token": "string",
            "delivery_mode": "string",
        },
        "enums": {"delivery_mode": {"background", "foreground"}},
    },
    "right_click": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "element_index",
            "element_token",
            "window_id",
            "x",
            "y",
            "modifier",
            "delivery_mode",
        },
        "required": {"pid"},
        "types": {
            "session": "string",
            "pid": "integer",
            "element_index": "integer",
            "element_token": "string",
            "window_id": "integer",
            "x": "number",
            "y": "number",
            "modifier": "array",
            "delivery_mode": "string",
        },
        "item_types": {"modifier": "string"},
        "enums": {"delivery_mode": {"background", "foreground"}},
    },
    "drag": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "window_id",
            "from_x",
            "from_y",
            "to_x",
            "to_y",
            "duration_ms",
            "steps",
            "modifier",
            "button",
            "from_zoom",
            "scope",
            "delivery_mode",
        },
        "required": {"from_x", "from_y", "to_x", "to_y"},
        "types": {
            "session": "string",
            "pid": "integer",
            "window_id": "integer",
            "from_x": "number",
            "from_y": "number",
            "to_x": "number",
            "to_y": "number",
            "duration_ms": "integer",
            "steps": "integer",
            "modifier": "array",
            "button": "string",
            "from_zoom": "boolean",
            "scope": "string",
            "delivery_mode": "string",
        },
        "item_types": {"modifier": "string"},
        "enums": {
            "button": {"left", "right", "middle"},
            "scope": {"window", "desktop"},
            "delivery_mode": {"background", "foreground"},
        },
        "defaults": {"scope": "window"},
        "limits": {
            "duration_ms": {"minimum": 0, "maximum": 10000},
            "steps": {"minimum": 1, "maximum": 200},
        },
    },
    "scroll": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "direction",
            "by",
            "amount",
            "window_id",
            "element_index",
            "element_token",
            "x",
            "y",
            "scope",
            "delivery_mode",
        },
        "required": {"direction"},
        "types": {
            "session": "string",
            "pid": "integer",
            "direction": "string",
            "by": "string",
            "amount": "integer",
            "window_id": "integer",
            "element_index": "integer",
            "element_token": "string",
            "x": "number",
            "y": "number",
            "scope": "string",
            "delivery_mode": "string",
        },
        "enums": {
            "direction": {"up", "down", "left", "right"},
            "by": {"line", "page"},
            "scope": {"window", "desktop"},
            "delivery_mode": {"background", "foreground"},
        },
        "defaults": {"scope": "window"},
        "limits": {"amount": {"minimum": 1, "maximum": 50}},
    },
    "type_text": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "text",
            "window_id",
            "element_index",
            "element_token",
            "x",
            "y",
            "delay_ms",
            "scope",
            "delivery_mode",
        },
        "required": {"text"},
        "types": {
            "session": "string",
            "pid": "integer",
            "text": "string",
            "window_id": "integer",
            "element_index": "integer",
            "element_token": "string",
            "x": "number",
            "y": "number",
            "delay_ms": "integer",
            "scope": "string",
            "delivery_mode": "string",
        },
        "enums": {
            "scope": {"window", "desktop"},
            "delivery_mode": {"background", "foreground"},
        },
        "defaults": {"scope": "window"},
        "limits": {"delay_ms": {"minimum": 0, "maximum": 200}},
    },
    "press_key": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "key",
            "modifiers",
            "window_id",
            "element_index",
            "element_token",
            "x",
            "y",
            "scope",
            "delivery_mode",
        },
        "required": {"key"},
        "types": {
            "session": "string",
            "pid": "integer",
            "key": "string",
            "modifiers": "array",
            "window_id": "integer",
            "element_index": "integer",
            "element_token": "string",
            "x": "number",
            "y": "number",
            "scope": "string",
            "delivery_mode": "string",
        },
        "item_types": {"modifiers": "string"},
        "enums": {
            "scope": {"window", "desktop"},
            "delivery_mode": {"background", "foreground"},
        },
        "defaults": {"scope": "window"},
    },
    "hotkey": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "keys",
            "x",
            "y",
            "window_id",
            "scope",
            "delivery_mode",
        },
        "required": {"keys"},
        "types": {
            "session": "string",
            "pid": "integer",
            "keys": "array",
            "x": "number",
            "y": "number",
            "window_id": "integer",
            "scope": "string",
            "delivery_mode": "string",
        },
        "item_types": {"keys": "string"},
        "enums": {
            "scope": {"window", "desktop"},
            "delivery_mode": {"background", "foreground"},
        },
        "defaults": {"scope": "window"},
        "limits": {"keys": {"minItems": 2}},
    },
    "set_value": {
        "additional_properties": False,
        "properties": {
            "session",
            "pid",
            "window_id",
            "element_index",
            "element_token",
            "value",
        },
        "required": {"pid", "value"},
        "types": {
            "session": "string",
            "pid": "integer",
            "window_id": "integer",
            "element_index": "integer",
            "element_token": "string",
            "value": "string",
        },
    },
    "zoom": {
        "additional_properties": False,
        "properties": {"window_id", "pid", "x1", "y1", "x2", "y2"},
        "required": {"window_id", "x1", "y1", "x2", "y2"},
        "types": {
            "window_id": "integer",
            "pid": "integer",
            "x1": "number",
            "y1": "number",
            "x2": "number",
            "y2": "number",
        },
    },
    "list_apps": {
        "additional_properties": False,
        "properties": set(),
        "required": set(),
    },
    "list_windows": {
        "additional_properties": False,
        "properties": {"pid", "on_screen_only"},
        "required": set(),
        "types": {"pid": "integer", "on_screen_only": "boolean"},
    },
    "kill_app": {
        "additional_properties": False,
        "properties": {"pid"},
        "required": {"pid"},
        "types": {"pid": "integer"},
    },
    "get_config": {
        "additional_properties": False,
        "properties": set(),
        "required": set(),
    },
    "set_config": {
        "additional_properties": False,
        "properties": {
            "key",
            "value",
            "max_image_dimension",
            "experimental_pip",
            "experimental_pip_geometry",
        },
        "required": set(),
        "types": {
            "key": "string",
            "max_image_dimension": "integer",
            "experimental_pip": "boolean",
            "experimental_pip_geometry": "string",
        },
    },
    "launch_app": {
        "additional_properties": False,
        "properties": {
            "bundle_id",
            "name",
            "urls",
            "webkit_inspector_port",
            "creates_new_application_instance",
            "additional_arguments",
        },
        "required": set(),
        "types": {
            "bundle_id": "string",
            "name": "string",
            "urls": "array",
            "webkit_inspector_port": "integer",
            "creates_new_application_instance": "boolean",
            "additional_arguments": "array",
        },
        "item_types": {"urls": "string", "additional_arguments": "string"},
    },
    "bring_to_front": {
        "additional_properties": False,
        "properties": {"pid", "window_id"},
        "required": {"pid"},
        "types": {"pid": "integer", "window_id": "integer"},
    },
    "start_session": {
        "additional_properties": True,
        "properties": {"session", "capture_scope", "cursor_theme"},
        "required": {"session"},
        "types": {
            "session": "string",
            "capture_scope": "string",
            "cursor_theme": ["object", "null"],
        },
        "enums": {"capture_scope": {"auto", "window", "desktop"}},
        "defaults": {"capture_scope": "auto"},
        "nested_properties": {"cursor_theme": {"theme_id", "reduced_motion"}},
        "nested_required": {"cursor_theme": {"theme_id"}},
        "nested_types": {
            ("cursor_theme", "theme_id"): "string",
            ("cursor_theme", "reduced_motion"): "string",
        },
        "nested_enums": {
            ("cursor_theme", "reduced_motion"): {"auto", "on", "off"},
        },
        "nested_defaults": {("cursor_theme", "reduced_motion"): "auto"},
    },
    "get_session_state": {
        "additional_properties": True,
        "properties": {"session"},
        "required": {"session"},
        "types": {"session": "string"},
    },
    "escalate_session": {
        "additional_properties": True,
        "properties": {"session", "reason", "detail"},
        "required": {"session", "reason"},
        "types": {"session": "string", "reason": "string", "detail": "string"},
        "enums": {
            "reason": {
                "ax_tree_pixel_mismatch",
                "background_delivery_failed",
                "foreground_ineffective",
                "no_window_target",
                "other",
            },
        },
        "limits": {"detail": {"maxLength": 200}},
    },
    "end_session": {
        "additional_properties": True,
        "properties": {"session"},
        "required": {"session"},
        "types": {"session": "string"},
    },
    "get_agent_cursor_state": {
        "additional_properties": False,
        "properties": {"session"},
        "required": {"session"},
        "types": {"session": "string"},
    },
    "set_agent_cursor_enabled": {
        "additional_properties": False,
        "properties": {"session", "enabled"},
        "required": {"session", "enabled"},
        "types": {"session": "string", "enabled": "boolean"},
    },
    "set_agent_cursor_motion": {
        "additional_properties": False,
        "properties": {
            "session",
            "start_handle",
            "end_handle",
            "arc_size",
            "arc_flow",
            "spring",
            "glide_duration_ms",
            "dwell_after_click_ms",
            "idle_hide_ms",
            "turn_radius",
        },
        "required": {"session"},
        "types": {
            "session": "string",
            "start_handle": ["number", "null"],
            "end_handle": ["number", "null"],
            "arc_size": ["number", "null"],
            "arc_flow": ["number", "null"],
            "spring": ["number", "null"],
            "glide_duration_ms": ["number", "null"],
            "dwell_after_click_ms": ["number", "null"],
            "idle_hide_ms": ["number", "null"],
            "turn_radius": ["number", "null"],
        },
    },
    "set_agent_cursor_theme": {
        "additional_properties": False,
        "properties": {"session", "theme_id", "reduced_motion"},
        "required": {"session", "theme_id"},
        "types": {
            "session": "string",
            "theme_id": "string",
            "reduced_motion": "string",
        },
        "enums": {"reduced_motion": {"auto", "on", "off"}},
        "defaults": {"reduced_motion": "auto"},
        "limits": {"theme_id": {"minLength": 1, "maxLength": 200}},
    },
    "start_recording": {
        "additional_properties": False,
        "properties": {"output_dir", "record_video"},
        "required": {"output_dir"},
        "types": {"output_dir": "string", "record_video": "boolean"},
    },
    "stop_recording": {
        "additional_properties": False,
        "properties": set(),
        "required": set(),
    },
    "get_recording_state": {
        "additional_properties": False,
        "properties": set(),
        "required": set(),
    },
    "install_ffmpeg": {
        "additional_properties": False,
        "properties": {"confirm"},
        "required": set(),
        "types": {"confirm": "boolean"},
    },
    "replay_trajectory": {
        "additional_properties": False,
        "properties": {"dir", "delay_ms", "stop_on_error"},
        "required": {"dir"},
        "types": {"dir": "string", "delay_ms": "integer", "stop_on_error": "boolean"},
        "limits": {"delay_ms": {"minimum": 0, "maximum": 10000}},
    },
}


def fail(message: str) -> None:
    raise SystemExit(f"native schema verification failed: {message}")


def run(binary: Path, *args: str) -> str:
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "0"
    env["CUA_DRIVER_RS_UPDATE_CHECK"] = "false"
    completed = subprocess.run(
        [str(binary), *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
        env=env,
    )
    if completed.returncode != 0:
        fail(f"{' '.join(args)} exited {completed.returncode}: {completed.stderr.strip()}")
    return completed.stdout


def describe(binary: Path, name: str) -> dict[str, Any]:
    output = run(binary, "describe", name)
    if not re.search(rf"(?m)^name: {re.escape(name)}$", output):
        fail(f"{name} describe output named a different tool")
    marker = "input_schema:"
    if marker not in output:
        fail(f"{name} describe output omitted input_schema")
    try:
        schema = json.loads(output.split(marker, 1)[1].strip())
    except json.JSONDecodeError as error:
        fail(f"{name} input_schema is not JSON: {error}")
    if schema.get("type") != "object":
        fail(f"{name} no longer advertises an object schema")
    return schema


def verify_schema(name: str, schema: dict[str, Any], contract: dict[str, Any]) -> None:
    actual_additional = schema.get("additionalProperties")
    expected_additional = contract["additional_properties"]
    if actual_additional is not expected_additional:
        fail(
            f"{name} additionalProperties drifted: expected {expected_additional}, "
            f"got {actual_additional!r}"
        )

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        fail(f"{name} omitted properties")
    actual_properties = set(properties)
    expected_properties = contract["properties"]
    if actual_properties != expected_properties:
        fail(
            f"{name} properties drifted: expected {sorted(expected_properties)}, "
            f"got {sorted(actual_properties)}"
        )

    actual_required = set(schema.get("required", []))
    expected_required = contract["required"]
    if actual_required != expected_required:
        fail(
            f"{name} required fields drifted: expected {sorted(expected_required)}, "
            f"got {sorted(actual_required)}"
        )

    for field, expected in contract.get("types", {}).items():
        actual = properties[field].get("type")
        if isinstance(expected, list):
            matches = isinstance(actual, list) and set(actual) == set(expected)
        else:
            matches = actual == expected
        if not matches:
            fail(f"{name}.{field} type drifted: {actual!r}")

    for field, expected in contract.get("enums", {}).items():
        actual = set(properties[field].get("enum", []))
        if actual != expected:
            fail(f"{name}.{field} enum drifted: {sorted(actual)}")

    for field, expected in contract.get("item_types", {}).items():
        actual = properties[field].get("items", {}).get("type")
        if actual != expected:
            fail(f"{name}.{field} item type drifted: {actual!r}")

    for field, expected in contract.get("defaults", {}).items():
        actual = properties[field].get("default")
        if actual != expected:
            fail(f"{name}.{field} default drifted: {actual!r}")

    for field, expected in contract.get("limits", {}).items():
        for key, value in expected.items():
            actual = properties[field].get(key)
            if actual != value:
                fail(f"{name}.{field}.{key} drifted: {actual!r}")

    for field, expected in contract.get("nested_properties", {}).items():
        actual = set(properties[field].get("properties", {}))
        if actual != expected:
            fail(f"{name}.{field} properties drifted: {sorted(actual)}")

    for field, expected in contract.get("nested_required", {}).items():
        actual = set(properties[field].get("required", []))
        if actual != expected:
            fail(f"{name}.{field} required fields drifted: {sorted(actual)}")

    for (field, child), expected in contract.get("nested_types", {}).items():
        actual = properties[field].get("properties", {}).get(child, {}).get("type")
        if actual != expected:
            fail(f"{name}.{field}.{child} type drifted: {actual!r}")

    for (field, child), expected in contract.get("nested_enums", {}).items():
        actual = set(
            properties[field].get("properties", {}).get(child, {}).get("enum", [])
        )
        if actual != expected:
            fail(f"{name}.{field}.{child} enum drifted: {sorted(actual)}")

    for (field, child), expected in contract.get("nested_defaults", {}).items():
        actual = properties[field].get("properties", {}).get(child, {}).get("default")
        if actual != expected:
            fail(f"{name}.{field}.{child} default drifted: {actual!r}")


def main() -> int:
    if len(sys.argv) != 2:
        fail("usage: verify-cua-native-schema.py /path/to/cua-driver")
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        fail(f"binary does not exist: {binary}")

    version = run(binary, "--version").strip()
    if version != f"cua-driver {EXPECTED_VERSION}":
        fail(f"expected Cua Driver {EXPECTED_VERSION}, got {version!r}")

    for name, contract in CONTRACTS.items():
        verify_schema(name, describe(binary, name), contract)

    print(f"Verified {len(CONTRACTS)} native schemas from Cua Driver {EXPECTED_VERSION}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
