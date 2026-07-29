from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "macos-computer-use"


class RepositoryContractTests(unittest.TestCase):
    def test_zcode_marketplace_points_to_plugin(self):
        marketplace = json.loads((ROOT / "marketplace.json").read_text(encoding="utf-8"))
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "macos-computer-use")
        self.assertEqual((ROOT / entry["source"]).resolve(), PLUGIN.resolve())
        manifest = json.loads((PLUGIN / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(entry["version"], manifest["version"])

    def test_zcode_and_codex_manifests_match(self):
        zcode = json.loads((PLUGIN / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for field in ("name", "version", "description", "license"):
            self.assertEqual(zcode[field], codex[field])
        self.assertTrue((PLUGIN / zcode["mcpServers"]).exists())
        self.assertTrue((PLUGIN / zcode["skills"] / "macos-computer-use" / "SKILL.md").exists())

    def test_codex_marketplace_and_interface_shape(self):
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], PLUGIN.name)
        self.assertEqual(entry["source"], {"source": "local", "path": "./plugins/macos-computer-use"})
        self.assertEqual(entry["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        self.assertEqual(entry["category"], "Productivity")

        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertIsInstance(prompts, list)
        self.assertTrue(1 <= len(prompts) <= 3)
        self.assertTrue(all(isinstance(prompt, str) and len(prompt) <= 128 for prompt in prompts))
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")

    def test_release_versions_are_synchronized(self):
        expected = json.loads((ROOT / "marketplace.json").read_text(encoding="utf-8"))["plugins"][0]["version"]
        package = (PLUGIN / "macos_cua" / "__init__.py").read_text(encoding="utf-8")
        launcher = (PLUGIN / "scripts" / "run-mcp.sh").read_text(encoding="utf-8")
        common = (PLUGIN / "scripts" / "runtime-common.sh").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'__version__ = "([^"]+)"', package).group(1), expected)
        self.assertEqual(re.search(r'MACOS_CUA_RUNTIME_VERSION="([^"]+)"', common).group(1), expected)
        self.assertIn('DEPENDENCY_ID="$MACOS_CUA_DEPENDENCY_ID"', launcher)

    def test_fallback_first_run_is_dependency_versioned_and_atomically_published(self):
        launcher = (PLUGIN / "scripts" / "run-mcp.sh").read_text(encoding="utf-8")
        common = (PLUGIN / "scripts" / "runtime-common.sh").read_text(encoding="utf-8")
        dependency_id = re.search(r'MACOS_CUA_DEPENDENCY_ID="([^"]+)"', common).group(1)
        lock_text = (PLUGIN / "requirements.txt").read_text(encoding="utf-8")
        canonical_lock = lock_text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        lock_digest = hashlib.sha256(canonical_lock).hexdigest()[:12]
        self.assertEqual(dependency_id, f"pyobjc-12.2.1-{lock_digest}")
        self.assertIn('DATA_VENV="$DATA_DIR/venv-$DEPENDENCY_ID"', launcher)
        self.assertIn('STAGING_VENV="$DATA_DIR/.venv-$DEPENDENCY_ID.install.$$"', launcher)
        self.assertIn('"$STAGING_PYTHON" -m macos_cua.server --self-test', launcher)
        self.assertIn('mv "$STAGING_VENV" "$DATA_VENV"', launcher)
        self.assertNotIn("runtime-version", launcher)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("Verify automatic fallback first run", workflow)
        self.assertIn('MACOS_CUA_DATA_DIR="$data" /bin/bash plugins/macos-computer-use/scripts/run-mcp.sh', workflow)
        self.assertIn('test -x "$data/venv-$MACOS_CUA_DEPENDENCY_ID/bin/python3"', workflow)
        self.assertIn("macos_cua_native_runtime_ready", launcher)

    def test_fallback_dependencies_are_exact_binary_wheels(self):
        requirement_text = (PLUGIN / "requirements.txt").read_text(encoding="utf-8")
        requirements = [line.split("==", 1)[0] for line in requirement_text.splitlines() if line and not line[0].isspace()]
        self.assertEqual(
            requirements,
            [
                "pyobjc-core",
                "pyobjc-framework-Cocoa",
                "pyobjc-framework-CoreText",
                "pyobjc-framework-Quartz",
                "pyobjc-framework-ApplicationServices",
            ],
        )
        self.assertEqual(requirement_text.count('==12.2.1; sys_platform == "darwin"'), 5)
        self.assertEqual(requirement_text.count("--hash=sha256:"), 45)
        self.assertNotIn(">=", requirement_text)
        source = (PLUGIN / "scripts" / "run-mcp.sh").read_text(encoding="utf-8")
        self.assertIn("--only-binary=:all:", source)
        self.assertIn("--no-deps", source)
        self.assertIn("--require-hashes", source)
        self.assertIn("--no-cache-dir", source)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("--only-binary=:all:", workflow)
        self.assertIn("--no-deps", workflow)
        self.assertIn("--require-hashes", workflow)
        common = (PLUGIN / "scripts" / "runtime-common.sh").read_text(encoding="utf-8")
        self.assertIn('(3, 10) <= sys.version_info < (3, 16)', common)
        self.assertIn("CPython 3.10 through 3.15", common)
        backend = (PLUGIN / "macos_cua" / "macos.py").read_text(encoding="utf-8")
        self.assertIn('EXPECTED_PYOBJC_VERSION = "12.2.1"', backend)
        self.assertIn("require_exact_pyobjc_versions()", backend)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "requires a Unix bash runtime")
    def test_runtime_accepts_the_ci_cpython(self):
        common = PLUGIN / "scripts" / "runtime-common.sh"
        script = (
            f"source {shlex.quote(str(common))}\n"
            f"python_is_supported {shlex.quote(sys.executable)}\n"
            f"require_supported_python {shlex.quote(sys.executable)}\n"
        )
        completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_mcp_launchers_are_local_stdio(self):
        config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(config["mcpServers"]),
            {"macos-computer-use", "macos-computer-use-fallback"},
        )
        for server in config["mcpServers"].values():
            self.assertEqual(server["type"], "stdio")
            self.assertEqual(server["command"], "/bin/bash")
            self.assertNotIn("url", server)
        self.assertEqual(
            config["mcpServers"]["macos-computer-use"]["env"]["CUA_DRIVER_TELEMETRY_HOME"],
            "${CLAUDE_PLUGIN_DATA}/cua-telemetry",
        )

    def test_primary_launcher_is_pinned_and_unrestricted(self):
        launcher = (PLUGIN / "scripts" / "run-cua-driver.sh").read_text(encoding="utf-8")
        self.assertIn('CUA_VERSION="0.13.1"', launcher)
        self.assertRegex(launcher, r'ASSET_SHA256="[0-9a-f]{64}"')
        self.assertIn(
            'ASSET_SHA256="236fc1aa02a09046945074623a02c86646b0be4c48754c6f502f9b1fff2bc032"',
            launcher,
        )
        self.assertIn(
            'EXPECTED_BINARY_SHA256="3b926c2ce6be80099176f43f0e00d81caf4ac9746a72756cbb7361bef8dbbbce"',
            launcher,
        )
        self.assertIn(
            'EXPECTED_CURSOR_HELPER_SHA256="04123f0f6611dfc5428aa13e863982c9da8e963d9ccde1a89fdc922b39093957"',
            launcher,
        )
        self.assertIn('ASSET_NAME="cua-driver-rs-${CUA_VERSION}-darwin-universal.tar.gz"', launcher)
        self.assertIn('verify_sha256 "$ASSET" "$ASSET_SHA256"', launcher)
        self.assertIn('matches_sha256 "$candidate" "$EXPECTED_BINARY_SHA256"', launcher)
        self.assertIn('matches_sha256 "$cursor_helper" "$EXPECTED_CURSOR_HELPER_SHA256"', launcher)
        self.assertIn('APP_ROOT="$APP_PARENT/v${CUA_VERSION}"', launcher)
        self.assertNotIn('/Applications/CuaDriver.app', launcher)
        self.assertIn('mv "$extracted" "$APP_ROOT"', launcher)
        self.assertIn('local download="$ASSET.download.$$"', launcher)
        self.assertIn('mv "$download" "$ASSET"', launcher)
        self.assertIn("codesign --verify --deep --strict", launcher)
        self.assertIn("spctl --assess --type execute", launcher)
        self.assertIn('EXPECTED_TEAM_ID="YCK386LBJ7"', launcher)
        self.assertIn('EXPECTED_AUTHORITY="Developer ID Application: Cua AI, Inc. (YCK386LBJ7)"', launcher)
        self.assertIn('TeamIdentifier=$EXPECTED_TEAM_ID', launcher)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("3b926c2ce6be80099176f43f0e00d81caf4ac9746a72756cbb7361bef8dbbbce"), 2)
        self.assertGreaterEqual(workflow.count("04123f0f6611dfc5428aa13e863982c9da8e963d9ccde1a89fdc922b39093957"), 2)
        self.assertIn("--permission-mode unrestricted", launcher)
        self.assertIn("--dangerously-bypass-approvals", launcher)
        self.assertIn("-u CUA_DRIVER_RS_PERMISSIONS_GATE", launcher)
        self.assertIn("--env CUA_DRIVER_RS_PERMISSIONS_GATE=0", launcher)
        self.assertIn("--env CUA_DRIVER_RS_PERMISSIONS_GATE=0", workflow)
        self.assertIn(
            "health_report get_config set_config get_accessibility_tree check_permissions",
            launcher,
        )
        self.assertIn("start_session get_session_state escalate_session end_session", launcher)
        self.assertIn("get_agent_cursor_state set_agent_cursor_enabled", launcher)
        self.assertIn("set_agent_cursor_motion", launcher)
        self.assertIn("set_agent_cursor_theme", launcher)
        self.assertIn("get_cursor_position get_screen_size", launcher)
        self.assertIn("move_cursor list_apps", launcher)
        self.assertIn("list_windows bring_to_front", launcher)
        self.assertIn("launch_app kill_app", launcher)
        self.assertIn("click double_click right_click", launcher)
        self.assertRegex(launcher, r"\bzoom\b")
        for browser_tool in (
            "page",
            "browser_prepare",
            "get_browser_state",
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_pointer",
            "browser_dialog",
            "browser_set_input_files",
            "browser_download",
        ):
            self.assertRegex(launcher, rf"\b{browser_tool}\b")
        for recording_tool in (
            "start_recording",
            "stop_recording",
            "get_recording_state",
            "replay_trajectory",
            "install_ffmpeg",
        ):
            self.assertRegex(launcher, rf"\b{recording_tool}\b")
        self.assertRegex(launcher, r"\bcheck_for_update\b")
        self.assertIn('"$BIN" permissions grant', launcher)
        self.assertIn('default_daemon_was_running', launcher)
        self.assertIn("CUA_DRIVER_RS_TELEMETRY_ENABLED=0", launcher)
        self.assertIn("CUA_DRIVER_RS_UPDATE_CHECK=false", launcher)
        self.assertIn("export CUA_DRIVER_ENABLE_LEGACY_PAGE_MUTATIONS=1", launcher)
        self.assertIn("--env CUA_DRIVER_RS_UPDATE_CHECK=false", launcher)
        self.assertIn("--env CUA_DRIVER_RS_TELEMETRY_ENABLED=0", launcher)
        self.assertIn("--env CUA_DRIVER_ENABLE_LEGACY_PAGE_MUTATIONS=1", launcher)
        self.assertIn('driver_allows_legacy_page_mutations "$BIN" "$SOCKET"', launcher)
        self.assertIn('TELEMETRY_HOME="$DATA_DIR/cua-telemetry"', launcher)
        self.assertIn('export CUA_DRIVER_TELEMETRY_HOME="$TELEMETRY_HOME"', launcher)
        self.assertIn('--env "CUA_DRIVER_TELEMETRY_HOME=$TELEMETRY_HOME"', launcher)
        self.assertIn("Never change the user's ~/.cua-driver preference", launcher)
        self.assertIn('Refusing unsafe plugin telemetry directory', launcher)
        self.assertIn('chmod 700 "$TELEMETRY_HOME"', launcher)
        self.assertIn('"source"[[:space:]]*:[[:space:]]*"persisted"', launcher)
        self.assertNotIn("telemetry disable >/dev/null 2>&1 || true", launcher)
        self.assertIn("--no-permissions-gate", launcher)
        self.assertIn('SOCKET_DIR="/tmp/zcode-cua-${UID}"', launcher)
        common = (PLUGIN / "scripts" / "runtime-common.sh").read_text(encoding="utf-8")
        self.assertIn('permission mode: unrestricted', common)
        self.assertIn('user policy: configured=false, active=false, valid=true', common)
        self.assertIn('managed policy: configured=false, active=false, valid=true', common)
        self.assertIn('session policy: configured=false, approved_at_startup=false, valid=true', common)
        self.assertIn("driver_allows_legacy_page_mutations", common)
        self.assertIn("Missing required parameter: pid", common)
        self.assertIn("disabled by default", common)
        self.assertIn('driver_reports_unrestricted "$BIN" "$SOCKET"', launcher)
        self.assertIn("will not mislabel a policy-constrained daemon as full access", launcher)
        for variable in (
            "CUA_DRIVER_POLICY_FILE",
            "CUA_DRIVER_MANAGED_POLICY_FILE",
            "CUA_DRIVER_DISABLE_UNRESTRICTED",
            "CUA_DRIVER_SESSION_POLICY_FILE",
            "CUA_DRIVER_SESSION_POLICY_APPROVED",
        ):
            self.assertIn(f"-u {variable}", launcher)
        self.assertIn("/usr/bin/open -n -g \\", launcher)
        self.assertIn('"$APP_BUNDLE" --args', launcher)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("cua-policy-proof.sock", workflow)
        self.assertIn('test ! -e "$unrelated_home/.cua-driver"', workflow)
        self.assertIn('test ! -e "$unrelated_home/.cua-driver-rs"', workflow)
        self.assertIn('stat -f \'%Lp\' "$data/cua-telemetry"', workflow)
        self.assertIn('CUA_DRIVER_TELEMETRY_HOME="$data/cua-telemetry"', workflow)
        self.assertIn("permission mode: unrestricted", workflow)
        self.assertIn("user policy: configured=false, active=false, valid=true", workflow)
        self.assertIn("managed policy: configured=false, active=false, valid=true", workflow)
        self.assertIn("session policy: configured=false, approved_at_startup=false, valid=true", workflow)
        self.assertIn("--env CUA_DRIVER_ENABLE_LEGACY_PAGE_MUTATIONS=1", workflow)

    def test_manual_install_and_doctor_reuse_the_automatic_fallback_runtime(self):
        install = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
        doctor = (PLUGIN / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        for source in (install, doctor):
            self.assertIn('"$ROOT/scripts/run-mcp.sh" --self-test', source)
        self.assertIn('"$ROOT/scripts/run-cua-driver.sh" --grant-permissions', install)
        self.assertNotIn('python3 -m venv "$ROOT/.venv"', install)

    def test_macos_ci_verifies_the_pinned_primary_schemas(self):
        browser_verifier = (
            PLUGIN / "scripts" / "verify-cua-browser-schema.py"
        ).read_text(encoding="utf-8")
        native_verifier = (
            PLUGIN / "scripts" / "verify-cua-native-schema.py"
        ).read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('EXPECTED_VERSION = "0.13.1"', browser_verifier)
        self.assertIn('"page"', browser_verifier)
        self.assertIn('"execute_javascript"', browser_verifier)
        self.assertIn('"enable_javascript_apple_events"', browser_verifier)
        self.assertIn('"user_has_confirmed_enabling"', browser_verifier)
        self.assertIn('"get_browser_state"', browser_verifier)
        self.assertIn('"semantic_v2"', browser_verifier)
        self.assertIn('"browser_pointer"', browser_verifier)
        self.assertIn('"dom_event"', browser_verifier)
        self.assertIn('"browser_download"', browser_verifier)
        self.assertIn('"destination_root"', browser_verifier)
        self.assertIn('EXPECTED_VERSION = "0.13.1"', native_verifier)
        for tool in (
            "health_report",
            "check_permissions",
            "get_accessibility_tree",
            "get_window_state",
            "get_desktop_state",
            "get_screen_size",
            "get_cursor_position",
            "move_cursor",
            "click",
            "double_click",
            "right_click",
            "drag",
            "scroll",
            "type_text",
            "press_key",
            "hotkey",
            "set_value",
            "zoom",
            "list_apps",
            "list_windows",
            "kill_app",
            "get_config",
            "set_config",
            "launch_app",
            "bring_to_front",
            "start_session",
            "get_session_state",
            "escalate_session",
            "end_session",
            "get_agent_cursor_state",
            "set_agent_cursor_enabled",
            "set_agent_cursor_motion",
            "set_agent_cursor_theme",
            "start_recording",
            "stop_recording",
            "get_recording_state",
            "replay_trajectory",
            "install_ffmpeg",
            "check_for_update",
        ):
            self.assertIn(f'"{tool}"', native_verifier)
        self.assertIn(
            'python plugins/macos-computer-use/scripts/verify-cua-native-schema.py "$binary"',
            workflow,
        )
        self.assertIn(
            'python plugins/macos-computer-use/scripts/verify-cua-browser-schema.py "$binary"',
            workflow,
        )
        self.assertIn(
            'python plugins/macos-computer-use/scripts/verify-cua-runtime-discovery.py "$product_binary" "$socket"',
            workflow,
        )
        self.assertIn(
            'python plugins/macos-computer-use/scripts/verify-cua-mcp-runtime.py',
            workflow,
        )
        self.assertIn('--tcc-status-file "$tcc_status"', workflow)
        self.assertIn('--env "HOME=$unrelated_home"', workflow)
        self.assertIn('test ! -e "$unrelated_home/.cua-driver/config.json"', workflow)
        self.assertIn('value["accessibility"] and value["screen_recording"]', workflow)
        self.assertIn('plugins/macos-computer-use/scripts/live-smoke.sh', workflow)
        self.assertIn('--backend fallback || fallback_live_result=$?', workflow)
        self.assertIn('--backend primary || primary_live_result=$?', workflow)
        self.assertIn('stop --socket "/tmp/zcode-cua-${UID}/v0.13.1.sock"', workflow)
        self.assertIn('"$product_app" --args', workflow)
        self.assertIn("--no-permissions-gate", workflow)
        mcp_runtime = (
            PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn('client.request("tools/list")', mcp_runtime)
        self.assertIn('"protocolVersion": "2025-06-18"', mcp_runtime)
        self.assertIn('"serverInfo": {"name": "cua-driver", "version": "0.13.1"}', mcp_runtime)
        self.assertIn('"instructions": EXPECTED_MACOS_INSTRUCTIONS', mcp_runtime)
        self.assertIn('"initialize contract drifted:', mcp_runtime)
        self.assertIn("client.verify_protocol_errors()", mcp_runtime)
        self.assertIn('"code": -32700, "message": "Parse error"', mcp_runtime)
        self.assertIn('"code": -32601', mcp_runtime)
        self.assertIn('"code": -32602', mcp_runtime)
        self.assertIn('"Invalid params: missing tool name"', mcp_runtime)
        self.assertIn('self.notify("zcode/unknown-notification")', mcp_runtime)
        self.assertIn('"Signed CuaDriver TCC state: "', mcp_runtime)
        self.assertIn('"--tcc-status-file"', mcp_runtime)
        self.assertIn('dump_docs(binary)', mcp_runtime)
        self.assertIn('"capability_version", "schema_version"', mcp_runtime)
        self.assertIn('"tools/list.description drifted from dump-docs', mcp_runtime)
        self.assertIn('"tools/list annotation values drifted from dump-docs', mcp_runtime)
        self.assertIn('"tools/list.capabilities drifted', mcp_runtime)
        self.assertIn('"tools/list.risk drifted', mcp_runtime)
        self.assertIn('"check_for_update": {', mcp_runtime)
        self.assertIn('"install_ffmpeg": {', mcp_runtime)
        self.assertIn("if advertised_names != required:", mcp_runtime)
        self.assertIn('"tools/list surface drifted: "', mcp_runtime)
        self.assertIn('"tools/list returned duplicate tool names:', mcp_runtime)
        self.assertIn('mcp_schema = entry["inputSchema"]', mcp_runtime)
        self.assertIn('direct_schema = describe(binary, name)', mcp_runtime)
        self.assertIn('client.call("health_report")', mcp_runtime)
        self.assertIn('{"include": ["binary_version"]}', mcp_runtime)
        self.assertIn("require_health_report(", mcp_runtime)
        self.assertIn(
            'permissions_value, permissions_content = client.call(', mcp_runtime
        )
        self.assertIn('"check_permissions", {"prompt": False}', mcp_runtime)
        self.assertIn(
            'default_permissions_value, default_permissions_content = peer.call(',
            mcp_runtime,
        )
        self.assertIn("PERMISSION_SOURCE_NOTE = (", mcp_runtime)
        self.assertIn('"status": "refused"', mcp_runtime)
        self.assertIn('"message": prompt_message', mcp_runtime)
        self.assertIn('{"key": "capture_scope", "value": "desktop"}', mcp_runtime)
        self.assertIn(
            '{"key": "max_image_dimension", "value": config_original}',
            mcp_runtime,
        )
        self.assertIn("(session-scoped; persisted default unchanged)", mcp_runtime)
        self.assertIn('client.call("get_accessibility_tree")', mcp_runtime)
        self.assertIn("def require_accessibility_discovery(", mcp_runtime)
        self.assertIn('peer.call(\n            "get_accessibility_tree"', mcp_runtime)
        self.assertIn("returned duplicate app pid", mcp_runtime)
        self.assertIn("returned duplicate window id", mcp_runtime)
        self.assertIn(
            "→ Call get_window_state(pid, window_id) to inspect a window's UI.",
            mcp_runtime,
        )
        self.assertIn("APP_ENTRY_FIELDS = {", mcp_runtime)
        self.assertIn("WINDOW_ENTRY_FIELDS = {", mcp_runtime)
        self.assertIn("LAUNCH_WINDOW_FIELDS = {", mcp_runtime)
        self.assertIn("def require_screen_geometry(", mcp_runtime)
        self.assertIn("def require_cursor_position(", mcp_runtime)
        self.assertIn("installed-not-running.", mcp_runtime)
        self.assertIn('f"Found {len(inventory[\'windows\'])} window(s)."', mcp_runtime)
        self.assertIn("✅ Main display:", mcp_runtime)
        self.assertIn("✅ Cursor at", mcp_runtime)
        self.assertIn('app["running"] is not (app["pid"] > 0)', mcp_runtime)
        self.assertIn('inventory["current_space_id"] is not None', mcp_runtime)
        self.assertIn("require_launch_result(", mcp_runtime)
        self.assertIn("fresh_window_ids", mcp_runtime)
        self.assertIn("def call_error(", mcp_runtime)
        self.assertIn('"error": "APP_NOT_INSTALLED"', mcp_runtime)
        self.assertIn('"error": "FILE_NOT_FOUND"', mcp_runtime)
        self.assertIn('"post-error list_apps"', mcp_runtime)
        self.assertIn('"kill_app success payload drifted:', mcp_runtime)
        self.assertIn("def require_session_state(", mcp_runtime)
        self.assertIn('"code": "session_policy_conflict"', mcp_runtime)
        self.assertIn('"code": "desktop_already_active"', mcp_runtime)
        self.assertIn("def require_ended_session_guard(", mcp_runtime)
        self.assertIn('result != {"exit_code": 1}', mcp_runtime)
        self.assertIn("def require_ended_session(", mcp_runtime)
        self.assertIn("revived=True", mcp_runtime)
        self.assertIn('client.call("get_recording_state")', mcp_runtime)
        self.assertIn('"record_video": False', mcp_runtime)
        self.assertIn('client.call("stop_recording")', mcp_runtime)
        self.assertIn('"zcode-primary-recorder-"', mcp_runtime)
        self.assertIn('"zcode-primary-recorder-takeover-"', mcp_runtime)
        self.assertIn("did not take over daemon-global ownership", mcp_runtime)
        self.assertIn("manual non-owner stop_recording", mcp_runtime)
        self.assertIn('"✅ Recording stopped."', mcp_runtime)
        self.assertIn('manifest["schema_version"] != 1', mcp_runtime)
        self.assertIn('"final recording session.json"', mcp_runtime)
        self.assertIn('"recording.mp4"', mcp_runtime)
        self.assertIn('client.call("page", {"action": "execute_javascript"})', mcp_runtime)
        self.assertIn('"legacy page mutations remained constrained', mcp_runtime)
        self.assertIn('"os_permission_prompt_requires_trusted_host"', mcp_runtime)
        self.assertIn('"start_session", {"session": session, "capture_scope": "auto"}', mcp_runtime)
        self.assertIn('"get_session_state", {"session": session}', mcp_runtime)
        self.assertIn('peer.call("get_config")', mcp_runtime)
        self.assertIn('"set_config", {"max_image_dimension": config_probe}', mcp_runtime)
        self.assertIn('("com.apple.calculator", "Calculator")', mcp_runtime)
        self.assertIn('("com.apple.TextEdit", "TextEdit")', mcp_runtime)
        self.assertIn('command.endswith(expected_suffix)', mcp_runtime)
        self.assertIn('killed_app = client.request(', mcp_runtime)
        self.assertIn('"arguments": {"pid": launched_pid}', mcp_runtime)
        self.assertIn('inventory_exit_deadline = time.monotonic() + 10', mcp_runtime)
        self.assertIn('"set_agent_cursor_enabled", {"session": session, "enabled": False}', mcp_runtime)
        self.assertIn("CURSOR_MOTION_FIELDS = {", mcp_runtime)
        self.assertIn('"glide_duration_ms": 0.0', mcp_runtime)
        self.assertIn('"idle_hide_ms": 20_000.0', mcp_runtime)
        self.assertIn('"id": "cua.default"', mcp_runtime)
        self.assertIn('client.call("set_agent_cursor_motion", {"session": session})', mcp_runtime)
        self.assertIn('"set_agent_cursor_theme",', mcp_runtime)
        self.assertIn('"restored get_agent_cursor_state"', mcp_runtime)
        self.assertIn('"reason": "no_window_target"', mcp_runtime)
        self.assertIn('client.call("end_session", {"session": session})', mcp_runtime)
        self.assertIn('"name": "kill_app"', mcp_runtime)
        self.assertIn('["/bin/sleep", "60"]', mcp_runtime)

        skill = (PLUGIN / "skills" / "macos-computer-use" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("installed-but-stopped apps", skill)
        self.assertIn("per-app `windows` array is deliberately empty", skill)
        self.assertIn("never treat pid 0 as an action target", skill)

    def test_primary_live_identity_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_identity_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        app = {
            "pid": 321,
            "name": "Calculator",
            "bundle_id": "com.apple.calculator",
            "active": False,
            "running": True,
            "launch_path": "/System/Applications/Calculator.app",
            "kind": "desktop",
            "last_used": None,
            "windows": [],
        }
        self.assertEqual(
            module.require_app_inventory(
                "apps",
                {"apps": [app]},
                [
                    {
                        "type": "text",
                        "text": (
                            "✅ Found 1 app(s): 1 running, 0 installed-not-running.\n"
                            "- Calculator (pid 321) [com.apple.calculator]"
                        ),
                    }
                ],
            )["apps"][0],
            app,
        )
        stopped = dict(app, pid=0, running=False)
        module.require_app_inventory("stopped apps", {"apps": [stopped]})
        with self.assertRaisesRegex(RuntimeError, "running/pid fields disagreed"):
            module.require_app_inventory(
                "drifted apps", {"apps": [dict(app, pid=0)]}
            )

        bounds = {"x": 12.0, "y": 34.0, "width": 640.0, "height": 480.0}
        window = {
            "window_id": 456,
            "pid": 321,
            "app_name": "Calculator",
            "title": "Calculator",
            "bounds": bounds,
            "layer": 0,
            "z_index": 3,
            "is_on_screen": True,
            "on_current_space": True,
            "space_ids": [1],
        }
        module.require_window_inventory(
            "windows",
            {"windows": [window], "current_space_id": None},
            [{"type": "text", "text": "Found 1 window(s)."}],
        )
        with self.assertRaisesRegex(RuntimeError, "current_space_id drifted"):
            module.require_window_inventory(
                "drifted windows", {"windows": [window], "current_space_id": 1}
            )

        launch_window = {
            key: value
            for key, value in window.items()
            if key in module.LAUNCH_WINDOW_FIELDS
        }
        launched = {
            "pid": 321,
            "bundle_id": "com.apple.calculator",
            "name": "Calculator",
            "windows": [launch_window],
            "self_activation_suppressed": True,
        }
        module.require_launch_result(
            "launch",
            launched,
            [
                {
                    "type": "text",
                    "text": (
                        "Launched Calculator (pid 321) in background.\n\n"
                        "Windows:\n"
                        '- "Calculator" [window_id: 456]\n'
                        "→ Call get_window_state(pid: 321, window_id) to inspect."
                    ),
                }
            ],
            "com.apple.calculator",
            "Calculator",
        )
        with self.assertRaisesRegex(RuntimeError, "window owned by another pid"):
            module.require_launch_result(
                "drifted launch",
                dict(launched, windows=[dict(launch_window, pid=999)]),
                [{"type": "text", "text": "unused"}],
                "com.apple.calculator",
                "Calculator",
            )
        with self.assertRaisesRegex(RuntimeError, "text content drifted"):
            module.require_launch_result(
                "drifted launch text",
                launched,
                [{"type": "text", "text": "wrong"}],
                "com.apple.calculator",
                "Calculator",
            )

        screen = {"width": 1512, "height": 982, "scale_factor": 2.0}
        self.assertEqual(
            module.require_screen_geometry(
                "screen",
                screen,
                [
                    {
                        "type": "text",
                        "text": "✅ Main display: 1512x982 points @ 2x",
                    }
                ],
            ),
            screen,
        )
        cursor = {"x": -120, "y": 45}
        self.assertEqual(
            module.require_cursor_position(
                "cursor",
                cursor,
                [{"type": "text", "text": "✅ Cursor at (-120, 45)"}],
            ),
            cursor,
        )
        with self.assertRaisesRegex(RuntimeError, "main-display geometry"):
            module.require_screen_geometry(
                "drifted screen",
                dict(screen, scale_factor=2),
                [],
            )
        with self.assertRaisesRegex(RuntimeError, "text content drifted"):
            module.require_cursor_position(
                "drifted cursor",
                cursor,
                [{"type": "text", "text": "wrong"}],
            )

    def test_primary_tool_errors_preserve_mcp_business_payload(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_tool_error_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        expected_structured = {
            "error": "APP_NOT_INSTALLED",
            "bundle_id": "com.example.missing",
        }
        expected_content = [{"type": "text", "text": "missing app"}]
        client = object.__new__(module.MCPClient)
        client.request = lambda method, params: {
            "content": expected_content,
            "isError": True,
            "structuredContent": expected_structured,
        }
        self.assertEqual(
            client.call_error("launch_app", {"bundle_id": "com.example.missing"}),
            (expected_structured, expected_content),
        )
        module.require_text_content("error", expected_content, "missing app")

        client.request = lambda method, params: {
            "content": expected_content,
            "isError": True,
        }
        with self.assertRaisesRegex(RuntimeError, "tool-error envelope drifted"):
            client.call_error("launch_app", {"bundle_id": "com.example.missing"})

    def test_primary_health_report_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_health_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        checks = [
            {
                "name": "binary_version",
                "status": "pass",
                "message": "cua-driver 0.13.1",
            },
            {
                "name": "platform_supported",
                "status": "pass",
                "message": "macOS 15.5 (arm64)",
                "data": {"os_version": "15.5", "architecture": "arm64"},
            },
            {
                "name": "session_active",
                "status": "pass",
                "message": "MCP session is active.",
            },
            {
                "name": "bundle_identity",
                "status": "pass",
                "message": "Bundle is com.trycua.driver.",
                "data": {
                    "bundle_identifier": "com.trycua.driver",
                    "executable_path": "/tmp/CuaDriver.app/Contents/MacOS/cua-driver",
                },
            },
            {
                "name": "tcc_accessibility",
                "status": "fail",
                "message": "Accessibility is NOT granted for this process.",
                "hint": "Grant Accessibility in System Settings.",
                "data": {"bundle_identifier": "com.trycua.driver"},
            },
            {
                "name": "tcc_screen_recording",
                "status": "fail",
                "message": "Screen Recording is NOT granted for this process.",
                "hint": "Grant Screen Recording in System Settings.",
                "data": {"bundle_identifier": "com.trycua.driver"},
            },
            {
                "name": "ax_capability",
                "status": "fail",
                "message": "AX is not trusted; UI inspection and event posting will fail.",
                "hint": "Resolve tcc_accessibility first.",
            },
            {
                "name": "screen_capture_capability",
                "status": "skip",
                "message": module.HEALTH_CAPTURE_SKIP_MESSAGE,
            },
        ]
        report = {
            "schema_version": "1",
            "platform": "darwin",
            "driver_version": "0.13.1",
            "overall": "degraded",
            "checks": checks,
        }
        validated = module.require_health_report("health", report)
        self.assertEqual(tuple(validated), module.HEALTH_CHECK_NAMES)

        filtered_checks = [checks[0]] + [
            {
                "name": check_name,
                "status": "skip",
                "message": module.HEALTH_FILTER_SKIP_MESSAGE,
            }
            for check_name in module.HEALTH_CHECK_NAMES[1:]
        ]
        module.require_health_report(
            "filtered health",
            dict(report, overall="ok", checks=filtered_checks),
            binary_only=True,
        )

        with self.assertRaisesRegex(RuntimeError, "check order drifted"):
            module.require_health_report(
                "reordered health",
                dict(report, checks=[checks[1], checks[0], *checks[2:]]),
            )
        with self.assertRaisesRegex(RuntimeError, "without a remediation hint"):
            drifted_checks = [dict(entry) for entry in checks]
            drifted_checks[4].pop("hint")
            module.require_health_report(
                "missing hint health", dict(report, checks=drifted_checks)
            )
        with self.assertRaisesRegex(RuntimeError, "overall disagreed"):
            module.require_health_report(
                "wrong overall health", dict(report, overall="ok")
            )

    def test_primary_permission_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_permission_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        permissions = {
            "accessibility": False,
            "screen_recording": False,
            "screen_recording_capturable": None,
            "direct_capture_status": "not_checked",
            "source": {
                "attribution": "driver-daemon",
                "pid": 777,
                "responsible_ppid": 1,
                "executable": "/tmp/CuaDriver.app/Contents/MacOS/cua-driver",
                "disclaim_env": False,
                "bundle_id": "com.trycua.driver",
                "note": module.PERMISSION_SOURCE_NOTE,
            },
        }
        content = [
            {
                "type": "text",
                "text": (
                    "❌ Accessibility: NOT granted.\n"
                    "❌ Screen Recording: NOT granted.\n"
                    f"{module.PERMISSION_READ_ONLY_NOTE}"
                ),
            }
        ]
        self.assertEqual(
            module.require_permissions("permissions", permissions, content),
            permissions,
        )
        with self.assertRaisesRegex(RuntimeError, "source.*fields drifted"):
            drifted_source = dict(permissions["source"])
            drifted_source.pop("note")
            module.require_permissions(
                "drifted permissions",
                dict(permissions, source=drifted_source),
                content,
            )
        with self.assertRaisesRegex(RuntimeError, "text content drifted"):
            module.require_permissions(
                "drifted text", permissions, [{"type": "text", "text": "wrong"}]
            )

    def test_primary_config_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_config_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        config = {
            "version": "0.13.1",
            "source_sha": None,
            "platform": "macos",
            "max_image_dimension": 1568,
            "agent_cursor": {"enabled": True},
            "experimental_pip": False,
            "experimental_pip_geometry": None,
        }
        self.assertEqual(module.require_config("config", config), config)
        self.assertEqual(
            module.require_set_config(
                "set config",
                {
                    "version": "0.13.1",
                    "platform": "macos",
                    "max_image_dimension": 0,
                },
                0,
            )["max_image_dimension"],
            0,
        )
        with self.assertRaisesRegex(RuntimeError, "runtime identity"):
            module.require_config(
                "drifted config",
                dict(config, agent_cursor={"enabled": True, "theme": "extra"}),
            )
        with self.assertRaisesRegex(RuntimeError, "response drifted"):
            module.require_set_config(
                "drifted set config",
                {
                    "version": "0.13.1",
                    "platform": "macos",
                    "max_image_dimension": 1,
                },
                0,
            )

    def test_primary_accessibility_discovery_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_accessibility_discovery_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        discovery = {
            "apps": [
                {"pid": 101, "name": "Finder", "bundle_id": "com.apple.finder"},
                {"pid": 202, "name": "Fixture", "bundle_id": None},
            ],
            "windows": [
                {
                    "window_id": 303,
                    "pid": 101,
                    "app_name": "Finder",
                    "title": "Desktop",
                },
                {
                    "window_id": 404,
                    "pid": 202,
                    "app_name": "Fixture",
                    "title": "",
                },
            ],
        }
        content = [
            {
                "type": "text",
                "text": (
                    "2 running app(s), 2 visible window(s)\n"
                    "- Finder (pid 101) [com.apple.finder]\n"
                    "- Fixture (pid 202)\n\n"
                    "Windows:\n"
                    '- Finder (pid 101) "Desktop" [window_id: 303]\n'
                    "- Fixture (pid 202) (no title) [window_id: 404]\n"
                    "→ Call get_window_state(pid, window_id) to inspect a window's UI."
                ),
            }
        ]
        self.assertEqual(
            module.require_accessibility_discovery(
                "discovery", discovery, content
            ),
            discovery,
        )

        duplicate_app = dict(discovery)
        duplicate_app["apps"] = [*discovery["apps"], dict(discovery["apps"][0])]
        with self.assertRaisesRegex(RuntimeError, "duplicate app pid"):
            module.require_accessibility_discovery(
                "duplicate app", duplicate_app, content
            )

        duplicate_window = dict(discovery)
        duplicate_window["windows"] = [
            *discovery["windows"],
            dict(discovery["windows"][0]),
        ]
        with self.assertRaisesRegex(RuntimeError, "duplicate window id"):
            module.require_accessibility_discovery(
                "duplicate window", duplicate_window, content
            )

        with self.assertRaisesRegex(RuntimeError, "text content drifted"):
            module.require_accessibility_discovery(
                "drifted discovery",
                discovery,
                [{"type": "text", "text": "wrong"}],
            )

    def test_primary_cursor_state_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_cursor_state_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        state = {
            "session": "cursor-contract",
            "enabled": True,
            "position": None,
            "theme": dict(module.DEFAULT_CURSOR_THEME),
            "visual_state": {
                "requested_action": "idle",
                "resolved_action": "idle",
                "modifiers": [],
                "phase": "loop",
                "frame": 9,
                "preempted_count": 0,
            },
            "motion": dict(module.DEFAULT_CURSOR_MOTION),
        }
        self.assertEqual(
            module.require_cursor_state(
                "cursor", state, "cursor-contract", True
            ),
            state,
        )
        with self.assertRaisesRegex(RuntimeError, "human-like defaults"):
            module.require_cursor_state(
                "drifted cursor",
                dict(state, motion=dict(state["motion"], spring=0.5)),
                "cursor-contract",
                True,
            )
        with self.assertRaisesRegex(RuntimeError, "dynamic visual state"):
            module.require_cursor_state(
                "drifted visual",
                dict(
                    state,
                    visual_state=dict(
                        state["visual_state"], requested_action="invented"
                    ),
                ),
                "cursor-contract",
                True,
            )

    def test_primary_recording_state_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_recording_state_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        disabled = {
            "recording": False,
            "enabled": False,
            "output_dir": None,
            "next_turn": 1,
            "last_error": None,
            "video_active": False,
            "last_video_path": None,
            "owner": None,
        }
        self.assertEqual(
            module.require_recording_state(
                "disabled recording",
                disabled,
                [{"type": "text", "text": "✅ recording: disabled"}],
            ),
            disabled,
        )
        enabled = dict(
            disabled,
            recording=True,
            enabled=True,
            output_dir="/tmp/zcode-recording",
            owner="connection-a",
        )
        self.assertEqual(
            module.require_recording_state(
                "enabled recording",
                enabled,
                [
                    {
                        "type": "text",
                        "text": (
                            "✅ recording: enabled "
                            "output_dir=/tmp/zcode-recording next_turn=1"
                        ),
                    }
                ],
            ),
            enabled,
        )
        with self.assertRaisesRegex(RuntimeError, "recording disagreed"):
            module.require_recording_state(
                "drifted recording", dict(enabled, recording=False)
            )
        with self.assertRaisesRegex(RuntimeError, "text content drifted"):
            module.require_recording_state(
                "drifted recording text",
                enabled,
                [{"type": "text", "text": "wrong"}],
            )

    def test_primary_session_lifecycle_validators_reject_drift(self):
        path = PLUGIN / "scripts" / "verify-cua-mcp-runtime.py"
        spec = importlib.util.spec_from_file_location(
            "zcode_primary_session_lifecycle_contract", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        session = "session-contract"
        state = {
            "session": session,
            "capture_scope": "auto",
            "effective_scope": "window",
            "desktop_unlocked": False,
            "escalation_reason": None,
            "escalation_detail": None,
        }
        state_content = [
            {
                "type": "text",
                "text": (
                    f"Session '{session}' uses capture_scope='auto' "
                    "(effective_scope='window')."
                ),
            }
        ]
        self.assertEqual(
            module.require_session_state(
                "session",
                state,
                state_content,
                session=session,
                capture_scope="auto",
                effective_scope="window",
                desktop_unlocked=False,
            ),
            state,
        )
        started = {**state, "active": True, "revived": False}
        self.assertEqual(
            module.require_started_session(
                "started session",
                started,
                [
                    {
                        "type": "text",
                        "text": (
                            f"✅ Session '{session}' is active with "
                            "capture_scope='auto'."
                        ),
                    }
                ],
                session=session,
                capture_scope="auto",
                effective_scope="window",
                desktop_unlocked=False,
                revived=False,
            ),
            started,
        )
        self.assertEqual(
            module.require_ended_session(
                "ended session",
                {"session": session, "active": False},
                [{"type": "text", "text": f"✅ Session '{session}' ended."}],
                session,
            ),
            {"session": session, "active": False},
        )
        guard = {"exit_code": 1}
        guard_content = [
            {
                "type": "text",
                "text": (
                    f"session '{session}' has ended; tool call "
                    "'get_session_state' was rejected. Call start_session with "
                    "this id to revive it before issuing further actions, or "
                    "use a new session id."
                ),
            }
        ]
        self.assertEqual(
            module.require_ended_session_guard(
                "ended guard",
                guard,
                guard_content,
                session=session,
                tool_name="get_session_state",
            ),
            guard,
        )
        with self.assertRaisesRegex(RuntimeError, "session state drifted"):
            module.require_session_state(
                "drifted session",
                dict(state, effective_scope="desktop"),
                state_content,
                session=session,
                capture_scope="auto",
                effective_scope="window",
                desktop_unlocked=False,
            )
        with self.assertRaisesRegex(RuntimeError, "text content drifted"):
            module.require_started_session(
                "drifted session text",
                started,
                [{"type": "text", "text": "wrong"}],
                session=session,
                capture_scope="auto",
                effective_scope="window",
                desktop_unlocked=False,
                revived=False,
            )
        with self.assertRaisesRegex(RuntimeError, "ended-session guard drifted"):
            module.require_ended_session_guard(
                "drifted ended guard",
                {"exit_code": 2},
                guard_content,
                session=session,
                tool_name="get_session_state",
            )

    def test_every_required_primary_tool_has_one_pinned_schema(self):
        launcher = (PLUGIN / "scripts" / "run-cua-driver.sh").read_text(
            encoding="utf-8"
        )
        match = re.search(r"for required in \\\n(?P<body>.*?)\; do", launcher, re.DOTALL)
        self.assertIsNotNone(match)
        required = set(re.findall(r"[a-z][a-z0-9_]+", match.group("body")))

        contract_sets = []
        for filename in (
            "verify-cua-native-schema.py",
            "verify-cua-browser-schema.py",
        ):
            path = PLUGIN / "scripts" / filename
            spec = importlib.util.spec_from_file_location(filename, path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            contract_sets.append(set(module.CONTRACTS))

        native, browser = contract_sets
        self.assertTrue(native.isdisjoint(browser))
        self.assertEqual(required, native | browser)
        self.assertEqual(len(required), 49)

    def test_skill_docs_route_every_required_primary_tool(self):
        required = set()
        for filename in (
            "verify-cua-native-schema.py",
            "verify-cua-browser-schema.py",
        ):
            path = PLUGIN / "scripts" / filename
            spec = importlib.util.spec_from_file_location(filename, path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            required.update(module.CONTRACTS)

        skill_root = PLUGIN / "skills" / "macos-computer-use"
        docs = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(skill_root.rglob("*.md"))
        )
        missing = sorted(
            name
            for name in required
            if re.search(rf"`{re.escape(name)}(?:`|\()", docs) is None
        )
        self.assertEqual(missing, [], f"primary tools missing from Skill docs: {missing}")
        self.assertEqual(len(required), 49)

    def test_live_smoke_reuses_the_automatic_fallback_runtime(self):
        smoke = (PLUGIN / "scripts" / "live-smoke.sh").read_text(encoding="utf-8")
        self.assertIn('DATA_PYTHON="$DATA_DIR/venv-$MACOS_CUA_DEPENDENCY_ID/bin/python3"', smoke)
        self.assertIn('"$ROOT/scripts/run-mcp.sh" --self-test', smoke)
        self.assertIn('macos_cua_native_runtime_ready "$DATA_PYTHON" "$ROOT"', smoke)
        self.assertIn('"$ROOT/scripts/live-smoke.py" "$@"', smoke)
        self.assertNotIn("source-checkout runtime is not installed", smoke)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "requires a Unix bash runtime")
    def test_runtime_lock_recovers_a_dead_owner(self):
        common = PLUGIN / "scripts" / "runtime-common.sh"
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "runtime.lock"
            lock.mkdir()
            (lock / "pid").write_text("999999999\n", encoding="ascii")
            script = (
                f'source "{common}"\n'
                f'acquire_runtime_lock "{lock}" "test runtime" 2 0\n'
                f'test "$(cat "{lock}/pid")" = "$$"\n'
                f'release_runtime_lock "{lock}"\n'
                f'test ! -e "{lock}"\n'
            )
            completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "requires a Unix bash runtime")
    def test_runtime_lock_rejects_an_incomplete_fresh_owner_and_recovers_pid_reuse(self):
        common = PLUGIN / "scripts" / "runtime-common.sh"
        with tempfile.TemporaryDirectory() as directory:
            fresh = Path(directory) / "fresh.lock"
            reused = Path(directory) / "reused.lock"
            script = (
                f'source "{common}"\n'
                f'mkdir "{fresh}"\n'
                f'! acquire_runtime_lock "{fresh}" "fresh incomplete test" 0 30\n'
                f'test -d "{fresh}"\n'
                f'rmdir "{fresh}"\n'
                f'mkdir "{reused}"\n'
                f'printf "%s\\n" "$$" > "{reused}/pid"\n'
                f'printf "%s\\n" "impossible-old-start" > "{reused}/started"\n'
                f'printf "%s\\n" "old-token" > "{reused}/token"\n'
                f'acquire_runtime_lock "{reused}" "pid reuse test" 2 0\n'
                f'test "$(cat "{reused}/pid")" = "$$"\n'
                f'test "$(cat "{reused}/token")" = "$RUNTIME_LOCK_TOKEN"\n'
                f'release_runtime_lock "{reused}"\n'
                f'test ! -e "{reused}"\n'
            )
            completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "requires a Unix bash runtime")
    def test_daemon_mode_probe_rejects_standard_and_accepts_unrestricted(self):
        common = PLUGIN / "scripts" / "runtime-common.sh"
        script = (
            f'source "{common}"\n'
            'standard_driver() { printf "%s\\n" "Cua Driver daemon is running" "  permission mode: standard (default)"; }\n'
            'restricted_driver() { printf "%s\\n" "  permission mode: unrestricted (environment)" '
            '"  user policy: configured=true, active=true, valid=true" '
            '"  managed policy: configured=false, active=false, valid=true" '
            '"  session policy: configured=false, approved_at_startup=false, valid=true"; }\n'
            'unrestricted_driver() { printf "%s\\n" "Cua Driver daemon is running" '
            '"  permission mode: unrestricted (environment)" '
            '"  user policy: configured=false, active=false, valid=true" '
            '"  managed policy: configured=false, active=false, valid=true" '
            '"  session policy: configured=false, approved_at_startup=false, valid=true"; }\n'
            '! driver_reports_unrestricted standard_driver /tmp/standard.sock\n'
            '! driver_reports_unrestricted restricted_driver /tmp/restricted.sock\n'
            'driver_reports_unrestricted unrestricted_driver /tmp/unrestricted.sock\n'
            'page_disabled_driver() { printf "%s\\n" "legacy page mutation is disabled by default" >&2; return 1; }\n'
            'page_enabled_driver() { printf "%s\\n" "Missing required parameter: pid" >&2; return 1; }\n'
            '! driver_allows_legacy_page_mutations page_disabled_driver /tmp/page-disabled.sock\n'
            'driver_allows_legacy_page_mutations page_enabled_driver /tmp/page-enabled.sock\n'
        )
        completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_skill_routes_primary_then_direct_fallback(self):
        skill = (PLUGIN / "skills" / "macos-computer-use" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        self.assertIn("## Completion evidence", skill)
        self.assertIn("Do not report success from an action response alone", skill)
        self.assertNotIn("let the user\n  with", skill)
        self.assertIn("macos-computer-use-fallback", skill)
        self.assertLess(skill.index("Background AX"), skill.index("Direct fallback"))
        self.assertIn("end_session", skill)
        self.assertLess(skill.index("check_permissions({prompt:false})"), skill.index("start_session"))
        self.assertLess(skill.index("health_report({})"), skill.index("check_permissions({prompt:false})"))
        self.assertIn('`schema_version:"1"`, `platform:"darwin"`', skill)
        self.assertIn("health call never grants access", skill)
        self.assertNotIn("check_permissions({prompt:true", skill)
        self.assertIn("Public MCP calls are status-only", skill)
        self.assertIn("permissions grant", skill)
        self.assertIn('escalate_session({session,reason:"no_window_target",detail:"menu bar required"})', skill)
        self.assertIn("keep optional detail bounded and free of", skill)
        self.assertIn("element_token", skill)
        self.assertIn("replace:true", skill)
        self.assertIn("## Keep the session cursor human-visible", skill)
        self.assertIn("Keep it at most 28 visible characters", skill)
        self.assertIn("never\n   put secrets or full user content in the label", skill)
        self.assertIn("local badge shows the public session label", skill)
        self.assertIn("set_agent_cursor_enabled({session,enabled:false})", skill)
        self.assertIn("get_agent_cursor_state({session})", skill)
        self.assertIn("`set_agent_cursor_motion` with only that session", skill)
        self.assertIn("Motion tuning affects only the semantic overlay", skill)
        self.assertIn("Do not add random target jitter", skill)
        self.assertIn("`set_agent_cursor_theme({session,theme_id,reduced_motion})`", skill)
        self.assertIn('`reduced_motion` is `"auto"`, `"on"`, or\n`"off"`', skill)
        self.assertIn("Never\npass a file path, URL, source artwork, or inline animation", skill)
        self.assertIn("`zoom({pid,window_id,x1,y1,x2,y2})`", skill)
        self.assertIn("one replaceable\ncoordinate context per pid", skill)
        self.assertIn("`from_zoom:true`", skill)
        self.assertIn('move_cursor({session,x,y,scope:"window"})', skill)
        self.assertIn('`scope:"desktop"` is a different operation', skill)
        self.assertIn("moves the user's\nreal OS pointer", skill)
        self.assertIn("creates_new_application_instance:true", skill)
        self.assertIn("`launch_app({bundle_id,urls:[target]})`", skill)
        self.assertIn("structured `FILE_NOT_FOUND`", skill)
        self.assertIn("`self_activation_suppressed`", skill)
        self.assertIn("`get_config({})`", skill)
        self.assertIn("`set_config({max_image_dimension:0})`", skill)
        self.assertIn("`get_window_state({pid,window_id})` snapshot without", skill)
        self.assertIn("`cursor_theme:{theme_id,reduced_motion}`", skill)
        self.assertIn("`get_accessibility_tree` for a fast broad inventory", skill)
        self.assertIn("An anonymous CLI/direct\n`set_config` instead changes", skill)
        self.assertIn("Preserve pre-existing pids before an isolated launch", skill)
        self.assertIn("`kill_app({pid})` only for that exact still-live pid", skill)
        self.assertIn("`bring_to_front({pid,window_id})` is an explicit persistent activation", skill)
        self.assertIn("The call has no `session` field", skill)
        self.assertIn("same pid to be `active:true` in a fresh `list_apps`", skill)
        self.assertIn("brief front -> act -> restore route", skill)
        self.assertIn('element_token,action:"pick"', skill)
        self.assertIn("`press`\n(default), `show_menu`, `pick`, `confirm`, `cancel`, and `open`", skill)
        self.assertIn("Never\nreuse the pre-open menu token", skill)
        self.assertIn("[browser-workflow.md](references/browser-workflow.md)", skill)
        self.assertIn("[recording-workflow.md](references/recording-workflow.md)", skill)
        self.assertIn("without moving the real OS pointer", (
            PLUGIN / "skills" / "macos-computer-use" / "references" / "tool-api.md"
        ).read_text(encoding="utf-8"))
        self.assertIn('scope:"desktop"', skill)
        self.assertIn("fallback is\nsessionless", skill)
        self.assertIn("screenshot as the final truth", skill)
        self.assertIn("global menu bar belongs to the frontmost app", skill)
        self.assertIn("Never send selection keys\n  from the pre-open observation", skill)
        self.assertIn("fallback `type_text` has no `element_index`", skill)
        self.assertIn("accessibility:true,screen_recording:false", skill)
        self.assertIn("preserve the whole\nwindow object, including `pid`", skill)
        self.assertIn("only when exactly one candidate remains", skill)
        self.assertIn("Input timeout means outcome unknown", skill)
        self.assertIn("Request only the signal needed", skill)
        self.assertIn("primary `get_window_state` has no `include_text` field", skill)
        self.assertIn("accepted `capture_mode` is a compatibility-only no-op", skill)
        self.assertIn("direct fallback, the different `get_window_state` schema", skill)
        self.assertIn("`tree_markdown` plus `element_count`", skill)
        self.assertIn("`screenshot_width`, `screenshot_height`, and\n`screenshot_mime_type`", skill)
        self.assertIn("`window_bounds`, `screenshot_scale`, and `screenshot_frame_valid:true`", skill)
        self.assertIn("task-owned `screenshot_out_file`\nwrites the image locally", skill)
        self.assertIn("and returns\n`screenshot_file_path`", skill)
        self.assertNotIn("use `include_screenshot:true,include_text:false`", skill)
        self.assertIn("always pass its fresh\n`screenshotId`", skill)
        self.assertIn("omitting it changes the coordinate space", skill)
        self.assertIn('`effect:"confirmed"` with `verified:true`', skill)
        self.assertIn('`effect:"unverifiable"` with `verified:false`', skill)
        self.assertIn('`effect:"suspected_noop"`', skill)
        self.assertIn('`effect:"partial"` with `code:"type_text_incomplete"`', skill)
        self.assertIn("retry only the remaining suffix", skill)
        self.assertIn('`escalation.recommended:"px"`', skill)
        self.assertIn('`escalation.recommended:"foreground"`', skill)
        self.assertLess(skill.index('effect:"suspected_noop"'), skill.index("Background pixel"))
        self.assertIn("`type_text({session,pid,window_id,x,y,text})`", skill)
        self.assertIn("primary driver also has a mutually exclusive pixel form", skill)
        self.assertIn('reports `code:"background_unavailable"`', skill)
        self.assertIn('same fresh drag once with `delivery_mode:"foreground"`', skill)
        self.assertIn("Keep window-scoped shortcuts bound to the returned pid/window", skill)
        self.assertIn("Re-observe after a shortcut or single key", skill)
        self.assertIn('require its `effect:"confirmed"`/`verified:true`', skill)
        self.assertIn("Fallback text can also return an MCP error", skill)
        self.assertIn("never the original full text", skill)
        self.assertIn('`has_screenshot:false`', skill)
        self.assertIn("Sparse Chromium AX tree", skill)
        self.assertIn("minimized window beeps or ignores Return/Space/Tab", skill)
        self.assertIn("AXManualAccessibility", skill)
        self.assertIn("`AXContents`", skill)
        self.assertIn("`max_tree_nodes` (up to 10,000)", skill)

    def test_typed_browser_reference_is_exact_and_unrestricted(self):
        reference_path = (
            PLUGIN
            / "skills"
            / "macos-computer-use"
            / "references"
            / "browser-workflow.md"
        )
        reference = reference_path.read_text(encoding="utf-8")
        for marker in (
            'binding_quality:"exact"',
            'mutation_allowed:true',
            'snapshot_format:"semantic_v2"',
            "pixel_to_css_scale_x",
            "pixel_to_css_scale_y",
            "browser_ref_stale",
            "browser_requires_setup",
            "browser_consent_required",
            "browser_binding_ambiguous",
            "browser_action_unavailable",
            "browser_input_trust_unavailable",
            'input_route:"dom_event"',
            "replace:true",
            "browser_set_input_files",
            "browser_download",
            "same session-scoped\nsemantic cursor overlay",
            "An unselected tab remains\naddressable, but its cursor stays hidden",
            "child-frame\npoint cannot be mapped safely",
            "intentionally creates no cursor motion",
            "does not move the physical pointer",
            "following fresh browser snapshot as completion evidence",
            "adds no approval prompt",
            "adds no second prompt or allowlist",
            "do not forge private approval fields",
        ):
            self.assertIn(marker, reference)
        for prohibited in (
            "ask the user for confirmation",
            "app allowlist",
            "target deny list",
        ):
            self.assertNotIn(prohibited, reference.lower())

    def test_recording_reference_preserves_local_ownership_and_freshness(self):
        reference = (
            PLUGIN
            / "skills"
            / "macos-computer-use"
            / "references"
            / "recording-workflow.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "literal action arguments including typed text",
            "Continue only when `enabled:false`",
            "manual\n   `stop_recording({})` is unconditional",
            "`start_recording({output_dir,record_video:false})`",
            "does not make stale element tokens, pixels, pids, or window IDs reusable",
            "live `output_dir` is still this run's directory",
            "Element\nindices and tokens are snapshot-scoped and must not be replayed",
            "`replay_trajectory({dir,delay_ms,stop_on_error:true})`",
            "Never infer success from the replay count alone",
            "`before_state.json` and `after_state.json` carry the same `tree_markdown`",
            "`before.png` and `after.png` are exact target-window images",
            "`evidence.json` classifies each requested capture phase",
            "`app_state.json`/`screenshot.png` are compatibility aliases",
            "Click-family actions may also produce `click.png`",
        ):
            self.assertIn(marker, reference)
        self.assertIn("does not narrow Full Access", reference)

    def test_readmes_and_project_memory_exist(self):
        for path in (
            "README.md",
            "README.zh-CN.md",
            "AGENTS.md",
            "Memory.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
        ):
            self.assertTrue((ROOT / path).exists(), path)

    def test_removed_hermes_skill_reference_is_commit_pinned(self):
        revision = "17dfc6bec4a8b7fd840d479c33e9a7b2449f805d"
        for path in ("README.md", "README.zh-CN.md", "THIRD_PARTY_NOTICES.md", "Memory.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn(revision, text, path)
            self.assertNotIn("hermes-agent/tree/main/skills/apple/macos-computer-use", text, path)

    def test_open_computer_use_reference_is_commit_pinned(self):
        revision = "a265277f6677ef00a1c597f54616cc3410d8d297"
        for path in ("README.md", "README.zh-CN.md", "THIRD_PARTY_NOTICES.md", "Memory.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn(revision, text, path)
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("packages/OpenComputerUseKit/Sources/OpenComputerUseKit/AccessibilitySnapshot.swift", notices)
        self.assertIn("License: MIT", notices)

    def test_live_smoke_sources_compile(self):
        for path in (
            PLUGIN / "scripts" / "live-smoke.py",
            PLUGIN / "scripts" / "verify-cua-native-schema.py",
            PLUGIN / "scripts" / "verify-cua-browser-schema.py",
            PLUGIN / "scripts" / "verify-cua-runtime-discovery.py",
            PLUGIN / "scripts" / "verify-cua-mcp-runtime.py",
            PLUGIN / "tests" / "live_fixture.py",
        ):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        smoke = (PLUGIN / "scripts" / "live-smoke.py").read_text(encoding="utf-8")
        for marker in (
            "run-cua-driver.sh",
            'check_permissions", {"prompt": False}',
            '"health_report"',
            '"get_config"',
            '"set_config"',
            '"get_accessibility_tree"',
            '"driver-daemon"',
            '"start_session"',
            '"get_session_state"',
            '"escalate_session"',
            '"end_session"',
            '"get_agent_cursor_state"',
            '"set_agent_cursor_enabled"',
            '"set_agent_cursor_motion"',
            '"set_agent_cursor_theme"',
            '"get_cursor_position"',
            '"get_screen_size"',
            '"get_desktop_state"',
            '"list_apps"',
            '"bring_to_front"',
            '"launch_app"',
            '"kill_app"',
            '"move_cursor"',
            '"press_key"',
            '"hotkey"',
            '"double_click"',
            '"right_click"',
            '"zoom"',
            '"start_recording"',
            '"stop_recording"',
            '"get_recording_state"',
            '"replay_trajectory"',
            "primary_element_target",
            "primary_screenshot_point",
            "require_cursor_action",
            "require_cursor_position",
            "element_token",
            '"primary_session_cursor_ready"',
            '"primary_stable_health_report_verified"',
            '"primary_human_cursor_motion_verified"',
            '"primary_cursor_theme_verified"',
            '"primary_cursor_theme_restored"',
            '"primary_cursor_motion_restored"',
            '"primary_zoom_bound_click_verified"',
            '"primary_local_recording_started"',
            '"primary_local_trajectory_evidence_verified"',
            '"primary_local_recording_stopped"',
            '"primary_two_snapshot_native_menu_verified"',
            '"primary_virtual_cursor_moved_without_real_pointer"',
            '"primary_background_right_click_verified"',
            '"primary_background_double_click_verified"',
            '"primary_foreground_drag_verified"',
            '"primary_set_value_verified"',
            '"primary_background_scroll_verified"',
            '"primary_background_hotkey_verified"',
            '"primary_background_press_key_verified"',
            '"primary_desktop_scope_verified"',
            '"primary_real_pointer_moved_from_fresh_desktop"',
            '"primary_real_pointer_restored_and_reobserved"',
            '"primary_isolated_app_lifecycle_verified"',
            '"primary_exact_window_frontmost_verified"',
            '"primary_background_file_url_launch_verified"',
            '"primary_exact_file_url_window_closed"',
            '"primary_connection_image_config_isolated_and_restored"',
            '"primary_initial_cursor_theme_verified"',
            '"primary_lightweight_desktop_discovery_verified"',
            '"primary_session_cursor_animated"',
            '"desktop_type_text"',
            '"primary_visible_result_verified"',
            '"fallback_visible_result_verified"',
            '"fallback_window_activated"',
            '"fallback_field_focus_verified"',
            '"fallback_physical_button_clicked"',
            '"fallback_physical_drag_verified"',
            '"fallback_physical_scroll_verified"',
            '"fallback_raw_mouse_sequence_verified"',
            '"fallback_cursor_restored"',
            "fixture_button_screenshot_point",
            "requested_backend",
            "desktop_screenshot_point",
            "restore_pointer_direct",
            "wait_for_process_exit",
            "require_action_verdict",
            "wait_for_fixture_state",
            "ZCODE_LIVE_FIXTURE_STATE",
            "self.process.kill()",
        ):
            self.assertIn(marker, smoke)
        fixture = (PLUGIN / "tests" / "live_fixture.py").read_text(encoding="utf-8")
        self.assertIn("class HotkeyTextField", fixture)
        self.assertIn("NSEventModifierFlagCommand | AppKit.NSEventModifierFlagShift", fixture)
        self.assertIn('button.setKeyEquivalent_(" ")', fixture)
        self.assertIn("def publish_state", fixture)
        self.assertIn('publish_state(field=value, received=value)', fixture)

    def test_live_smoke_maps_cocoa_fixture_geometry_into_png_pixels(self):
        path = PLUGIN / "scripts" / "live-smoke.py"
        spec = importlib.util.spec_from_file_location("zcode_live_smoke_contract", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = {
            "window": {"bounds": {"width": 640, "height": 322}},
            "screenshots": [{"width": 1280, "height": 644}],
        }
        self.assertEqual(module.fixture_button_screenshot_point(state), (230.0, 400.0))
        self.assertEqual(module.fixture_screenshot_point(state, 252.0, 122.0), (504.0, 400.0))
        self.assertEqual(module.fixture_screenshot_point(state, 510.0, 57.0), (1020.0, 530.0))
        primary_state = {
            "window_bounds": {"width": 640, "height": 322},
            "screenshot_width": 1280,
            "screenshot_height": 644,
        }
        self.assertEqual(module.primary_screenshot_point(primary_state, 510.0, 22.0), (1020.0, 600.0))
        desktop_state = {
            "screen_width": 720,
            "screen_height": 450,
            "screenshot_width": 1440,
            "screenshot_height": 900,
        }
        self.assertEqual(module.desktop_screenshot_point(desktop_state, 100, 50), (200.0, 100.0))
        self.assertEqual(
            module.nudged_primary_pointer_target(desktop_state, {"x": 100, "y": 50}),
            {"x": 112, "y": 50},
        )
        self.assertTrue(module.cursor_matches({"x": 112, "y": 51}, {"x": 112, "y": 50}))
        self.assertFalse(module.cursor_matches({"x": 114, "y": 50}, {"x": 112, "y": 50}))
        screen_state = {
            "window": {"bounds": {"x": 100, "y": 50, "width": 640, "height": 322}}
        }
        self.assertEqual(module.fixture_screen_point(screen_state, 252.0, 122.0), (352.0, 250.0))
        self.assertEqual(module.demo_cursor_target(100.0, 80.0), {"x": 25.0, "y": 24.0})
        self.assertEqual(module.demo_cursor_target(10.0, 10.0), {"x": 9.0, "y": 9.0})
        with self.assertRaisesRegex(RuntimeError, "too small"):
            module.demo_cursor_target(1.0, 80.0)
        session = module.new_live_session()
        self.assertRegex(session, r"^zcode-smoke-[0-9a-f]{8}$")
        self.assertLessEqual(len(session), 28)
        self.assertEqual(module.requested_backend([]), "all")
        self.assertEqual(module.requested_backend(["--backend", "primary"]), "primary")
        self.assertEqual(module.requested_backend(["--backend", "fallback"]), "fallback")
        with self.assertRaisesRegex(RuntimeError, "usage"):
            module.requested_backend(["--backend", "unknown"])

    def test_live_smoke_validates_semantic_cursor_action_and_position(self):
        path = PLUGIN / "scripts" / "live-smoke.py"
        spec = importlib.util.spec_from_file_location("zcode_live_smoke_cursor_contract", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = {
            "position": {"x": 120, "y": 240.5},
            "visual_state": {"requested_action": "text", "resolved_action": "text"},
        }

        class Client:
            def call(self, name, arguments):
                self.assert_call = (name, arguments)
                return state, []

        client = Client()
        self.assertIs(module.require_cursor_action(client, "smoke", "text", "type_text"), state)
        self.assertEqual(module.require_cursor_position(state, "click"), {"x": 120.0, "y": 240.5})
        self.assertEqual(
            client.assert_call,
            ("get_agent_cursor_state", {"session": "smoke"}),
        )

    def test_github_actions_are_commit_pinned(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_macos_ci_covers_apple_silicon_and_intel(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("os: macos-15\n", workflow)
        self.assertIn("architecture: arm64", workflow)
        self.assertIn("os: macos-15-intel", workflow)
        self.assertIn("architecture: x86_64", workflow)
        self.assertIn('lipo "$binary" -verify_arch arm64 x86_64', workflow)


if __name__ == "__main__":
    unittest.main()
