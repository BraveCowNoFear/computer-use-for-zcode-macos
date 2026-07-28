from __future__ import annotations

import base64
import io
import json
import sys
import time
import unittest
from pathlib import Path

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
        self.assertTrue(hasattr(backend.AppKit, "NSWorkspace"))

    def test_codex_core_is_complete(self):
        self.assertTrue(CORE_CODEX_TOOL_NAMES.issubset(TOOL_NAMES))
        self.assertEqual(len(TOOL_NAMES), len(TOOL_DEFINITIONS))

    def test_tool_schemas_are_objects(self):
        for tool in TOOL_DEFINITIONS:
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            self.assertIn("description", tool)

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
            "bounds": {"x": 100, "y": 50, "width": 800, "height": 600},
        }
        backend._screenshot_cache["retina"] = {
            "windowKey": ("com.example.App", 7),
            "bounds": dict(window["bounds"]),
            "imageWidth": 1600,
            "imageHeight": 1200,
            "created": time.monotonic(),
            "path": str(PLUGIN_ROOT / "__never_created_test_shot__.png"),
        }
        self.assertEqual(backend._relative_point(window, 800, 600, "retina"), (500, 350))

    def test_state_changing_action_can_invalidate_observation_handles(self):
        backend = MacOSBackend()
        window = {
            "id": 8,
            "app": "com.example.App",
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        key = ("com.example.App", 8)
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


if __name__ == "__main__":
    unittest.main()
