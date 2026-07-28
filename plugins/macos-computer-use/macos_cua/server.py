"""Small dependency-free MCP stdio server for ZCode and compatible hosts."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import traceback
from typing import Any, TextIO

from . import __version__
from .contracts import TOOL_DEFINITIONS, TOOL_NAMES, ToolError


SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}
TOOL_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOL_DEFINITIONS}


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _validate_schema(value: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        raise ToolError(f"{path} must be {expected}")
    if expected == "number" and not math.isfinite(float(value)):
        raise ToolError(f"{path} must be finite")
    if expected == "object":
        assert isinstance(value, dict)
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise ToolError(f"{path}.{required} is required")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ToolError(f"{path} contains unsupported fields: {extras}")
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                _validate_schema(item, child, f"{path}.{key}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{path} must be one of {schema['enum']}")
    if "minimum" in schema and value < schema["minimum"]:
        raise ToolError(f"{path} must be at least {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        raise ToolError(f"{path} must be at most {schema['maximum']}")


def _backend_factory() -> Any:
    if sys.platform != "darwin":
        from .unsupported import UnsupportedBackend

        return UnsupportedBackend()
    from .macos import MacOSBackend

    return MacOSBackend()


def _public_payload(value: Any, image_blocks: list[dict[str, str]]) -> Any:
    """Remove private image bytes from JSON while emitting proper MCP image blocks."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        private_data = value.get("_image_base64")
        if isinstance(private_data, str):
            image_blocks.append(
                {
                    "type": "image",
                    "data": private_data,
                    "mimeType": str(value.get("mimeType", "image/png")),
                }
            )
        for key, item in value.items():
            if key.startswith("_"):
                continue
            result[key] = _public_payload(item, image_blocks)
        return result
    if isinstance(value, list):
        return [_public_payload(item, image_blocks) for item in value]
    return value


class MCPServer:
    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend if backend is not None else _backend_factory()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request: method must be a string")

        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            requested = message.get("params", {}).get("protocolVersion", "2025-03-26")
            protocol = requested if requested in SUPPORTED_PROTOCOLS else "2025-03-26"
            return self._result(
                request_id,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "macos-computer-use", "version": __version__},
                    "instructions": "Select one returned window, observe, perform one action, then refresh state.",
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            return self._call_tool(request_id, message.get("params", {}))
        return self._error(request_id, -32601, f"Method not found: {method}")

    def _call_tool(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name not in TOOL_NAMES:
            return self._error(request_id, -32602, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "Tool arguments must be an object")
        try:
            _validate_schema(arguments, TOOL_SCHEMAS[str(name)])
            raw = self.backend.call(str(name), arguments)
            image_blocks: list[dict[str, str]] = []
            public = _public_payload(copy.deepcopy(raw), image_blocks)
            text_block = {"type": "text", "text": json.dumps(public, ensure_ascii=False, separators=(",", ":"))}
            result = {
                "content": [text_block, *image_blocks],
                "structuredContent": public,
                "isError": False,
            }
            return self._result(request_id, result)
        except (ToolError, ValueError, KeyError, TypeError) as error:
            content = {"type": "text", "text": str(error)}
            return self._result(request_id, {"content": [content], "isError": True})
        except Exception as error:  # Keep MCP alive after native failures.
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            content = {"type": "text", "text": f"Native Computer Use failure: {error}"}
            return self._result(request_id, {"content": [content], "isError": True})

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, backend: Any | None = None) -> None:
    server = MCPServer(backend)
    try:
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("message must be an object")
                response = server.handle(message)
                if response is not None:
                    stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stdout.flush()
            except Exception as error:
                response = MCPServer._error(None, -32700, f"Parse error: {error}")
                stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                stdout.flush()
    finally:
        close = getattr(server.backend, "close", None)
        if callable(close):
            close()


def self_test() -> int:
    server = MCPServer()
    initialized = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    health = server.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "computer_use_health", "arguments": {}}}
    )
    report = {
        "initialize": initialized,
        "toolCount": len(listed["result"]["tools"]) if listed else 0,
        "health": health,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    structured_health = health["result"].get("structuredContent", {}) if health else {}
    native_ready = sys.platform != "darwin" or structured_health.get("nativeDependencies") is True
    return 0 if report["toolCount"] >= 13 and native_ready else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="macOS Computer Use MCP server")
    parser.add_argument("--self-test", action="store_true", help="Run a no-input MCP and backend health smoke test")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
