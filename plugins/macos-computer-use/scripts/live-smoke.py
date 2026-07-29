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
FIXTURE_BUTTON_CENTER_X = 115.0
FIXTURE_BUTTON_CENTER_Y_FROM_CONTENT_BOTTOM = 122.0
FIXTURE_SLIDER_START_X = 252.0
FIXTURE_SLIDER_END_X = 588.0
FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM = 122.0
FIXTURE_SCROLL_PROBE_CENTER_X = 510.0
FIXTURE_SCROLL_PROBE_CENTER_Y_FROM_CONTENT_BOTTOM = 57.0
FIXTURE_GESTURE_PROBE_CENTER_X = 510.0
FIXTURE_GESTURE_PROBE_CENTER_Y_FROM_CONTENT_BOTTOM = 22.0


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
                "clientInfo": {"name": "zcode-live-smoke", "version": "0.11.1"},
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
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
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


def require_action_verdict(result: dict[str, Any], step: str) -> None:
    effect = result.get("effect")
    verified = result.get("verified")
    if effect not in {"confirmed", "unverifiable", "suspected_noop"} or not isinstance(verified, bool):
        raise RuntimeError(f"{step} returned no usable action verdict: {result}")


def fixture_screenshot_point(
    state: dict[str, Any], content_x: float, content_y_from_bottom: float
) -> tuple[float, float]:
    """Map a disposable-fixture Cocoa content point into returned PNG pixels."""
    window = state["window"]
    screenshot = state["screenshots"][0]
    bounds = window["bounds"]
    width = float(bounds["width"])
    height = float(bounds["height"])
    image_width = float(screenshot["width"])
    image_height = float(screenshot["height"])
    if min(width, height, image_width, image_height) <= 0:
        raise RuntimeError(f"Fallback fixture returned invalid screenshot geometry: {state}")
    frame_y_from_top = height - content_y_from_bottom
    return (
        content_x * image_width / width,
        frame_y_from_top * image_height / height,
    )


def primary_screenshot_point(
    state: dict[str, Any], content_x: float, content_y_from_bottom: float
) -> tuple[float, float]:
    """Map a fixture Cocoa point into Cua Driver's returned window PNG."""
    bounds = state["window_bounds"]
    width = float(bounds["width"])
    height = float(bounds["height"])
    image_width = float(state["screenshot_width"])
    image_height = float(state["screenshot_height"])
    if min(width, height, image_width, image_height) <= 0:
        raise RuntimeError(f"Primary fixture returned invalid screenshot geometry: {state}")
    return (
        content_x * image_width / width,
        (height - content_y_from_bottom) * image_height / height,
    )


def fixture_button_screenshot_point(state: dict[str, Any]) -> tuple[float, float]:
    return fixture_screenshot_point(
        state,
        FIXTURE_BUTTON_CENTER_X,
        FIXTURE_BUTTON_CENTER_Y_FROM_CONTENT_BOTTOM,
    )


def fixture_screen_point(
    state: dict[str, Any], content_x: float, content_y_from_bottom: float
) -> tuple[float, float]:
    bounds = state["window"]["bounds"]
    return (
        float(bounds["x"]) + content_x,
        float(bounds["y"]) + float(bounds["height"]) - content_y_from_bottom,
    )


def demo_cursor_target(width: float, height: float) -> dict[str, float]:
    """Choose an obvious in-bounds screen point for the session-only cursor glide."""

    def axis_target(size: float) -> float:
        if size <= 2:
            raise RuntimeError(f"Primary screen axis was too small for a cursor target: {size}")
        return round(min(max(24.0, size * 0.25), size - 1.0), 3)

    return {"x": axis_target(float(width)), "y": axis_target(float(height))}


def new_live_session() -> str:
    """Return a unique task label that remains readable in the cursor badge."""
    return f"zcode-smoke-{uuid.uuid4().hex[:8]}"


def desktop_screenshot_point(
    state: dict[str, Any], screen_x: float, screen_y: float
) -> tuple[float, float]:
    """Map a primary-display Quartz point into its fresh desktop PNG pixels."""
    screen_width = float(state["screen_width"])
    screen_height = float(state["screen_height"])
    image_width = float(state["screenshot_width"])
    image_height = float(state["screenshot_height"])
    if min(screen_width, screen_height, image_width, image_height) <= 0:
        raise RuntimeError(f"Primary desktop returned invalid geometry: {state}")
    return (
        float(screen_x) * image_width / screen_width,
        float(screen_y) * image_height / screen_height,
    )


def nudged_primary_pointer_target(
    state: dict[str, Any], current: dict[str, Any]
) -> dict[str, float]:
    """Choose a small, observable pointer move that stays on the primary display."""
    width = float(state["screen_width"])
    height = float(state["screen_height"])
    if width <= 30 or height <= 4:
        raise RuntimeError(f"Primary display was too small for pointer proof: {state}")
    x = min(max(float(current["x"]), 2.0), width - 3.0)
    y = min(max(float(current["y"]), 2.0), height - 3.0)
    x += 12.0 if x <= width - 15.0 else -12.0
    return {"x": round(x), "y": round(y)}


def cursor_matches(actual: dict[str, Any], expected: dict[str, Any], tolerance: float = 1.0) -> bool:
    return all(
        isinstance(actual.get(axis), (int, float))
        and isinstance(expected.get(axis), (int, float))
        and abs(float(actual[axis]) - float(expected[axis])) <= tolerance
        for axis in ("x", "y")
    )


def restore_pointer_direct(position: dict[str, Any]) -> None:
    """Best-effort emergency restoration, including a cursor on another display."""
    import Quartz  # type: ignore[import-not-found]

    result = Quartz.CGWarpMouseCursorPosition(
        (float(position["x"]), float(position["y"]))
    )
    if result not in (None, 0):
        raise RuntimeError(f"Quartz pointer restoration failed with CGError {result}")
    time.sleep(0.05)


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_process_exit(pid: int, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True
        time.sleep(0.05)
    return not process_is_alive(pid)


def primary_element(elements: list[dict[str, Any]], role: str, text: str | None = None) -> dict[str, Any]:
    for element in elements:
        searchable = " ".join(str(element.get(key, "")) for key in ("label", "value"))
        if element.get("role") == role and (text is None or text in searchable):
            return element
    suffix = f" containing {text!r}" if text else ""
    raise RuntimeError(f"Could not find primary {role}{suffix} in {len(elements)} structured elements")


def primary_element_target(element: dict[str, Any]) -> dict[str, Any]:
    token = element.get("element_token")
    if isinstance(token, str) and token:
        return {"element_token": token}
    index = element.get("element_index")
    if isinstance(index, int):
        return {"element_index": index}
    raise RuntimeError(f"Primary element has neither an element_token nor element_index: {element}")


def require_cursor_position(state: dict[str, Any], label: str) -> dict[str, float]:
    position = state.get("position")
    if not isinstance(position, dict) or not all(
        isinstance(position.get(axis), (int, float)) for axis in ("x", "y")
    ):
        raise RuntimeError(f"{label} did not update the visible session cursor: {state}")
    return {"x": float(position["x"]), "y": float(position["y"])}


def require_cursor_action(
    client: MCPClient, session: str, expected: str, label: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 0.35
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_state, _ = client.call("get_agent_cursor_state", {"session": session})
        visual = last_state.get("visual_state", {})
        if (
            visual.get("requested_action") == expected
            and visual.get("resolved_action") == expected
        ):
            return last_state
        time.sleep(0.01)
    raise RuntimeError(f"{label} did not animate as {expected!r}: {last_state}")


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
    session = new_live_session()
    session_started = False
    original_real_cursor: dict[str, Any] | None = None
    isolated_app_pid: int | None = None
    report: dict[str, Any] = {"sessionLabel": session, "steps": []}
    try:
        initialized = client.initialize()
        advertised = client.request("tools/list").get("tools", [])
        names = {tool.get("name") for tool in advertised}
        required = {
            "check_permissions",
            "start_session",
            "get_session_state",
            "escalate_session",
            "end_session",
            "get_agent_cursor_state",
            "set_agent_cursor_enabled",
            "get_cursor_position",
            "get_screen_size",
            "move_cursor",
            "list_apps",
            "list_windows",
            "launch_app",
            "get_window_state",
            "get_desktop_state",
            "kill_app",
            "type_text",
            "press_key",
            "hotkey",
            "click",
            "double_click",
            "right_click",
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

        client.call("start_session", {"session": session, "capture_scope": "auto"})
        session_started = True
        cursor_disabled, _ = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": False}
        )
        if cursor_disabled.get("session") != session or cursor_disabled.get("enabled") is not False:
            raise RuntimeError(f"Primary cursor did not disable for its exact session: {cursor_disabled}")
        cursor_enabled, _ = client.call(
            "set_agent_cursor_enabled", {"session": session, "enabled": True}
        )
        cursor_state, _ = client.call("get_agent_cursor_state", {"session": session})
        theme = cursor_state.get("theme", {})
        if (
            cursor_enabled.get("session") != session
            or cursor_enabled.get("enabled") is not True
            or cursor_state.get("session") != session
            or cursor_state.get("enabled") is not True
            or not isinstance(theme.get("id"), str)
            or not theme.get("id")
        ):
            raise RuntimeError(f"Primary session cursor was not ready: {cursor_state}")
        report["cursorTheme"] = theme["id"]
        report["steps"].append("primary_session_cursor_ready")

        apps_before, _ = client.call("list_apps", {})
        existing_calculator_pids = {
            int(app["pid"])
            for app in apps_before.get("apps", [])
            if app.get("bundle_id") == "com.apple.calculator"
            and isinstance(app.get("pid"), int)
            and int(app["pid"]) > 0
        }
        launched, _ = client.call(
            "launch_app",
            {
                "session": session,
                "bundle_id": "com.apple.calculator",
                "creates_new_application_instance": True,
            },
        )
        launched_pid = launched.get("pid")
        if (
            not isinstance(launched_pid, int)
            or launched_pid <= 0
            or launched_pid in existing_calculator_pids
            or launched_pid in {fixture_pid, os.getpid()}
            or launched.get("bundle_id") != "com.apple.calculator"
        ):
            raise RuntimeError(f"Primary launch_app did not return an isolated Calculator: {launched}")
        isolated_app_pid = launched_pid
        launched_windows = [
            window
            for window in launched.get("windows", [])
            if window.get("pid") == launched_pid and isinstance(window.get("window_id"), int)
        ]
        window_deadline = time.monotonic() + 3.0
        while not launched_windows and time.monotonic() < window_deadline:
            listed_windows, _ = client.call("list_windows", {"pid": launched_pid})
            launched_windows = [
                window
                for window in listed_windows.get("windows", [])
                if window.get("pid") == launched_pid and isinstance(window.get("window_id"), int)
            ]
            if not launched_windows:
                time.sleep(0.05)
        if not launched_windows:
            raise RuntimeError(f"Primary isolated Calculator returned no owned window: {launched}")
        calculator_window = launched_windows[0]
        quit_result, _ = client.call(
            "hotkey",
            {
                "session": session,
                "pid": launched_pid,
                "window_id": calculator_window["window_id"],
                "keys": ["cmd", "q"],
                "delivery_mode": "foreground",
            },
        )
        require_action_verdict(quit_result, "primary isolated Calculator quit")
        if not wait_for_process_exit(launched_pid):
            client.call("kill_app", {"pid": launched_pid})
            if not wait_for_process_exit(launched_pid):
                raise RuntimeError(f"Primary could not clean up isolated Calculator pid {launched_pid}")
            report["isolatedAppCleanup"] = "kill_app_after_quit_noop"
        else:
            report["isolatedAppCleanup"] = "foreground_cmd_q"
        isolated_app_pid = None
        report["isolatedAppPid"] = launched_pid
        report["steps"].append("primary_isolated_app_lifecycle_verified")

        # Keep these read-only diagnostics anonymous. Tying a desktop-scoped
        # helper to a window-only session would correctly require escalation.
        screen_size, _ = client.call("get_screen_size", {})
        width = screen_size.get("width")
        height = screen_size.get("height")
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            raise RuntimeError(f"Primary screen size was not numeric: {screen_size}")
        real_cursor_before, _ = client.call("get_cursor_position", {})
        if not isinstance(real_cursor_before, dict) or not cursor_matches(
            real_cursor_before, real_cursor_before, tolerance=0
        ):
            raise RuntimeError(f"Primary real pointer position was invalid: {real_cursor_before}")
        original_real_cursor = dict(real_cursor_before)
        virtual_target = demo_cursor_target(width, height)
        client.call(
            "move_cursor",
            {"session": session, "scope": "window", **virtual_target},
        )
        cursor_state, _ = client.call("get_agent_cursor_state", {"session": session})
        moved_cursor_position = require_cursor_position(cursor_state, "Primary move_cursor")
        if moved_cursor_position != virtual_target:
            raise RuntimeError(
                f"Primary virtual cursor reached {moved_cursor_position}, expected {virtual_target}"
            )
        real_cursor_after, _ = client.call("get_cursor_position", {})
        if real_cursor_after != real_cursor_before:
            raise RuntimeError(
                "Window-scoped move_cursor changed the user's real pointer: "
                f"before={real_cursor_before}, after={real_cursor_after}"
            )
        report["virtualCursorPosition"] = moved_cursor_position
        report["steps"].append("primary_virtual_cursor_moved_without_real_pointer")
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

        gesture_point = primary_screenshot_point(
            state,
            FIXTURE_GESTURE_PROBE_CENTER_X,
            FIXTURE_GESTURE_PROBE_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        right_clicked, _ = client.call(
            "right_click",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "x": gesture_point[0],
                "y": gesture_point[1],
            },
        )
        require_action_verdict(right_clicked, "primary right_click")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after right click")
        if "Gesture: right" not in state.get("tree_markdown", ""):
            raise RuntimeError("Primary right_click did not reach the fixture gesture probe")
        report["steps"].append("primary_background_right_click_verified")

        gesture_point = primary_screenshot_point(
            state,
            FIXTURE_GESTURE_PROBE_CENTER_X,
            FIXTURE_GESTURE_PROBE_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        double_clicked, _ = client.call(
            "double_click",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "x": gesture_point[0],
                "y": gesture_point[1],
            },
        )
        require_action_verdict(double_clicked, "primary double_click")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after double click")
        if "Gesture: double" not in state.get("tree_markdown", ""):
            raise RuntimeError("Primary double_click did not reach the fixture gesture probe")
        report["steps"].append("primary_background_double_click_verified")

        drag_start = primary_screenshot_point(
            state,
            FIXTURE_SLIDER_START_X,
            FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        drag_end = primary_screenshot_point(
            state,
            FIXTURE_SLIDER_END_X,
            FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        dragged, _ = client.call(
            "drag",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "from_x": drag_start[0],
                "from_y": drag_start[1],
                "to_x": drag_end[0],
                "to_y": drag_end[1],
                "duration_ms": 350,
                "steps": 16,
                "delivery_mode": "foreground",
            },
        )
        require_action_verdict(dragged, "primary foreground drag")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after drag")
        slider_match = re.search(r"Slider: (\d+)", state.get("tree_markdown", ""))
        if slider_match is None or int(slider_match.group(1)) < 80:
            raise RuntimeError("Primary drag did not move the fixture slider near its end")
        report["steps"].append("primary_foreground_drag_verified")

        slider = primary_element(state.get("elements", []), "AXSlider", "Smoke slider")
        set_result, _ = client.call(
            "set_value",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                **primary_element_target(slider),
                "value": 25,
            },
        )
        require_action_verdict(set_result, "primary set_value")
        if set_result.get("effect") != "confirmed" or set_result.get("verified") is not True:
            raise RuntimeError(f"Primary set_value was not confirmed: {set_result}")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after set_value")
        slider = primary_element(state.get("elements", []), "AXSlider", "Smoke slider")
        try:
            slider_value = float(slider.get("value"))
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"Primary slider returned no numeric value: {slider}") from error
        if abs(slider_value - 25.0) > 0.5:
            raise RuntimeError(f"Primary set_value left the slider at {slider_value}, expected 25")
        report["steps"].append("primary_set_value_verified")

        scroll_point = primary_screenshot_point(
            state,
            FIXTURE_SCROLL_PROBE_CENTER_X,
            FIXTURE_SCROLL_PROBE_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        scrolled, _ = client.call(
            "scroll",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "x": scroll_point[0],
                "y": scroll_point[1],
                "direction": "down",
                "by": "line",
                "amount": 1,
            },
        )
        require_action_verdict(scrolled, "primary background scroll")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after scroll")
        scroll_match = re.search(r"Scrolled: (\d+)", state.get("tree_markdown", ""))
        if scroll_match is None or int(scroll_match.group(1)) <= 0:
            raise RuntimeError("Primary scroll did not reach the fixture scroll probe")
        report["steps"].append("primary_background_scroll_verified")

        field = primary_element(state.get("elements", []), "AXTextField", "Smoke input")
        token = f"zcode-primary-{uuid.uuid4().hex[:10]}"
        typed, _ = client.call(
            "type_text",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                **primary_element_target(field),
                "text": token,
            },
        )
        require_action_verdict(typed, "primary type_text")
        cursor_state = require_cursor_action(client, session, "text", "Primary type_text")
        typed_cursor_position = None
        if cursor_state.get("position") is not None:
            typed_cursor_position = require_cursor_position(cursor_state, "Primary type_text")
        report["steps"].append("primary_background_text_typed")

        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after typing")
        if token not in state.get("tree_markdown", ""):
            raise RuntimeError("Fresh primary state did not contain the text sent through type_text")

        hotkeyed, _ = client.call(
            "hotkey",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                "keys": ["cmd", "shift", "k"],
            },
        )
        require_action_verdict(hotkeyed, "primary hotkey")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after hotkey")
        if "Hotkey: received" not in state.get("tree_markdown", ""):
            raise RuntimeError("Primary hotkey did not reach the focused fixture field")
        report["steps"].append("primary_background_hotkey_verified")

        button = primary_element(state.get("elements", []), "AXButton", "Copy value")
        key_pressed, _ = client.call(
            "press_key",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                **primary_element_target(button),
                "key": "space",
                "modifiers": [],
            },
        )
        require_action_verdict(key_pressed, "primary press_key")
        state, content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(content, "primary window state after press_key")
        expected = f"Received: {token}"
        if expected not in state.get("tree_markdown", ""):
            raise RuntimeError("Primary press_key did not submit the fixture value")
        report["steps"].append("primary_background_press_key_verified")

        button = primary_element(state.get("elements", []), "AXButton", "Copy value")
        clicked, _ = client.call(
            "click",
            {
                "session": session,
                "pid": pid,
                "window_id": window_id,
                **primary_element_target(button),
            },
        )
        require_action_verdict(clicked, "primary click")
        cursor_state, _ = client.call("get_agent_cursor_state", {"session": session})
        clicked_cursor_position = require_cursor_position(cursor_state, "Primary click")
        if typed_cursor_position is not None and clicked_cursor_position == typed_cursor_position:
            raise RuntimeError(
                "Primary type_text and click left the session cursor at the same target: "
                f"{clicked_cursor_position}"
            )
        report["cursorPosition"] = clicked_cursor_position
        report["steps"].append("primary_session_cursor_animated")
        report["steps"].append("primary_background_button_clicked")

        final_state, final_content = client.call(
            "get_window_state",
            {"session": session, "pid": pid, "window_id": window_id},
        )
        require_image(final_content, "primary final window state")
        if expected not in final_state.get("tree_markdown", ""):
            raise RuntimeError(f"Primary final visible/AX result did not contain {expected!r}")
        report["steps"].append("primary_visible_result_verified")

        escalated, _ = client.call(
            "escalate_session",
            {
                "session": session,
                "reason": "no_window_target",
                "detail": "live-smoke desktop pointer proof",
            },
        )
        if escalated.get("effective_scope") != "desktop" or escalated.get("desktop_unlocked") is not True:
            raise RuntimeError(f"Primary session did not escalate to desktop: {escalated}")
        session_state, _ = client.call("get_session_state", {"session": session})
        if (
            session_state.get("capture_scope") != "auto"
            or session_state.get("effective_scope") != "desktop"
            or session_state.get("escalation_reason") != "no_window_target"
        ):
            raise RuntimeError(f"Primary desktop session state was inconsistent: {session_state}")
        report["steps"].append("primary_desktop_scope_verified")

        desktop_state, desktop_content = client.call("get_desktop_state", {"session": session})
        require_image(desktop_content, "primary desktop state before pointer move")
        current_pointer, _ = client.call("get_cursor_position", {"session": session})
        target_screen = nudged_primary_pointer_target(desktop_state, current_pointer)
        target_pixels = desktop_screenshot_point(
            desktop_state,
            target_screen["x"],
            target_screen["y"],
        )
        moved, _ = client.call(
            "move_cursor",
            {
                "session": session,
                "scope": "desktop",
                "x": target_pixels[0],
                "y": target_pixels[1],
            },
        )
        if moved.get("scope") != "desktop" or moved.get("effect") != "unverifiable":
            raise RuntimeError(f"Primary desktop pointer move returned no usable verdict: {moved}")
        moved_pointer, _ = client.call("get_cursor_position", {"session": session})
        if not cursor_matches(moved_pointer, target_screen):
            raise RuntimeError(
                f"Primary desktop pointer reached {moved_pointer}, expected {target_screen}"
            )
        report["steps"].append("primary_real_pointer_moved_from_fresh_desktop")

        restore_state, restore_content = client.call("get_desktop_state", {"session": session})
        require_image(restore_content, "primary desktop state before pointer restoration")
        screen_width = float(restore_state["screen_width"])
        screen_height = float(restore_state["screen_height"])
        original_on_primary = (
            0 <= float(original_real_cursor["x"]) < screen_width
            and 0 <= float(original_real_cursor["y"]) < screen_height
        )
        if original_on_primary:
            restore_pixels = desktop_screenshot_point(
                restore_state,
                float(original_real_cursor["x"]),
                float(original_real_cursor["y"]),
            )
            client.call(
                "move_cursor",
                {
                    "session": session,
                    "scope": "desktop",
                    "x": restore_pixels[0],
                    "y": restore_pixels[1],
                },
            )
            report["pointerRestorePath"] = "primary_desktop"
        else:
            restore_pointer_direct(original_real_cursor)
            report["pointerRestorePath"] = "quartz_multidisplay_cleanup"
        restored_pointer, _ = client.call("get_cursor_position", {"session": session})
        if not cursor_matches(restored_pointer, original_real_cursor):
            raise RuntimeError(
                f"Primary pointer restoration reached {restored_pointer}, expected {original_real_cursor}"
            )
        final_desktop_state, final_desktop_content = client.call(
            "get_desktop_state", {"session": session}
        )
        require_image(final_desktop_content, "primary final desktop state")
        if final_desktop_state.get("display") != "primary":
            raise RuntimeError(f"Primary final desktop state was malformed: {final_desktop_state}")
        original_real_cursor = None
        report["steps"].append("primary_real_pointer_restored_and_reobserved")
        return report
    finally:
        if original_real_cursor is not None:
            try:
                restore_pointer_direct(original_real_cursor)
            except Exception:
                pass
        if isolated_app_pid is not None and process_is_alive(isolated_app_pid):
            try:
                client.call("kill_app", {"pid": isolated_app_pid})
                wait_for_process_exit(isolated_app_pid)
            except Exception:
                pass
        if session_started:
            try:
                client.call("end_session", {"session": session})
            except Exception:
                pass
        client.close()


def run_fallback() -> dict[str, Any]:
    client = MCPClient([sys.executable, "-m", "macos_cua.server"])
    report: dict[str, Any] = {"steps": []}
    original_cursor: dict[str, Any] | None = None
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
        original_cursor, _ = client.call("get_cursor_position")

        windows, _ = client.call("list_windows")
        matches = [window for window in windows if window.get("title") == "ZCode Computer Use Live Smoke"]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one fallback fixture window, found {len(matches)}")
        window = matches[0]
        report["window"] = {key: window.get(key) for key in ("id", "app", "pid", "title")}
        report["steps"].append("fallback_window_bound")

        activated, _ = client.call("activate_window", {"window": window})
        require_action_verdict(activated, "fallback activate_window")
        window = activated["window"]
        report["steps"].append("fallback_window_activated")

        state, content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(content, "fallback initial window state")
        tree = state["accessibility"]["tree"]
        field_index = element_index(tree, "AXTextField")
        focused, _ = client.call("click", {"window": window, "element_index": field_index})
        require_action_verdict(focused, "fallback field click")
        report["steps"].append("fallback_field_focused")

        focus_state, focus_content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(focus_content, "fallback window state after focus click")
        focused_element = focus_state["accessibility"].get("focused_element") or ""
        if "AXTextField" not in focused_element:
            raise RuntimeError(f"Fallback focus click did not focus the text field: {focused_element!r}")
        report["steps"].append("fallback_field_focus_verified")

        desktop, desktop_content = client.call("get_desktop_state")
        require_image(desktop_content, "fallback desktop state before shortcut")
        desktop_id = desktop["screenshots"][0]["id"]
        selected, _ = client.call("desktop_press_key", {"key": "Command+a", "screenshotId": desktop_id})
        require_action_verdict(selected, "fallback desktop_press_key")

        desktop, desktop_content = client.call("get_desktop_state")
        require_image(desktop_content, "fallback desktop state before typing")
        desktop_id = desktop["screenshots"][0]["id"]
        token = f"zcode-fallback-{uuid.uuid4().hex[:10]}"
        typed, _ = client.call("desktop_type_text", {"text": token, "screenshotId": desktop_id})
        require_action_verdict(typed, "fallback desktop_type_text")
        report["steps"].append("fallback_desktop_text_typed")

        state, content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(content, "fallback window state after typing")
        tree = state["accessibility"]["tree"]
        if token not in tree:
            raise RuntimeError("Fresh fallback state did not contain the text sent through desktop_type_text")
        element_index(tree, "AXSlider", "Smoke slider")
        drag_start = fixture_screenshot_point(
            state,
            FIXTURE_SLIDER_START_X,
            FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        drag_end = fixture_screenshot_point(
            state,
            FIXTURE_SLIDER_END_X,
            FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        dragged, _ = client.call(
            "drag",
            {
                "window": state["window"],
                "from_x": drag_start[0],
                "from_y": drag_start[1],
                "to_x": drag_end[0],
                "to_y": drag_end[1],
                "duration": 0.35,
                "screenshotId": state["screenshots"][0]["id"],
            },
        )
        require_action_verdict(dragged, "fallback physical slider drag")
        state, content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(content, "fallback window state after physical drag")
        slider_match = re.search(r"Slider: (\d+)", state["accessibility"]["tree"])
        if slider_match is None or int(slider_match.group(1)) < 80:
            raise RuntimeError(
                f"Fallback physical drag did not move the slider near its end: {state['accessibility']['tree']}"
            )
        report["steps"].append("fallback_physical_drag_verified")

        raw_start = fixture_screen_point(
            state,
            FIXTURE_SLIDER_START_X,
            FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        bound_end = fixture_screenshot_point(
            state,
            FIXTURE_SLIDER_END_X,
            FIXTURE_SLIDER_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        held, _ = client.call(
            "mouse_down",
            {
                "window": state["window"],
                "x": bound_end[0],
                "y": bound_end[1],
                "screenshotId": state["screenshots"][0]["id"],
                "mouse_button": "left",
            },
        )
        require_action_verdict(held, "fallback raw mouse_down")
        moved, _ = client.call(
            "move_mouse",
            {"x": raw_start[0], "y": raw_start[1], "duration": 0.35},
        )
        require_action_verdict(moved, "fallback held move_mouse")
        released, _ = client.call(
            "mouse_up",
            {"x": raw_start[0], "y": raw_start[1], "mouse_button": "left"},
        )
        require_action_verdict(released, "fallback raw mouse_up")
        state, content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(content, "fallback window state after raw held drag")
        slider_match = re.search(r"Slider: (\d+)", state["accessibility"]["tree"])
        if slider_match is None or int(slider_match.group(1)) > 20:
            raise RuntimeError(
                f"Fallback raw mouse sequence did not return the slider near its start: {state['accessibility']['tree']}"
            )
        report["steps"].append("fallback_raw_mouse_sequence_verified")

        tree = state["accessibility"]["tree"]
        element_index(tree, "AXButton", "Copy value")
        click_x, click_y = fixture_button_screenshot_point(state)
        clicked, _ = client.call(
            "click",
            {
                "window": state["window"],
                "x": click_x,
                "y": click_y,
                "screenshotId": state["screenshots"][0]["id"],
            },
        )
        require_action_verdict(clicked, "fallback physical coordinate click")
        report["steps"].append("fallback_physical_button_clicked")

        final_state, final_content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(final_content, "fallback final window state")
        expected = f"Received: {token}"
        if expected not in final_state["accessibility"]["tree"]:
            raise RuntimeError(f"Fallback final visible/AX result did not contain {expected!r}")
        report["steps"].append("fallback_visible_result_verified")

        scroll_point = fixture_screenshot_point(
            final_state,
            FIXTURE_SCROLL_PROBE_CENTER_X,
            FIXTURE_SCROLL_PROBE_CENTER_Y_FROM_CONTENT_BOTTOM,
        )
        scrolled, _ = client.call(
            "scroll",
            {
                "window": final_state["window"],
                "x": scroll_point[0],
                "y": scroll_point[1],
                "scrollX": 0,
                "scrollY": 120,
                "screenshotId": final_state["screenshots"][0]["id"],
            },
        )
        require_action_verdict(scrolled, "fallback physical scroll")
        scroll_state, scroll_content = client.call(
            "get_window_state",
            {"window": window, "include_screenshot": True, "include_text": True},
        )
        require_image(scroll_content, "fallback window state after physical scroll")
        scroll_match = re.search(r"Scrolled: (\d+)", scroll_state["accessibility"]["tree"])
        if scroll_match is None or int(scroll_match.group(1)) <= 0:
            raise RuntimeError(
                f"Fallback physical scroll did not reach its coordinate probe: {scroll_state['accessibility']['tree']}"
            )
        report["steps"].append("fallback_physical_scroll_verified")

        restored, _ = client.call(
            "move_mouse",
            {"x": original_cursor["x"], "y": original_cursor["y"], "duration": 0.1},
        )
        if restored.get("effect") != "confirmed" or restored.get("verified") is not True:
            raise RuntimeError(f"fallback cursor restoration was not confirmed: {restored}")
        original_cursor = None
        report["steps"].append("fallback_cursor_restored")
        return report
    finally:
        if original_cursor is not None:
            try:
                client.call(
                    "move_mouse",
                    {"x": original_cursor["x"], "y": original_cursor["y"], "duration": 0.1},
                )
            except Exception:
                pass
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
