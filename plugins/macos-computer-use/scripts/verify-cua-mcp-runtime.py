#!/usr/bin/env python3
"""Exercise the signed primary backend through its real stdio MCP proxy."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any


EXPECTED_MACOS_INSTRUCTIONS = """cua-driver: cross-platform background computer-use automation.

Tools let you interact with any app without stealing keyboard focus or moving the visible cursor. Prefer element_index (AX (Accessibility)) paths over pixel coordinates — they work on backgrounded/hidden windows.

Workflow per turn:
0. start_session(session) once at the start of a run → declares THIS run's identity (a stable id you choose, e.g. "research-1"). Pass that same `session` on every action below. It owns your agent cursor (a distinct color per id) and follows the run across apps/windows. End with end_session(session) when done. Concurrent runs/subagents each use their OWN `session`. (Omitting `session` still works, just with no cursor.)
1. launch_app  → idempotent, returns pid + windows array in one call. Pass creates_new_application_instance:true if another run may touch the same app, so you get your own window.
2. (skip list_windows when launch_app already returned a single window)
3. get_window_state(pid, window_id) → refresh the AX (Accessibility) snapshot, get element indices
4. click/type_text/press_key using element_index from step 3 (+ your `session`)
5. get_window_state(pid, window_id) again → verify the action landed

Agent cursor: a per-SESSION overlay cursor visualises where a run is acting without moving the real pointer. It is shown only for a DECLARED session (pass `session`), is color-coded by the session id, and is removed by end_session or the idle-TTL. The same id over MCP, the CLI, or the raw socket drives the same cursor. set_agent_cursor_* tools hide/show/customise it. Note: a pure accessibility-action (element_index) click snaps the cursor with a brief pulse on its first action rather than a long glide, so it can be easy to miss — issue a pixel click or move_cursor first for a visibly gliding demo/recording.

If a `cua-driver` skill is loaded in your harness (Claude Code / Codex / OpenClaw / OpenCode dirs), prefer its detailed workflow — SKILL.md plus MACOS.md (no-foreground contract, AXMenuBar navigation, SkyLight click dispatch). Install with `cua-driver skills install` if not yet present."""


def fail(message: str) -> None:
    raise RuntimeError(f"primary MCP runtime verification failed: {message}")


def load_contracts(path: Path) -> set[str]:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        fail(f"cannot load schema verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return set(module.CONTRACTS)


def describe(binary: Path, name: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "0"
    env["CUA_DRIVER_RS_UPDATE_CHECK"] = "false"
    completed = subprocess.run(
        [str(binary), "describe", name],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
        env=env,
    )
    if completed.returncode != 0:
        fail(f"describe {name} exited {completed.returncode}: {completed.stderr.strip()}")
    if not re.search(rf"(?m)^name: {re.escape(name)}$", completed.stdout):
        fail(f"describe {name} named a different tool")
    marker = "input_schema:"
    if marker not in completed.stdout:
        fail(f"describe {name} omitted input_schema")
    try:
        schema = json.loads(completed.stdout.split(marker, 1)[1].strip())
    except json.JSONDecodeError as error:
        fail(f"describe {name} returned invalid input_schema: {error}")
    if not isinstance(schema, dict):
        fail(f"describe {name} returned a non-object schema")
    return schema


def dump_docs(binary: Path) -> dict[str, dict[str, Any]]:
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "0"
    env["CUA_DRIVER_RS_UPDATE_CHECK"] = "false"
    completed = subprocess.run(
        [str(binary), "dump-docs", "--type", "mcp"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
        env=env,
    )
    if completed.returncode != 0:
        fail(
            "dump-docs --type mcp exited "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(f"dump-docs --type mcp returned invalid JSON: {error}")
    if not isinstance(document, dict) or document.get("version") != "0.13.1":
        fail(f"dump-docs returned the wrong runtime identity: {document}")
    tools = document.get("tools")
    if not isinstance(tools, list):
        fail("dump-docs omitted tools")
    expected_fields = {
        "name",
        "description",
        "input_schema",
        "read_only",
        "destructive",
        "idempotent",
    }
    docs: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != expected_fields:
            fail(f"dump-docs returned a malformed tool entry: {tool}")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            fail(f"dump-docs returned an invalid tool name: {name!r}")
        if name in docs:
            fail(f"dump-docs returned duplicate tool name: {name}")
        if (
            not isinstance(tool["description"], str)
            or not isinstance(tool["input_schema"], dict)
            or any(
                type(tool[field]) is not bool
                for field in ("read_only", "destructive", "idempotent")
            )
        ):
            fail(f"dump-docs metadata types drifted for {name}")
        docs[name] = tool
    return docs


class MCPClient:
    def __init__(self, binary: Path, socket: str) -> None:
        env = os.environ.copy()
        env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "0"
        env["CUA_DRIVER_RS_UPDATE_CHECK"] = "false"
        self.process = subprocess.Popen(
            [str(binary), "mcp", "--socket", socket],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=env,
        )
        self.next_id = 1

    def exchange_line(self, line: str, label: str) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            fail("MCP proxy has no stdio pipes")
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], 35)
        if not ready:
            fail(f"{label} timed out")
        response_line = self.process.stdout.readline()
        if not response_line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            fail(f"{label} received EOF: {stderr.strip()}")
        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as error:
            fail(f"{label} response was not JSON: {error}")
        if not isinstance(response, dict):
            fail(f"{label} returned a non-object JSON-RPC envelope: {response}")
        return response

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        response = self.exchange_line(
            json.dumps(message, separators=(",", ":")), method
        )
        if response.get("id") != request_id:
            fail(f"{method} response id drifted: {response}")
        if "error" in response:
            fail(f"{method} returned {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):
            fail(f"{method} returned no result object")
        return result

    def notify(self, method: str) -> None:
        if self.process.stdin is None or self.process.stdout is None:
            fail("MCP proxy has no stdio pipes")
        message = {"jsonrpc": "2.0", "method": method, "params": {}}
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], 0.25)
        if ready:
            line = self.process.stdout.readline()
            if line:
                fail(f"{method} notification unexpectedly returned: {line.strip()}")
            stderr = self.process.stderr.read() if self.process.stderr else ""
            fail(f"{method} notification closed the MCP proxy: {stderr.strip()}")

    def verify_protocol_errors(self) -> None:
        expected_parse = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        }
        parsed = self.exchange_line('{"jsonrpc":"2.0","id":', "malformed JSON")
        if parsed != expected_parse:
            fail(f"parse-error contract drifted: {parsed}")

        unknown_id = self.next_id
        self.next_id += 1
        unknown_method = "zcode/unknown-method"
        unknown = self.exchange_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": unknown_id,
                    "method": unknown_method,
                    "params": {},
                },
                separators=(",", ":"),
            ),
            "unknown method",
        )
        expected_unknown = {
            "jsonrpc": "2.0",
            "id": unknown_id,
            "error": {
                "code": -32601,
                "message": f"Unknown method: {unknown_method}",
            },
        }
        if unknown != expected_unknown:
            fail(f"method-not-found contract drifted: {unknown}")

        invalid_id = self.next_id
        self.next_id += 1
        invalid = self.exchange_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": invalid_id,
                    "method": "tools/call",
                    "params": {},
                },
                separators=(",", ":"),
            ),
            "invalid tools/call",
        )
        expected_invalid = {
            "jsonrpc": "2.0",
            "id": invalid_id,
            "error": {
                "code": -32602,
                "message": "Invalid params: missing tool name",
            },
        }
        if invalid != expected_invalid:
            fail(f"invalid-params contract drifted: {invalid}")

        self.notify("zcode/unknown-notification")

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "zcode-ci-mcp", "version": "0.17.32"},
            },
        )
        expected = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "cua-driver", "version": "0.13.1"},
            "instructions": EXPECTED_MACOS_INSTRUCTIONS,
        }
        if result != expected:
            fail(f"initialize contract drifted: {result}")
        self.notify("notifications/initialized")

    def call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> tuple[Any, list[dict[str, Any]]]:
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if result.get("isError"):
            fail(f"{name} returned a tool error: {result.get('content')}")
        content = result.get("content", [])
        if not isinstance(content, list):
            fail(f"{name}.content is not an array")
        return result.get("structuredContent"), content

    def call_error(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> tuple[Any, list[dict[str, Any]]]:
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if set(result) != {"content", "isError", "structuredContent"}:
            fail(f"{name} tool-error envelope drifted: {result}")
        if result["isError"] is not True:
            fail(f"{name} unexpectedly succeeded: {result}")
        content = result["content"]
        if not isinstance(content, list):
            fail(f"{name}.content is not an array")
        return result["structuredContent"], content

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def require_object(name: str, value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{name} returned no structured object")
    if set(value) != fields:
        fail(f"{name} fields drifted: {sorted(value)}")
    return value


def require_text_content(
    name: str, value: Any, expected_text: str
) -> list[dict[str, Any]]:
    expected = [{"type": "text", "text": expected_text}]
    if value != expected:
        fail(f"{name} text content drifted: {value}")
    return value


def require_browser_refusal(
    name: str,
    value: Any,
    content: Any,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    expected = {
        "status": "refused",
        "refusal": {"code": code, "message": message},
    }
    if value != expected:
        fail(f"{name} structured refusal drifted: {value}")
    require_text_content(name, content, f"refused ({code}): {message}")
    return expected


def require_ffmpeg_preview(name: str, value: Any, content: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("ran") is not False:
        fail(f"{name} did not remain a non-running preview: {value}")
    if value.get("installed") is True:
        result = require_object(name, value, {"installed", "ran", "path"})
        path = result["path"]
        if (
            not isinstance(path, str)
            or not path
            or Path(path).name != "ffmpeg"
            or shutil.which(path) is None
        ):
            fail(f"{name} returned an unresolved existing ffmpeg path: {path!r}")
        require_text_content(
            name,
            content,
            f"✅ ffmpeg already available ({path}). Nothing to install.",
        )
        return result
    if value.get("installed") is False:
        result = require_object(
            name, value, {"installed", "ran", "manager", "command"}
        )
        if result["manager"] != "brew" or result["command"] != "brew install ffmpeg":
            fail(f"{name} returned the wrong macOS install preview: {result}")
        require_text_content(
            name,
            content,
            "ffmpeg is not installed. To install it via brew, re-call "
            "install_ffmpeg with confirm=true.\n\n"
            "Command that will run:\n  brew install ffmpeg",
        )
        return result
    fail(f"{name}.installed is not boolean: {value}")


UPDATE_STATE_FIELDS = {
    "current_version",
    "latest_version",
    "update_available",
    "source",
    "checked_at",
    "cache_hit",
    "install_command",
    "release_notes_url",
    "error",
}


def require_update_state(name: str, value: Any, content: Any) -> dict[str, Any]:
    state = require_object(name, value, UPDATE_STATE_FIELDS)
    if (
        state["current_version"] != "0.13.1"
        or state["source"] != "github_releases"
        or type(state["update_available"]) is not bool
        or type(state["cache_hit"]) is not bool
        or not isinstance(state["checked_at"], str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", state["checked_at"])
        is None
    ):
        fail(f"{name} returned malformed update identity/state: {state}")
    latest = state["latest_version"]
    error = state["error"]
    if latest is not None and (
        not isinstance(latest, str)
        or re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", latest) is None
    ):
        fail(f"{name}.latest_version is neither semver nor null: {latest!r}")
    if error is not None and (not isinstance(error, str) or not error):
        fail(f"{name}.error is neither a non-empty string nor null: {error!r}")
    if error is not None:
        if (
            latest is not None
            or state["update_available"] is not False
            or state["install_command"] is not None
            or state["release_notes_url"] is not None
        ):
            fail(f"{name} returned inconsistent failed-update state: {state}")
        summary = f"Update check failed: {error}"
    elif state["update_available"]:
        if latest is None:
            fail(f"{name} reported an update without a latest version")
        if (
            state["install_command"]
            != "curl -fsSL https://cua.ai/driver/install.sh | bash"
            or state["release_notes_url"]
            != f"https://github.com/trycua/cua/releases/tag/cua-driver-rs-v{latest}"
        ):
            fail(f"{name} returned inconsistent update links: {state}")
        summary = f"Update available: cua-driver {latest} (you have 0.13.1)."
    else:
        if (
            latest is None
            or state["install_command"] is not None
            or state["release_notes_url"] is not None
        ):
            fail(f"{name} returned inconsistent up-to-date state: {state}")
        summary = "Up to date (cua-driver 0.13.1)."
    require_text_content(name, content, summary)
    return state


HEALTH_CHECK_NAMES = (
    "binary_version",
    "platform_supported",
    "session_active",
    "bundle_identity",
    "tcc_accessibility",
    "tcc_screen_recording",
    "ax_capability",
    "screen_capture_capability",
)
HEALTH_DATA_FIELDS = {
    "bundle_identifier",
    "executable_path",
    "os_version",
    "architecture",
    "display_count",
    "error_detail",
}
HEALTH_FILTER_SKIP_MESSAGE = "Skipped by include/skip filter."
HEALTH_CAPTURE_SKIP_MESSAGE = (
    "Direct ScreenCaptureKit readiness was not probed because health_report is "
    "read-only; run `cua-driver permissions grant` to request and verify it "
    "explicitly."
)


def require_health_entry(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{name} returned a non-object health check")
    required = {"name", "status", "message"}
    allowed = required | {"hint", "data"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        fail(f"{name} health-check fields drifted: {sorted(value)}")
    if value["status"] not in {"pass", "fail", "skip"}:
        fail(f"{name} returned an invalid health-check status: {value}")
    if (
        not isinstance(value["name"], str)
        or not value["name"]
        or not isinstance(value["message"], str)
        or not value["message"]
        or "\n" in value["message"]
    ):
        fail(f"{name} returned malformed health-check text: {value}")
    if value["status"] == "fail":
        if not isinstance(value.get("hint"), str) or not value["hint"]:
            fail(f"{name} returned a failure without a remediation hint: {value}")
    elif "hint" in value:
        fail(f"{name} returned a hint for a non-failure: {value}")
    if "data" in value:
        data = value["data"]
        if (
            not isinstance(data, dict)
            or not data
            or not set(data).issubset(HEALTH_DATA_FIELDS)
        ):
            fail(f"{name} returned malformed health-check data: {data}")
        for field, item in data.items():
            if field == "display_count":
                if type(item) is not int or item < 0:
                    fail(f"{name}.data.display_count is not a non-negative integer")
            elif not isinstance(item, str) or not item:
                fail(f"{name}.data.{field} is not a non-empty string")
    return value


def require_health_report(
    name: str, value: Any, *, binary_only: bool = False
) -> dict[str, dict[str, Any]]:
    report = require_object(
        name,
        value,
        {"schema_version", "platform", "driver_version", "overall", "checks"},
    )
    if (
        report["schema_version"] != "1"
        or report["platform"] != "darwin"
        or report["driver_version"] != "0.13.1"
        or not isinstance(report["checks"], list)
        or len(report["checks"]) != len(HEALTH_CHECK_NAMES)
    ):
        fail(f"{name} returned the wrong runtime identity or check count: {report}")
    checks = [
        require_health_entry(f"{name}.checks[{index}]", entry)
        for index, entry in enumerate(report["checks"])
    ]
    actual_names = tuple(entry["name"] for entry in checks)
    if actual_names != HEALTH_CHECK_NAMES:
        fail(f"{name} check order drifted: {actual_names}")
    by_name = {entry["name"]: entry for entry in checks}

    expected_binary = {
        "name": "binary_version",
        "status": "pass",
        "message": "cua-driver 0.13.1",
    }
    if by_name["binary_version"] != expected_binary:
        fail(f"{name}.binary_version drifted: {by_name['binary_version']}")

    if binary_only:
        if report["overall"] != "ok":
            fail(f"{name} filtered overall drifted: {report['overall']}")
        for check_name in HEALTH_CHECK_NAMES[1:]:
            expected = {
                "name": check_name,
                "status": "skip",
                "message": HEALTH_FILTER_SKIP_MESSAGE,
            }
            if by_name[check_name] != expected:
                fail(
                    f"{name}.{check_name} filtered result drifted: "
                    f"{by_name[check_name]}"
                )
        return by_name

    platform_entry = by_name["platform_supported"]
    platform_data = platform_entry.get("data")
    if (
        set(platform_entry) != {"name", "status", "message", "data"}
        or platform_entry["status"] != "pass"
        or not isinstance(platform_data, dict)
        or set(platform_data) != {"os_version", "architecture"}
        or platform_data["architecture"] not in {"arm64", "x86_64"}
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", platform_data["os_version"])
        or platform_entry["message"]
        != f"macOS {platform_data['os_version']} ({platform_data['architecture']})"
    ):
        fail(f"{name}.platform_supported drifted: {platform_entry}")

    expected_session = {
        "name": "session_active",
        "status": "pass",
        "message": "MCP session is active.",
    }
    if by_name["session_active"] != expected_session:
        fail(f"{name}.session_active drifted: {by_name['session_active']}")

    bundle_entry = by_name["bundle_identity"]
    bundle_data = bundle_entry.get("data")
    if (
        set(bundle_entry) != {"name", "status", "message", "data"}
        or bundle_entry["status"] != "pass"
        or bundle_entry["message"] != "Bundle is com.trycua.driver."
        or not isinstance(bundle_data, dict)
        or set(bundle_data) != {"bundle_identifier", "executable_path"}
        or bundle_data["bundle_identifier"] != "com.trycua.driver"
        or not bundle_data["executable_path"].endswith(
            "/CuaDriver.app/Contents/MacOS/cua-driver"
        )
    ):
        fail(f"{name}.bundle_identity drifted: {bundle_entry}")

    tcc_messages = {
        "tcc_accessibility": (
            "Accessibility is granted.",
            "Accessibility is NOT granted for this process.",
        ),
        "tcc_screen_recording": (
            "Screen Recording is granted.",
            "Screen Recording is NOT granted for this process.",
        ),
    }
    for check_name, (pass_message, fail_message) in tcc_messages.items():
        entry = by_name[check_name]
        expected_fields = {"name", "status", "message", "data"}
        if entry["status"] == "fail":
            expected_fields.add("hint")
        if (
            entry["status"] not in {"pass", "fail"}
            or set(entry) != expected_fields
            or entry["message"]
            != (pass_message if entry["status"] == "pass" else fail_message)
            or entry.get("data") != {"bundle_identifier": "com.trycua.driver"}
        ):
            fail(f"{name}.{check_name} drifted: {entry}")

    ax_entry = by_name["ax_capability"]
    accessibility_passed = by_name["tcc_accessibility"]["status"] == "pass"
    expected_ax = {
        "name": "ax_capability",
        "status": "pass" if accessibility_passed else "fail",
        "message": (
            "AX is trusted and reachable."
            if accessibility_passed
            else "AX is not trusted; UI inspection and event posting will fail."
        ),
    }
    if not accessibility_passed:
        expected_ax["hint"] = ax_entry.get("hint")
        if (
            not isinstance(expected_ax["hint"], str)
            or "tcc_accessibility" not in expected_ax["hint"]
        ):
            fail(f"{name}.ax_capability remediation drifted: {ax_entry}")
    if ax_entry != expected_ax:
        fail(f"{name}.ax_capability drifted: {ax_entry}")

    expected_capture = {
        "name": "screen_capture_capability",
        "status": "skip",
        "message": HEALTH_CAPTURE_SKIP_MESSAGE,
    }
    if by_name["screen_capture_capability"] != expected_capture:
        fail(
            f"{name}.screen_capture_capability drifted: "
            f"{by_name['screen_capture_capability']}"
        )

    failed = [entry["name"] for entry in checks if entry["status"] == "fail"]
    core_failed = any(
        check_name in {"binary_version", "platform_supported", "session_active"}
        for check_name in failed
    )
    expected_overall = "failed" if core_failed else ("degraded" if failed else "ok")
    if report["overall"] != expected_overall:
        fail(
            f"{name} overall disagreed with check statuses: "
            f"{report['overall']} != {expected_overall}"
        )
    return by_name


PERMISSION_SOURCE_NOTE = (
    "These booleans reflect the CuaDriver daemon's own TCC identity "
    "(com.trycua.driver) because this process is its own responsible process."
)
PERMISSION_READ_ONLY_NOTE = (
    "ℹ️  Direct ScreenCaptureKit readiness was not probed because this is a "
    "staged or read-only check. Run `cua-driver permissions grant` to request "
    "and verify direct capture explicitly."
)


def require_permissions(
    name: str, value: Any, content: Any
) -> dict[str, Any]:
    permissions = require_object(
        name,
        value,
        {
            "accessibility",
            "screen_recording",
            "screen_recording_capturable",
            "direct_capture_status",
            "source",
        },
    )
    if (
        type(permissions["accessibility"]) is not bool
        or type(permissions["screen_recording"]) is not bool
        or permissions["screen_recording_capturable"] is not None
        or permissions["direct_capture_status"] != "not_checked"
    ):
        fail(f"{name} did not return read-only TCC state: {permissions}")
    source = require_object(
        f"{name}.source",
        permissions["source"],
        {
            "attribution",
            "pid",
            "responsible_ppid",
            "executable",
            "disclaim_env",
            "bundle_id",
            "note",
        },
    )
    if (
        source["attribution"] != "driver-daemon"
        or type(source["pid"]) is not int
        or source["pid"] <= 0
        or type(source["responsible_ppid"]) is not int
        or source["responsible_ppid"] <= 0
        or not isinstance(source["executable"], str)
        or not source["executable"].endswith(
            "/CuaDriver.app/Contents/MacOS/cua-driver"
        )
        or type(source["disclaim_env"]) is not bool
        or source["bundle_id"] != "com.trycua.driver"
        or source["note"] != PERMISSION_SOURCE_NOTE
    ):
        fail(f"{name} source identity drifted: {source}")
    ax_state = "granted" if permissions["accessibility"] else "NOT granted"
    sr_state = (
        "granted" if permissions["screen_recording"] else "NOT granted"
    )
    expected_text = (
        f"{'✅' if permissions['accessibility'] else '❌'} "
        f"Accessibility: {ax_state}.\n"
        f"{'✅' if permissions['screen_recording'] else '❌'} "
        f"Screen Recording: {sr_state}.\n"
        f"{PERMISSION_READ_ONLY_NOTE}"
    )
    require_text_content(name, content, expected_text)
    return permissions


def require_accessibility_discovery(
    name: str, value: Any, content: Any
) -> dict[str, Any]:
    discovery = require_object(name, value, {"apps", "windows"})
    apps = discovery["apps"]
    windows = discovery["windows"]
    if not isinstance(apps, list) or not isinstance(windows, list):
        fail(f"{name} did not return app/window arrays")

    app_pids: set[int] = set()
    for index, app in enumerate(apps):
        app = require_object(
            f"{name}.apps[{index}]", app, {"pid", "name", "bundle_id"}
        )
        if (
            type(app["pid"]) is not int
            or app["pid"] <= 0
            or not isinstance(app["name"], str)
            or not app["name"]
            or (
                app["bundle_id"] is not None
                and (
                    not isinstance(app["bundle_id"], str)
                    or not app["bundle_id"]
                )
            )
        ):
            fail(f"{name} returned malformed regular-app identity: {app}")
        if app["pid"] in app_pids:
            fail(f"{name} returned duplicate app pid {app['pid']}")
        app_pids.add(app["pid"])

    window_ids: set[int] = set()
    for index, window in enumerate(windows):
        window = require_object(
            f"{name}.windows[{index}]",
            window,
            {"window_id", "pid", "app_name", "title"},
        )
        if (
            type(window["window_id"]) is not int
            or window["window_id"] <= 0
            or type(window["pid"]) is not int
            or window["pid"] <= 0
            or not isinstance(window["app_name"], str)
            or not window["app_name"]
            or not isinstance(window["title"], str)
        ):
            fail(f"{name} returned malformed visible-window identity: {window}")
        if window["window_id"] in window_ids:
            fail(f"{name} returned duplicate window id {window['window_id']}")
        window_ids.add(window["window_id"])

    lines = [f"{len(apps)} running app(s), {len(windows)} visible window(s)"]
    for app in apps:
        bundle = f" [{app['bundle_id']}]" if app["bundle_id"] is not None else ""
        lines.append(f"- {app['name']} (pid {app['pid']}){bundle}")
    if windows:
        lines.extend(("", "Windows:"))
        for window in windows:
            title = f'"{window["title"]}"' if window["title"] else "(no title)"
            lines.append(
                f"- {window['app_name']} (pid {window['pid']}) {title} "
                f"[window_id: {window['window_id']}]"
            )
        lines.append(
            "→ Call get_window_state(pid, window_id) to inspect a window's UI."
        )
    require_text_content(name, content, "\n".join(lines))
    return discovery


def require_config(name: str, value: Any) -> dict[str, Any]:
    config = require_object(
        name,
        value,
        {
            "version",
            "source_sha",
            "platform",
            "max_image_dimension",
            "agent_cursor",
            "experimental_pip",
            "experimental_pip_geometry",
        },
    )
    if (
        config["version"] != "0.13.1"
        or config["source_sha"] is not None
        or config["platform"] != "macos"
        or config["agent_cursor"] != {"enabled": True}
        or config["experimental_pip"] is not False
        or config["experimental_pip_geometry"] is not None
    ):
        fail(f"{name} returned the wrong runtime identity: {config}")
    dimension = config["max_image_dimension"]
    if type(dimension) is not int or dimension < 0:
        fail(f"{name}.max_image_dimension is not a non-negative integer")
    return config


def require_set_config(
    name: str, value: Any, expected_dimension: int
) -> dict[str, Any]:
    result = require_object(
        name, value, {"version", "platform", "max_image_dimension"}
    )
    expected = {
        "version": "0.13.1",
        "platform": "macos",
        "max_image_dimension": expected_dimension,
    }
    if result != expected:
        fail(f"{name} response drifted: {result}")
    return result


RECORDING_STATE_FIELDS = {
    "recording",
    "enabled",
    "output_dir",
    "next_turn",
    "last_error",
    "video_active",
    "last_video_path",
    "owner",
}

CURSOR_MOTION_FIELDS = {
    "start_handle",
    "end_handle",
    "arc_size",
    "arc_flow",
    "spring",
    "glide_duration_ms",
    "dwell_after_click_ms",
    "idle_hide_ms",
    "turn_radius",
}
DEFAULT_CURSOR_MOTION = {
    "start_handle": 0.3,
    "end_handle": 0.3,
    "arc_size": 0.25,
    "arc_flow": 0.0,
    "spring": 0.72,
    "glide_duration_ms": 0.0,
    "dwell_after_click_ms": 80.0,
    "idle_hide_ms": 20_000.0,
    "turn_radius": 80.0,
}
DEFAULT_CURSOR_THEME = {
    "id": "cua.default",
    "version": "1.0.0",
    "profile": "cua-driver-full-v1",
    "reduced_motion": "auto",
    "fallback": None,
}
CURSOR_ACTIONS = {
    "idle",
    "observe",
    "click",
    "drag",
    "scroll",
    "text",
    "key",
    "navigate",
    "app",
    "transfer",
    "record",
    "system",
}
CURSOR_MODIFIERS = {"background", "foreground", "ax", "pixel", "browser", "desktop"}


def require_cursor_motion(name: str, value: Any) -> dict[str, Any]:
    motion = require_object(name, value, CURSOR_MOTION_FIELDS)
    if any(type(motion[field]) not in {int, float} for field in motion):
        fail(f"{name} contains a non-numeric motion value: {motion}")
    if motion != DEFAULT_CURSOR_MOTION:
        fail(f"{name} drifted from the pinned human-like defaults: {motion}")
    return motion


def require_cursor_theme(name: str, value: Any) -> dict[str, Any]:
    theme = require_object(
        name, value, {"id", "version", "profile", "reduced_motion", "fallback"}
    )
    if theme != DEFAULT_CURSOR_THEME:
        fail(f"{name} drifted from the embedded default theme: {theme}")
    return theme


def require_cursor_state(
    name: str, value: Any, session: str, enabled: bool
) -> dict[str, Any]:
    state = require_object(
        name,
        value,
        {"session", "enabled", "position", "theme", "visual_state", "motion"},
    )
    if state["session"] != session or state["enabled"] is not enabled:
        fail(f"{name} returned the wrong session/enabled state: {state}")
    if state["position"] is not None:
        position = require_object(f"{name}.position", state["position"], {"x", "y"})
        if any(type(position[field]) not in {int, float} for field in position):
            fail(f"{name}.position is not numeric: {position}")
    require_cursor_theme(f"{name}.theme", state["theme"])
    visual = require_object(
        f"{name}.visual_state",
        state["visual_state"],
        {
            "requested_action",
            "resolved_action",
            "modifiers",
            "phase",
            "frame",
            "preempted_count",
        },
    )
    modifiers = visual["modifiers"]
    if (
        visual["requested_action"] not in CURSOR_ACTIONS
        or visual["resolved_action"] not in CURSOR_ACTIONS
        or not isinstance(modifiers, list)
        or len(modifiers) > 2
        or len(set(modifiers)) != len(modifiers)
        or any(modifier not in CURSOR_MODIFIERS for modifier in modifiers)
        or visual["phase"] not in {"loop", "sustain", "one_shot"}
        or type(visual["frame"]) is not int
        or visual["frame"] < 0
        or type(visual["preempted_count"]) is not int
        or visual["preempted_count"] < 0
    ):
        fail(f"{name} returned malformed dynamic visual state: {visual}")
    require_cursor_motion(f"{name}.motion", state["motion"])
    return state


SESSION_STATE_FIELDS = {
    "session",
    "capture_scope",
    "effective_scope",
    "desktop_unlocked",
    "escalation_reason",
    "escalation_detail",
}


def expected_session_state(
    session: str,
    capture_scope: str,
    effective_scope: str,
    desktop_unlocked: bool,
    escalation_reason: str | None = None,
    escalation_detail: str | None = None,
) -> dict[str, Any]:
    return {
        "session": session,
        "capture_scope": capture_scope,
        "effective_scope": effective_scope,
        "desktop_unlocked": desktop_unlocked,
        "escalation_reason": escalation_reason,
        "escalation_detail": escalation_detail,
    }


def require_session_state(
    name: str,
    value: Any,
    content: Any,
    *,
    session: str,
    capture_scope: str,
    effective_scope: str,
    desktop_unlocked: bool,
    escalation_reason: str | None = None,
    escalation_detail: str | None = None,
) -> dict[str, Any]:
    state = require_object(name, value, SESSION_STATE_FIELDS)
    expected = expected_session_state(
        session,
        capture_scope,
        effective_scope,
        desktop_unlocked,
        escalation_reason,
        escalation_detail,
    )
    if state != expected:
        fail(f"{name} session state drifted: {state}")
    require_text_content(
        name,
        content,
        f"Session '{session}' uses capture_scope='{capture_scope}' "
        f"(effective_scope='{effective_scope}').",
    )
    return state


def require_started_session(
    name: str,
    value: Any,
    content: Any,
    *,
    session: str,
    capture_scope: str,
    effective_scope: str,
    desktop_unlocked: bool,
    revived: bool,
) -> dict[str, Any]:
    state = require_object(
        name, value, SESSION_STATE_FIELDS | {"active", "revived"}
    )
    expected = {
        **expected_session_state(
            session, capture_scope, effective_scope, desktop_unlocked
        ),
        "active": True,
        "revived": revived,
    }
    if state != expected:
        fail(f"{name} started-session state drifted: {state}")
    require_text_content(
        name,
        content,
        f"✅ Session '{session}' is active with capture_scope='{capture_scope}'.",
    )
    return state


def require_ended_session(
    name: str, value: Any, content: Any, session: str
) -> dict[str, Any]:
    state = require_object(name, value, {"session", "active"})
    expected = {"session": session, "active": False}
    if state != expected:
        fail(f"{name} ended-session state drifted: {state}")
    require_text_content(name, content, f"✅ Session '{session}' ended.")
    return state


def require_ended_session_guard(
    name: str,
    value: Any,
    content: Any,
    *,
    session: str,
    tool_name: str,
) -> dict[str, Any]:
    result = require_object(name, value, {"exit_code"})
    if result != {"exit_code": 1}:
        fail(f"{name} ended-session guard drifted: {result}")
    require_text_content(
        name,
        content,
        f"session '{session}' has ended; tool call '{tool_name}' was rejected. "
        "Call start_session with this id to revive it before issuing further "
        "actions, or use a new session id.",
    )
    return result


def require_recording_state(
    name: str, value: Any, content: Any | None = None
) -> dict[str, Any]:
    state = require_object(name, value, RECORDING_STATE_FIELDS)
    if type(state["recording"]) is not bool or type(state["enabled"]) is not bool:
        fail(f"{name} omitted recording booleans")
    if state["recording"] is not state["enabled"]:
        fail(f"{name}.recording disagreed with enabled")
    if type(state["next_turn"]) is not int or state["next_turn"] < 1:
        fail(f"{name}.next_turn is not a positive integer")
    if type(state["video_active"]) is not bool:
        fail(f"{name}.video_active is not boolean")
    for field in ("output_dir", "last_error", "last_video_path", "owner"):
        if state[field] is not None and not isinstance(state[field], str):
            fail(f"{name}.{field} is neither a string nor null")
    if content is not None:
        summary = (
            f"recording: enabled output_dir={state['output_dir']} "
            f"next_turn={state['next_turn']}"
            if state["enabled"]
            else "recording: disabled"
        )
        require_text_content(name, content, f"✅ {summary}")
    return state


def require_recording_manifest(name: str, path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        fail(f"{name} is missing or empty")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"{name} is invalid JSON: {error}")
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "started_at_monotonic_ms",
        "video",
        "cursor",
    }:
        fail(f"{name} fields drifted: {manifest}")
    if (
        manifest["schema_version"] != 1
        or type(manifest["started_at_monotonic_ms"]) is not int
        or manifest["started_at_monotonic_ms"] <= 0
        or manifest["video"] != {"present": False}
    ):
        fail(f"{name} identity/video contract drifted: {manifest}")
    cursor = manifest["cursor"]
    if (
        not isinstance(cursor, dict)
        or set(cursor) != {"present", "sample_count"}
        or type(cursor["present"]) is not bool
        or type(cursor["sample_count"]) is not int
        or cursor["sample_count"] < 0
    ):
        fail(f"{name}.cursor contract drifted: {cursor}")
    return manifest


def require_replay_result(
    name: str,
    value: Any,
    content: Any,
    *,
    directory: Path,
    session: str,
) -> dict[str, Any]:
    disabled_summary = f"Agent cursor for session '{session}' disabled."
    expected = {
        "directory": str(directory),
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "stop_on_error": True,
        "turns": [
            {
                "turn": "turn-00001",
                "tool": "set_agent_cursor_enabled",
                "ok": True,
                "result_summary": disabled_summary,
            }
        ],
    }
    if value != expected:
        fail(f"{name} structured result drifted: {value}")
    require_text_content(
        name,
        content,
        f"replay {directory.name}: attempted=1 succeeded=1 failed=0",
    )
    return value


APP_ENTRY_FIELDS = {
    "pid",
    "name",
    "bundle_id",
    "active",
    "running",
    "launch_path",
    "kind",
    "last_used",
    "windows",
}


def require_app_inventory(
    name: str, value: Any, content: Any | None = None
) -> dict[str, Any]:
    inventory = require_object(name, value, {"apps"})
    apps = inventory["apps"]
    if not isinstance(apps, list):
        fail(f"{name}.apps is not an array")
    for app in apps:
        if not isinstance(app, dict) or set(app) != APP_ENTRY_FIELDS:
            fail(f"{name} app fields drifted: {app}")
        if (
            type(app["pid"]) is not int
            or app["pid"] < 0
            or not isinstance(app["name"], str)
            or type(app["active"]) is not bool
            or type(app["running"]) is not bool
            or app["windows"] != []
        ):
            fail(f"{name} returned malformed app state: {app}")
        for field in ("bundle_id", "launch_path", "kind", "last_used"):
            if app[field] is not None and not isinstance(app[field], str):
                fail(f"{name}.{field} is neither a string nor null: {app}")
        if app["running"] is not (app["pid"] > 0):
            fail(f"{name} running/pid fields disagreed: {app}")
        if app["active"] and not app["running"]:
            fail(f"{name} reported an active stopped app: {app}")
        if app["kind"] not in {None, "desktop"}:
            fail(f"{name} returned an unexpected macOS app kind: {app}")
    if content is not None:
        running = [app for app in apps if app["running"]]
        lines = [
            f"✅ Found {len(apps)} app(s): {len(running)} running, "
            f"{len(apps) - len(running)} installed-not-running."
        ]
        for app in running:
            bundle = (
                f" [{app['bundle_id']}]" if app["bundle_id"] is not None else ""
            )
            lines.append(f"- {app['name']} (pid {app['pid']}){bundle}")
        require_text_content(name, content, "\n".join(lines))
    return inventory


WINDOW_ENTRY_FIELDS = {
    "window_id",
    "pid",
    "app_name",
    "title",
    "bounds",
    "layer",
    "z_index",
    "is_on_screen",
    "on_current_space",
    "space_ids",
}
LAUNCH_WINDOW_FIELDS = {
    "window_id",
    "pid",
    "app_name",
    "title",
    "bounds",
    "is_on_screen",
}


def require_window_bounds(name: str, value: Any) -> dict[str, Any]:
    bounds = require_object(name, value, {"x", "y", "width", "height"})
    if any(type(bounds[field]) not in {int, float} for field in bounds):
        fail(f"{name} contains non-numeric geometry: {bounds}")
    return bounds


def require_window_entry(
    name: str, value: Any, fields: set[str]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        fail(f"{name} fields drifted: {value}")
    if (
        type(value["window_id"]) is not int
        or value["window_id"] <= 0
        or type(value["pid"]) is not int
        or value["pid"] <= 0
        or not isinstance(value["app_name"], str)
        or not isinstance(value["title"], str)
        or type(value["is_on_screen"]) is not bool
    ):
        fail(f"{name} returned malformed identity/state: {value}")
    require_window_bounds(f"{name}.bounds", value["bounds"])
    if fields == WINDOW_ENTRY_FIELDS:
        if (
            type(value["layer"]) is not int
            or value["layer"] != 0
            or type(value["z_index"]) is not int
            or value["z_index"] < 0
            or not (
                value["on_current_space"] is None
                or type(value["on_current_space"]) is bool
            )
            or (
                value["space_ids"] is not None
                and (
                    not isinstance(value["space_ids"], list)
                    or any(type(space_id) is not int for space_id in value["space_ids"])
                )
            )
        ):
            fail(f"{name} returned malformed WindowServer metadata: {value}")
    return value


def require_window_inventory(
    name: str, value: Any, content: Any | None = None
) -> dict[str, Any]:
    inventory = require_object(name, value, {"windows", "current_space_id"})
    if inventory["current_space_id"] is not None:
        fail(f"{name}.current_space_id drifted from the pinned null contract")
    if not isinstance(inventory["windows"], list):
        fail(f"{name}.windows is not an array")
    for index, window in enumerate(inventory["windows"]):
        require_window_entry(
            f"{name}.windows[{index}]", window, WINDOW_ENTRY_FIELDS
        )
    if content is not None:
        require_text_content(
            name, content, f"Found {len(inventory['windows'])} window(s)."
        )
    return inventory


def require_screen_geometry(
    name: str, value: Any, content: Any
) -> dict[str, Any]:
    screen = require_object(name, value, {"width", "height", "scale_factor"})
    if (
        type(screen["width"]) is not int
        or type(screen["height"]) is not int
        or type(screen["scale_factor"]) is not float
        or screen["width"] <= 0
        or screen["height"] <= 0
        or screen["scale_factor"] <= 0
        or screen["scale_factor"] * 2 != round(screen["scale_factor"] * 2)
    ):
        fail(f"{name} returned invalid main-display geometry: {screen}")
    scale = format(screen["scale_factor"], "g")
    require_text_content(
        name,
        content,
        f"✅ Main display: {screen['width']}x{screen['height']} points @ {scale}x",
    )
    return screen


def require_cursor_position(
    name: str, value: Any, content: Any
) -> dict[str, Any]:
    cursor = require_object(name, value, {"x", "y"})
    if any(type(cursor[key]) is not int for key in ("x", "y")):
        fail(f"{name} did not return exact integer screen-point coordinates")
    require_text_content(
        name, content, f"✅ Cursor at ({cursor['x']}, {cursor['y']})"
    )
    return cursor


def require_launch_result(
    name: str,
    value: Any,
    content: Any,
    bundle_id: str,
    app_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) not in (
        {"pid", "bundle_id", "name", "windows"},
        {"pid", "bundle_id", "name", "windows", "self_activation_suppressed"},
    ):
        fail(f"{name} fields drifted: {value}")
    if (
        type(value["pid"]) is not int
        or value["pid"] <= 0
        or value["bundle_id"] != bundle_id
        or value["name"] != app_name
        or not isinstance(value["windows"], list)
    ):
        fail(f"{name} returned malformed app identity: {value}")
    if "self_activation_suppressed" in value and type(
        value["self_activation_suppressed"]
    ) is not bool:
        fail(f"{name}.self_activation_suppressed is not boolean")
    for index, window in enumerate(value["windows"]):
        require_window_entry(
            f"{name}.windows[{index}]", window, LAUNCH_WINDOW_FIELDS
        )
        if window["pid"] != value["pid"]:
            fail(f"{name} returned a window owned by another pid: {window}")
    lines = [f"Launched {value['name']} (pid {value['pid']}) in background."]
    if value["windows"]:
        lines.extend(("", "Windows:"))
        for window in value["windows"]:
            title = f'"{window["title"]}"' if window["title"] else "(no title)"
            lines.append(f"- {title} [window_id: {window['window_id']}]")
        lines.append(
            f"→ Call get_window_state(pid: {value['pid']}, window_id) to inspect."
        )
    require_text_content(name, content, "\n".join(lines))
    return value


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command(pid: int) -> str:
    completed = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "comm="],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def main() -> int:
    if len(sys.argv) not in {3, 5} or (
        len(sys.argv) == 5 and sys.argv[3] != "--tcc-status-file"
    ):
        raise SystemExit(
            "usage: verify-cua-mcp-runtime.py /path/to/cua-driver /path/to/socket "
            "[--tcc-status-file /path/to/report.json]"
        )
    binary = Path(sys.argv[1]).resolve()
    socket = sys.argv[2]
    tcc_status_path = Path(sys.argv[4]).resolve() if len(sys.argv) == 5 else None
    scripts = Path(__file__).resolve().parent
    required = load_contracts(scripts / "verify-cua-native-schema.py") | load_contracts(
        scripts / "verify-cua-browser-schema.py"
    )
    if len(required) != 49:
        fail(f"expected 49 pinned tools, got {len(required)}")
    direct_docs = dump_docs(binary)
    if set(direct_docs) != required:
        fail(
            "dump-docs surface drifted: "
            f"missing={sorted(required - set(direct_docs))}, "
            f"unexpected={sorted(set(direct_docs) - required)}"
        )

    client = MCPClient(binary, socket)
    peer = MCPClient(binary, socket)
    probe: subprocess.Popen[str] | None = None
    owned_app_pid: int | None = None
    session = f"zcode-ci-mcp-{os.getpid()}"
    session_started = False
    config_original: int | None = None
    config_changed = False
    recording_dir: Path | None = None
    recording_dirs: list[Path] = []
    recording_started = False
    browser_probe_dir: Path | None = None
    try:
        client.initialize()
        peer.initialize()
        client.verify_protocol_errors()
        listed = client.request("tools/list")
        if set(listed) != {"tools", "capability_version", "schema_version"}:
            fail(f"tools/list envelope drifted: {sorted(listed)}")
        if listed["capability_version"] != "1" or listed["schema_version"] != "1":
            fail(f"tools/list contract versions drifted: {listed}")
        tools = listed.get("tools")
        if not isinstance(tools, list):
            fail("tools/list omitted tools")
        if not all(
            isinstance(tool, dict) and isinstance(tool.get("name"), str)
            for tool in tools
        ):
            fail("tools/list returned a malformed tool entry")
        names = [tool["name"] for tool in tools]
        if len(names) != len(set(names)):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            fail(f"tools/list returned duplicate tool names: {duplicates}")
        advertised = {tool["name"]: tool for tool in tools}
        advertised_names = set(advertised)
        if advertised_names != required:
            fail(
                "tools/list surface drifted: "
                f"missing={sorted(required - advertised_names)}, "
                f"unexpected={sorted(advertised_names - required)}"
            )
        for name in sorted(required):
            entry = advertised[name]
            expected_entry_fields = {
                "name",
                "description",
                "inputSchema",
                "annotations",
                "capabilities",
                "risk",
            }
            if set(entry) != expected_entry_fields:
                fail(f"tools/list entry fields drifted for {name}: {sorted(entry)}")
            direct_doc = direct_docs[name]
            if entry["description"] != direct_doc["description"]:
                fail(f"tools/list.description drifted from dump-docs for {name}")
            mcp_schema = entry["inputSchema"]
            if mcp_schema != direct_doc["input_schema"]:
                fail(f"tools/list.inputSchema drifted from dump-docs for {name}")
            direct_schema = describe(binary, name)
            if mcp_schema != direct_schema:
                fail(f"tools/list.inputSchema drifted from describe for {name}")
            annotations = entry["annotations"]
            annotation_fields = {
                "readOnlyHint",
                "destructiveHint",
                "idempotentHint",
                "openWorldHint",
            }
            if not isinstance(annotations, dict) or set(annotations) != annotation_fields:
                fail(f"tools/list.annotations drifted for {name}: {annotations}")
            expected_annotations = {
                "readOnlyHint": direct_doc["read_only"],
                "destructiveHint": direct_doc["destructive"],
                "idempotentHint": direct_doc["idempotent"],
            }
            if any(
                annotations[field] is not value
                for field, value in expected_annotations.items()
            ):
                fail(f"tools/list annotation values drifted from dump-docs for {name}")
            if type(annotations["openWorldHint"]) is not bool:
                fail(f"tools/list.openWorldHint is not boolean for {name}")
            capabilities = entry["capabilities"]
            if (
                not isinstance(capabilities, list)
                or not all(
                    isinstance(capability, str) and capability
                    for capability in capabilities
                )
                or len(capabilities) != len(set(capabilities))
            ):
                fail(f"tools/list.capabilities drifted for {name}: {capabilities}")
            risk = entry["risk"]
            if (
                not isinstance(risk, dict)
                or set(risk) != {"class", "enforcement", "operation_sensitive", "version"}
                or risk["class"] not in {"r0", "r1", "r2", "r3", "r4", "unclassified"}
                or risk["enforcement"] not in {"active", "metadata_only", "not_exposed"}
                or type(risk["operation_sensitive"]) is not bool
                or risk["version"] != "1"
            ):
                fail(f"tools/list.risk drifted for {name}: {risk}")

        service_annotations = {
            "check_for_update": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "install_ffmpeg": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        }
        for name, expected in service_annotations.items():
            if advertised[name]["annotations"] != expected:
                fail(f"tools/list service annotations drifted for {name}")

        ffmpeg_value, ffmpeg_content = client.call("install_ffmpeg")
        require_ffmpeg_preview("install_ffmpeg preview", ffmpeg_value, ffmpeg_content)

        update_value, update_content = client.call("check_for_update")
        require_update_state("check_for_update", update_value, update_content)

        filtered_health, _ = client.call(
            "health_report", {"include": ["binary_version"]}
        )
        require_health_report(
            "filtered health_report", filtered_health, binary_only=True
        )

        health, _ = client.call("health_report")
        health_checks = require_health_report("health_report", health)

        permissions_value, permissions_content = client.call(
            "check_permissions", {"prompt": False}
        )
        permissions = require_permissions(
            "check_permissions", permissions_value, permissions_content
        )
        if permissions["source"]["pid"] in {
            os.getpid(),
            client.process.pid,
            peer.process.pid,
        }:
            fail("check_permissions attributed TCC to a verifier/proxy process")
        try:
            os.kill(permissions["source"]["pid"], 0)
        except OSError as error:
            fail(f"check_permissions returned a dead daemon pid: {error}")

        default_permissions_value, default_permissions_content = peer.call(
            "check_permissions"
        )
        default_permissions = require_permissions(
            "default check_permissions",
            default_permissions_value,
            default_permissions_content,
        )
        if default_permissions != permissions:
            fail("empty-input check_permissions differed from explicit prompt:false")
        if (
            health_checks["tcc_accessibility"].get("status") == "pass"
        ) != permissions["accessibility"]:
            fail("health_report and check_permissions disagreed on Accessibility")
        if (
            health_checks["tcc_screen_recording"].get("status") == "pass"
        ) != permissions["screen_recording"]:
            fail("health_report and check_permissions disagreed on Screen Recording")
        signed_tcc = {
            "accessibility": permissions["accessibility"],
            "screen_recording": permissions["screen_recording"],
            "attribution": permissions["source"]["attribution"],
            "bundle_id": permissions["source"]["bundle_id"],
        }
        print(
            "Signed CuaDriver TCC state: "
            + json.dumps(signed_tcc, sort_keys=True, separators=(",", ":"))
        )
        if tcc_status_path is not None:
            tcc_status_path.write_text(
                json.dumps(signed_tcc, sort_keys=True) + "\n", encoding="utf-8"
            )

        discovery_value, discovery_content = client.call("get_accessibility_tree")
        discovery = require_accessibility_discovery(
            "get_accessibility_tree", discovery_value, discovery_content
        )
        peer_discovery_value, peer_discovery_content = peer.call(
            "get_accessibility_tree"
        )
        require_accessibility_discovery(
            "peer get_accessibility_tree",
            peer_discovery_value,
            peer_discovery_content,
        )

        initial_recording_value, initial_recording_content = client.call(
            "get_recording_state"
        )
        initial_recording = require_recording_state(
            "initial get_recording_state",
            initial_recording_value,
            initial_recording_content,
        )
        expected_disabled_recording = {
            "recording": False,
            "enabled": False,
            "output_dir": None,
            "next_turn": 1,
            "last_error": None,
            "video_active": False,
            "last_video_path": None,
            "owner": None,
        }
        if initial_recording != expected_disabled_recording:
            fail(
                "private daemon began with non-canonical recording state: "
                f"{initial_recording}"
            )
        temp_parent = os.environ.get("RUNNER_TEMP")
        recording_dir = Path(
            tempfile.mkdtemp(
                prefix="zcode-primary-recorder-",
                dir=temp_parent if temp_parent else None,
            )
        ).resolve()
        recording_dirs.append(recording_dir)
        started_recording_value, started_recording_content = client.call(
            "start_recording",
            {"output_dir": str(recording_dir), "record_video": False},
        )
        started_recording = require_recording_state(
            "start_recording",
            started_recording_value,
        )
        require_text_content(
            "start_recording",
            started_recording_content,
            f"✅ Recording started -> {recording_dir}",
        )
        recording_started = True
        if (
            started_recording["recording"] is not True
            or started_recording["output_dir"] != str(recording_dir)
            or started_recording["next_turn"] != 1
            or started_recording["last_error"] is not None
            or started_recording["video_active"] is not False
            or started_recording["last_video_path"] is not None
            or not started_recording["owner"]
        ):
            fail(f"start_recording returned inconsistent state: {started_recording}")
        owner_readback_value, owner_readback_content = client.call(
            "get_recording_state"
        )
        if require_recording_state(
            "recording owner readback",
            owner_readback_value,
            owner_readback_content,
        ) != started_recording:
            fail("get_recording_state did not preserve the recording owner/state")
        peer_readback_value, peer_readback_content = peer.call(
            "get_recording_state"
        )
        if require_recording_state(
            "peer recording readback",
            peer_readback_value,
            peer_readback_content,
        ) != started_recording:
            fail("peer MCP connection did not observe daemon-global recording state")
        first_session_file = recording_dir / "session.json"
        started_manifest = require_recording_manifest(
            "started recording session.json", first_session_file
        )
        if started_manifest["cursor"]["sample_count"] != 0:
            fail(f"new recording began with cursor samples: {started_manifest}")

        replacement_dir = Path(
            tempfile.mkdtemp(
                prefix="zcode-primary-recorder-takeover-",
                dir=temp_parent if temp_parent else None,
            )
        ).resolve()
        recording_dirs.append(replacement_dir)
        replacement_value, replacement_content = peer.call(
            "start_recording",
            {"output_dir": str(replacement_dir), "record_video": False},
        )
        replacement_recording = require_recording_state(
            "peer replacement start_recording", replacement_value
        )
        require_text_content(
            "peer replacement start_recording",
            replacement_content,
            f"✅ Recording started -> {replacement_dir}",
        )
        if (
            replacement_recording["recording"] is not True
            or replacement_recording["output_dir"] != str(replacement_dir)
            or replacement_recording["next_turn"] != 1
            or replacement_recording["last_error"] is not None
            or replacement_recording["video_active"] is not False
            or replacement_recording["last_video_path"] is not None
            or not replacement_recording["owner"]
            or replacement_recording["owner"] == started_recording["owner"]
        ):
            fail(
                "peer start_recording did not take over daemon-global ownership: "
                f"first={started_recording}, replacement={replacement_recording}"
            )
        recording_dir = replacement_dir
        takeover_readback_value, takeover_readback_content = client.call(
            "get_recording_state"
        )
        if require_recording_state(
            "cross-connection takeover readback",
            takeover_readback_value,
            takeover_readback_content,
        ) != replacement_recording:
            fail("original MCP connection did not observe recorder ownership takeover")
        require_recording_manifest(
            "preserved replaced recording session.json", first_session_file
        )
        session_file = replacement_dir / "session.json"
        replacement_manifest = require_recording_manifest(
            "replacement recording session.json", session_file
        )
        if replacement_manifest["cursor"]["sample_count"] != 0:
            fail(
                "replacement recording began with cursor samples: "
                f"{replacement_manifest}"
            )

        stopped_recording_value, stopped_recording_content = client.call(
            "stop_recording"
        )
        stopped_recording = require_recording_state(
            "non-owner stop_recording", stopped_recording_value
        )
        require_text_content(
            "non-owner stop_recording",
            stopped_recording_content,
            "✅ Recording stopped.",
        )
        recording_started = False
        if stopped_recording != expected_disabled_recording:
            fail(
                "manual non-owner stop_recording did not stop the global recorder: "
                f"{stopped_recording}"
            )
        post_stop_value, post_stop_content = peer.call("get_recording_state")
        if require_recording_state(
            "post-stop recording state",
            post_stop_value,
            post_stop_content,
        ) != expected_disabled_recording:
            fail("peer MCP connection retained stopped recording state")
        require_recording_manifest("final recording session.json", session_file)
        if any((directory / "recording.mp4").exists() for directory in recording_dirs):
            fail("record_video:false unexpectedly created a recording.mp4")

        try:
            client.call("page", {"action": "execute_javascript"})
        except RuntimeError as error:
            page_probe = str(error)
            if (
                "Missing required parameter: pid" not in page_probe
                or "disabled by default" in page_probe
            ):
                fail(f"legacy page mutations remained constrained: {page_probe}")
        else:
            fail("invalid page mutation probe unexpectedly succeeded")

        apps_value, apps_content = client.call("list_apps")
        apps = require_app_inventory("list_apps", apps_value, apps_content)
        existing_app_pids = {
            app.get("pid")
            for app in apps["apps"]
            if isinstance(app, dict)
            and type(app.get("pid")) is int
            and app["pid"] > 0
        }

        lifecycle_candidates = (
            ("com.apple.calculator", "Calculator"),
            ("com.apple.TextEdit", "TextEdit"),
        )
        lifecycle_target: tuple[str, str] | None = None
        for candidate in lifecycle_candidates:
            bundle_id, _ = candidate
            matching_apps = [
                app
                for app in apps["apps"]
                if isinstance(app, dict) and app.get("bundle_id") == bundle_id
            ]
            if matching_apps and not any(
                type(app.get("pid")) is int and app["pid"] > 0
                for app in matching_apps
            ):
                lifecycle_target = candidate
                break
        if lifecycle_target is None:
            fail("no installed, stopped Calculator or TextEdit is safe to own")
        owned_bundle_id, owned_app_name = lifecycle_target

        missing_bundle_id = f"com.zcode.ci.definitely-not-installed.{os.getpid()}"
        missing_app, missing_app_content = client.call_error(
            "launch_app", {"bundle_id": missing_bundle_id}
        )
        if missing_app != {
            "error": "APP_NOT_INSTALLED",
            "bundle_id": missing_bundle_id,
        }:
            fail(f"launch_app APP_NOT_INSTALLED payload drifted: {missing_app}")
        require_text_content(
            "launch_app APP_NOT_INSTALLED",
            missing_app_content,
            f"No installed macOS app found for bundle_id '{missing_bundle_id}'.",
        )

        missing_launch_path = recording_dir / "definitely-missing-launch-target.md"
        if missing_launch_path.exists():
            fail(f"owned missing-file fixture unexpectedly exists: {missing_launch_path}")
        missing_file, missing_file_content = client.call_error(
            "launch_app",
            {"bundle_id": owned_bundle_id, "urls": [str(missing_launch_path)]},
        )
        if missing_file != {
            "error": "FILE_NOT_FOUND",
            "url": str(missing_launch_path),
            "path": str(missing_launch_path),
        }:
            fail(f"launch_app FILE_NOT_FOUND payload drifted: {missing_file}")
        require_text_content(
            "launch_app FILE_NOT_FOUND",
            missing_file_content,
            f"Local launch_app url target does not exist: {missing_launch_path}",
        )
        after_preflight_error, _ = client.call("list_apps")
        after_preflight_error = require_app_inventory(
            "post-error list_apps", after_preflight_error
        )
        if any(
            app["bundle_id"] == owned_bundle_id and app["pid"] > 0
            for app in after_preflight_error["apps"]
        ):
            fail("launch_app FILE_NOT_FOUND mutated the stopped target lifecycle")

        launched, launched_content = client.call(
            "launch_app",
            {"bundle_id": owned_bundle_id},
        )
        launched = require_launch_result(
            f"launch_app {owned_app_name}",
            launched,
            launched_content,
            owned_bundle_id,
            owned_app_name,
        )
        launched_pid = launched["pid"]
        if (
            type(launched_pid) is not int
            or launched_pid <= 0
            or launched_pid in existing_app_pids
            or launched_pid in {os.getpid(), client.process.pid, peer.process.pid}
        ):
            fail(f"launch_app did not return a new owned {owned_app_name} pid: {launched}")
        owned_app_pid = launched_pid

        launched_app: dict[str, Any] | None = None
        identity_deadline = time.monotonic() + 5
        while time.monotonic() < identity_deadline:
            app_inventory, _ = client.call("list_apps")
            app_inventory = require_app_inventory(
                "post-launch list_apps", app_inventory
            )
            launched_app = next(
                (
                    app
                    for app in app_inventory["apps"]
                    if isinstance(app, dict) and app.get("pid") == launched_pid
                ),
                None,
            )
            if (
                launched_app is not None
                and launched_app.get("bundle_id") == owned_bundle_id
            ):
                break
            time.sleep(0.05)
        if (
            launched_app is None
            or launched_app.get("bundle_id") != owned_bundle_id
        ):
            command = process_command(launched_pid)
            expected_suffix = f"/{owned_app_name}.app/Contents/MacOS/{owned_app_name}"
            if not command.endswith(expected_suffix):
                fail(
                    "fresh list_apps and the OS process path did not bind the owned pid "
                    f"to {owned_bundle_id}: launch={launched}, "
                    f"inventory_app={launched_app}, command={command!r}"
                )

        launched_windows = launched["windows"]
        owned_windows: list[dict[str, Any]] = []
        window_deadline = time.monotonic() + 5
        while time.monotonic() < window_deadline:
            app_windows, _ = client.call("list_windows", {"pid": launched_pid})
            app_windows = require_window_inventory(
                f"{owned_app_name} list_windows", app_windows
            )
            owned_windows = [
                window
                for window in app_windows["windows"]
                if window["pid"] == launched_pid
            ]
            if owned_windows:
                break
            time.sleep(0.05)
        if not owned_windows:
            fail(f"new {owned_app_name} pid {launched_pid} exposed no owned window")
        fresh_window_ids = {window["window_id"] for window in owned_windows}
        if any(
            window["window_id"] not in fresh_window_ids for window in launched_windows
        ):
            fail(
                "launch_app returned a window identity absent from fresh list_windows: "
                f"launch={launched_windows}, fresh={owned_windows}"
            )

        killed_app = client.request(
            "tools/call", {"name": "kill_app", "arguments": {"pid": launched_pid}}
        )
        if killed_app != {
            "content": [
                {"type": "text", "text": f"✅ Sent SIGKILL to pid {launched_pid}."}
            ]
        }:
            fail(f"kill_app success payload drifted: {killed_app}")
        exit_deadline = time.monotonic() + 5
        while process_exists(launched_pid) and time.monotonic() < exit_deadline:
            time.sleep(0.05)
        if process_exists(launched_pid):
            fail(f"kill_app left owned {owned_app_name} pid {launched_pid} alive")
        inventory_exit_deadline = time.monotonic() + 10
        while True:
            apps_after_kill, _ = client.call("list_apps")
            apps_after_kill = require_app_inventory(
                "post-kill list_apps", apps_after_kill
            )
            if not any(
                isinstance(app, dict) and app.get("pid") == launched_pid
                for app in apps_after_kill["apps"]
            ):
                break
            if time.monotonic() >= inventory_exit_deadline:
                fail(f"list_apps retained killed {owned_app_name} pid {launched_pid}")
            time.sleep(0.05)
        owned_app_pid = None

        windows_value, windows_content = client.call("list_windows")
        windows = require_window_inventory(
            "list_windows", windows_value, windows_content
        )

        screen_value, screen_content = client.call("get_screen_size")
        screen = require_screen_geometry(
            "get_screen_size", screen_value, screen_content
        )

        cursor_value, cursor_content = client.call("get_cursor_position")
        cursor = require_cursor_position(
            "get_cursor_position", cursor_value, cursor_content
        )

        config_value, config_content = client.call("get_config")
        require_text_content(
            "get_config", config_content, "cua-driver-rs configuration"
        )
        config_snapshot = require_config("get_config", config_value)
        config_original = config_snapshot["max_image_dimension"]

        peer_value, peer_content = peer.call("get_config")
        require_text_content(
            "peer get_config", peer_content, "cua-driver-rs configuration"
        )
        peer_snapshot = require_config("peer get_config", peer_value)
        if peer_snapshot != config_snapshot:
            fail("fresh MCP peers began with different image configuration")

        retired, retired_content = peer.call_error(
            "set_config", {"key": "capture_scope", "value": "desktop"}
        )
        expected_retired = {
            "code": "config_key_retired",
            "key": "capture_scope",
            "replacement": "start_session.capture_scope",
        }
        if retired != expected_retired:
            fail(f"set_config retired-key payload drifted: {retired}")
        require_text_content(
            "set_config retired key",
            retired_content,
            "config key 'capture_scope' is retired; pass "
            "capture_scope=auto|window|desktop to start_session",
        )
        if require_config(
            "post-retired-key get_config", peer.call("get_config")[0]
        ) != peer_snapshot:
            fail("retired set_config key mutated peer configuration")

        keyed, keyed_content = peer.call(
            "set_config",
            {"key": "max_image_dimension", "value": config_original},
        )
        require_set_config("key/value set_config", keyed, config_original)
        require_text_content(
            "key/value set_config",
            keyed_content,
            f"Config updated: max_image_dimension={config_original} "
            "(session-scoped; persisted default unchanged)",
        )
        if require_config(
            "key/value get_config", peer.call("get_config")[0]
        ) != peer_snapshot:
            fail("idempotent key/value set_config changed peer configuration")

        config_probe = 0 if config_original != 0 else 1024
        changed, changed_content = client.call(
            "set_config", {"max_image_dimension": config_probe}
        )
        config_changed = True
        require_set_config("set_config", changed, config_probe)
        require_text_content(
            "set_config",
            changed_content,
            f"Config updated: max_image_dimension={config_probe} "
            "(session-scoped; persisted default unchanged)",
        )
        changed_snapshot = require_config(
            "changed get_config", client.call("get_config")[0]
        )
        if changed_snapshot != dict(
            config_snapshot, max_image_dimension=config_probe
        ):
            fail("set_config was not visible on the same MCP connection")
        if require_config(
            "isolated peer get_config", peer.call("get_config")[0]
        ) != peer_snapshot:
            fail("set_config leaked across MCP connection identities")
        restored, restored_content = client.call(
            "set_config", {"max_image_dimension": config_original}
        )
        require_set_config("restored set_config", restored, config_original)
        require_text_content(
            "restored set_config",
            restored_content,
            f"Config updated: max_image_dimension={config_original} "
            "(session-scoped; persisted default unchanged)",
        )
        config_changed = False
        if require_config(
            "restored get_config", client.call("get_config")[0]
        ) != config_snapshot:
            fail("restored configuration was not visible on the same connection")

        started, started_content = client.call(
            "start_session", {"session": session, "capture_scope": "auto"}
        )
        session_started = True
        require_started_session(
            "start_session",
            started,
            started_content,
            session=session,
            capture_scope="auto",
            effective_scope="window",
            desktop_unlocked=False,
            revived=False,
        )
        repeated, repeated_content = client.call(
            "start_session", {"session": session, "capture_scope": "auto"}
        )
        require_started_session(
            "idempotent start_session",
            repeated,
            repeated_content,
            session=session,
            capture_scope="auto",
            effective_scope="window",
            desktop_unlocked=False,
            revived=False,
        )
        conflict_message = (
            f"session '{session}' already uses capture_scope='auto'; "
            "capture scope is immutable until the session ends"
        )
        conflict, conflict_content = client.call_error(
            "start_session", {"session": session, "capture_scope": "window"}
        )
        expected_conflict = {
            "code": "session_policy_conflict",
            "session": session,
            "capture_scope": "auto",
            "requested_capture_scope": "window",
        }
        if conflict != expected_conflict:
            fail(f"start_session scope-conflict response drifted: {conflict}")
        require_text_content(
            "start_session scope conflict", conflict_content, conflict_message
        )
        state, state_content = client.call(
            "get_session_state", {"session": session}
        )
        require_session_state(
            "get_session_state after conflict",
            state,
            state_content,
            session=session,
            capture_scope="auto",
            effective_scope="window",
            desktop_unlocked=False,
        )

        prepare_message = (
            "no owned endpoint is available; pass allow_launch=true with an "
            "isolated profile and verified approval"
        )
        prepare_value, prepare_content = client.call(
            "browser_prepare", {"session": session, "pid": os.getpid()}
        )
        require_browser_refusal(
            "browser_prepare no-launch route",
            prepare_value,
            prepare_content,
            code="browser_requires_setup",
            message=prepare_message,
        )

        browser_probe_dir = Path(
            tempfile.mkdtemp(
                prefix="zcode-primary-browser-refusal-",
                dir=temp_parent if temp_parent else None,
            )
        ).resolve()
        upload_probe = browser_probe_dir / "upload-probe.txt"
        upload_probe.write_text("local typed-browser route probe\n", encoding="utf-8")
        download_root = browser_probe_dir / "downloads"
        download_root.mkdir()
        missing_target = "bt-zcode-missing"
        missing_tab = "tab-zcode-missing"
        stale_message = (
            f"target {missing_target} is not a live binding in this session — "
            "re-run get_browser_state with pid + window_id"
        )
        typed_browser_refusals = (
            (
                "get_browser_state",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "snapshot_format": "semantic_v2",
                    "include_screenshot": False,
                },
            ),
            (
                "browser_navigate",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "url": "about:blank",
                },
            ),
            (
                "browser_click",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "ref": "p1:0",
                },
            ),
            (
                "browser_type",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "ref": "p1:0",
                    "text": "zcode-browser-probe",
                },
            ),
            (
                "browser_pointer",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "action": "hover",
                    "ref": "p1:0",
                },
            ),
            (
                "browser_dialog",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "action": "inspect",
                },
            ),
            (
                "browser_set_input_files",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "ref": "p1:0",
                    "files": [str(upload_probe)],
                },
            ),
            (
                "browser_download",
                {
                    "session": session,
                    "target_id": missing_target,
                    "tab_id": missing_tab,
                    "ref": "p1:0",
                    "destination_root": str(download_root),
                },
            ),
        )
        for tool_name, arguments in typed_browser_refusals:
            refusal_value, refusal_content = client.call(tool_name, arguments)
            require_browser_refusal(
                f"{tool_name} unknown-target route",
                refusal_value,
                refusal_content,
                code="browser_binding_stale",
                message=stale_message,
            )
        if list(download_root.iterdir()) or upload_probe.read_text(encoding="utf-8") != (
            "local typed-browser route probe\n"
        ):
            fail("typed browser refusal routes changed local probe state")

        cursor_state, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        cursor_state = require_cursor_state(
            "get_agent_cursor_state", cursor_state, session, True
        )
        if cursor_state["position"] is not None:
            fail(f"fresh session cursor unexpectedly had a position: {cursor_state}")
        motion_set, _ = client.call("set_agent_cursor_motion", {"session": session})
        motion_set = require_object(
            "set_agent_cursor_motion", motion_set, {"session", "motion"}
        )
        if motion_set["session"] != session:
            fail(f"set_agent_cursor_motion returned the wrong session: {motion_set}")
        require_cursor_motion("set_agent_cursor_motion.motion", motion_set["motion"])
        theme_set, _ = client.call(
            "set_agent_cursor_theme",
            {
                "session": session,
                "theme_id": "cua.default",
                "reduced_motion": "auto",
            },
        )
        theme_set = require_object(
            "set_agent_cursor_theme", theme_set, {"session", "theme"}
        )
        if theme_set["session"] != session:
            fail(f"set_agent_cursor_theme returned the wrong session: {theme_set}")
        require_cursor_theme("set_agent_cursor_theme.theme", theme_set["theme"])
        replay_recording_dir = Path(
            tempfile.mkdtemp(
                prefix="zcode-primary-replay-",
                dir=temp_parent if temp_parent else None,
            )
        ).resolve()
        recording_dirs.append(replay_recording_dir)
        recording_dir = replay_recording_dir
        replay_start_value, replay_start_content = client.call(
            "start_recording",
            {"output_dir": str(replay_recording_dir), "record_video": False},
        )
        replay_start = require_recording_state(
            "replay fixture start_recording", replay_start_value
        )
        require_text_content(
            "replay fixture start_recording",
            replay_start_content,
            f"✅ Recording started -> {replay_recording_dir}",
        )
        recording_started = True
        if (
            replay_start["enabled"] is not True
            or replay_start["output_dir"] != str(replay_recording_dir)
            or replay_start["next_turn"] != 1
            or not replay_start["owner"]
        ):
            fail(f"replay fixture recording did not start exactly: {replay_start}")

        disabled, disabled_content = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": False}
        )
        if disabled != {"session": session, "enabled": False}:
            fail(f"set_agent_cursor_enabled did not disable the session cursor: {disabled}")
        require_text_content(
            "recorded set_agent_cursor_enabled",
            disabled_content,
            f"Agent cursor for session '{session}' disabled.",
        )
        disabled_state, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        require_cursor_state(
            "disabled get_agent_cursor_state", disabled_state, session, False
        )

        replay_live_value, replay_live_content = client.call("get_recording_state")
        replay_live = require_recording_state(
            "replay fixture recording state", replay_live_value, replay_live_content
        )
        if (
            replay_live["output_dir"] != str(replay_recording_dir)
            or replay_live["next_turn"] != 2
            or replay_live["owner"] != replay_start["owner"]
        ):
            fail(f"replay fixture did not record exactly one cursor action: {replay_live}")
        replay_stop_value, replay_stop_content = client.call("stop_recording")
        replay_stop = require_recording_state(
            "replay fixture stop_recording", replay_stop_value
        )
        require_text_content(
            "replay fixture stop_recording",
            replay_stop_content,
            "✅ Recording stopped.",
        )
        recording_started = False
        if replay_stop != expected_disabled_recording:
            fail(f"replay fixture recorder did not stop exactly: {replay_stop}")
        require_recording_manifest(
            "replay fixture session.json", replay_recording_dir / "session.json"
        )
        replay_action_path = replay_recording_dir / "turn-00001" / "action.json"
        try:
            replay_action = json.loads(replay_action_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            fail(f"replay fixture action.json is unavailable: {error}")
        if (
            replay_action.get("tool") != "set_agent_cursor_enabled"
            or replay_action.get("arguments")
            != {"session": session, "enabled": False}
        ):
            fail(f"replay fixture recorded the wrong action: {replay_action}")

        enabled, enabled_content = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": True}
        )
        if enabled != {"session": session, "enabled": True}:
            fail(f"set_agent_cursor_enabled did not restore the session cursor: {enabled}")
        require_text_content(
            "set_agent_cursor_enabled before replay",
            enabled_content,
            f"Agent cursor for session '{session}' enabled.",
        )
        restored_cursor, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        require_cursor_state(
            "restored get_agent_cursor_state", restored_cursor, session, True
        )

        replayed, replayed_content = client.call(
            "replay_trajectory",
            {
                "dir": str(replay_recording_dir),
                "delay_ms": 0,
                "stop_on_error": True,
            },
        )
        require_replay_result(
            "replay_trajectory",
            replayed,
            replayed_content,
            directory=replay_recording_dir,
            session=session,
        )
        replayed_cursor, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        require_cursor_state(
            "replayed get_agent_cursor_state", replayed_cursor, session, False
        )
        final_enabled, final_enabled_content = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": True}
        )
        if final_enabled != {"session": session, "enabled": True}:
            fail(f"set_agent_cursor_enabled did not restore after replay: {final_enabled}")
        require_text_content(
            "set_agent_cursor_enabled after replay",
            final_enabled_content,
            f"Agent cursor for session '{session}' enabled.",
        )
        final_cursor, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        require_cursor_state(
            "final restored get_agent_cursor_state", final_cursor, session, True
        )

        escalated, escalated_content = client.call(
            "escalate_session",
            {
                "session": session,
                "reason": "no_window_target",
                "detail": "ci contract proof",
            },
        )
        expected_escalated = expected_session_state(
            session,
            "auto",
            "desktop",
            True,
            "no_window_target",
            "ci contract proof",
        )
        if escalated != expected_escalated:
            fail(f"escalate_session returned inconsistent state: {escalated}")
        require_text_content(
            "escalate_session",
            escalated_content,
            f"✅ Session '{session}' escalated to desktop scope.",
        )
        repeated_escalation, repeated_escalation_content = client.call_error(
            "escalate_session",
            {"session": session, "reason": "other"},
        )
        if repeated_escalation != {
            "code": "desktop_already_active",
            "session": session,
        }:
            fail(
                "repeated escalate_session response drifted: "
                f"{repeated_escalation}"
            )
        require_text_content(
            "repeated escalate_session",
            repeated_escalation_content,
            f"session '{session}' already has effective desktop scope",
        )
        escalated_state, escalated_state_content = client.call(
            "get_session_state", {"session": session}
        )
        require_session_state(
            "get_session_state after escalation",
            escalated_state,
            escalated_state_content,
            session=session,
            capture_scope="auto",
            effective_scope="desktop",
            desktop_unlocked=True,
            escalation_reason="no_window_target",
            escalation_detail="ci contract proof",
        )
        if escalated_state != escalated:
            fail(
                "get_session_state did not preserve the exact one-way desktop escalation"
            )

        probe = subprocess.Popen(["/bin/sleep", "60"], text=True)
        result = client.request(
            "tools/call", {"name": "kill_app", "arguments": {"pid": probe.pid}}
        )
        if result != {
            "content": [
                {"type": "text", "text": f"✅ Sent SIGKILL to pid {probe.pid}."}
            ]
        }:
            fail(f"kill_app success payload drifted: {result}")
        probe.wait(timeout=5)
        if probe.returncode != -signal.SIGKILL:
            fail(f"kill_app left disposable pid {probe.pid} with status {probe.returncode}")
        probe = None
        ended, ended_content = client.call("end_session", {"session": session})
        require_ended_session("end_session", ended, ended_content, session)
        session_started = False
        ended_state, ended_state_content = client.call_error(
            "get_session_state", {"session": session}
        )
        require_ended_session_guard(
            "ended get_session_state",
            ended_state,
            ended_state_content,
            session=session,
            tool_name="get_session_state",
        )
        revived, revived_content = client.call(
            "start_session", {"session": session, "capture_scope": "desktop"}
        )
        session_started = True
        require_started_session(
            "revived start_session",
            revived,
            revived_content,
            session=session,
            capture_scope="desktop",
            effective_scope="desktop",
            desktop_unlocked=True,
            revived=True,
        )
        revived_state, revived_state_content = client.call(
            "get_session_state", {"session": session}
        )
        require_session_state(
            "revived get_session_state",
            revived_state,
            revived_state_content,
            session=session,
            capture_scope="desktop",
            effective_scope="desktop",
            desktop_unlocked=True,
        )
        reended, reended_content = client.call(
            "end_session", {"session": session}
        )
        require_ended_session(
            "revived end_session", reended, reended_content, session
        )
        session_started = False
        # The intentional trusted-host refusal may close that MCP proxy on
        # some Intel macOS hosts. Exercise it last on the otherwise idle peer
        # so an expected refusal cannot contaminate normal ZCode calls.
        prompt_refusal, prompt_content = peer.call_error(
            "check_permissions", {"prompt": True}
        )
        prompt_message = (
            "operating-system permission prompts must be initiated by a trusted "
            "host outside the agent tool path; call check_permissions with "
            "prompt=false to inspect state"
        )
        expected_prompt_refusal = {
            "status": "refused",
            "refusal": {
                "code": "os_permission_prompt_requires_trusted_host",
                "message": prompt_message,
            },
        }
        if prompt_refusal != expected_prompt_refusal:
            fail(f"public MCP prompt=true refusal drifted: {prompt_refusal}")
        require_text_content(
            "check_permissions prompt=true", prompt_content, prompt_message
        )
    finally:
        if owned_app_pid is not None and process_exists(owned_app_pid):
            try:
                client.call("kill_app", {"pid": owned_app_pid})
            except RuntimeError:
                try:
                    os.kill(owned_app_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if probe is not None and probe.poll() is None:
            probe.terminate()
            try:
                probe.wait(timeout=2)
            except subprocess.TimeoutExpired:
                probe.kill()
                probe.wait(timeout=2)
        if session_started:
            try:
                client.call("end_session", {"session": session})
            except RuntimeError:
                pass
        if config_changed and config_original is not None:
            try:
                client.call(
                    "set_config", {"max_image_dimension": config_original}
                )
            except RuntimeError:
                pass
        if recording_started and recording_dir is not None:
            try:
                current_recording = require_recording_state(
                    "cleanup recording state", client.call("get_recording_state")[0]
                )
                if (
                    current_recording["enabled"] is True
                    and current_recording["output_dir"] == str(recording_dir)
                ):
                    client.call("stop_recording")
                    recording_started = False
            except RuntimeError:
                pass
        peer.close()
        client.close()
        if browser_probe_dir is not None:
            shutil.rmtree(browser_probe_dir, ignore_errors=True)
        for directory in recording_dirs:
            shutil.rmtree(directory, ignore_errors=True)

    print(
        "Verified exact MCP handshake/errors, unrestricted legacy page mutation routing, all typed browser stdio routes through exact no-side-effect refusals, permission-free desktop inventory, recorder/replay, and non-running service-helper responses, primary diagnostics, complete schemas, connection isolation, idempotent/conflict/revival native session lifecycle, app/cursor control, and process control over stdio MCP."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
