"""Non-macOS backend used for contract tests and actionable diagnostics."""

from __future__ import annotations

import sys
from typing import Any

from .contracts import ToolError


class UnsupportedBackend:
    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "computer_use_health":
            return {
                "ok": False,
                "platform": sys.platform,
                "nativeDependencies": False,
                "message": "The MCP contract is healthy, but live control requires macOS.",
            }
        if name == "permission_status":
            return {
                "platform": sys.platform,
                "accessibility": False,
                "screenRecording": False,
                "message": "Permission status is only available on macOS.",
            }
        raise ToolError(f"{name} requires macOS; current platform is {sys.platform}")
