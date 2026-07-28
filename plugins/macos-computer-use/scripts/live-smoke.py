"""Interactive Mac-only MCP smoke test against a disposable AppKit window."""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, TextIO


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "live_fixture.py"
DATA_DIR = Path(os.environ.get("MACOS_CUA_DATA_DIR", str(ROOT / ".local-data")))


def read_line(stream: TextIO, timeout: float, label: str) -> str:
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise RuntimeError(f"Timed out waiting for {label}")
    line = stream.readline()
    if not line:
        raise RuntimeError(f"{label} exited without a response")
    return line.rstrip("\r\n")


class MCPClient:
    def __init__(self, command: list[str], *, extra_env: dict[str, str] | None = None) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(extra_env or {})
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.next_id = 1

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = json.loads(read_line(self.process.stdout, 35, method))
        if response.get("id") != request_id:
            raise RuntimeError(f"Unexpected MCP response id for {method}: {response}")
        if "error" in response:
            raise RuntimeError(f"MCP {method} failed: {response['error']}")
        return response["result"]

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        assert self.process.stdin is not None
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def initialize(self) -> dict[str, Any]:
        initialized = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "zcode-live-smoke", "version": "0.8.27"},
            },
        )
        self.notify("notifications/initialized")
        return initialized

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[Any, list[dict[str, Any]]]:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            detail = " | ".join(str(item.get("text", "")) for item in result.get("content", []))
            raise RuntimeError(f"{name} returned an error: {detail}")
        return result.get("structuredContent"), result.get("content", [])

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)


def element_index(tree: str, role: str, text: str | None = None) -> int:
    for line in tree.splitlines():
        match = re.match(rf"\s*\[(\d+)\]\s+{re.escape(role)}(?:\s|$)", line)
        if match and (text is None or text in line):
            return int(match.group(1))
    suffix = f" containing {text!r}" if text else ""
    raise RuntimeError(f"Could not find {role}{suffix} in Accessibility tree:\n{tree}")


def require_image(content: list[dict[str, Any]], step: str) -> None:
    if not any(item.get("type") == "image" and item.get("data") for item in content):
        raise RuntimeError(f"{step} did not return a native MCP image block")


def primary_element(elements: list[dict[str, Any]], role: str, text: str | None = None) -> dict[str, Any]:
    for element in elements:
        searchable = " ".join(str(element.get(key, "")) for key in ("label", "value"))
        if element.get("role") == role and (text is None or text in searchable):
            return element
    suffix = f" containing {text!r}" if text else ""
    raise RuntimeError(f"Could not find primary {role}{suffix} in {len(elements)} structured elements")


def terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_primary(fixture_pid: int) -> dict[str, Any]:
    client = MCPClient(
        ["/bin/bash", str(ROOT / "scripts" / "run-cua-driver.sh")],
        extra_env={
            "MACOS_CUA_PLUGIN_ROOT": str(ROOT),
            "MACOS_CUA_DATA_DIR": str(DATA_DIR),
        },
    )
    session = f"zcode-live-{uuid.uuid4().hex}"
    session_started = False
    report: dict[str, Any] = {"steps": []}
    try:
        initialized = client.initialize()
        advertised = client.request("tools/list").get("tools", [])
        names = {tool.get("name") for tool in advertised}
        required = {
            "check_permissions",
            "start_session",
            "end_session",
            "list_windows",
            "get_window_state",
            "type_text",
            "click",
        }
        missing = sorted(required - names)
        if missing:
            raise RuntimeError(f"Primary MCP is missing required live-smoke tools: {', '.join(missing)}")
        report["serverVersion"] = initialized["serverInfo"]["version"]
        report["steps"].append("primary_initialized")

        permissions, _ = client.call("check_permissions", {"prompt": False})
        if not permissions.get("accessibility") or not permissions.get("screen_recording"):
            raise RuntimeError(
                "Grant Accessibility and Screen Recording to CuaDriver.app, restart ZCode, and rerun live-smoke.sh"
            )
        attribution = permissions.get("source", {}).get("attribution")
        if attribution != "driver-daemon":
            raise RuntimeError(f"Primary permission status was attributed to {attribution!r}, not driver-daemon")
        report["permissionAttribution"] = attribution
        report["steps"].append("primary_permissions_ready")

        client.call("start_session", {"session": session, "capture_scope": "window"})
        session_started = True
        windows_state, _ = client.call("list_windows", {"pid": fixture_pid})
        matches = [
            window
            for window in windows_state.get("windows", [])
            if window.get("title") == "ZCode Computer Use Live Smoke"
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one primary fixture window, found {len(matches)}")
        window = matches[0]
        pid = int(window["pid"])
        window_id = int(window["window_id"])
        report["window"] = {key: window.get(key) for key in ("window_id", "pid", "app_name", "title")}
        report["steps"].append("primary_window_bound")

        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary initial window state")
        field = primary_element(state.get("elements", []), "AXTextField", "Smoke input")
        token = f"zcode-primary-{uuid.uuid4().hex[:10]}"
        client.call(
            "type_text",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "element_index": field["element_index"],
                "text": token,
            },
        )
        report["steps"].append("primary_background_text_typed")

        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after typing")
        if token not in state.get("tree_markdown", ""):
            raise RuntimeError("Fresh primary state did not contain the text sent through type_text")
        button = primary_element(state.get("elements", []), "AXButton", "Copy value")
        client.call(
            "click",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "element_index": button["element_index"],
            },
        )
        report["steps"].append("primary_background_button_clicked")

        final_state, final_content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(final_content, "primary final window state")
        expected = f"Received: {token}"
        if expected not in final_state.get("tree_markdown", ""):
            raise RuntimeError(f"Primary final visible/AX result did not contain {expected!r}")
        report["steps"].append("primary_visible_result_verified")
        return report
    finally:
        if session_started:
            try:
                client.call("end_session", {"session": session})
            except Exception:
                pass
        client.close()


def run_fallback() -> dict[str, Any]:
    client = MCPClient([sys.executable, "-m", "macos_cua.server"])
    report: dict[str, Any] = {"steps": []}
    try:
        initialized = client.initialize()
        client.request("tools/list")
        health, _ = client.call("computer_use_health")
        if not health.get("accessibility") or not health.get("screenRecording"):
            raise RuntimeError(
                "Grant Accessibility and Screen Recording to this Python/ZCode runtime, restart it, and rerun live-smoke.sh"
            )
        report["serverVersion"] = initialized["serverInfo"]["version"]
        report["steps"].append("fallback_permissions_ready")

        windows, _ = client.call("list_windows")
        matches = [window for window in windows if window.get("title") == "ZCode Computer Use Live Smoke"]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one fallback fixture window, found {len(matches)}")
        window = matches[0]
        report["window"] = {key: window.get(key) for key in ("id", "app", "pid", "title")}
        report["steps"].append("fallback_window_bound")

        activated, _ = client.call("activate_window", {"window": window})
        window = activated["window"]
        report["steps"].append("fallback_window_activated")

        state, content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(content, "fallback initial window state")
        tree = state["accessibility"]["tree"]
        field_index = element_index(tree, "AXTextField")
        client.call("click", {"window": window, "element_index": field_index})
        report["steps"].append("fallback_field_focused")

        desktop, desktop_content = client.call("get_desktop_state")
        require_image(desktop_content, "fallback desktop state before shortcut")
        desktop_id = desktop["screenshots"][0]["id"]
        client.call("desktop_press_key", {"key": "Command+a", "screenshotId": desktop_id})

        desktop, desktop_content = client.call("get_desktop_state")
        require_image(desktop_content, "fallback desktop state before typing")
        desktop_id = desktop["screenshots"][0]["id"]
        token = f"zcode-fallback-{uuid.uuid4().hex[:10]}"
        client.call("desktop_type_text", {"text": token, "screenshotId": desktop_id})
        report["steps"].append("fallback_desktop_text_typed")

        state, content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(content, "fallback window state after typing")
        tree = state["accessibility"]["tree"]
        if token not in tree:
            raise RuntimeError("Fresh fallback state did not contain the text sent through desktop_type_text")
        button_index = element_index(tree, "AXButton", "Copy value")
        client.call("click", {"window": window, "element_index": button_index})
        report["steps"].append("fallback_button_clicked")

        final_state, final_content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(final_content, "fallback final window state")
        expected = f"Received: {token}"
        if expected not in final_state["accessibility"]["tree"]:
            raise RuntimeError(f"Fallback final visible/AX result did not contain {expected!r}")
        report["steps"].append("fallback_visible_result_verified")
        return report
    finally:
        client.close()


def main() -> int:
    if sys.platform != "darwin":
        raise RuntimeError("The live GUI smoke test must run in a logged-in macOS desktop session")

    fixture = subprocess.Popen(
        [sys.executable, str(FIXTURE)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report: dict[str, Any] = {"ok": False, "steps": []}
    try:
        assert fixture.stdout is not None
        ready = read_line(fixture.stdout, 20, "AppKit fixture")
        if not ready.startswith("READY "):
            raise RuntimeError(f"Fixture failed to become ready: {ready}")
        report["steps"].append("fixture_ready")
        report["primary"] = run_primary(fixture.pid)
        report["steps"].append("primary_complete")
        report["fallback"] = run_fallback()
        report["steps"].append("fallback_complete")
        report["ok"] = True
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        terminate(fixture)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
