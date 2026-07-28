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
        self.assertIn('CUA_VERSION="0.12.6"', launcher)
        self.assertRegex(launcher, r'ASSET_SHA256="[0-9a-f]{64}"')
        self.assertIn('ASSET_NAME="cua-driver-rs-${CUA_VERSION}-darwin-universal.tar.gz"', launcher)
        self.assertIn('verify_sha256 "$ASSET" "$ASSET_SHA256"', launcher)
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
        self.assertIn("--permission-mode unrestricted", launcher)
        self.assertIn("--dangerously-bypass-approvals", launcher)
        self.assertIn("CUA_DRIVER_RS_TELEMETRY_ENABLED=0", launcher)
        self.assertIn("CUA_DRIVER_RS_UPDATE_CHECK=false", launcher)
        self.assertIn("--env CUA_DRIVER_RS_UPDATE_CHECK=false", launcher)
        self.assertIn("--env CUA_DRIVER_RS_TELEMETRY_ENABLED=0", launcher)
        self.assertIn('TELEMETRY_HOME="$DATA_DIR/cua-telemetry"', launcher)
        self.assertIn('export CUA_DRIVER_TELEMETRY_HOME="$TELEMETRY_HOME"', launcher)
        self.assertIn('--env "CUA_DRIVER_TELEMETRY_HOME=$TELEMETRY_HOME"', launcher)
        self.assertIn("Never change the user's ~/.cua-driver preference", launcher)
        self.assertIn('Refusing unsafe plugin telemetry directory', launcher)
        self.assertIn('chmod 700 "$TELEMETRY_HOME"', launcher)
        self.assertIn('"source"[[:space:]]*:[[:space:]]*"persisted"', launcher)
        self.assertNotIn("telemetry disable >/dev/null 2>&1 || true", launcher)
        self.assertNotIn("--no-permissions-gate", launcher)
        self.assertIn('SOCKET_DIR="/tmp/zcode-cua-${UID}"', launcher)
        common = (PLUGIN / "scripts" / "runtime-common.sh").read_text(encoding="utf-8")
        self.assertIn('permission mode: unrestricted', common)
        self.assertIn('user policy: configured=false, active=false, valid=true', common)
        self.assertIn('managed policy: configured=false, active=false, valid=true', common)
        self.assertIn('session policy: configured=false, approved_at_startup=false, valid=true', common)
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

    def test_manual_install_and_doctor_reuse_the_automatic_fallback_runtime(self):
        install = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
        doctor = (PLUGIN / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        for source in (install, doctor):
            self.assertIn('"$ROOT/scripts/run-mcp.sh" --self-test', source)
        self.assertNotIn('python3 -m venv "$ROOT/.venv"', install)

    def test_live_smoke_reuses_the_automatic_fallback_runtime(self):
        smoke = (PLUGIN / "scripts" / "live-smoke.sh").read_text(encoding="utf-8")
        self.assertIn('DATA_PYTHON="$DATA_DIR/venv-$MACOS_CUA_DEPENDENCY_ID/bin/python3"', smoke)
        self.assertIn('"$ROOT/scripts/run-mcp.sh" --self-test', smoke)
        self.assertIn('macos_cua_native_runtime_ready "$DATA_PYTHON" "$ROOT"', smoke)
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
        self.assertIn("check_permissions({prompt:true,probe_direct_capture:false})", skill)
        self.assertNotIn("prompt:true`, which the driver refuses", skill)
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
        self.assertIn("include_screenshot:true,include_text:false", skill)
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
        self.assertIn("Fallback text can also return an MCP error", skill)
        self.assertIn("never the original full text", skill)
        self.assertIn('`has_screenshot:false`', skill)
        self.assertIn("Sparse Chromium AX tree", skill)
        self.assertIn("minimized window beeps or ignores Return/Space/Tab", skill)
        self.assertIn("AXManualAccessibility", skill)
        self.assertIn("`AXContents`", skill)
        self.assertIn("`max_tree_nodes` (up to 10,000)", skill)

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
            PLUGIN / "tests" / "live_fixture.py",
        ):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        smoke = (PLUGIN / "scripts" / "live-smoke.py").read_text(encoding="utf-8")
        for marker in (
            "run-cua-driver.sh",
            'check_permissions", {"prompt": False}',
            '"driver-daemon"',
            '"start_session"',
            '"end_session"',
            '"desktop_type_text"',
            '"primary_visible_result_verified"',
            '"fallback_visible_result_verified"',
            '"fallback_window_activated"',
            '"fallback_field_focus_verified"',
            '"fallback_physical_button_clicked"',
            '"fallback_cursor_restored"',
            "fixture_button_screenshot_point",
            "require_action_verdict",
            "self.process.kill()",
        ):
            self.assertIn(marker, smoke)

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
