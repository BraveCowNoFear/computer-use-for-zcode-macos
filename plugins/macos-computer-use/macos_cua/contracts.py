"""MCP tool contracts shared by the runtime and contract tests."""

from __future__ import annotations

from typing import Any


class ToolError(RuntimeError):
    """A user-correctable Computer Use tool error."""


WINDOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "integer", "description": "Opaque window ID returned by this server."},
        "app": {"type": "string", "description": "Optional app identifier returned with the window."},
        "title": {"type": "string"},
        "pid": {"type": "integer"},
    },
    "additionalProperties": True,
}


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "computer_use_health",
        "description": "Check platform, native dependencies, permissions, and local runtime readiness.",
        "inputSchema": _object({}),
    },
    {
        "name": "permission_status",
        "description": "Read macOS Accessibility and Screen Recording permission status without prompting.",
        "inputSchema": _object({}),
    },
    {
        "name": "request_permissions",
        "description": "Ask macOS for only the requested native Accessibility and/or Screen Recording grants.",
        "inputSchema": _object(
            {
                "accessibility": {"type": "boolean", "default": True},
                "screen_recording": {"type": "boolean", "default": False},
                "open_settings": {"type": "boolean", "default": True},
            }
        ),
    },
    {
        "name": "list_windows",
        "description": "List open targetable macOS windows from front to back.",
        "inputSchema": _object({}),
    },
    {
        "name": "get_window",
        "description": "Rehydrate a currently open window by an ID returned by list_windows or list_apps.",
        "inputSchema": _object(
            {"id": {"type": "integer"}, "app": {"type": "string"}, "pid": {"type": "integer"}},
            ["id"],
        ),
    },
    {
        "name": "list_apps",
        "description": "List installed and running macOS applications with their targetable windows.",
        "inputSchema": _object({}),
    },
    {
        "name": "launch_app",
        "description": "Launch an app by bundle ID, display name, or .app path and return its pid and current windows.",
        "inputSchema": _object({"app": {"type": "string"}}, ["app"]),
    },
    {
        "name": "get_window_state",
        "description": "Capture a point-in-time screenshot and/or indexed macOS Accessibility tree for a window.",
        "inputSchema": _object(
            {
                "window": WINDOW_SCHEMA,
                "include_screenshot": {"type": "boolean", "default": True},
                "include_text": {"type": "boolean", "default": False},
            },
            ["window"],
        ),
    },
    {
        "name": "get_desktop_state",
        "description": "Capture one fresh screenshot per active macOS display for precise menu bar, Dock, desktop, and system UI control across mixed Retina scales.",
        "inputSchema": _object({}),
    },
    {
        "name": "click",
        "description": "Click a window-relative coordinate or an indexed element from the latest Accessibility state.",
        "inputSchema": _object(
            {
                "window": WINDOW_SCHEMA,
                "x": {"type": "number"},
                "y": {"type": "number"},
                "element_index": {"type": "integer", "minimum": 0},
                "screenshotId": {"type": "string"},
                "mouse_button": {"type": "string", "enum": ["left", "right", "middle", "l", "r", "m"], "default": "left"},
                "click_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            },
            ["window"],
        ),
    },
    {
        "name": "press_key",
        "description": "Press a + separated macOS key chord in a target window, including Command and Option chords.",
        "inputSchema": _object({"window": WINDOW_SCHEMA, "key": {"type": "string"}}, ["window", "key"]),
    },
    {
        "name": "type_text",
        "description": "Type literal Unicode text into the current focus in a target window.",
        "inputSchema": _object({"window": WINDOW_SCHEMA, "text": {"type": "string"}}, ["window", "text"]),
    },
    {
        "name": "scroll",
        "description": "Scroll by pixel deltas from a window-relative coordinate; positive Y scrolls down.",
        "inputSchema": _object(
            {
                "window": WINDOW_SCHEMA,
                "x": {"type": "number"},
                "y": {"type": "number"},
                "scrollX": {"type": "number"},
                "scrollY": {"type": "number"},
                "screenshotId": {"type": "string"},
            },
            ["window", "x", "y", "scrollX", "scrollY"],
        ),
    },
    {
        "name": "set_value",
        "description": "Replace the value of an indexed editable Accessibility element.",
        "inputSchema": _object(
            {"window": WINDOW_SCHEMA, "element_index": {"type": "integer", "minimum": 0}, "value": {"type": "string"}},
            ["window", "element_index", "value"],
        ),
    },
    {
        "name": "drag",
        "description": "Drag from one window-relative coordinate to another.",
        "inputSchema": _object(
            {
                "window": WINDOW_SCHEMA,
                "from_x": {"type": "number"},
                "from_y": {"type": "number"},
                "to_x": {"type": "number"},
                "to_y": {"type": "number"},
                "duration": {"type": "number", "minimum": 0, "maximum": 30, "default": 0.35},
                "screenshotId": {"type": "string"},
            },
            ["window", "from_x", "from_y", "to_x", "to_y"],
        ),
    },
    {
        "name": "perform_secondary_action",
        "description": "Run a named Accessibility action such as Raise, Expand, Collapse, Show Menu, or Scroll Down.",
        "inputSchema": _object(
            {"window": WINDOW_SCHEMA, "element_index": {"type": "integer", "minimum": 0}, "action": {"type": "string"}},
            ["window", "element_index", "action"],
        ),
    },
    {
        "name": "activate_window",
        "description": "Bring a returned window and its owning app to the foreground.",
        "inputSchema": _object({"window": WINDOW_SCHEMA}, ["window"]),
    },
    {
        "name": "desktop_click",
        "description": "Click a coordinate from the latest direct desktop screenshot without an app/window restriction.",
        "inputSchema": _object(
            {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "screenshotId": {"type": "string"},
                "mouse_button": {"type": "string", "enum": ["left", "right", "middle", "l", "r", "m"], "default": "left"},
                "click_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            },
            ["x", "y", "screenshotId"],
        ),
    },
    {
        "name": "desktop_press_key",
        "description": "Press a macOS key chord into the current desktop focus after a fresh desktop observation.",
        "inputSchema": _object(
            {"key": {"type": "string"}, "screenshotId": {"type": "string"}},
            ["key", "screenshotId"],
        ),
    },
    {
        "name": "desktop_type_text",
        "description": "Type literal Unicode into the current desktop focus after a fresh desktop observation.",
        "inputSchema": _object(
            {"text": {"type": "string"}, "screenshotId": {"type": "string"}},
            ["text", "screenshotId"],
        ),
    },
    {
        "name": "desktop_scroll",
        "description": "Scroll at a coordinate from the latest complete desktop screenshot.",
        "inputSchema": _object(
            {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "scrollX": {"type": "number"},
                "scrollY": {"type": "number"},
                "screenshotId": {"type": "string"},
            },
            ["x", "y", "scrollX", "scrollY", "screenshotId"],
        ),
    },
    {
        "name": "desktop_drag",
        "description": "Drag between two coordinates from the latest complete desktop screenshot.",
        "inputSchema": _object(
            {
                "from_x": {"type": "number"},
                "from_y": {"type": "number"},
                "to_x": {"type": "number"},
                "to_y": {"type": "number"},
                "duration": {"type": "number", "minimum": 0, "maximum": 30, "default": 0.35},
                "screenshotId": {"type": "string"},
            },
            ["from_x", "from_y", "to_x", "to_y", "screenshotId"],
        ),
    },
    {
        "name": "move_mouse",
        "description": "Move the pointer to a screen coordinate or a target-window-relative coordinate.",
        "inputSchema": _object(
            {"x": {"type": "number"}, "y": {"type": "number"}, "window": WINDOW_SCHEMA, "screenshotId": {"type": "string"}, "duration": {"type": "number", "minimum": 0, "maximum": 30, "default": 0}},
            ["x", "y"],
        ),
    },
    {
        "name": "mouse_down",
        "description": "Press and hold a mouse button at the current pointer or a supplied coordinate.",
        "inputSchema": _object(
            {"x": {"type": "number"}, "y": {"type": "number"}, "window": WINDOW_SCHEMA, "screenshotId": {"type": "string"}, "mouse_button": {"type": "string", "enum": ["left", "right", "middle", "l", "r", "m"], "default": "left"}}
        ),
    },
    {
        "name": "mouse_up",
        "description": "Release a held mouse button at the current pointer or a supplied coordinate.",
        "inputSchema": _object(
            {"x": {"type": "number"}, "y": {"type": "number"}, "window": WINDOW_SCHEMA, "screenshotId": {"type": "string"}, "mouse_button": {"type": "string", "enum": ["left", "right", "middle", "l", "r", "m"], "default": "left"}}
        ),
    },
    {
        "name": "get_cursor_position",
        "description": "Get the current global mouse cursor position.",
        "inputSchema": _object({}),
    },
    {
        "name": "clipboard_get",
        "description": "Read plain text from the local macOS clipboard.",
        "inputSchema": _object({}),
    },
    {
        "name": "clipboard_set",
        "description": "Replace the local macOS clipboard with plain text.",
        "inputSchema": _object({"text": {"type": "string"}}, ["text"]),
    },
]

TOOL_NAMES = frozenset(tool["name"] for tool in TOOL_DEFINITIONS)
CORE_CODEX_TOOL_NAMES = frozenset(
    {
        "list_windows",
        "get_window",
        "list_apps",
        "launch_app",
        "get_window_state",
        "click",
        "press_key",
        "type_text",
        "scroll",
        "set_value",
        "drag",
        "perform_secondary_action",
        "activate_window",
    }
)
