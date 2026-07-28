from __future__ import annotations

import base64
import io
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from macos_cua.contracts import CORE_CODEX_TOOL_NAMES, TOOL_DEFINITIONS, TOOL_NAMES, ToolError
from macos_cua.macos import MacOSBackend, parse_key_chord
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

    def test_window_state_observes_pixels_and_accessibility_by_default(self):
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
        self.assertEqual(state["accessibility"], {"tree": "[0] AXWindow"})

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

    def test_tool_errors_do_not_kill_server(self):
        server = MCPServer(FakeBackend())
        response = server.handle(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "click", "arguments": {}}}
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("fresh observation", response["result"]["content"][0]["text"])

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

    def test_key_chords_support_mac_modifiers_and_numpad(self):
        key, modifiers = parse_key_chord("Command+Shift+period")
        self.assertEqual(key, 47)
        self.assertEqual(modifiers, {"command", "shift"})
        self.assertEqual(parse_key_chord("KP_0")[0], 82)

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
        self.assertEqual(backend._relative_point(window, 800, 600, "retina"), (500, 350))

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
        self.assertEqual(backend._desktop_relative_point(1600, 600, "desktop"), (0, 300))

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
        self.assertEqual(backend._desktop_relative_point(1440, 900, "retina-left"), (-720, 450))
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


if __name__ == "__main__":
    unittest.main()
