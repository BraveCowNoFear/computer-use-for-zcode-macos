from __future__ import annotations

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

    def test_release_versions_are_synchronized(self):
        expected = json.loads((ROOT / "marketplace.json").read_text(encoding="utf-8"))["plugins"][0]["version"]
        package = (PLUGIN / "macos_cua" / "__init__.py").read_text(encoding="utf-8")
        launcher = (PLUGIN / "scripts" / "run-mcp.sh").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'__version__ = "([^"]+)"', package).group(1), expected)
        self.assertEqual(re.search(r'RUNTIME_VERSION="([^"]+)"', launcher).group(1), expected)

    def test_fallback_first_run_is_versioned_and_atomically_published(self):
        launcher = (PLUGIN / "scripts" / "run-mcp.sh").read_text(encoding="utf-8")
        self.assertIn('DATA_VENV="$DATA_DIR/venv-$RUNTIME_VERSION"', launcher)
        self.assertIn('STAGING_VENV="$DATA_DIR/.venv-$RUNTIME_VERSION.install.$$"', launcher)
        self.assertIn('"$STAGING_PYTHON" -m macos_cua.server --self-test', launcher)
        self.assertIn('mv "$STAGING_VENV" "$DATA_VENV"', launcher)
        self.assertNotIn("runtime-version", launcher)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("Verify automatic fallback first run", workflow)
        self.assertIn('MACOS_CUA_DATA_DIR="$data" bash plugins/macos-computer-use/scripts/run-mcp.sh', workflow)
        self.assertIn('test -x "$data/venv-$version/bin/python3"', workflow)

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
        for script in ("install.sh", "run-mcp.sh"):
            source = (PLUGIN / "scripts" / script).read_text(encoding="utf-8")
            self.assertIn("--only-binary=:all:", source)
            self.assertIn("--no-deps", source)
            self.assertIn("--require-hashes", source)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("--only-binary=:all:", workflow)
        self.assertIn("--no-deps", workflow)
        self.assertIn("--require-hashes", workflow)
        common = (PLUGIN / "scripts" / "runtime-common.sh").read_text(encoding="utf-8")
        self.assertIn('(3, 10) <= sys.version_info < (3, 16)', common)
        self.assertIn("CPython 3.10 through 3.15", common)

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
            self.assertEqual(server["command"], "bash")
            self.assertNotIn("url", server)

    def test_primary_launcher_is_pinned_and_unrestricted(self):
        launcher = (PLUGIN / "scripts" / "run-cua-driver.sh").read_text(encoding="utf-8")
        self.assertIn('CUA_VERSION="0.12.6"', launcher)
        self.assertRegex(launcher, r'INSTALLER_SHA256="[0-9a-f]{64}"')
        self.assertRegex(launcher, r'INSTALLER_COMMON_SHA256="[0-9a-f]{64}"')
        self.assertRegex(launcher, r'ASSET_SHA256="[0-9a-f]{64}"')
        self.assertIn('ASSET_NAME="cua-driver-rs-${CUA_VERSION}-darwin-universal.tar.gz"', launcher)
        self.assertIn('PATH="$CURL_SHIM_DIR:$PATH"', launcher)
        self.assertIn('verify_sha256 "$ASSET" "$ASSET_SHA256"', launcher)
        self.assertIn("codesign --verify --deep --strict", launcher)
        self.assertIn("spctl --assess --type execute", launcher)
        self.assertIn("--permission-mode unrestricted", launcher)
        self.assertIn("--dangerously-bypass-approvals", launcher)
        self.assertIn("CUA_DRIVER_RS_TELEMETRY_ENABLED=0", launcher)
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
        self.assertIn('/usr/bin/open -n -g "$APP_BUNDLE"', launcher)

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

    def test_pinned_curl_only_intercepts_the_exact_release_asset(self):
        shim = (PLUGIN / "scripts" / "pinned-curl" / "curl").read_text(encoding="utf-8")
        self.assertIn('requested_url" == "$asset_url', shim)
        self.assertIn('exec "$REAL_CURL" "$@"', shim)
        self.assertNotIn("eval", shim)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "requires a Unix bash runtime")
    def test_pinned_curl_serves_the_verified_local_archive(self):
        shim = PLUGIN / "scripts" / "pinned-curl" / "curl"
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "asset.tar.gz"
            output = Path(directory) / "output.tar.gz"
            asset.write_bytes(b"verified-release-bytes")
            environment = os.environ.copy()
            environment.update(
                {
                    "PINNED_CUA_ASSET_URL": "https://example.invalid/pinned.tar.gz",
                    "PINNED_CUA_ASSET_PATH": str(asset),
                }
            )
            completed = subprocess.run(
                ["bash", str(shim), "-fsSL", "-o", str(output), environment["PINNED_CUA_ASSET_URL"]],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output.read_bytes(), asset.read_bytes())

    def test_skill_routes_primary_then_direct_fallback(self):
        skill = (PLUGIN / "skills" / "macos-computer-use" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        self.assertIn("macos-computer-use-fallback", skill)
        self.assertLess(skill.index("Background AX"), skill.index("Direct fallback"))
        self.assertIn("end_session", skill)
        self.assertLess(skill.index("check_permissions({prompt:false})"), skill.index("start_session"))
        self.assertIn("check_permissions({prompt:true,probe_direct_capture:false})", skill)
        self.assertNotIn("prompt:true`, which the driver refuses", skill)
        self.assertIn('scope:"desktop"', skill)
        self.assertIn("fallback is\nstateless", skill)

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
        ):
            self.assertIn(marker, smoke)


if __name__ == "__main__":
    unittest.main()
