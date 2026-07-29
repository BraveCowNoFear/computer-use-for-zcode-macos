#!/usr/bin/env python3
"""Exercise the signed primary backend through its real stdio MCP proxy."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import select
import signal
import subprocess
import sys
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
                "clientInfo": {"name": "zcode-ci-mcp", "version": "0.17.6"},
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


def require_config(name: str, value: Any) -> int:
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
    if config["version"] != "0.13.1" or config["platform"] != "macos":
        fail(f"{name} returned the wrong runtime identity: {config}")
    dimension = config["max_image_dimension"]
    if type(dimension) is not int or dimension < 0:
        fail(f"{name}.max_image_dimension is not a non-negative integer")
    cursor = config["agent_cursor"]
    if not isinstance(cursor, dict) or type(cursor.get("enabled")) is not bool:
        fail(f"{name}.agent_cursor omitted the enabled boolean")
    return dimension


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

        health, _ = client.call("health_report")
        health = require_object(
            "health_report",
            health,
            {"schema_version", "platform", "driver_version", "overall", "checks"},
        )
        if (
            health["schema_version"] != "1"
            or health["platform"] != "darwin"
            or health["driver_version"] != "0.13.1"
            or health["overall"] not in {"ok", "degraded"}
            or not isinstance(health["checks"], list)
        ):
            fail(f"health_report returned the wrong runtime identity: {health}")
        health_checks = {
            entry.get("name"): entry
            for entry in health["checks"]
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }
        expected_health_checks = {
            "binary_version",
            "platform_supported",
            "session_active",
            "bundle_identity",
            "tcc_accessibility",
            "tcc_screen_recording",
            "ax_capability",
            "screen_capture_capability",
        }
        if set(health_checks) != expected_health_checks:
            fail(f"health_report checks drifted: {sorted(health_checks)}")
        for name in (
            "binary_version",
            "platform_supported",
            "session_active",
            "bundle_identity",
        ):
            if health_checks[name].get("status") != "pass":
                fail(f"health_report core check {name} did not pass: {health_checks[name]}")
        if health_checks["screen_capture_capability"].get("status") != "skip":
            fail("read-only health_report unexpectedly probed direct screen capture")

        permissions, _ = client.call("check_permissions", {"prompt": False})
        permissions = require_object(
            "check_permissions",
            permissions,
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
            or not isinstance(permissions["source"], dict)
            or permissions["source"].get("attribution") != "driver-daemon"
            or permissions["source"].get("bundle_id") != "com.trycua.driver"
        ):
            fail(f"check_permissions did not return read-only daemon state: {permissions}")
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

        apps, _ = client.call("list_apps")
        apps = require_object("list_apps", apps, {"apps"})
        if not isinstance(apps["apps"], list):
            fail("list_apps.apps is not an array")
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

        launched, _ = client.call(
            "launch_app",
            {"bundle_id": owned_bundle_id},
        )
        launched_pid = launched.get("pid") if isinstance(launched, dict) else None
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
            app_inventory = require_object(
                "post-launch list_apps", app_inventory, {"apps"}
            )
            if not isinstance(app_inventory["apps"], list):
                fail("post-launch list_apps.apps is not an array")
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

        launched_windows = launched.get("windows", [])
        if not isinstance(launched_windows, list):
            fail(f"launch_app.windows is not an array: {launched}")
        owned_windows = [
            window
            for window in launched_windows
            if isinstance(window, dict)
            and window.get("pid") == launched_pid
            and type(window.get("window_id")) is int
        ]
        window_deadline = time.monotonic() + 5
        while not owned_windows and time.monotonic() < window_deadline:
            app_windows, _ = client.call("list_windows", {"pid": launched_pid})
            app_windows = require_object(
                f"{owned_app_name} list_windows",
                app_windows,
                {"windows", "current_space_id"},
            )
            if not isinstance(app_windows["windows"], list):
                fail(f"{owned_app_name} list_windows.windows is not an array")
            owned_windows = [
                window
                for window in app_windows["windows"]
                if isinstance(window, dict)
                and window.get("pid") == launched_pid
                and type(window.get("window_id")) is int
            ]
            if not owned_windows:
                time.sleep(0.05)
        if not owned_windows:
            fail(f"new {owned_app_name} pid {launched_pid} exposed no owned window")

        client.call("kill_app", {"pid": launched_pid})
        exit_deadline = time.monotonic() + 5
        while process_exists(launched_pid) and time.monotonic() < exit_deadline:
            time.sleep(0.05)
        if process_exists(launched_pid):
            fail(f"kill_app left owned {owned_app_name} pid {launched_pid} alive")
        apps_after_kill, _ = client.call("list_apps")
        apps_after_kill = require_object(
            "post-kill list_apps", apps_after_kill, {"apps"}
        )
        if any(
            isinstance(app, dict) and app.get("pid") == launched_pid
            for app in apps_after_kill["apps"]
        ):
            fail(f"list_apps retained killed {owned_app_name} pid {launched_pid}")
        owned_app_pid = None

        windows, _ = client.call("list_windows")
        windows = require_object(
            "list_windows", windows, {"windows", "current_space_id"}
        )
        if not isinstance(windows["windows"], list):
            fail("list_windows.windows is not an array")

        screen, _ = client.call("get_screen_size")
        screen = require_object(
            "get_screen_size", screen, {"width", "height", "scale_factor"}
        )
        if screen["width"] <= 0 or screen["height"] <= 0 or screen["scale_factor"] <= 0:
            fail("get_screen_size returned non-positive geometry")

        cursor, _ = client.call("get_cursor_position")
        cursor = require_object("get_cursor_position", cursor, {"x", "y"})
        if not all(isinstance(cursor[key], (int, float)) for key in ("x", "y")):
            fail("get_cursor_position returned non-numeric coordinates")

        config_original = require_config("get_config", client.call("get_config")[0])
        peer_original = require_config("peer get_config", peer.call("get_config")[0])
        if peer_original != config_original:
            fail("fresh MCP peers began with different image configuration")
        config_probe = 0 if config_original != 0 else 1024
        changed, _ = client.call(
            "set_config", {"max_image_dimension": config_probe}
        )
        config_changed = True
        changed = require_object(
            "set_config", changed, {"version", "platform", "max_image_dimension"}
        )
        if changed.get("max_image_dimension") != config_probe:
            fail(f"set_config did not return the requested value: {changed}")
        if require_config("changed get_config", client.call("get_config")[0]) != config_probe:
            fail("set_config was not visible on the same MCP connection")
        if require_config("isolated peer get_config", peer.call("get_config")[0]) != config_original:
            fail("set_config leaked across MCP connection identities")
        restored, _ = client.call(
            "set_config", {"max_image_dimension": config_original}
        )
        if require_object(
            "restored set_config",
            restored,
            {"version", "platform", "max_image_dimension"},
        ).get("max_image_dimension") != config_original:
            fail("set_config did not restore the original image configuration")
        config_changed = False
        if require_config("restored get_config", client.call("get_config")[0]) != config_original:
            fail("restored configuration was not visible on the same connection")

        started, _ = client.call(
            "start_session", {"session": session, "capture_scope": "auto"}
        )
        session_started = True
        if (
            not isinstance(started, dict)
            or started.get("session") != session
            or started.get("capture_scope") != "auto"
            or started.get("effective_scope") != "window"
            or started.get("desktop_unlocked") is not False
            or started.get("active") is not True
        ):
            fail(f"start_session returned inconsistent state: {started}")
        state, _ = client.call("get_session_state", {"session": session})
        if (
            not isinstance(state, dict)
            or state.get("session") != session
            or state.get("capture_scope") != "auto"
            or state.get("effective_scope") != "window"
            or state.get("desktop_unlocked") is not False
        ):
            fail(f"get_session_state returned inconsistent state: {state}")

        cursor_state, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        if (
            not isinstance(cursor_state, dict)
            or cursor_state.get("session") != session
            or cursor_state.get("enabled") is not True
            or not isinstance(cursor_state.get("theme"), dict)
            or not isinstance(cursor_state.get("visual_state"), dict)
            or not isinstance(cursor_state.get("motion"), dict)
        ):
            fail(f"get_agent_cursor_state returned inconsistent state: {cursor_state}")
        disabled, _ = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": False}
        )
        if disabled != {"session": session, "enabled": False}:
            fail(f"set_agent_cursor_enabled did not disable the session cursor: {disabled}")
        disabled_state, _ = client.call(
            "get_agent_cursor_state", {"session": session}
        )
        if not isinstance(disabled_state, dict) or disabled_state.get("enabled") is not False:
            fail("disabled session cursor did not read back as disabled")
        enabled, _ = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": True}
        )
        if enabled != {"session": session, "enabled": True}:
            fail(f"set_agent_cursor_enabled did not restore the session cursor: {enabled}")

        escalated, _ = client.call(
            "escalate_session",
            {
                "session": session,
                "reason": "no_window_target",
                "detail": "ci contract proof",
            },
        )
        if (
            not isinstance(escalated, dict)
            or escalated.get("session") != session
            or escalated.get("capture_scope") != "auto"
            or escalated.get("effective_scope") != "desktop"
            or escalated.get("desktop_unlocked") is not True
            or escalated.get("escalation_reason") != "no_window_target"
            or escalated.get("escalation_detail") != "ci contract proof"
        ):
            fail(f"escalate_session returned inconsistent state: {escalated}")
        escalated_state, _ = client.call("get_session_state", {"session": session})
        if escalated_state != escalated:
            fail(
                "get_session_state did not preserve the exact one-way desktop escalation"
            )

        probe = subprocess.Popen(["/bin/sleep", "60"], text=True)
        result = client.request(
            "tools/call", {"name": "kill_app", "arguments": {"pid": probe.pid}}
        )
        if result.get("isError"):
            fail(f"kill_app returned a tool error: {result.get('content')}")
        probe.wait(timeout=5)
        if probe.returncode != -signal.SIGKILL:
            fail(f"kill_app left disposable pid {probe.pid} with status {probe.returncode}")
        probe = None
        ended, _ = client.call("end_session", {"session": session})
        if (
            not isinstance(ended, dict)
            or ended.get("session") != session
            or ended.get("active") is not False
        ):
            fail(f"end_session returned inconsistent state: {ended}")
        session_started = False
        # The intentional trusted-host refusal may close that MCP proxy on
        # some Intel macOS hosts. Exercise it last on the otherwise idle peer
        # so an expected refusal cannot contaminate normal ZCode calls.
        prompt_result = peer.request(
            "tools/call",
            {"name": "check_permissions", "arguments": {"prompt": True}},
        )
        if not prompt_result.get(
            "isError"
        ) or "os_permission_prompt_requires_trusted_host" not in json.dumps(
            prompt_result, ensure_ascii=False
        ):
            fail(
                "public MCP prompt=true did not fail at the trusted-host TCC "
                f"boundary: {prompt_result}"
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
        peer.close()
        client.close()

    print(
        "Verified exact MCP handshake/errors, unrestricted legacy page mutation routing, primary diagnostics, complete schemas, connection isolation, native app/cursor/session lifecycle, and process control over stdio MCP."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
