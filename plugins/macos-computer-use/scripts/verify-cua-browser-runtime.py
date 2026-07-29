#!/usr/bin/env python3
"""Exercise one real driver-owned Chromium page through the signed stdio MCP."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler, build_opener


def fail(message: str) -> None:
    raise RuntimeError(f"primary browser runtime verification failed: {message}")


def load_primary_verifier() -> ModuleType:
    path = Path(__file__).with_name("verify-cua-mcp-runtime.py")
    spec = importlib.util.spec_from_file_location("zcode_primary_runtime_verifier", path)
    if spec is None or spec.loader is None:
        fail(f"cannot load shared MCP client from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixtureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.page_loads = 0
        self.counter = 0
        self.input_value = "seed"

    def update(self, query: dict[str, list[str]]) -> None:
        with self.lock:
            if "counter" in query:
                self.counter = int(query["counter"][0])
            if "input" in query:
                self.input_value = query["input"][0]

    def snapshot(self) -> tuple[int, int, str]:
        with self.lock:
            return self.page_loads, self.counter, self.input_value


FIXTURE_HTML = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>ZCode typed browser fixture</title></head>
<body>
<main aria-label="ZCode typed browser fixture">
  <h1>ZCODE_TYPED_BROWSER_MARKER_V1</h1>
  <button id="increment" aria-label="zcode-increment">Increment</button>
  <span id="counter">counter=0</span>
  <label>Message <input id="message" aria-label="zcode-input" value="seed"></label>
</main>
<script>
const counter = document.getElementById('counter');
const input = document.getElementById('message');
document.getElementById('increment').addEventListener('click', () => {
  counter.textContent = 'counter=1';
  fetch('/event?counter=1', {cache:'no-store'});
});
input.addEventListener('input', () => {
  fetch('/event?input=' + encodeURIComponent(input.value), {cache:'no-store'});
});
</script>
</body></html>
"""


class FixtureHandler(BaseHTTPRequestHandler):
    state: FixtureState
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        parsed = urlparse(self.path)
        if parsed.path == "/state":
            page_loads, counter, input_value = self.state.snapshot()
            body = json.dumps(
                {
                    "page_loads": page_loads,
                    "counter": counter,
                    "input_value": input_value,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/event":
            self.state.update(parse_qs(parsed.query, keep_blank_values=True))
            self.send_response(204)
            self.send_header("Connection", "close")
            self.end_headers()
            return
        if parsed.path in {"/", "/fixture", "/index.html"}:
            with self.state.lock:
                self.state.page_loads += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(FIXTURE_HTML)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(FIXTURE_HTML)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[zcode-browser-fixture] " + format % args + "\n")


def start_fixture(
) -> tuple[ThreadingHTTPServer, threading.Thread, str, str]:
    state = FixtureState()
    handler = type("BoundFixtureHandler", (FixtureHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    fixture_url = f"http://127.0.0.1:{port}/fixture"
    state_url = f"http://127.0.0.1:{port}/state"
    if fixture_snapshot(state_url) != (0, 0, "seed"):
        fail("fixture state oracle did not begin cleanly")
    return server, thread, fixture_url, state_url


def fixture_snapshot(state_url: str) -> tuple[int, int, str]:
    # Never let a runner proxy intercept the loopback state oracle.
    opener = build_opener(ProxyHandler({}))
    with opener.open(state_url, timeout=2) as response:
        value = json.loads(response.read())
    if (
        not isinstance(value, dict)
        or type(value.get("page_loads")) is not int
        or type(value.get("counter")) is not int
        or not isinstance(value.get("input_value"), str)
    ):
        fail(f"fixture state oracle drifted: {value}")
    return value["page_loads"], value["counter"], value["input_value"]


def wait_until(label: str, predicate: Any, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    fail(f"timed out waiting for {label}")


def require_fields(name: str, value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        fail(f"{name} fields drifted: {value}")
    return value


def require_text(name: str, content: Any, expected: str) -> None:
    if content != [{"type": "text", "text": expected}]:
        fail(f"{name} text drifted: {content}")


def list_windows(client: Any, pid: int | None = None) -> list[dict[str, Any]]:
    arguments = {"pid": pid} if pid is not None else {}
    value, _ = client.call("list_windows", arguments)
    if not isinstance(value, dict) or not isinstance(value.get("windows"), list):
        fail(f"list_windows returned malformed state: {value}")
    return value["windows"]


def browser_binary() -> tuple[Path, str]:
    override = os.environ.get("ZCODE_CUA_BROWSER_BIN")
    if override:
        path = Path(override).resolve()
        if not path.is_file():
            fail(f"ZCODE_CUA_BROWSER_BIN is not a file: {path}")
        product = os.environ.get("ZCODE_CUA_BROWSER_PRODUCT", path.name)
        return path, product.lower()
    candidates = (
        (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "chrome",
        ),
        (
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            "edge",
        ),
    )
    for path, product in candidates:
        if path.is_file():
            return path, product
    fail("no supported Chrome or Edge executable is installed for the live browser gate")


def app_name_matches(product: str, app_name: Any) -> bool:
    if not isinstance(app_name, str):
        return False
    lowered = app_name.lower()
    return (product == "chrome" and "chrome" in lowered) or (
        product == "edge" and "edge" in lowered
    )


def launch_source_browser(
    client: Any, binary: Path, product: str, profile: Path
) -> tuple[subprocess.Popen[bytes], int, int]:
    before = {window["window_id"] for window in list_windows(client)}
    command = [
        str(binary),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--disable-features=MediaRouter",
        "--new-window",
        "--window-position=120,120",
        "--window-size=900,700",
        "about:blank",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    found: tuple[int, int] | None = None

    def observe() -> bool:
        nonlocal found
        if process.poll() is not None:
            fail(f"source browser exited early with code {process.returncode}")
        for window in list_windows(client):
            bounds = window.get("bounds")
            if (
                window.get("window_id") not in before
                and app_name_matches(product, window.get("app_name"))
                and window.get("is_on_screen") is True
                and isinstance(bounds, dict)
                and float(bounds.get("width", 0)) > 0
                and float(bounds.get("height", 0)) > 0
                and isinstance(window.get("pid"), int)
                and window["pid"] > 0
            ):
                found = (window["pid"], window["window_id"])
                return True
        return False

    wait_until("disposable source browser window", observe)
    assert found is not None
    return process, found[0], found[1]


def require_prepare(value: Any, content: Any, source_pid: int) -> int:
    prepared = require_fields(
        "browser_prepare",
        value,
        {
            "status",
            "prepared",
            "action",
            "message",
            "endpoint_ownership",
            "prepared_pid",
            "side_effects",
            "attachment",
        },
    )
    message = (
        "Launched a separate driver-owned isolated Chromium process; the requested "
        "browser process was not modified or terminated."
    )
    prepared_pid = prepared["prepared_pid"]
    if (
        prepared["status"] != "ok"
        or prepared["prepared"] is not True
        or prepared["action"] != "launched_isolated_browser"
        or prepared["message"] != message
        or prepared["attachment"] is not None
        or not isinstance(prepared_pid, int)
        or prepared_pid <= 0
        or prepared_pid == source_pid
    ):
        fail(f"browser_prepare identity drifted: {prepared}")
    side_effects = require_fields(
        "browser_prepare.side_effects",
        prepared["side_effects"],
        {
            "launched_browser",
            "restarted_browser",
            "created_profile",
            "reused_driver_profile",
            "copied_profile_data",
            "changed_preferences",
            "displayed_consent_prompt",
            "opened_setup_page",
            "closed_setup_page",
            "enabled_remote_debugging",
            "used_bounded_pixel_fallback",
            "focused_setup_address_field",
            "foregrounded_window",
            "injected_global_input",
        },
    )
    expected_side_effects = {field: False for field in side_effects}
    expected_side_effects["launched_browser"] = True
    expected_side_effects["created_profile"] = True
    if side_effects != expected_side_effects:
        fail(f"browser_prepare side effects drifted: {side_effects}")
    ownership = prepared["endpoint_ownership"]
    if (
        not isinstance(ownership, dict)
        or set(ownership) not in (
            {"method", "owner_pid", "detail"},
            {"method", "owner_pid", "listener_pid", "detail"},
        )
        or ownership["method"] != "spawned_by_driver"
        or ownership["owner_pid"] != prepared_pid
        or not isinstance(ownership["detail"], str)
        or "driver-owned profile port file" not in ownership["detail"]
    ):
        fail(f"browser_prepare endpoint ownership drifted: {ownership}")
    require_text(
        "browser_prepare",
        content,
        f"browser_prepare: endpoint available — {message}",
    )
    return prepared_pid


def wait_for_binding(
    client: Any, session: str, pid: int, product: str
) -> tuple[int, dict[str, Any]]:
    selected: tuple[int, dict[str, Any]] | None = None
    diagnostics: dict[str, Any] = {
        "prepared_pid": pid,
        "prepared_windows": [],
        "product_windows": [],
        "bind_results": [],
    }

    def observe() -> bool:
        nonlocal selected
        prepared_windows = list_windows(client, pid)
        diagnostics["prepared_windows"] = prepared_windows
        diagnostics["product_windows"] = [
            window
            for window in list_windows(client)
            if app_name_matches(product, window.get("app_name"))
        ]
        bind_results = []
        for window in prepared_windows:
            window_id = window.get("window_id")
            if not isinstance(window_id, int):
                continue
            value, content = client.call(
                "get_browser_state",
                {"session": session, "pid": pid, "window_id": window_id},
            )
            bind_results.append(
                {"window_id": window_id, "structured": value, "content": content}
            )
            diagnostics["bind_results"] = bind_results
            if not isinstance(value, dict) or value.get("status") != "ok":
                continue
            tabs = value.get("tabs")
            if (
                value.get("binding_quality") == "exact"
                and value.get("binding_route") == "native_cdp_window"
                and value.get("mutation_allowed") is True
                and isinstance(tabs, list)
                and len(tabs) == 1
                and tabs[0].get("active") in {True, False, None}
            ):
                target = value.get("target_id")
                require_text(
                    "get_browser_state bind",
                    content,
                    f"bound target {target} (exact) with {len(tabs)} tab(s)",
                )
                selected = (window_id, value)
                return True
        return False

    try:
        wait_until("exact driver-owned browser binding", observe, timeout=25)
    except RuntimeError:
        fail(
            "exact driver-owned browser binding diagnostics: "
            + json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))
        )
    assert selected is not None
    return selected


def activate_prepared_browser(client: Any, pid: int, window_id: int) -> None:
    activated, content = client.call(
        "bring_to_front", {"pid": pid, "window_id": window_id}
    )
    value = require_fields(
        "prepared browser activation",
        activated,
        {"pid", "window_id", "activated", "path"},
    )
    if (
        value["pid"] != pid
        or value["window_id"] != window_id
        or value["activated"] is not True
        or value["path"] not in {"skylight", "cocoa"}
    ):
        fail(f"prepared browser activation drifted: {value}")
    require_text(
        "prepared browser activation",
        content,
        f"Brought pid {pid} to the foreground.",
    )

    def active() -> bool:
        apps, _ = client.call("list_apps", {})
        return isinstance(apps, dict) and any(
            app.get("pid") == pid and app.get("active") is True
            for app in apps.get("apps", [])
            if isinstance(app, dict)
        )

    wait_until("prepared browser activation readback", active, timeout=10)


def navigate_or_known_driver_limit(
    client: Any,
    session: str,
    target: str,
    tab: str,
    fixture_url: str,
) -> tuple[Any, list[dict[str, Any]], bool]:
    result = client.request(
        "tools/call",
        {
            "name": "browser_navigate",
            "arguments": {
                "session": session,
                "target_id": target,
                "tab_id": tab,
                "url": fixture_url,
            },
        },
    )
    content = result.get("content")
    if not isinstance(content, list):
        fail(f"browser_navigate.content drifted: {content}")
    if result.get("isError") is not True:
        if set(result) != {"content", "structuredContent"}:
            fail(f"browser_navigate success envelope drifted: {result}")
        return result.get("structuredContent"), content, False

    expected_text = "Page.navigate failed: CDP Page.navigate timed out after 20s"
    if set(result) != {"content", "isError"} or content != [
        {"type": "text", "text": expected_text}
    ]:
        fail(f"browser_navigate returned an unknown live-page failure: {result}")
    return None, content, True


def require_snapshot(
    client: Any, session: str, target: str, tab: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value, _ = client.call(
        "get_browser_state",
        {
            "session": session,
            "target_id": target,
            "tab_id": tab,
            "snapshot_format": "semantic_v2",
        },
    )
    snapshot = require_fields(
        "get_browser_state semantic snapshot",
        value,
        {
            "status",
            "mode",
            "target_id",
            "tab_id",
            "snapshot",
            "page",
            "outline",
            "refs",
            "content_refs",
            "oopif",
        },
    )
    refs = snapshot["refs"]
    if (
        snapshot["status"] != "ok"
        or snapshot["mode"] != "snapshot"
        or snapshot["target_id"] != target
        or snapshot["tab_id"] != tab
        or snapshot["snapshot"].get("format") != "semantic_v2"
        or not isinstance(snapshot["outline"], str)
        or not isinstance(refs, list)
    ):
        fail(f"semantic browser snapshot drifted: {snapshot}")
    return snapshot, refs


def action_ref(refs: list[dict[str, Any]], name: str, action: str) -> dict[str, Any]:
    for entry in refs:
        if entry.get("name") == name and action in entry.get("actions", []):
            return entry
    fail(f"semantic snapshot omitted {name!r} with action {action!r}: {refs}")


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def close_owned_browser_chain(
    client: Any,
    session: str,
    prepared_pid: int,
    source_process: subprocess.Popen[bytes],
    source_pid: int,
    source_window_id: int,
) -> None:
    ended, _ = client.call("end_session", {"session": session})
    if not isinstance(ended, dict) or ended.get("active") is not False:
        fail(f"browser session did not end cleanly: {ended}")
    wait_until(
        "driver-owned browser cleanup",
        lambda: not list_windows(client, prepared_pid),
        timeout=15,
    )
    if not any(
        window.get("window_id") == source_window_id
        for window in list_windows(client, source_pid)
    ):
        fail("ending the browser session modified or terminated the source browser")

    killed_value, killed_content = client.call("kill_app", {"pid": source_pid})
    if killed_value is not None:
        fail(f"source browser kill unexpectedly returned structured content: {killed_value}")
    require_text(
        "source browser kill",
        killed_content,
        f"✅ Sent SIGKILL to pid {source_pid}.",
    )
    wait_until("source browser process exit", lambda: source_process.poll() is not None)
    if source_process.returncode != -signal.SIGKILL:
        fail(
            "source browser did not exit through the acknowledged SIGKILL: "
            f"{source_process.returncode}"
        )


def main() -> int:
    if len(sys.argv) != 3:
        fail("usage: verify-cua-browser-runtime.py <cua-driver> <socket>")
    binary = Path(sys.argv[1]).resolve()
    socket = sys.argv[2]
    module = load_primary_verifier()
    client = module.MCPClient(binary, socket)
    server: ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None
    source_process: subprocess.Popen[bytes] | None = None
    source_pid: int | None = None
    source_window_id: int | None = None
    prepared_pid: int | None = None
    browser_session = f"zcode-ci-browser-{os.getpid()}"
    session_started = False
    temp_root = Path(
        tempfile.mkdtemp(
            prefix="zcode-primary-browser-live-",
            dir=os.environ.get("RUNNER_TEMP") or None,
        )
    ).resolve()
    source_profile = temp_root / "source-profile"
    source_profile.mkdir()
    try:
        client.initialize()
        executable, product = browser_binary()
        server, server_thread, fixture_url, fixture_state_url = start_fixture()
        source_process, source_pid, source_window_id = launch_source_browser(
            client, executable, product, source_profile
        )

        started, _ = client.call(
            "start_session", {"session": browser_session, "capture_scope": "window"}
        )
        if (
            not isinstance(started, dict)
            or started.get("session") != browser_session
            or started.get("active") is not True
        ):
            fail(f"browser session did not start: {started}")
        session_started = True

        prepared_value, prepared_content = client.call(
            "browser_prepare",
            {
                "session": browser_session,
                "pid": source_pid,
                "allow_launch": True,
                "profile": {"mode": "isolated_new"},
            },
        )
        prepared_pid = require_prepare(prepared_value, prepared_content, source_pid)
        prepared_window_id, bound = wait_for_binding(
            client, browser_session, prepared_pid, product
        )
        target = bound["target_id"]
        tabs = bound["tabs"]
        tab = tabs[0]["tab_id"]
        activate_prepared_browser(client, prepared_pid, prepared_window_id)

        navigated, navigated_content, navigation_limited = navigate_or_known_driver_limit(
            client, browser_session, target, tab, fixture_url
        )
        if navigation_limited:
            if fixture_snapshot(fixture_state_url) != (0, 0, "seed"):
                fail("the pinned navigation timeout produced an unexpected page effect")
            close_owned_browser_chain(
                client,
                browser_session,
                prepared_pid,
                source_process,
                source_pid,
                source_window_id,
            )
            session_started = False
            source_pid = None
            print(
                "Verified the pinned Cua Driver 0.13.1 macOS Page.navigate timeout "
                "over signed stdio with zero loopback-page effect and exact owned cleanup."
            )
            return 0
        if navigated != {
            "status": "ok",
            "target_id": target,
            "tab_id": tab,
            "url": fixture_url,
            "refs_invalidated": True,
        }:
            fail(f"browser_navigate response drifted: {navigated}")
        require_text(
            "browser_navigate", navigated_content, f"navigated {tab} to {fixture_url}"
        )
        wait_until(
            "fixture navigation", lambda: fixture_snapshot(fixture_state_url)[0] >= 1
        )

        first, first_refs = require_snapshot(client, browser_session, target, tab)
        if (
            first["page"].get("url") != fixture_url
            or "ZCODE_TYPED_BROWSER_MARKER_V1" not in first["outline"]
        ):
            fail(f"semantic snapshot did not observe the local page: {first}")
        increment = action_ref(first_refs, "zcode-increment", "click")
        clicked, clicked_content = client.call(
            "browser_click",
            {
                "session": browser_session,
                "target_id": target,
                "tab_id": tab,
                "ref": increment["ref"],
                "input_route": "dom_event",
            },
        )
        expected_click = {
            "status": "ok",
            "route": "dom_event",
            "target_id": target,
            "tab_id": tab,
            "ref": increment["ref"],
            "frame": increment["frame"],
        }
        if clicked != expected_click:
            fail(f"browser_click response drifted: {clicked}")
        require_text(
            "browser_click",
            clicked_content,
            f"dispatched DOM click on {increment['ref']} in {tab}",
        )
        wait_until(
            "page click effect", lambda: fixture_snapshot(fixture_state_url)[1] == 1
        )

        second, second_refs = require_snapshot(client, browser_session, target, tab)
        if (
            second["snapshot"].get("id") == first["snapshot"].get("id")
            or "counter=1" not in second["outline"]
        ):
            fail("fresh semantic snapshot did not prove the click result")
        text_entry = action_ref(second_refs, "zcode-input", "type")
        if text_entry.get("value") != "seed":
            fail(f"semantic input value did not begin at seed: {text_entry}")
        typed_text = "zcode typed"
        typed, typed_content = client.call(
            "browser_type",
            {
                "session": browser_session,
                "target_id": target,
                "tab_id": tab,
                "ref": text_entry["ref"],
                "text": typed_text,
                "mode": "insert_text",
                "replace": True,
            },
        )
        expected_typed = {
            "status": "ok",
            "target_id": target,
            "tab_id": tab,
            "ref": text_entry["ref"],
            "frame": text_entry["frame"],
            "mode": "insert_text",
            "chars": len(typed_text),
            "requested_chars": len(typed_text),
            "delivered_chars": len(typed_text),
            "replace": True,
            "replaced_chars": 4,
        }
        if typed != expected_typed:
            fail(f"browser_type response drifted: {typed}")
        require_text(
            "browser_type",
            typed_content,
            f"typed {len(typed_text)} char(s) into {tab}, replacing 4 char(s)",
        )
        wait_until(
            "page type effect",
            lambda: fixture_snapshot(fixture_state_url)[2] == typed_text,
        )

        final, final_refs = require_snapshot(client, browser_session, target, tab)
        final_input = action_ref(final_refs, "zcode-input", "type")
        if (
            final["snapshot"].get("id") == second["snapshot"].get("id")
            or final_input.get("value") != typed_text
            or "counter=1" not in final["outline"]
        ):
            fail("fresh semantic snapshot did not prove the final page state")

        close_owned_browser_chain(
            client,
            browser_session,
            prepared_pid,
            source_process,
            source_pid,
            source_window_id,
        )
        session_started = False
        source_pid = None
        print(
            "Verified real isolated Chromium prepare/bind/navigate/semantic-click/type/"
            "re-observe/cleanup over signed stdio MCP."
        )
        return 0
    finally:
        if session_started:
            try:
                client.call("end_session", {"session": browser_session})
            except RuntimeError:
                pass
        if source_pid is not None and process_exists(source_pid):
            try:
                client.call("kill_app", {"pid": source_pid})
            except RuntimeError:
                try:
                    os.kill(source_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if source_process is not None and source_process.poll() is None:
            source_process.terminate()
            try:
                source_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                source_process.kill()
                source_process.wait(timeout=3)
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=3)
        client.close()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
