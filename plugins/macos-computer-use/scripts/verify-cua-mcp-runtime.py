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
from pathlib import Path
from types import ModuleType
from typing import Any


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

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        if self.process.stdin is None or self.process.stdout is None:
            fail("MCP proxy has no stdio pipes")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], 35)
        if not ready:
            fail(f"{method} timed out")
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            fail(f"{method} received EOF: {stderr.strip()}")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            fail(f"{method} response was not JSON: {error}")
        if response.get("id") != request_id:
            fail(f"{method} response id drifted: {response}")
        if "error" in response:
            fail(f"{method} returned {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):
            fail(f"{method} returned no result object")
        return result

    def notify(self, method: str) -> None:
        if self.process.stdin is None:
            fail("MCP proxy has no stdin")
        message = {"jsonrpc": "2.0", "method": method, "params": {}}
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "zcode-ci-mcp", "version": "0.15.0"},
            },
        )
        if not isinstance(result.get("serverInfo"), dict):
            fail("initialize omitted serverInfo")
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


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: verify-cua-mcp-runtime.py /path/to/cua-driver /path/to/socket"
        )
    binary = Path(sys.argv[1]).resolve()
    socket = sys.argv[2]
    scripts = Path(__file__).resolve().parent
    required = load_contracts(scripts / "verify-cua-native-schema.py") | load_contracts(
        scripts / "verify-cua-browser-schema.py"
    )
    if len(required) != 46:
        fail(f"expected 46 pinned tools, got {len(required)}")

    client = MCPClient(binary, socket)
    probe: subprocess.Popen[str] | None = None
    session = f"zcode-ci-mcp-{os.getpid()}"
    session_started = False
    try:
        client.initialize()
        listed = client.request("tools/list")
        tools = listed.get("tools")
        if not isinstance(tools, list):
            fail("tools/list omitted tools")
        advertised = {
            tool.get("name"): tool
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        missing = required - set(advertised)
        if missing:
            fail(f"tools/list omitted required tools: {sorted(missing)}")
        for name in sorted(required):
            mcp_schema = advertised[name].get("inputSchema")
            direct_schema = describe(binary, name)
            if mcp_schema != direct_schema:
                fail(f"tools/list.inputSchema drifted from describe for {name}")

        apps, _ = client.call("list_apps")
        apps = require_object("list_apps", apps, {"apps"})
        if not isinstance(apps["apps"], list):
            fail("list_apps.apps is not an array")

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

        started, _ = client.call(
            "start_session", {"session": session, "capture_scope": "window"}
        )
        session_started = True
        if (
            not isinstance(started, dict)
            or started.get("session") != session
            or started.get("capture_scope") != "window"
            or started.get("effective_scope") != "window"
            or started.get("desktop_unlocked") is not False
            or started.get("active") is not True
        ):
            fail(f"start_session returned inconsistent state: {started}")
        state, _ = client.call("get_session_state", {"session": session})
        if (
            not isinstance(state, dict)
            or state.get("session") != session
            or state.get("capture_scope") != "window"
            or state.get("effective_scope") != "window"
            or state.get("desktop_unlocked") is not False
        ):
            fail(f"get_session_state returned inconsistent state: {state}")

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
    finally:
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
        client.close()

    print(
        "Verified the complete primary surface, session lifecycle, and process control over stdio MCP."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
