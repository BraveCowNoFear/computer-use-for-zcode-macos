from __future__ import annotations

import base64
import io
import json
import math
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from macos_cua.contracts import CORE_CODEX_TOOL_NAMES, TOOL_DEFINITIONS, TOOL_NAMES, ToolError
from macos_cua.macos import (
    ACCESSIBILITY_REQUIRED_TOOLS,
    EXPECTED_PYOBJC_VERSION,
    PYOBJC_DISTRIBUTIONS,
    MacOSBackend,
    parse_key_chord,
    require_exact_pyobjc_versions,
)
from macos_cua import server as server_module
from macos_cua.server import MCPServer, serve


class FakeBackend:
    def call(self, name, arguments):
        if name == "get_window_state":
            return {
                "window": arguments["window"],
                "screenshots": [
                    {
                        "id": "shot-1",
                        "path": "/tmp/shot.png",
                        "mimeType": "image/png",
                        "_image_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                    }
                ],
                "accessibility": None,
            }
        if name == "click":
            raise ToolError("fresh observation required")
        return {"ok": True, "name": name, "arguments": arguments}


class ContractTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "native framework import runs on macOS CI")
    def test_native_frameworks_expose_ax_and_cg_entry_points(self):
        backend = MacOSBackend()
        self.assertIsNone(backend.native_error)
        self.assertTrue(hasattr(backend.ApplicationServices, "AXUIElementCreateApplication"))
        self.assertTrue(hasattr(backend.ApplicationServices, "AXIsProcessTrusted"))
        self.assertTrue(hasattr(backend.Quartz, "CGEventPost"))
        self.assertTrue(hasattr(backend.Quartz, "CGWindowListCreateImage"))
        self.assertTrue(hasattr(backend.AppKit, "NSWorkspace"))
        self.assertTrue(hasattr(backend.AppKit, "NSBitmapImageFileTypePNG"))

    def test_codex_core_is_complete(self):
        self.assertTrue(CORE_CODEX_TOOL_NAMES.issubset(TOOL_NAMES))
        self.assertEqual(len(TOOL_NAMES), len(TOOL_DEFINITIONS))
        definitions = {tool["name"]: tool for tool in TOOL_DEFINITIONS}
        for name in ("click", "scroll", "drag"):
            self.assertNotIn("screenshotId", definitions[name]["inputSchema"].get("required", []))

    def test_direct_desktop_fallback_surface_is_complete(self):
        self.assertTrue(
            {
                "get_desktop_state",
                "desktop_click",
                "desktop_press_key",
                "desktop_type_text",
                "desktop_scroll",
                "desktop_drag",
            }.issubset(TOOL_NAMES)
        )
        definitions = {tool["name"]: tool for tool in TOOL_DEFINITIONS}
        for name in ("desktop_click", "desktop_press_key", "desktop_type_text", "desktop_scroll", "desktop_drag"):
            self.assertIn("screenshotId", definitions[name]["inputSchema"]["required"])

    def test_tool_schemas_are_objects(self):
        for tool in TOOL_DEFINITIONS:
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            self.assertIn("description", tool)

    def test_window_state_defaults_to_pixels_and_can_request_accessibility(self):
        backend = MacOSBackend()
        window = {
            "id": 7,
            "app": "com.example.Editor",
            "pid": 55,
            "bounds": {"x": 0, "y": 0, "width": 800, "height": 600},
        }
        backend._get_window = lambda value: window
        backend._capture_window = lambda value: {"id": "shot"}
        backend._accessibility_state = lambda value: {"tree": "[0] AXWindow"}
        state = backend.tool_get_window_state({"window": {"id": 7}})
        self.assertEqual(state["screenshots"], [{"id": "shot"}])
        self.assertIsNone(state["accessibility"])
        state = backend.tool_get_window_state({"window": {"id": 7}, "include_text": True})
        self.assertEqual(state["screenshots"], [{"id": "shot"}])
        self.assertEqual(state["accessibility"], {"tree": "[0] AXWindow"})
        definition = next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "get_window_state")
        self.assertFalse(definition["inputSchema"]["properties"]["include_text"]["default"])

    def test_health_keeps_ax_control_available_without_screen_recording(self):
        backend = MacOSBackend()
        backend.native_error = None
        backend.AppKit = object()
        backend.ApplicationServices = object()
        backend.Quartz = object()
        backend._permission_status = lambda: {"accessibility": True, "screenRecording": False}
        health = backend.tool_computer_use_health({})
        self.assertTrue(health["ok"])
        self.assertTrue(health["axControlReady"])
        self.assertFalse(health["pixelObservationReady"])
        self.assertFalse(health["desktopObservationReady"])
        self.assertIn("AX-only", health["message"])

    def test_native_runtime_requires_the_exact_pyobjc_closure(self):
        versions = require_exact_pyobjc_versions(lambda distribution: "12.2.1")
        self.assertEqual(set(versions), set(PYOBJC_DISTRIBUTIONS))
        self.assertTrue(all(version == EXPECTED_PYOBJC_VERSION for version in versions.values()))

        def mismatched(distribution):
            return "12.2.0" if distribution == "pyobjc-framework-Quartz" else "12.2.1"

        with self.assertRaisesRegex(RuntimeError, "pyobjc-framework-Quartz=12.2.0"):
            require_exact_pyobjc_versions(mismatched)
        with self.assertRaisesRegex(RuntimeError, "Missing required distribution pyobjc-core"):
            require_exact_pyobjc_versions(lambda distribution: (_ for _ in ()).throw(LookupError("absent")))

    def test_permission_request_only_prompts_for_requested_grants(self):
        prompts = {"accessibility": 0, "screenRecording": 0}

        class FakeAX:
            kAXTrustedCheckOptionPrompt = "prompt"

            @staticmethod
            def AXIsProcessTrusted():
                return False

            @staticmethod
            def AXIsProcessTrustedWithOptions(options):
                self.assertEqual(options, {"prompt": True})
                prompts["accessibility"] += 1

        class FakeQuartz:
            @staticmethod
            def CGPreflightScreenCaptureAccess():
                return False

            @staticmethod
            def CGRequestScreenCaptureAccess():
                prompts["screenRecording"] += 1

        backend = MacOSBackend()
        backend.native_error = None
        backend.ApplicationServices = FakeAX
        backend.Quartz = FakeQuartz
        backend.AppKit = object()
        backend.tool_request_permissions(
            {"accessibility": True, "screen_recording": False, "open_settings": False}
        )
        self.assertEqual(prompts, {"accessibility": 1, "screenRecording": 0})
        backend.tool_request_permissions(
            {"accessibility": False, "screen_recording": True, "open_settings": False}
        )
        self.assertEqual(prompts, {"accessibility": 1, "screenRecording": 1})

    def test_input_and_ax_actions_fail_fast_without_accessibility(self):
        backend = MacOSBackend()
        backend.native_error = None
        backend.AppKit = object()
        backend.ApplicationServices = object()
        backend.Quartz = object()
        backend._permission_status = lambda: {"accessibility": False, "screenRecording": True}
        action = mock.Mock(return_value={"ok": True})
        backend.tool_click = action
        with self.assertRaisesRegex(ToolError, "Accessibility permission is not granted"):
            backend.call("click", {"window": {"id": 1}, "x": 1, "y": 2})
        action.assert_not_called()
        self.assertIn("mouse_up", ACCESSIBILITY_REQUIRED_TOOLS)

        state = mock.Mock(return_value={"screenshots": []})
        backend.tool_get_window_state = state
        with self.assertRaisesRegex(ToolError, "Accessibility permission is not granted"):
            backend.call("get_window_state", {"window": {"id": 1}, "include_text": True})
        self.assertEqual(
            backend.call("get_window_state", {"window": {"id": 1}, "include_text": False}),
            {"screenshots": []},
        )

    def test_launch_app_returns_the_running_pid_and_exact_windows(self):
        class FakeRunningApp:
            def bundleIdentifier(self):
                return "com.example.Editor"

            def processIdentifier(self):
                return 55

            def localizedName(self):
                return "Editor"

            def bundleURL(self):
                return types.SimpleNamespace(path=lambda: "/Applications/Editor.app")

        running = FakeRunningApp()
        workspace = types.SimpleNamespace(runningApplications=lambda: [running])
        backend = MacOSBackend()
        backend.AppKit = types.SimpleNamespace(
            NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: workspace),
        )
        expected_window = {
            "id": 99,
            "app": "com.example.Editor",
            "pid": 55,
            "bounds": {"x": 0, "y": 0, "width": 800, "height": 600},
        }
        backend._list_windows = lambda: [expected_window]
        with mock.patch("macos_cua.macos.subprocess.run") as launch:
            launch.return_value = types.SimpleNamespace(returncode=0, stderr="")
            result = backend.tool_launch_app({"app": "com.example.Editor"})
        launch.assert_called_once_with(
            ["/usr/bin/open", "-b", "com.example.Editor"], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result["pid"], 55)
        self.assertEqual(result["windows"], [expected_window])
        self.assertEqual(result["bundleId"], "com.example.Editor")

    def test_window_order_is_front_to_back_and_zindex_increases_toward_front(self):
        infos = [
            {"layer": 0, "onscreen": True, "id": 10},
            {"layer": 1, "onscreen": True, "id": 99},
            {"layer": 0, "onscreen": True, "id": 20},
            {"layer": 0, "onscreen": False, "id": 30},
        ]

        class FakeQuartz:
            kCGWindowListOptionAll = 1
            kCGWindowListExcludeDesktopElements = 2
            kCGNullWindowID = 0
            kCGWindowLayer = "layer"
            kCGWindowIsOnscreen = "onscreen"

            @staticmethod
            def CGWindowListCopyWindowInfo(options, window_id):
                return infos

        backend = MacOSBackend()
        backend.Quartz = FakeQuartz
        backend._require_native = lambda: None
        backend._window_from_info = lambda info, z_index: {
            "id": info["id"],
            "ownerName": "Example",
            "zIndex": z_index,
        }
        windows = backend._list_windows()
        self.assertEqual([window["id"] for window in windows], [10, 20, 30])
        self.assertEqual([window["zIndex"] for window in windows], [2, 1, 0])

    def test_installed_app_cache_never_caches_running_state_or_pid(self):
        class FakeRunningApp:
            def bundleIdentifier(self):
                return "com.example.Editor"

            def processIdentifier(self):
                return 55

            def localizedName(self):
                return "Editor"

            def bundleURL(self):
                return types.SimpleNamespace(path=lambda: "/Applications/Editor.app")

        running = [FakeRunningApp()]
        workspace = types.SimpleNamespace(runningApplications=lambda: list(running))
        backend = MacOSBackend()
        backend.AppKit = types.SimpleNamespace(
            NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: workspace),
        )
        backend._installed_cache = (
            time.monotonic(),
            [
                {
                    "id": "com.example.Editor",
                    "displayName": "Editor",
                    "path": "/Applications/Editor.app",
                    "isRunning": False,
                }
            ],
        )

        first = backend._installed_apps()
        self.assertTrue(first[0]["isRunning"])
        self.assertEqual(first[0]["pid"], 55)

        running.clear()
        second = backend._installed_apps()
        self.assertFalse(second[0]["isRunning"])
        self.assertNotIn("pid", second[0])

    def test_initialize_and_list(self):
        server = MCPServer(FakeBackend())
        response = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
        )
        self.assertEqual(response["result"]["serverInfo"]["name"], "macos-computer-use")
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]}, set(TOOL_NAMES))

    def test_screenshot_becomes_mcp_image_without_base64_in_text(self):
        server = MCPServer(FakeBackend())
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_window_state", "arguments": {"window": {"id": 1, "app": "example"}}},
            }
        )
        content = response["result"]["content"]
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(base64.b64decode(content[1]["data"]), b"png-bytes")
        self.assertNotIn("_image_base64", content[0]["text"])

    def test_failed_window_capture_removes_unpublished_png(self):
        backend = MacOSBackend()
        backend._permission_status = lambda: {"accessibility": True, "screenRecording": True}
        window = {
            "id": 42,
            "pid": 420,
            "app": "com.example.capture",
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 80},
        }
        with tempfile.TemporaryDirectory() as directory:
            backend._screenshot_dir = Path(directory) / "private-shots"

            def failed_capture(command, **_kwargs):
                Path(command[-1]).write_bytes(b"partial-png")
                return types.SimpleNamespace(returncode=1, stderr="injected capture failure")

            with mock.patch("macos_cua.macos.shutil.which", return_value="/usr/sbin/screencapture"), mock.patch(
                "macos_cua.macos.subprocess.run", side_effect=failed_capture
            ):
                with self.assertRaisesRegex(ToolError, "injected capture failure"):
                    backend._capture_window(window)
            self.assertEqual(list(backend._screenshot_dir.glob("*.png")), [])
            self.assertEqual(backend._screenshot_cache, {})

    def test_failed_window_observation_rolls_back_a_completed_screenshot(self):
        backend = MacOSBackend()
        window = {
            "id": 9,
            "app": "com.example.Editor",
            "pid": 90,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        backend._get_window = lambda value: window
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "completed.png"
            path.write_bytes(b"complete")

            def capture(_window):
                backend._screenshot_cache["shot"] = {
                    "windowKey": backend._window_key(window),
                    "path": str(path),
                    "created": time.monotonic(),
                }
                return {"id": "shot", "path": str(path)}

            backend._capture_window = capture
            backend._accessibility_state = mock.Mock(side_effect=ToolError("AX failed"))
            with self.assertRaisesRegex(ToolError, "AX failed"):
                backend.tool_get_window_state(
                    {"window": {"id": 9}, "include_screenshot": True, "include_text": True}
                )
            self.assertEqual(backend._screenshot_cache, {})
            self.assertFalse(path.exists())

    def test_failed_multidisplay_observation_rolls_back_earlier_screens(self):
        backend = MacOSBackend()
        layout = [
            {"displayId": 1, "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}},
            {"displayId": 2, "bounds": {"x": 100, "y": 0, "width": 100, "height": 100}},
        ]
        backend._permission_status = lambda: {"screenRecording": True}
        backend._desktop_layout = lambda: layout
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "first.png"
            path.write_bytes(b"complete")

            def capture(screen, _layout):
                if screen["displayId"] == 2:
                    raise ToolError("second display failed")
                backend._screenshot_cache["first"] = {
                    "windowKey": None,
                    "path": str(path),
                    "created": time.monotonic(),
                }
                return {"id": "first", "path": str(path)}

            backend._capture_desktop_screen = capture
            with self.assertRaisesRegex(ToolError, "second display failed"):
                backend.tool_get_desktop_state({})
            self.assertEqual(backend._screenshot_cache, {})
            self.assertFalse(path.exists())

    def test_screenshot_directory_rejects_a_non_directory_target(self):
        backend = MacOSBackend()
        with tempfile.TemporaryDirectory() as directory:
            unsafe = Path(directory) / "screenshots"
            unsafe.write_text("not a directory", encoding="utf-8")
            backend._screenshot_dir = unsafe
            with self.assertRaisesRegex(ToolError, "unsafe screenshot directory"):
                backend._ensure_screenshot_dir()

    @unittest.skipIf(os.name == "nt", "ordinary Windows users may not create symlinks")
    def test_orphan_cleanup_never_follows_a_screenshot_directory_symlink(self):
        backend = MacOSBackend()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            old_png = target / "keep.png"
            old_png.write_bytes(b"user-file")
            os.utime(old_png, (time.time() - 48 * 60 * 60,) * 2)
            link = root / "screenshots"
            link.symlink_to(target, target_is_directory=True)
            backend._screenshot_dir = link
            backend._cleanup_orphaned_screenshots()
            self.assertEqual(old_png.read_bytes(), b"user-file")

    def test_tool_errors_do_not_kill_server(self):
        server = MCPServer(FakeBackend())
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "click", "arguments": {"window": {"id": 1}}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("fresh observation", response["result"]["content"][0]["text"])

    def test_structured_tool_errors_survive_mcp_transport(self):
        class PartialBackend:
            def call(self, name, arguments):
                raise ToolError(
                    "retry only the remaining suffix",
                    {
                        "code": "type_text_incomplete",
                        "effect": "partial",
                        "delivered_chars": 32,
                        "retry_from_character": 32,
                    },
                )

        response = MCPServer(PartialBackend()).handle(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "computer_use_health", "arguments": {}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["effect"], "partial")
        self.assertEqual(response["result"]["structuredContent"]["retry_from_character"], 32)

    def test_partial_unicode_delivery_reports_exact_suffix_and_expires_state(self):
        backend = MacOSBackend()
        created = 0

        def create_keyboard_event(_source, _key_code, is_down):
            nonlocal created
            created += 1
            return None if created == 3 else {"down": is_down}

        backend.Quartz = types.SimpleNamespace(
            kCGHIDEventTap="hid",
            CGEventCreateKeyboardEvent=create_keyboard_event,
            CGEventKeyboardSetUnicodeString=lambda *_args: None,
            CGEventPost=lambda *_args: None,
        )
        expired = []
        backend._activate_current = lambda window: window
        backend._invalidate_window_observations = lambda window: expired.append(window)
        window = {"id": 7, "pid": 70}
        with self.assertRaises(ToolError) as caught:
            backend.tool_type_text({"window": window, "text": "a" * 40})
        self.assertEqual(caught.exception.structured_content["effect"], "partial")
        self.assertEqual(caught.exception.structured_content["requested_chars"], 40)
        self.assertEqual(caught.exception.structured_content["delivered_chars"], 32)
        self.assertEqual(caught.exception.structured_content["retry_from_character"], 32)
        self.assertEqual(expired, [window])

    def test_clipboard_set_verifies_the_exact_written_text(self):
        class Pasteboard:
            value = "old"

            def clearContents(self):
                self.value = None

            def setString_forType_(self, value, _value_type):
                self.value = value
                return True

            def stringForType_(self, _value_type):
                return self.value

        pasteboard = Pasteboard()
        backend = MacOSBackend()
        backend.AppKit = types.SimpleNamespace(
            NSPasteboard=types.SimpleNamespace(generalPasteboard=lambda: pasteboard),
            NSPasteboardTypeString="public.utf8-plain-text",
        )
        result = backend.tool_clipboard_set({"text": "你好\nMac"})
        self.assertEqual(pasteboard.value, "你好\nMac")
        self.assertEqual(
            result,
            {"ok": True, "characters": 6, "effect": "confirmed", "verified": True},
        )

    def test_clipboard_set_reports_clear_then_write_failure_as_partial(self):
        class Pasteboard:
            value = "old"

            def clearContents(self):
                self.value = None

            def setString_forType_(self, _value, _value_type):
                return False

        pasteboard = Pasteboard()
        backend = MacOSBackend()
        backend.AppKit = types.SimpleNamespace(
            NSPasteboard=types.SimpleNamespace(generalPasteboard=lambda: pasteboard),
            NSPasteboardTypeString="public.utf8-plain-text",
        )
        with self.assertRaises(ToolError) as caught:
            backend.tool_clipboard_set({"text": "replacement"})
        self.assertIsNone(pasteboard.value)
        self.assertEqual(caught.exception.structured_content["code"], "clipboard_update_incomplete")
        self.assertEqual(caught.exception.structured_content["effect"], "partial")
        self.assertTrue(caught.exception.structured_content["clipboard_cleared"])

    def test_failed_window_action_expires_its_observation(self):
        backend = MacOSBackend()
        window = {"id": 8, "pid": 80}
        backend._activate_current = lambda value: value
        backend._relative_point = lambda *_args: (10.0, 20.0)
        backend._button = lambda _value: ("button", "down", "up", "dragged")
        backend._click_pointer = mock.Mock(side_effect=RuntimeError("injected click failure"))
        expired = []
        backend._invalidate_window_observations = lambda value: expired.append(value)
        with self.assertRaisesRegex(RuntimeError, "injected click failure"):
            backend.tool_click({"window": window, "x": 10, "y": 20})
        self.assertEqual(expired, [window])

    def test_failed_key_release_is_retained_for_shutdown_cleanup(self):
        backend = MacOSBackend()
        down = {"kind": "down"}
        up = {"kind": "up"}
        posts = []
        up_failures = 2

        def post(_tap, event):
            nonlocal up_failures
            posts.append(event)
            if event is up and up_failures:
                up_failures -= 1
                raise RuntimeError("injected key-up failure")

        backend.Quartz = types.SimpleNamespace(kCGHIDEventTap="hid", CGEventPost=post)
        backend._post_key_down(down, up)
        with self.assertRaisesRegex(RuntimeError, "injected key-up failure"):
            backend._post_key_up(up)
        self.assertEqual(backend._held_key_releases, [up])
        backend.close()
        self.assertEqual(backend._held_key_releases, [])
        self.assertIs(posts[-1], up)
        self.assertEqual(sum(event is up for event in posts), 3)

    def test_failed_mouse_down_keeps_a_shutdown_release(self):
        backend = MacOSBackend()
        events = []
        down_failures = 1
        up_failures = 1

        def post(event, button, x, y, click_count=1):
            nonlocal down_failures, up_failures
            events.append(event)
            if event == "down" and down_failures:
                down_failures -= 1
                raise RuntimeError("injected mouse-down interruption")
            if event == "up" and up_failures:
                up_failures -= 1
                raise RuntimeError("injected immediate release failure")

        backend._post_mouse = post
        with self.assertRaisesRegex(RuntimeError, "mouse-down interruption"):
            backend._post_mouse_down("button", "down", "up", "dragged", 4, 5)
        self.assertIn("button", backend._held_buttons)
        backend.close()
        self.assertNotIn("button", backend._held_buttons)
        self.assertEqual(events, ["down", "up", "up"])

    def test_mcp_rejects_schema_violations_before_backend_dispatch(self):
        backend = mock.Mock()
        server = MCPServer(backend)
        invalid_calls = [
            ("get_window_state", {"window": {"id": 1}, "include_screenshot": "false"}),
            ("permission_status", {"unexpected": True}),
            ("click", {"window": {"id": 1}, "click_count": 0}),
            ("drag", {"window": {"id": 1}, "from_x": math.nan, "from_y": 0, "to_x": 1, "to_y": 1}),
        ]
        for request_id, (name, arguments) in enumerate(invalid_calls, 10):
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            )
            self.assertTrue(response["result"]["isError"])
        backend.call.assert_not_called()

    def test_line_protocol_end_to_end_in_memory(self):
        source = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
        )
        target = io.StringIO()
        serve(source, target, FakeBackend())
        responses = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2])

    def test_key_chords_support_mac_modifiers_numpad_and_shifted_keysyms(self):
        key, modifiers = parse_key_chord("Command+Shift+period")
        self.assertEqual(key, 47)
        self.assertEqual(modifiers, {"command", "shift"})
        self.assertEqual(parse_key_chord("KP_0")[0], 82)
        self.assertEqual(parse_key_chord("greater"), (47, {"shift"}))
        self.assertEqual(parse_key_chord("less"), (43, {"shift"}))
        self.assertEqual(parse_key_chord("question"), (44, {"shift"}))
        self.assertEqual(parse_key_chord("Command+plus"), (24, {"command", "shift"}))
        self.assertEqual(parse_key_chord("Meta_L+colon"), (41, {"command", "shift"}))
        self.assertEqual(parse_key_chord("ISO_Left_Tab"), (48, {"shift"}))
        for symbol in "!@#$%^&*()_{}|:\"<>?~":
            _, modifiers = parse_key_chord(symbol)
            self.assertEqual(modifiers, {"shift"}, symbol)

    def test_retina_screenshot_pixels_map_to_logical_window_points(self):
        backend = MacOSBackend()
        window = {
            "id": 7,
            "app": "com.example.App",
            "pid": 70,
            "bounds": {"x": 100, "y": 50, "width": 800, "height": 600},
        }
        backend._screenshot_cache["retina"] = {
            "windowKey": ("com.example.App", 70, 7),
            "bounds": dict(window["bounds"]),
            "imageWidth": 1600,
            "imageHeight": 1200,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_test_shot__.png"),
        }
        self.assertEqual(backend._relative_point(window, 799, 599, "retina"), (499.5, 349.5))
        backend._get_window = lambda value: window
        self.assertEqual(
            backend._optional_point(
                {"window": window, "x": 799, "y": 599, "screenshotId": "retina"}
            ),
            (499.5, 349.5),
        )
        with self.assertRaisesRegex(ToolError, "outside"):
            backend._relative_point(window, 1600, 600, "retina")

    def test_retina_desktop_pixels_map_to_quartz_screen_points(self):
        backend = MacOSBackend()
        backend._desktop_bounds = lambda: {"x": -800, "y": 0, "width": 1600, "height": 600}
        backend._screenshot_cache["desktop"] = {
            "scope": "desktop",
            "windowKey": None,
            "bounds": {"x": -800, "y": 0, "width": 1600, "height": 600},
            "imageWidth": 3200,
            "imageHeight": 1200,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_desktop_test_shot__.png"),
        }
        self.assertEqual(backend._desktop_relative_point(1599, 599, "desktop"), (-0.5, 299.5))
        with self.assertRaisesRegex(ToolError, "outside"):
            backend._desktop_relative_point(3200, 600, "desktop")

    def test_mixed_scale_desktop_screens_map_independently(self):
        backend = MacOSBackend()
        layout = [
            {
                "displayId": 1,
                "bounds": {"x": -1440, "y": 0, "width": 1440, "height": 900},
                "backingScaleFactor": 2.0,
                "isMain": False,
            },
            {
                "displayId": 2,
                "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "backingScaleFactor": 1.0,
                "isMain": True,
            },
        ]
        backend._desktop_layout = lambda: layout
        fingerprint = backend._layout_fingerprint(layout)
        backend._screenshot_cache["retina-left"] = {
            "scope": "desktop",
            "windowKey": None,
            "displayId": 1,
            "bounds": dict(layout[0]["bounds"]),
            "layout": fingerprint,
            "imageWidth": 2880,
            "imageHeight": 1800,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_left_desktop_test_shot__.png"),
        }
        backend._screenshot_cache["standard-right"] = {
            "scope": "desktop",
            "windowKey": None,
            "displayId": 2,
            "bounds": dict(layout[1]["bounds"]),
            "layout": fingerprint,
            "imageWidth": 1920,
            "imageHeight": 1080,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_right_desktop_test_shot__.png"),
        }
        self.assertEqual(backend._desktop_relative_point(1439, 899, "retina-left"), (-720.5, 449.5))
        self.assertEqual(backend._desktop_relative_point(960, 540, "standard-right"), (960, 540))

    def test_appkit_screen_layout_converts_each_display_to_quartz_coordinates(self):
        class FakeScreen:
            def __init__(self, display_id, x, y, width, height, scale):
                self._display_id = display_id
                self._frame = types.SimpleNamespace(
                    origin=types.SimpleNamespace(x=x, y=y),
                    size=types.SimpleNamespace(width=width, height=height),
                )
                self._scale = scale

            def frame(self):
                return self._frame

            def deviceDescription(self):
                return {"ScreenNumber": self._display_id}

            def backingScaleFactor(self):
                return self._scale

        screens = [
            FakeScreen(10, 0, 0, 1920, 1080, 1),
            FakeScreen(20, -1440, 180, 1440, 900, 2),
            FakeScreen(30, 0, 1080, 1280, 720, 2),
        ]
        backend = MacOSBackend()
        backend.AppKit = types.SimpleNamespace(
            NSScreen=types.SimpleNamespace(screens=lambda: screens),
            NSScreenNumber="ScreenNumber",
        )
        layout = backend._desktop_layout()
        self.assertEqual([item["displayId"] for item in layout], [10, 20, 30])
        self.assertEqual(layout[0]["bounds"], {"x": 0.0, "y": 0.0, "width": 1920.0, "height": 1080.0})
        self.assertEqual(layout[1]["bounds"], {"x": -1440.0, "y": 0.0, "width": 1440.0, "height": 900.0})
        self.assertEqual(layout[2]["bounds"], {"x": 0.0, "y": -720.0, "width": 1280.0, "height": 720.0})
        self.assertEqual([item["backingScaleFactor"] for item in layout], [1.0, 2.0, 2.0])

    def test_desktop_screenshot_rejects_display_layout_drift(self):
        backend = MacOSBackend()
        original = [
            {
                "displayId": 1,
                "bounds": {"x": 0, "y": 0, "width": 1000, "height": 800},
                "backingScaleFactor": 2.0,
                "isMain": True,
            }
        ]
        backend._desktop_layout = lambda: original
        backend._screenshot_cache["before-drift"] = {
            "scope": "desktop",
            "windowKey": None,
            "displayId": 1,
            "bounds": dict(original[0]["bounds"]),
            "layout": backend._layout_fingerprint(original),
            "imageWidth": 2000,
            "imageHeight": 1600,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_layout_drift_test_shot__.png"),
        }
        changed = [dict(original[0], backingScaleFactor=1.0)]
        backend._desktop_layout = lambda: changed
        with self.assertRaisesRegex(ToolError, "display layout changed"):
            backend._desktop_relative_point(100, 100, "before-drift")

    def test_state_changing_action_can_invalidate_observation_handles(self):
        backend = MacOSBackend()
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        key = ("com.example.App", 80, 8)
        backend._element_cache[key] = {"elements": [object()]}
        backend._screenshot_cache["shot"] = {
            "windowKey": key,
            "bounds": dict(window["bounds"]),
            "imageWidth": 100,
            "imageHeight": 100,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_test_shot__.png"),
        }
        backend._invalidate_window_observations(window)
        self.assertNotIn(key, backend._element_cache)
        self.assertNotIn("shot", backend._screenshot_cache)

    def test_accessibility_indexes_expire_with_observation_handles(self):
        backend = MacOSBackend()
        window = {"id": 8, "app": "com.example.App", "pid": 80}
        key = ("com.example.App", 80, 8)
        backend._element_cache[key] = {
            "generation": "old",
            "elements": [object()],
            "created": time.monotonic() - 301,
        }
        with self.assertRaisesRegex(ToolError, "Accessibility observation is stale"):
            backend._cached_element(window, 0)
        self.assertNotIn(key, backend._element_cache)

    def test_window_observations_do_not_cross_process_restarts(self):
        backend = MacOSBackend()
        old_window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        new_process_window = {**old_window, "pid": 81}
        old_key = ("com.example.App", 80, 8)
        backend._screenshot_cache["old-shot"] = {
            "windowKey": old_key,
            "bounds": dict(old_window["bounds"]),
            "imageWidth": 100,
            "imageHeight": 100,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_test_shot__.png"),
        }
        backend._element_cache[old_key] = {
            "generation": "old-process",
            "elements": [object()],
            "created": time.monotonic(),
        }

        with self.assertRaisesRegex(ToolError, "unknown or belongs to another window"):
            backend._validate_screenshot("old-shot", new_process_window)
        with self.assertRaisesRegex(ToolError, "No Accessibility observation exists"):
            backend._cached_element(new_process_window, 0)

    def test_window_rehydration_rejects_a_reused_id_from_another_process(self):
        backend = MacOSBackend()
        current = {
            "id": 8,
            "app": "com.example.App",
            "pid": 81,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        backend._list_windows = lambda: [current]
        self.assertEqual(backend._get_window(current), current)
        with self.assertRaisesRegex(ToolError, "pid=80"):
            backend._get_window({**current, "pid": 80})

    def test_activation_rehydrates_the_window_before_an_action(self):
        backend = MacOSBackend()
        before = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        after = {**before, "bounds": {"x": 300, "y": 200, "width": 100, "height": 100}}
        windows = [before]
        backend._list_windows = lambda: [windows[0]]

        def activate(window):
            windows[0] = after

        backend._activate = activate
        self.assertEqual(backend._activate_current(before)["bounds"], after["bounds"])

    def test_activation_reports_an_explicit_appkit_refusal(self):
        backend = MacOSBackend()
        running = types.SimpleNamespace(activateWithOptions_=lambda options: False)
        backend.AppKit = types.SimpleNamespace(
            NSRunningApplication=types.SimpleNamespace(
                runningApplicationWithProcessIdentifier_=lambda pid: running
            ),
            NSApplicationActivateIgnoringOtherApps=2,
            NSApplicationActivateAllWindows=1,
        )
        with self.assertRaisesRegex(ToolError, "refused to activate"):
            backend._activate({"pid": 80})

    def test_accessibility_window_prefers_the_exact_cg_window_number(self):
        backend = MacOSBackend()
        first, second = object(), object()
        backend.ApplicationServices = types.SimpleNamespace(
            AXUIElementCreateApplication=lambda pid: "application"
        )
        values = {
            ("application", "AXWindows"): [first, second],
            (first, "AXWindowNumber"): 7,
            (second, "AXWindowNumber"): 8,
        }
        backend._ax_copy = lambda element, attribute: values.get((element, attribute))
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "title": "Same title",
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        self.assertIs(backend._ax_window(window), second)

    def test_focused_and_selected_elements_remain_actionable_and_keep_newlines(self):
        backend = MacOSBackend()
        root, focused, app = object(), object(), object()
        backend.ApplicationServices = types.SimpleNamespace(
            AXUIElementCreateApplication=lambda pid: app
        )
        backend._ax_window = lambda window: root
        backend._format_element = lambda element, index, depth: f"[{index}] item"
        values = {
            (root, "AXChildren"): [],
            (root, "AXSelectedChildren"): [focused],
            (app, "AXFocusedUIElement"): focused,
            (focused, "AXWindow"): root,
            (focused, "AXSelectedText"): "alpha\nbeta",
            (focused, "AXRole"): "AXTextArea",
            (focused, "AXValue"): "line one\nline two",
        }
        backend._ax_copy = lambda element, attribute: values.get((element, attribute))
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        state = backend._accessibility_state(window)
        self.assertEqual(state["focused_element"], "[1] item")
        self.assertEqual(state["selected_elements"], ["[1] item"])
        self.assertEqual(state["selected_text"], "alpha\nbeta")
        self.assertEqual(state["document_text"], "line one\nline two")
        self.assertIs(backend._cached_element(window, 1), focused)

    def test_focused_element_from_another_window_is_not_cached(self):
        backend = MacOSBackend()
        root, other_root, focused, app = object(), object(), object(), object()
        backend.ApplicationServices = types.SimpleNamespace(
            AXUIElementCreateApplication=lambda pid: app
        )
        backend._ax_window = lambda window: root
        backend._format_element = lambda element, index, depth: f"[{index}] item"
        values = {
            (root, "AXChildren"): [],
            (app, "AXFocusedUIElement"): focused,
            (focused, "AXWindow"): other_root,
            (other_root, "AXWindowNumber"): 9,
        }
        backend._ax_copy = lambda element, attribute: values.get((element, attribute))
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        state = backend._accessibility_state(window)
        self.assertIsNone(state["focused_element"])
        self.assertEqual(len(backend._element_cache[("com.example.App", 80, 8)]["elements"]), 1)

    def test_accessibility_depth_truncation_is_reported(self):
        backend = MacOSBackend()
        nodes = [object() for _ in range(15)]
        app = object()
        backend.ApplicationServices = types.SimpleNamespace(
            AXUIElementCreateApplication=lambda pid: app
        )
        backend._ax_window = lambda window: nodes[0]
        backend._format_element = lambda element, index, depth: f"[{index}] item"
        values = {(app, "AXFocusedUIElement"): None}
        for index, node in enumerate(nodes):
            values[(node, "AXChildren")] = [nodes[index + 1]] if index + 1 < len(nodes) else []
        backend._ax_copy = lambda element, attribute: values.get((element, attribute))
        state = backend._accessibility_state(
            {
                "id": 8,
                "app": "com.example.App",
                "pid": 80,
                "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            }
        )
        self.assertTrue(state["truncated"])

    def test_large_accessibility_selection_is_bounded_and_reported(self):
        backend = MacOSBackend()
        root, app = object(), object()
        selected = [object() for _ in range(64)]
        omitted_row = object()
        backend.ApplicationServices = types.SimpleNamespace(
            AXUIElementCreateApplication=lambda pid: app
        )
        backend._ax_window = lambda window: root
        backend._format_element = lambda element, index, depth: f"[{index}] item"
        values = {
            (root, "AXChildren"): [],
            (root, "AXSelectedChildren"): selected,
            (root, "AXSelectedRows"): [omitted_row],
            (app, "AXFocusedUIElement"): None,
        }
        backend._ax_copy = lambda element, attribute: values.get((element, attribute))
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        state = backend._accessibility_state(window)
        self.assertTrue(state["truncated"])
        self.assertEqual(len(state["selected_elements"]), 64)
        self.assertEqual(len(backend._element_cache[("com.example.App", 80, 8)]["elements"]), 65)

    def test_accessibility_window_rejects_an_equal_distance_tie(self):
        backend = MacOSBackend()
        first, second = object(), object()
        backend.ApplicationServices = types.SimpleNamespace(
            AXUIElementCreateApplication=lambda pid: "application"
        )
        values = {
            ("application", "AXWindows"): [first, second],
            (first, "AXTitle"): "Same",
            (second, "AXTitle"): "Same",
            (first, "AXPosition"): (0, 0),
            (second, "AXPosition"): (0, 0),
            (first, "AXSize"): (100, 100),
            (second, "AXSize"): (100, 100),
        }
        backend._ax_copy = lambda element, attribute: values.get((element, attribute))
        backend._ax_value = lambda value, value_type: value
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "title": "Same",
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        with self.assertRaisesRegex(ToolError, "Accessibility"):
            backend._ax_window(window)

    def test_failed_drag_releases_the_mouse_button(self):
        backend = MacOSBackend()
        backend._button = lambda value: ("button", "down", "up", "dragged")
        events = []

        def post(event_type, button, x, y, click_count=1):
            events.append((event_type, x, y))
            if event_type == "dragged":
                raise RuntimeError("injected drag failure")

        backend._post_mouse = post
        with self.assertRaisesRegex(RuntimeError, "injected drag failure"):
            backend._drag_pointer((1.0, 2.0), (10.0, 20.0), 0.1)
        self.assertEqual(events[0][0], "down")
        self.assertEqual(events[-1][0], "up")
        self.assertFalse(backend._held_buttons)

    def test_failed_drag_release_is_retained_for_shutdown_cleanup(self):
        backend = MacOSBackend()
        backend._button = lambda value: ("button", "down", "up", "dragged")
        events = []
        up_failures = 1

        def post(event_type, button, x, y, click_count=1):
            nonlocal up_failures
            events.append(event_type)
            if event_type == "dragged":
                raise RuntimeError("injected drag failure")
            if event_type == "up" and up_failures:
                up_failures -= 1
                raise RuntimeError("injected release failure")

        backend._post_mouse = post
        with self.assertRaisesRegex(RuntimeError, "injected drag failure"):
            backend._drag_pointer((1.0, 2.0), (10.0, 20.0), 0.1)
        self.assertIn("button", backend._held_buttons)
        backend.close()
        self.assertEqual(events, ["down", "dragged", "up", "up"])
        self.assertFalse(backend._held_buttons)

    def test_optional_pointer_coordinates_must_be_paired(self):
        backend = MacOSBackend()
        with self.assertRaisesRegex(ToolError, "supplied together"):
            backend._optional_point({"x": 1})
        with self.assertRaisesRegex(ToolError, "require both"):
            backend._optional_point({"window": {"id": 1}})

    def test_raw_pointer_honors_desktop_screenshot_bindings(self):
        backend = MacOSBackend()
        backend._desktop_bounds = lambda: {"x": -400, "y": 0, "width": 800, "height": 600}
        backend._screenshot_cache["desktop"] = {
            "scope": "desktop",
            "windowKey": None,
            "bounds": {"x": -400, "y": 0, "width": 800, "height": 600},
            "imageWidth": 1600,
            "imageHeight": 1200,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_desktop_shot__.png"),
        }
        self.assertEqual(
            backend._optional_point({"x": 800, "y": 600, "screenshotId": "desktop"}),
            (0.0, 300.0),
        )
        with self.assertRaisesRegex(ToolError, "direct desktop observation"):
            backend._optional_point({"x": 1, "y": 2, "screenshotId": "unknown"})

    def test_cursor_read_failure_is_explicit(self):
        backend = MacOSBackend()
        backend.Quartz = types.SimpleNamespace(CGEventCreate=lambda source: None)
        with self.assertRaisesRegex(ToolError, "cursor position"):
            backend._cursor()

    def test_failed_click_release_is_retained_for_shutdown_cleanup(self):
        backend = MacOSBackend()
        events = []
        up_failures = 2

        def post(event_type, button, x, y, click_count=1):
            nonlocal up_failures
            events.append(event_type)
            if event_type == "up" and up_failures:
                up_failures -= 1
                raise RuntimeError("injected release failure")

        backend._post_mouse = post
        with self.assertRaisesRegex(RuntimeError, "injected release failure"):
            backend._click_pointer("button", "down", "up", "dragged", 1, 2, 1)
        self.assertIn("button", backend._held_buttons)
        backend.close()
        self.assertEqual(events, ["down", "up", "up", "up"])
        self.assertFalse(backend._held_buttons)

    def test_raw_held_button_moves_as_a_drag_and_releases_on_close(self):
        backend = MacOSBackend()
        backend.Quartz = types.SimpleNamespace(
            kCGMouseButtonLeft="left-button",
            kCGEventLeftMouseDown="left-down",
            kCGEventLeftMouseUp="left-up",
            kCGEventLeftMouseDragged="left-dragged",
            kCGEventMouseMoved="moved",
        )
        positions = iter([(1.0, 2.0), (1.0, 2.0)])
        backend._cursor = lambda: next(positions)
        events = []
        backend._post_mouse = lambda event, button, x, y, click_count=1: events.append(
            (event, button, x, y)
        )
        backend.tool_mouse_down({"x": 1, "y": 2})
        backend.tool_move_mouse({"x": 5, "y": 6, "duration": 0})
        backend.close()
        self.assertEqual([event[0] for event in events], ["left-down", "left-dragged", "left-up"])

    def test_interrupted_held_move_releases_at_last_delivered_point(self):
        backend = MacOSBackend()
        backend.Quartz = types.SimpleNamespace(
            kCGMouseButtonLeft="left-button",
            kCGEventLeftMouseDown="left-down",
            kCGEventLeftMouseUp="left-up",
            kCGEventLeftMouseDragged="left-dragged",
            kCGEventMouseMoved="moved",
        )
        backend._cursor = lambda: (1.0, 2.0)
        events = []
        delivered_drags = 0
        invalidations = []
        backend._invalidate_all_observations = lambda: invalidations.append(True)

        def post(event, button, x, y, click_count=1):
            nonlocal delivered_drags
            if event == "left-dragged":
                delivered_drags += 1
                if delivered_drags == 3:
                    raise RuntimeError("injected move failure")
            events.append((event, button, x, y))

        backend._post_mouse = post
        backend.tool_mouse_down({"x": 1, "y": 2})
        with self.assertRaisesRegex(RuntimeError, "injected move failure"):
            backend.tool_move_mouse({"x": 7, "y": 8, "duration": 0.1})
        self.assertEqual(len(invalidations), 2)
        last_delivered = [event for event in events if event[0] == "left-dragged"][-1]
        backend.close()
        self.assertEqual(events[-1][0], "left-up")
        self.assertEqual(events[-1][2:], last_delivered[2:])

    def test_main_routes_sigterm_and_interrupt_through_server_cleanup(self):
        previous = object()
        with (
            mock.patch.object(server_module.signal, "signal", side_effect=[previous, previous]) as install,
            mock.patch.object(server_module, "serve", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(server_module.main([]), 0)
        self.assertEqual(install.call_args_list[0].args[0], server_module.signal.SIGTERM)
        self.assertIs(install.call_args_list[0].args[1], server_module._interrupt_for_shutdown)
        self.assertEqual(install.call_args_list[1].args, (server_module.signal.SIGTERM, previous))

    def test_serve_closes_backend_when_interrupted(self):
        backend = types.SimpleNamespace(close=mock.Mock())

        class InterruptingInput:
            def __iter__(self):
                return self

            def __next__(self):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            serve(InterruptingInput(), io.StringIO(), backend)
        backend.close.assert_called_once_with()

    def test_repeated_mouse_down_and_click_do_not_double_press_a_held_button(self):
        backend = MacOSBackend()
        backend.Quartz = types.SimpleNamespace(
            kCGMouseButtonLeft="left-button",
            kCGEventLeftMouseDown="left-down",
            kCGEventLeftMouseUp="left-up",
            kCGEventLeftMouseDragged="left-dragged",
        )
        events = []
        backend._post_mouse = lambda event, button, x, y, click_count=1: events.append(event)
        backend.tool_mouse_down({"x": 1, "y": 2})
        with self.assertRaisesRegex(ToolError, "already held"):
            backend.tool_mouse_down({"x": 3, "y": 4})
        with self.assertRaisesRegex(ToolError, "already held"):
            backend._click_pointer("left-button", "left-down", "left-up", "left-dragged", 3, 4, 1)
        self.assertEqual(events, ["left-down"])
        backend.close()

    def test_invalid_click_count_never_posts_an_event(self):
        backend = MacOSBackend()
        window = {
            "id": 8,
            "app": "com.example.App",
            "pid": 80,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        backend._activate_current = lambda value: window
        backend._post_mouse = mock.Mock()
        with self.assertRaisesRegex(ToolError, "between 1 and 4"):
            backend.tool_click({"window": window, "x": 1, "y": 2, "click_count": 0})
        backend._post_mouse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
