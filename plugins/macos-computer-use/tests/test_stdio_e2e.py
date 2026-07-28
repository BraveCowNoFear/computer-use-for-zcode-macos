from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class StdioEndToEndTests(unittest.TestCase):
    def test_real_server_process_negotiates_and_lists_tools(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PLUGIN_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [sys.executable, "-m", "macos_cua.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "computer_use_health", "arguments": {}}},
        ]
        stdout, stderr = process.communicate("".join(json.dumps(item) + "\n" for item in requests), timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        responses = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3])
        self.assertGreaterEqual(len(responses[1]["result"]["tools"]), 13)
        self.assertFalse(responses[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
