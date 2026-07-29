#!/usr/bin/env python3
"""Fail closed when the pinned Cua Driver browser request schemas drift."""

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
    "get_browser_state": {
        "properties": {
            "session", "pid", "window_id", "target_id", "tab_id",
            "snapshot_format", "include_screenshot", "query", "scope_ref",
            "continuation",
        },
        "enums": {"snapshot_format": {"dom_refs_v1", "semantic_v2"}},
        "defaults": {"include_screenshot": False},
    },
    "browser_prepare": {
        "properties": {
            "session", "pid", "window_id", "profile", "strategy",
            "allow_launch",
        },
        "required": {"pid"},
        "nested_enums": {
            ("profile", "mode"): {"isolated_new", "isolated_named"},
            ("strategy", "kind"): {"existing_profile"},
        },
        "nested_required": {"profile": {"mode"}, "strategy": {"kind"}},
    },
    "browser_navigate": {
        "properties": {"session", "target_id", "tab_id", "url"},
        "required": {"target_id", "tab_id", "url"},
    },
    "browser_click": {
        "properties": {"session", "target_id", "tab_id", "ref", "x", "y", "input_route"},
        "required": {"target_id", "tab_id"},
        "enums": {"input_route": {"trusted", "dom_event"}},
    },
    "browser_type": {
        "properties": {"session", "target_id", "tab_id", "ref", "text", "mode", "replace"},
        "required": {"target_id", "tab_id", "ref", "text"},
        "enums": {"mode": {"insert_text", "keystrokes"}},
        "types": {"replace": "boolean"},
    },
    "browser_pointer": {
        "properties": {
            "session", "target_id", "tab_id", "action", "input_route", "ref",
            "destination_ref", "x", "y", "to_x", "to_y", "delta_x", "delta_y",
        },
        "required": {"session", "target_id", "tab_id", "action"},
        "enums": {
            "action": {"hover", "right_click", "double_click", "scroll", "drag"},
            "input_route": {"trusted", "dom_event"},
        },
        "defaults": {"input_route": "trusted"},
    },
    "browser_dialog": {
        "properties": {
            "session", "target_id", "tab_id", "action", "dialog_id",
            "prompt_text", "delivery_mode",
        },
        "required": {"target_id", "tab_id", "action"},
        "enums": {
            "action": {"inspect", "accept", "dismiss"},
            "delivery_mode": {"background", "foreground"},
        },
        "defaults": {"delivery_mode": "background"},
    },
    "browser_set_input_files": {
        "properties": {"session", "target_id", "tab_id", "ref", "files"},
        "required": {"target_id", "tab_id", "ref", "files"},
        "types": {"files": "array"},
        "item_types": {"files": "string"},
        "limits": {"files": {"minItems": 1, "maxItems": 32}},
    },
    "browser_download": {
        "properties": {"session", "target_id", "tab_id", "ref", "destination_root"},
        "required": {"session", "target_id", "tab_id", "ref", "destination_root"},
    },
}


def fail(message: str) -> None:
    raise SystemExit(f"browser schema verification failed: {message}")


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
    if schema.get("type") != "object" or schema.get("additionalProperties") is not True:
        fail(f"{name} no longer advertises an extensible object schema")
    return schema


def verify_schema(name: str, schema: dict[str, Any], contract: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        fail(f"{name} omitted properties")

    missing = contract.get("properties", set()) - properties.keys()
    if missing:
        fail(f"{name} omitted properties {sorted(missing)}")

    required = set(schema.get("required", []))
    missing_required = contract.get("required", set()) - required
    if missing_required:
        fail(f"{name} made required fields optional: {sorted(missing_required)}")

    for field, expected in contract.get("enums", {}).items():
        actual = set(properties[field].get("enum", []))
        if actual != expected:
            fail(f"{name}.{field} enum drifted: {sorted(actual)}")

    for (field, child), expected in contract.get("nested_enums", {}).items():
        actual = set(properties[field].get("properties", {}).get(child, {}).get("enum", []))
        if actual != expected:
            fail(f"{name}.{field}.{child} enum drifted: {sorted(actual)}")

    for field, expected in contract.get("nested_required", {}).items():
        actual = set(properties[field].get("required", []))
        if not expected <= actual:
            fail(f"{name}.{field} made nested fields optional: {sorted(expected - actual)}")

    for field, expected in contract.get("defaults", {}).items():
        actual = properties[field].get("default")
        if actual != expected:
            fail(f"{name}.{field} default drifted: {actual!r}")

    for field, expected in contract.get("types", {}).items():
        actual = properties[field].get("type")
        if actual != expected:
            fail(f"{name}.{field} type drifted: {actual!r}")

    for field, expected in contract.get("item_types", {}).items():
        actual = properties[field].get("items", {}).get("type")
        if actual != expected:
            fail(f"{name}.{field} item type drifted: {actual!r}")

    for field, expected in contract.get("limits", {}).items():
        for key, value in expected.items():
            actual = properties[field].get(key)
            if actual != value:
                fail(f"{name}.{field}.{key} drifted: {actual!r}")


def main() -> int:
    if len(sys.argv) != 2:
        fail("usage: verify-cua-browser-schema.py /path/to/cua-driver")
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        fail(f"binary does not exist: {binary}")

    version = run(binary, "--version").strip()
    if version != f"cua-driver {EXPECTED_VERSION}":
        fail(f"expected Cua Driver {EXPECTED_VERSION}, got {version!r}")

    for name, contract in CONTRACTS.items():
        verify_schema(name, describe(binary, name), contract)

    print(f"Verified {len(CONTRACTS)} typed browser schemas from Cua Driver {EXPECTED_VERSION}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
