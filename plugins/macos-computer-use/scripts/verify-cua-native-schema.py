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
    "health_report": {
        "properties": {"include", "skip"},
        "required": set(),
        "types": {"include": "array", "skip": "array"},
        "item_types": {"include": "string", "skip": "string"},
    },
    "check_permissions": {
        "properties": {"prompt", "probe_direct_capture"},
        "required": set(),
        "types": {"prompt": "boolean", "probe_direct_capture": "boolean"},
        "defaults": {"prompt": False},
    },
    "get_accessibility_tree": {
        "properties": set(),
        "required": set(),
    },
    "get_config": {
        "properties": set(),
        "required": set(),
    },
    "set_config": {
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
        "properties": {"pid", "window_id"},
        "required": {"pid"},
        "types": {"pid": "integer", "window_id": "integer"},
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
    if schema.get("additionalProperties") is not False:
        fail(f"{name} request schema is no longer closed")
    return schema


def verify_schema(name: str, schema: dict[str, Any], contract: dict[str, Any]) -> None:
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
        if actual != expected:
            fail(f"{name}.{field} type drifted: {actual!r}")

    for field, expected in contract.get("item_types", {}).items():
        actual = properties[field].get("items", {}).get("type")
        if actual != expected:
            fail(f"{name}.{field} item type drifted: {actual!r}")

    for field, expected in contract.get("defaults", {}).items():
        actual = properties[field].get("default")
        if actual != expected:
            fail(f"{name}.{field} default drifted: {actual!r}")


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
