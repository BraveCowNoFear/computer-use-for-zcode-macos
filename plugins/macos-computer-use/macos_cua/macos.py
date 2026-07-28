"""Native macOS implementation backed by Quartz, AppKit, and Accessibility."""

from __future__ import annotations

import base64
from importlib import metadata
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .contracts import ToolError


EXPECTED_PYOBJC_VERSION = "12.2.1"
PYOBJC_DISTRIBUTIONS = (
    "pyobjc-core",
    "pyobjc-framework-Cocoa",
    "pyobjc-framework-CoreText",
    "pyobjc-framework-Quartz",
    "pyobjc-framework-ApplicationServices",
)
ACCESSIBILITY_REQUIRED_TOOLS = frozenset(
    {
        "click",
        "press_key",
        "type_text",
        "scroll",
        "set_value",
        "drag",
        "perform_secondary_action",
        "activate_window",
        "desktop_click",
        "desktop_press_key",
        "desktop_type_text",
        "desktop_scroll",
        "desktop_drag",
        "move_mouse",
        "mouse_down",
        "mouse_up",
    }
)


def require_exact_pyobjc_versions(version_getter: Any | None = None) -> dict[str, str]:
    """Resolve and enforce the exact native wheel closure tested by the plugin."""
    getter = version_getter or metadata.version
    versions: dict[str, str] = {}
    for distribution in PYOBJC_DISTRIBUTIONS:
        try:
            versions[distribution] = str(getter(distribution))
        except Exception as error:
            raise RuntimeError(f"Missing required distribution {distribution}: {error}") from error
    mismatched = {
        distribution: version
        for distribution, version in versions.items()
        if version != EXPECTED_PYOBJC_VERSION
    }
    if mismatched:
        detail = ", ".join(f"{name}={version}" for name, version in mismatched.items())
        raise RuntimeError(
            f"PyObjC runtime mismatch; expected {EXPECTED_PYOBJC_VERSION} for every package, found {detail}"
        )
    return versions


KEY_CODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "return": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43,
    "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "delete": 51,
    "escape": 53, "command": 55, "shift": 56, "capslock": 57, "option": 58, "control": 59,
    "rightshift": 60, "rightoption": 61, "rightcontrol": 62, "f17": 64, "kp_decimal": 65,
    "kp_multiply": 67, "kp_add": 69, "kp_clear": 71, "volumeup": 72, "volumedown": 73,
    "mute": 74, "kp_divide": 75, "kp_enter": 76, "kp_subtract": 78, "f18": 79, "f19": 80,
    "kp_equals": 81, "kp_0": 82, "kp_1": 83, "kp_2": 84, "kp_3": 85, "kp_4": 86,
    "kp_5": 87, "kp_6": 88, "kp_7": 89, "f20": 90, "kp_8": 91, "kp_9": 92,
    "f5": 96, "f6": 97, "f7": 98, "f3": 99, "f8": 100, "f9": 101, "f11": 103,
    "f13": 105, "f16": 106, "f14": 107, "f10": 109, "f12": 111, "f15": 113,
    "help": 114, "home": 115, "pageup": 116, "forwarddelete": 117, "f4": 118, "end": 119,
    "f2": 120, "pagedown": 121, "f1": 122, "left": 123, "right": 124, "down": 125, "up": 126,
}

KEY_ALIASES: dict[str, str] = {
    "enter": "return", "esc": "escape", "backspace": "delete", "back_space": "delete",
    "forward_delete": "forwarddelete", "page_up": "pageup", "page_down": "pagedown",
    "arrowleft": "left", "arrowright": "right", "arrowup": "up", "arrowdown": "down",
    "period": ".", "comma": ",", "slash": "/", "backslash": "\\", "minus": "-",
    "hyphen": "-", "equal": "=", "equals": "=", "semicolon": ";", "apostrophe": "'",
    "quote": "'", "grave": "`", "backtick": "`", "leftbracket": "[", "rightbracket": "]",
    "numpad_0": "kp_0", "numpad_1": "kp_1", "numpad_2": "kp_2", "numpad_3": "kp_3",
    "numpad_4": "kp_4", "numpad_5": "kp_5", "numpad_6": "kp_6", "numpad_7": "kp_7",
    "numpad_8": "kp_8", "numpad_9": "kp_9", "numpad_add": "kp_add",
    "numpad_subtract": "kp_subtract", "numpad_multiply": "kp_multiply",
    "numpad_divide": "kp_divide", "numpad_decimal": "kp_decimal", "numpad_enter": "kp_enter",
}

MODIFIER_ALIASES: dict[str, str] = {
    "cmd": "command", "cmd_l": "command", "cmd_r": "command",
    "command": "command", "command_l": "command", "command_r": "command",
    "meta": "command", "meta_l": "command", "meta_r": "command",
    "super": "command", "super_l": "command", "super_r": "command",
    "os": "command", "os_l": "command", "os_r": "command",
    "control": "control", "ctrl": "control", "control_l": "control", "control_r": "control",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "option", "option": "option", "alt_l": "option", "alt_r": "option",
}

SHIFTED_KEY_ALIASES: dict[str, str] = {
    "!": "1", "exclam": "1",
    "@": "2", "at": "2",
    "#": "3", "numbersign": "3",
    "$": "4", "dollar": "4",
    "%": "5", "percent": "5",
    "^": "6", "asciicircum": "6",
    "&": "7", "ampersand": "7",
    "*": "8", "asterisk": "8",
    "(": "9", "parenleft": "9",
    ")": "0", "parenright": "0",
    "_": "-", "underscore": "-",
    "plus": "=",
    "{": "[", "braceleft": "[",
    "}": "]", "braceright": "]",
    "|": "\\", "bar": "\\",
    ":": ";", "colon": ";",
    '"': "'", "quotedbl": "'",
    "<": ",", "less": ",",
    ">": ".", "greater": ".",
    "?": "/", "question": "/",
    "~": "`", "asciitilde": "`",
    "iso_left_tab": "tab",
}


def normalize_key_name(value: str) -> str:
    stripped = value.strip()
    if len(stripped) == 1:
        return stripped.lower()
    lowered = re.sub(r"[\s-]+", "_", stripped.lower())
    return KEY_ALIASES.get(lowered, lowered)


def parse_key_chord(value: str) -> tuple[int, set[str]]:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("key must be a non-empty string")
    parts = [part.strip() for part in value.split("+") if part.strip()]
    modifiers: set[str] = set()
    normal: list[str] = []
    uppercase_shift = False
    for raw in parts:
        normalized_modifier = re.sub(r"[\s-]+", "_", raw.lower())
        modifier = MODIFIER_ALIASES.get(normalized_modifier)
        if modifier:
            modifiers.add(modifier)
            continue
        if normalized_modifier in SHIFTED_KEY_ALIASES:
            modifiers.add("shift")
            normal.append(SHIFTED_KEY_ALIASES[normalized_modifier])
            continue
        if len(raw) == 1 and raw.isalpha() and raw.isupper():
            uppercase_shift = True
        normal.append(normalize_key_name(raw))
    if uppercase_shift:
        modifiers.add("shift")
    if len(normal) > 1:
        raise ToolError("A key chord may contain only one non-modifier key")
    if not normal:
        if len(modifiers) != 1:
            raise ToolError("A modifier-only chord must contain exactly one modifier")
        key = next(iter(modifiers))
        modifiers.clear()
    else:
        key = normal[0]
    if key not in KEY_CODES:
        raise ToolError(f"Unsupported key name: {key}")
    return KEY_CODES[key], modifiers


class MacOSBackend:
    def __init__(self) -> None:
        self.AppKit: Any | None = None
        self.ApplicationServices: Any | None = None
        self.Quartz: Any | None = None
        self.native_error: str | None = None
        self.native_versions: dict[str, str] = {}
        try:
            import AppKit  # type: ignore[import-not-found]
            import ApplicationServices  # type: ignore[import-not-found]
            import Quartz  # type: ignore[import-not-found]

            self.native_versions = require_exact_pyobjc_versions()
            self.AppKit = AppKit
            self.ApplicationServices = ApplicationServices
            self.Quartz = Quartz
        except Exception as error:
            self.native_error = str(error)
        self._element_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._screenshot_cache: dict[str, dict[str, Any]] = {}
        self._installed_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._screenshot_dir = Path(tempfile.gettempdir()) / "zcode-macos-computer-use"
        self._held_buttons: dict[Any, tuple[Any, Any, float, float]] = {}
        self._cleanup_orphaned_screenshots()

    def _cleanup_orphaned_screenshots(self) -> None:
        if not self._screenshot_dir.is_dir():
            return
        cutoff = time.time() - 24 * 60 * 60
        for path in self._screenshot_dir.glob("*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def close(self) -> None:
        for button, (up, _dragged, x, y) in list(self._held_buttons.items()):
            try:
                self._post_mouse(up, button, x, y)
            except Exception:
                pass
        self._held_buttons.clear()
        self._invalidate_all_observations()

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            raise ToolError(f"Unknown tool: {name}")
        if name not in {"computer_use_health", "permission_status"}:
            self._require_native()
        requires_accessibility = name in ACCESSIBILITY_REQUIRED_TOOLS or (
            name == "get_window_state" and bool(arguments.get("include_text", False))
        )
        if requires_accessibility and not self._permission_status()["accessibility"]:
            raise ToolError(
                "Accessibility permission is not granted. Run request_permissions with "
                "accessibility=true, grant it in System Settings, and restart ZCode."
            )
        return method(arguments)

    def _require_native(self) -> None:
        if (
            self.native_error
            or self.Quartz is None
            or self.AppKit is None
            or self.ApplicationServices is None
        ):
            detail = self.native_error or "PyObjC is unavailable"
            raise ToolError(
                "Native macOS dependencies are missing. Run scripts/install.sh, restart the plugin, "
                f"and retry. Detail: {detail}"
            )

    def tool_computer_use_health(self, arguments: dict[str, Any]) -> dict[str, Any]:
        native = (
            self.native_error is None
            and self.Quartz is not None
            and self.AppKit is not None
            and self.ApplicationServices is not None
        )
        permissions = self._permission_status() if native else {"accessibility": False, "screenRecording": False}
        ax_ready = bool(native and permissions["accessibility"])
        pixel_ready = bool(ax_ready and permissions["screenRecording"])
        return {
            "ok": ax_ready,
            "platform": "darwin",
            "nativeDependencies": native,
            "nativeError": self.native_error,
            "pyobjcVersions": self.native_versions,
            **permissions,
            "axControlReady": ax_ready,
            "pixelObservationReady": pixel_ready,
            "desktopObservationReady": pixel_ready,
            "localOnly": True,
            "extraConfirmationLayer": False,
            "message": (
                "Ready for AX and pixel-based live macOS control."
                if pixel_ready
                else "Ready for AX-only control; grant Screen Recording only when screenshots or pixels are needed."
                if ax_ready
                else "Install dependencies and grant Accessibility, then restart ZCode."
            ),
        }

    def _permission_status(self) -> dict[str, Any]:
        self._require_native()
        AX = self.ApplicationServices
        Q = self.Quartz
        accessibility = bool(AX.AXIsProcessTrusted())
        preflight = getattr(Q, "CGPreflightScreenCaptureAccess", None)
        screen_recording = bool(preflight()) if callable(preflight) else False
        return {
            "accessibility": accessibility,
            "screenRecording": screen_recording,
            "fullDiskAccess": "not-required-for-UI-control",
            "settings": {
                "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                "screenRecording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            },
        }

    def tool_permission_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.native_error:
            return {
                "accessibility": False,
                "screenRecording": False,
                "nativeDependencies": False,
                "nativeError": self.native_error,
                "pyobjcVersions": self.native_versions,
            }
        return self._permission_status()

    def tool_request_permissions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        AX = self.ApplicationServices
        Q = self.Quartz
        request_accessibility = bool(arguments.get("accessibility", True))
        request_screen_recording = bool(arguments.get("screen_recording", False))
        if request_accessibility:
            options = {getattr(AX, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt"): True}
            try:
                AX.AXIsProcessTrustedWithOptions(options)
            except Exception:
                pass
        if request_screen_recording:
            request_capture = getattr(Q, "CGRequestScreenCaptureAccess", None)
            if callable(request_capture):
                try:
                    request_capture()
                except Exception:
                    pass
        status = self._permission_status()
        if arguments.get("open_settings", True) and request_accessibility and not status["accessibility"]:
            subprocess.Popen(["/usr/bin/open", status["settings"]["accessibility"]])
        elif arguments.get("open_settings", True) and request_screen_recording and not status["screenRecording"]:
            subprocess.Popen(["/usr/bin/open", status["settings"]["screenRecording"]])
        status["requested"] = {
            "accessibility": request_accessibility,
            "screenRecording": request_screen_recording,
        }
        status["restartRequiredAfterGrant"] = True
        return status

    def _app_id(self, pid: int, owner_name: str) -> str:
        A = self.AppKit
        app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        bundle_id = app.bundleIdentifier() if app is not None else None
        return str(bundle_id) if bundle_id else f"process:{pid}:{owner_name}"

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _window_from_info(self, info: dict[str, Any], z_index: int) -> dict[str, Any] | None:
        Q = self.Quartz
        window_id = int(info.get(Q.kCGWindowNumber, 0) or 0)
        pid = int(info.get(Q.kCGWindowOwnerPID, 0) or 0)
        owner = str(info.get(Q.kCGWindowOwnerName, "") or "")
        bounds_raw = info.get(Q.kCGWindowBounds, {}) or {}
        x = self._number(bounds_raw.get("X", bounds_raw.get("x", 0)))
        y = self._number(bounds_raw.get("Y", bounds_raw.get("y", 0)))
        width = self._number(bounds_raw.get("Width", bounds_raw.get("width", 0)))
        height = self._number(bounds_raw.get("Height", bounds_raw.get("height", 0)))
        if window_id <= 0 or pid <= 0 or width < 2 or height < 2:
            return None
        return {
            "id": window_id,
            "app": self._app_id(pid, owner),
            "title": str(info.get(Q.kCGWindowName, "") or ""),
            "pid": pid,
            "ownerName": owner,
            "bounds": {"x": x, "y": y, "width": width, "height": height},
            "zIndex": z_index,
        }

    def _list_windows(self) -> list[dict[str, Any]]:
        self._require_native()
        Q = self.Quartz
        # Keep off-Space and minimized candidates available to the foreground
        # fallback. Rehydration still proves the current id/app pair on every
        # action, and screenshots/actions fail clearly if macOS cannot expose it.
        options = Q.kCGWindowListOptionAll | Q.kCGWindowListExcludeDesktopElements
        infos = Q.CGWindowListCopyWindowInfo(options, Q.kCGNullWindowID) or []
        windows: list[dict[str, Any]] = []
        for z_index, info in enumerate(infos):
            if int(info.get(Q.kCGWindowLayer, 0) or 0) != 0:
                continue
            window = self._window_from_info(info, z_index)
            if window is not None and window["ownerName"] not in {"Window Server", "Dock"}:
                window["onScreen"] = bool(info.get(Q.kCGWindowIsOnscreen, False))
                windows.append(window)
        # CGWindowListCopyWindowInfo is front-to-back, while the Codex
        # Screenshot contract defines larger zIndex values as visually above.
        for index, window in enumerate(windows):
            window["zIndex"] = len(windows) - index - 1
        return windows

    def tool_list_windows(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._list_windows()

    def _get_window(
        self, value: Any, app: str | None = None, pid: int | None = None
    ) -> dict[str, Any]:
        if isinstance(value, dict):
            window_id = int(value.get("id", 0))
            app = str(value.get("app")) if value.get("app") is not None else app
            pid = int(value["pid"]) if value.get("pid") is not None else pid
        else:
            window_id = int(value)
        candidates = [window for window in self._list_windows() if window["id"] == window_id]
        if app:
            candidates = [window for window in candidates if window["app"] == app]
        if pid is not None:
            candidates = [window for window in candidates if int(window["pid"]) == int(pid)]
        if len(candidates) != 1:
            identity = f"id={window_id}, app={app!r}, pid={pid!r}"
            raise ToolError(f"Expected one current window for {identity}; found {len(candidates)}. Re-run list_windows.")
        return candidates[0]

    @staticmethod
    def _window_key(window: dict[str, Any]) -> tuple[str, int, int]:
        """Bind observations to one concrete app process and window instance."""
        return str(window["app"]), int(window["pid"]), int(window["id"])

    def tool_get_window(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._get_window(arguments["id"], arguments.get("app"), arguments.get("pid"))

    def _installed_apps(self) -> list[dict[str, Any]]:
        A = self.AppKit
        apps: dict[str, dict[str, Any]] = {}
        workspace = A.NSWorkspace.sharedWorkspace()
        # Running state and PIDs are process-lifetime facts, so refresh them on
        # every call. Only the comparatively expensive installed-app catalog is
        # cached; otherwise a quit/relaunch can be reported stale for 60 seconds.
        for running in workspace.runningApplications():
            bundle_id = running.bundleIdentifier()
            pid = int(running.processIdentifier())
            name = str(running.localizedName() or bundle_id or f"Process {pid}")
            app_id = str(bundle_id) if bundle_id else f"process:{pid}:{name}"
            url = running.bundleURL()
            apps[app_id] = {
                "id": app_id,
                "displayName": name,
                "path": str(url.path()) if url is not None else None,
                "isRunning": True,
                "pid": pid,
            }

        if self._installed_cache and time.monotonic() - self._installed_cache[0] < 60:
            installed = [dict(item) for item in self._installed_cache[1]]
        else:
            catalog: dict[str, dict[str, Any]] = {}
            roots = [Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications"]
            discovered = 0
            for root in roots:
                if not root.exists():
                    continue
                for current, dirs, _files in os.walk(root):
                    app_dirs = [directory for directory in dirs if directory.lower().endswith(".app")]
                    dirs[:] = [directory for directory in dirs if not directory.lower().endswith(".app")]
                    for directory in app_dirs:
                        path = Path(current) / directory
                        bundle = A.NSBundle.bundleWithPath_(str(path))
                        info = bundle.infoDictionary() if bundle is not None else None
                        bundle_id = bundle.bundleIdentifier() if bundle is not None else None
                        app_id = str(bundle_id or path)
                        display = None
                        if info:
                            display = info.get("CFBundleDisplayName") or info.get("CFBundleName")
                        catalog.setdefault(
                            app_id,
                            {
                                "id": app_id,
                                "displayName": str(display or path.stem),
                                "path": str(path),
                                "isRunning": False,
                            },
                        )
                        discovered += 1
                        if discovered >= 4000:
                            break
                    if discovered >= 4000:
                        break
                if discovered >= 4000:
                    break
            installed = list(catalog.values())
            self._installed_cache = (time.monotonic(), [dict(item) for item in installed])

        for installed_app in installed:
            entry = apps.setdefault(str(installed_app["id"]), dict(installed_app))
            if not entry.get("path"):
                entry["path"] = installed_app.get("path")
        result = list(apps.values())
        return result

    def tool_list_apps(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        windows = self._list_windows()
        by_app: dict[str, list[dict[str, Any]]] = {}
        for window in windows:
            by_app.setdefault(window["app"], []).append(window)
        apps = self._installed_apps()
        known = {app["id"] for app in apps}
        for app in apps:
            app["windows"] = by_app.get(app["id"], [])
            if app["windows"]:
                app["isRunning"] = True
        for app_id, app_windows in by_app.items():
            if app_id not in known:
                first = app_windows[0]
                apps.append(
                    {
                        "id": app_id,
                        "displayName": first["ownerName"],
                        "isRunning": True,
                        "path": None,
                        "windows": app_windows,
                    }
                )
        apps.sort(key=lambda app: (not bool(app.get("isRunning")), str(app.get("displayName", "")).lower()))
        return apps

    def tool_launch_app(self, arguments: dict[str, Any]) -> dict[str, Any]:
        app = str(arguments["app"]).strip()
        if not app:
            raise ToolError("app must be non-empty")
        if app.startswith("process:"):
            raise ToolError("Process-backed identifiers cannot launch an app; use a bundle ID, name, or .app path")
        if app.lower().endswith(".app") or "/" in app:
            command = ["/usr/bin/open", app]
        elif re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", app):
            command = ["/usr/bin/open", "-b", app]
        else:
            command = ["/usr/bin/open", "-a", app]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if completed.returncode != 0:
            raise ToolError(completed.stderr.strip() or f"Failed to launch {app}")
        self._installed_cache = None

        target_bundle_id: str | None = None
        target_path: str | None = None
        target_name: str | None = None
        if app.lower().endswith(".app") or "/" in app:
            target_path = str(Path(app).expanduser().resolve())
            bundle = self.AppKit.NSBundle.bundleWithPath_(target_path)
            bundle_id = bundle.bundleIdentifier() if bundle is not None else None
            target_bundle_id = str(bundle_id) if bundle_id else None
        elif re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", app):
            target_bundle_id = app
        else:
            target_name = app.casefold()

        deadline = time.monotonic() + 5
        candidate = None
        windows: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            running = list(self.AppKit.NSWorkspace.sharedWorkspace().runningApplications() or [])
            matches = []
            for item in running:
                bundle_id = item.bundleIdentifier()
                url = item.bundleURL()
                path = str(url.path()) if url is not None else None
                name = str(item.localizedName() or "")
                if target_bundle_id and str(bundle_id or "") == target_bundle_id:
                    matches.append(item)
                elif target_path and path and str(Path(path).resolve()) == target_path:
                    matches.append(item)
                elif target_name and name.casefold() == target_name:
                    matches.append(item)
            if len(matches) > 1 and target_name:
                identifiers = sorted({str(item.bundleIdentifier() or item.localizedName()) for item in matches})
                raise ToolError(f"App name {app!r} is ambiguous after launch; use one bundle ID: {identifiers}")
            if matches:
                candidate = matches[0]
                pid = int(candidate.processIdentifier())
                windows = [window for window in self._list_windows() if int(window["pid"]) == pid]
                if windows or time.monotonic() + 0.5 >= deadline:
                    break
            time.sleep(0.1)
        if candidate is None:
            raise ToolError(f"macOS accepted the launch request for {app!r}, but no matching running app appeared")
        bundle_id = candidate.bundleIdentifier()
        return {
            "ok": True,
            "app": str(bundle_id or app),
            "bundleId": str(bundle_id) if bundle_id else None,
            "displayName": str(candidate.localizedName() or app),
            "pid": int(candidate.processIdentifier()),
            "windows": windows,
        }

    def _ax_attr(self, name: str, fallback: str) -> Any:
        return getattr(self.ApplicationServices, name, fallback)

    def _ax_copy(self, element: Any, attribute: Any) -> Any:
        AX = self.ApplicationServices
        try:
            result = AX.AXUIElementCopyAttributeValue(element, attribute, None)
        except Exception:
            return None
        if isinstance(result, tuple) and len(result) == 2:
            error, value = result
            return value if int(error) == 0 else None
        return result

    def _ax_actions(self, element: Any) -> list[str]:
        AX = self.ApplicationServices
        try:
            result = AX.AXUIElementCopyActionNames(element, None)
        except Exception:
            return []
        if isinstance(result, tuple) and len(result) == 2:
            error, value = result
            return [str(item) for item in value] if int(error) == 0 and value else []
        return [str(item) for item in result] if result else []

    @staticmethod
    def _ax_ok(result: Any) -> bool:
        if isinstance(result, tuple):
            result = result[0]
        try:
            return int(result) == 0
        except (TypeError, ValueError):
            return result is None or result is True

    def _ax_perform(self, element: Any, action: str) -> bool:
        try:
            return self._ax_ok(self.ApplicationServices.AXUIElementPerformAction(element, action))
        except Exception:
            return False

    def _ax_set(self, element: Any, attribute: Any, value: Any) -> bool:
        try:
            return self._ax_ok(self.ApplicationServices.AXUIElementSetAttributeValue(element, attribute, value))
        except Exception:
            return False

    def _ax_value(self, value: Any, value_type: Any) -> Any:
        if value is None:
            return None
        try:
            result = self.ApplicationServices.AXValueGetValue(value, value_type, None)
        except Exception:
            return value
        if isinstance(result, tuple) and len(result) == 2:
            success, converted = result
            return converted if success else None
        return result

    @staticmethod
    def _point_components(value: Any) -> tuple[float, float] | None:
        if value is None:
            return None
        if hasattr(value, "x") and hasattr(value, "y"):
            return float(value.x), float(value.y)
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return float(value[0]), float(value[1])
        if isinstance(value, dict):
            return float(value.get("x", 0)), float(value.get("y", 0))
        return None

    def _ax_window(self, window: dict[str, Any]) -> Any:
        AX = self.ApplicationServices
        app_element = AX.AXUIElementCreateApplication(int(window["pid"]))
        windows = self._ax_copy(app_element, self._ax_attr("kAXWindowsAttribute", "AXWindows")) or []
        if not isinstance(windows, (list, tuple)):
            windows = [windows]
        number_attr = self._ax_attr("kAXWindowNumberAttribute", "AXWindowNumber")
        number_matches = []
        for item in windows:
            try:
                if int(self._ax_copy(item, number_attr)) == int(window["id"]):
                    number_matches.append(item)
            except (TypeError, ValueError):
                continue
        if len(number_matches) == 1:
            return number_matches[0]
        if len(number_matches) > 1:
            raise ToolError("Accessibility exposed multiple windows with the target CGWindowID; use fresh pixels")
        title_attr = self._ax_attr("kAXTitleAttribute", "AXTitle")
        position_attr = self._ax_attr("kAXPositionAttribute", "AXPosition")
        size_attr = self._ax_attr("kAXSizeAttribute", "AXSize")
        title_matches = [item for item in windows if str(self._ax_copy(item, title_attr) or "") == window.get("title", "")]
        if len(title_matches) == 1:
            candidates = title_matches
        elif len(windows) == 1:
            candidates = list(windows)
        else:
            raise ToolError(
                "Accessibility does not expose AXWindowNumber and no unique title binds this window; use fresh pixels"
            )
        target_bounds = window["bounds"]
        ranked: list[tuple[float, Any]] = []
        for item in candidates:
            position = self._point_components(
                self._ax_value(self._ax_copy(item, position_attr), self._ax_attr("kAXValueCGPointType", 1))
            )
            size = self._point_components(
                self._ax_value(self._ax_copy(item, size_attr), self._ax_attr("kAXValueCGSizeType", 2))
            )
            if position and size:
                distance = (
                    abs(position[0] - target_bounds["x"])
                    + abs(position[1] - target_bounds["y"])
                    + abs(size[0] - target_bounds["width"])
                    + abs(size[1] - target_bounds["height"])
                )
            else:
                distance = 1_000_000
            ranked.append((distance, item))
        ranked.sort(key=lambda item: item[0])
        if not ranked:
            raise ToolError("The target app exposes no Accessibility window. Grant Accessibility and re-observe.")
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) <= 1:
            raise ToolError("Accessibility window binding is ambiguous; use a fresh pixel observation for this window")
        return ranked[0][1]

    @staticmethod
    def _short(value: Any, limit: int = 240) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).replace("\x00", "").split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @staticmethod
    def _preserve_text(value: Any, limit: int) -> str:
        if value is None:
            return ""
        text = str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _format_element(self, element: Any, index: int, depth: int) -> str:
        role = self._short(self._ax_copy(element, self._ax_attr("kAXRoleAttribute", "AXRole"))) or "AXUnknown"
        title = self._short(self._ax_copy(element, self._ax_attr("kAXTitleAttribute", "AXTitle")))
        description = self._short(self._ax_copy(element, self._ax_attr("kAXDescriptionAttribute", "AXDescription")))
        value = self._short(self._ax_copy(element, self._ax_attr("kAXValueAttribute", "AXValue")))
        parts = [f"[{index}]", role]
        label = title or description
        if label:
            parts.append(json.dumps(label, ensure_ascii=False))
        if value and value != label:
            parts.append(f"value={json.dumps(value, ensure_ascii=False)}")
        actions = [action.removeprefix("AX") for action in self._ax_actions(element)]
        if actions:
            parts.append("actions=" + ",".join(actions[:8]))
        return "  " * depth + " ".join(parts)

    def _accessibility_state(self, window: dict[str, Any]) -> dict[str, Any]:
        root = self._ax_window(window)
        child_attr = self._ax_attr("kAXChildrenAttribute", "AXChildren")
        elements: list[Any] = []
        lines: list[str] = []
        seen: set[int] = set()
        truncated = False

        def walk(element: Any, depth: int) -> None:
            nonlocal truncated
            if depth > 12 or len(elements) >= 350:
                truncated = True
                return
            identity = id(element)
            if identity in seen:
                return
            seen.add(identity)
            index = len(elements)
            elements.append(element)
            lines.append(self._format_element(element, index, depth))
            children = self._ax_copy(element, child_attr) or []
            if not isinstance(children, (list, tuple)):
                children = [children]
            for child in children:
                walk(child, depth + 1)

        walk(root, 0)
        key = self._window_key(window)
        generation = uuid.uuid4().hex
        self._element_cache[key] = {"generation": generation, "elements": elements, "created": time.monotonic()}

        AX = self.ApplicationServices
        app_element = AX.AXUIElementCreateApplication(int(window["pid"]))
        focused = self._ax_copy(app_element, self._ax_attr("kAXFocusedUIElementAttribute", "AXFocusedUIElement"))
        focused_line = None
        selected_text = None
        document_text = None
        selected_elements: list[str] = []
        if focused is not None and focused not in elements:
            owner = self._ax_copy(focused, self._ax_attr("kAXWindowAttribute", "AXWindow"))
            owner_number = self._ax_copy(
                owner, self._ax_attr("kAXWindowNumberAttribute", "AXWindowNumber")
            ) if owner is not None else None
            try:
                belongs = owner == root or int(owner_number) == int(window["id"])
            except (TypeError, ValueError):
                belongs = owner == root
            if not belongs:
                focused = None
        if focused is not None:
            try:
                focused_index = elements.index(focused)
            except ValueError:
                focused_index = len(elements)
                # A focused element can live outside the traversed window tree
                # (or beyond its cap). If we advertise an index, keep its native
                # reference actionable for this observation.
                elements.append(focused)
            focused_line = self._format_element(focused, focused_index, 0)
            selected_text = self._preserve_text(
                self._ax_copy(focused, self._ax_attr("kAXSelectedTextAttribute", "AXSelectedText")), 4000
            ) or None
            role = self._short(self._ax_copy(focused, self._ax_attr("kAXRoleAttribute", "AXRole")))
            if role in {"AXTextArea", "AXTextField", "AXWebArea", "AXStaticText"}:
                document_text = self._preserve_text(
                    self._ax_copy(focused, self._ax_attr("kAXValueAttribute", "AXValue")), 12000
                ) or None
        selected_seen: set[int] = set()
        for container in (root, focused):
            if container is None:
                continue
            for attr_name, fallback in (
                ("kAXSelectedChildrenAttribute", "AXSelectedChildren"),
                ("kAXSelectedRowsAttribute", "AXSelectedRows"),
                ("kAXSelectedCellsAttribute", "AXSelectedCells"),
            ):
                selected = self._ax_copy(container, self._ax_attr(attr_name, fallback)) or []
                if not isinstance(selected, (list, tuple)):
                    selected = [selected]
                for item in selected:
                    identity = id(item)
                    if identity in selected_seen:
                        continue
                    selected_seen.add(identity)
                    try:
                        index = elements.index(item)
                    except ValueError:
                        index = len(elements)
                        elements.append(item)
                    selected_elements.append(self._format_element(item, index, 0))
        return {
            "tree": "\n".join(lines),
            "focused_element": focused_line,
            "selected_text": selected_text,
            "selected_elements": selected_elements,
            "document_text": document_text,
            "generation": generation,
            "truncated": truncated,
        }

    def _capture_window(self, window: dict[str, Any]) -> dict[str, Any]:
        status = self._permission_status()
        if not status["screenRecording"]:
            raise ToolError("Screen Recording permission is not granted. Run request_permissions, grant it, and restart ZCode.")
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._screenshot_dir, 0o700)
        except OSError:
            pass
        screenshot_id = uuid.uuid4().hex
        path = self._screenshot_dir / f"{window['id']}-{screenshot_id}.png"
        executable = shutil.which("screencapture") or "/usr/sbin/screencapture"
        completed = subprocess.run(
            [executable, "-x", "-o", "-l", str(window["id"]), str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0 or not path.exists():
            raise ToolError(completed.stderr.strip() or "macOS could not capture the selected window")
        raw = path.read_bytes()
        width = int(round(window["bounds"]["width"]))
        height = int(round(window["bounds"]["height"]))
        try:
            image_rep = self.AppKit.NSBitmapImageRep.imageRepWithContentsOfFile_(str(path))
            if image_rep is not None:
                width = int(image_rep.pixelsWide())
                height = int(image_rep.pixelsHigh())
        except Exception:
            pass
        cached = {
            "id": screenshot_id,
            "windowKey": self._window_key(window),
            "bounds": dict(window["bounds"]),
            "imageWidth": width,
            "imageHeight": height,
            "created": time.monotonic(),
            "path": str(path),
        }
        self._screenshot_cache[screenshot_id] = cached
        if len(self._screenshot_cache) > 64:
            oldest = sorted(self._screenshot_cache.items(), key=lambda item: item[1]["created"])[:16]
            for old_id, old in oldest:
                self._screenshot_cache.pop(old_id, None)
                self._delete_cached_screenshot(old)
        return {
            "id": screenshot_id,
            "width": width,
            "height": height,
            "originX": window["bounds"]["x"],
            "originY": window["bounds"]["y"],
            "zIndex": window.get("zIndex", 0),
            "path": str(path),
            "mimeType": "image/png",
            "_image_base64": base64.b64encode(raw).decode("ascii"),
        }

    def tool_get_window_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._get_window(arguments["window"])
        self._invalidate_window_observations(window)
        include_screenshot = bool(arguments.get("include_screenshot", True))
        include_text = bool(arguments.get("include_text", False))
        screenshots = [self._capture_window(window)] if include_screenshot else []
        accessibility = self._accessibility_state(window) if include_text else None
        return {"window": window, "screenshots": screenshots, "accessibility": accessibility}

    def _desktop_layout(self) -> list[dict[str, Any]]:
        screens = list(self.AppKit.NSScreen.screens() or [])
        if not screens:
            raise ToolError("macOS reports no active display")
        main_frame = screens[0].frame()
        main_height = float(main_frame.size.height)
        layout: list[dict[str, Any]] = []
        screen_number_key = getattr(self.AppKit, "NSScreenNumber", "NSScreenNumber")
        for index, screen in enumerate(screens):
            frame = screen.frame()
            width = float(frame.size.width)
            height = float(frame.size.height)
            x = float(frame.origin.x)
            # NSScreen is bottom-left-origin; Quartz input/window coordinates
            # are top-left-origin relative to the main display.
            y = main_height - (float(frame.origin.y) + height)
            description = screen.deviceDescription() or {}
            display_id = description.get(screen_number_key, description.get("NSScreenNumber", index))
            layout.append(
                {
                    "displayId": int(display_id),
                    "bounds": {"x": x, "y": y, "width": width, "height": height},
                    "backingScaleFactor": float(screen.backingScaleFactor()),
                    "isMain": index == 0,
                }
            )
        return layout

    @staticmethod
    def _bounds_for_layout(layout: list[dict[str, Any]]) -> dict[str, float]:
        rectangles = [screen["bounds"] for screen in layout]
        left = min(float(item["x"]) for item in rectangles)
        top = min(float(item["y"]) for item in rectangles)
        right = max(float(item["x"]) + float(item["width"]) for item in rectangles)
        bottom = max(float(item["y"]) + float(item["height"]) for item in rectangles)
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def _desktop_bounds(self) -> dict[str, float]:
        return self._bounds_for_layout(self._desktop_layout())

    @staticmethod
    def _layout_fingerprint(layout: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "displayId": int(screen["displayId"]),
                "bounds": {key: float(screen["bounds"][key]) for key in ("x", "y", "width", "height")},
                "backingScaleFactor": float(screen["backingScaleFactor"]),
            }
            for screen in layout
        ]

    def _capture_desktop_screen(
        self, screen: dict[str, Any], layout: list[dict[str, Any]]
    ) -> dict[str, Any]:
        Q = self.Quartz
        A = self.AppKit
        bounds = screen["bounds"]
        rect = Q.CGRectMake(bounds["x"], bounds["y"], bounds["width"], bounds["height"])
        image = Q.CGWindowListCreateImage(
            rect,
            Q.kCGWindowListOptionOnScreenOnly,
            Q.kCGNullWindowID,
            Q.kCGWindowImageDefault,
        )
        if image is None:
            raise ToolError("macOS could not capture the visible desktop")
        representation = A.NSBitmapImageRep.alloc().initWithCGImage_(image)
        if representation is None:
            raise ToolError("macOS could not encode the desktop screenshot")
        data = representation.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
        if data is None:
            raise ToolError("macOS could not encode the desktop screenshot as PNG")
        raw = bytes(data)
        width = int(representation.pixelsWide())
        height = int(representation.pixelsHigh())
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._screenshot_dir, 0o700)
        except OSError:
            pass
        screenshot_id = uuid.uuid4().hex
        path = self._screenshot_dir / f"desktop-{screen['displayId']}-{screenshot_id}.png"
        path.write_bytes(raw)
        cached = {
            "id": screenshot_id,
            "scope": "desktop",
            "windowKey": None,
            "displayId": int(screen["displayId"]),
            "bounds": dict(bounds),
            "layout": self._layout_fingerprint(layout),
            "imageWidth": width,
            "imageHeight": height,
            "created": time.monotonic(),
            "path": str(path),
        }
        self._screenshot_cache[screenshot_id] = cached
        return {
            "id": screenshot_id,
            "width": width,
            "height": height,
            "displayId": int(screen["displayId"]),
            "backingScaleFactor": float(screen["backingScaleFactor"]),
            "originX": bounds["x"],
            "originY": bounds["y"],
            "path": str(path),
            "mimeType": "image/png",
            "_image_base64": base64.b64encode(raw).decode("ascii"),
        }

    def tool_get_desktop_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._invalidate_all_observations()
        status = self._permission_status()
        if not status["screenRecording"]:
            raise ToolError("Screen Recording permission is not granted. Run request_permissions, grant it, and restart ZCode.")
        layout = self._desktop_layout()
        screenshots = [self._capture_desktop_screen(screen, layout) for screen in layout]
        return {
            "scope": "desktop",
            "bounds": self._bounds_for_layout(layout),
            "displays": layout,
            "screenshots": screenshots,
        }

    def _validate_desktop_screenshot(self, screenshot_id: Any) -> dict[str, Any]:
        cached = self._screenshot_cache.get(str(screenshot_id))
        if cached is None or cached.get("scope") != "desktop":
            raise ToolError("screenshotId is not the latest direct desktop observation; call get_desktop_state")
        if time.monotonic() - float(cached["created"]) > 300:
            raise ToolError("desktop screenshotId is stale; call get_desktop_state again")
        if cached.get("layout") is not None:
            current_layout = self._layout_fingerprint(self._desktop_layout())
            if cached["layout"] != current_layout:
                raise ToolError("The desktop display layout changed after this screenshot; re-observe before acting")
        else:
            # Backward-compatible validation for observations created before
            # the per-display layout contract or by focused unit tests.
            current = self._desktop_bounds()
            if any(abs(float(cached["bounds"][field]) - float(current[field])) > 1 for field in current):
                raise ToolError("The desktop display layout changed after this screenshot; re-observe before acting")
        return cached

    def _desktop_relative_point(self, x: Any, y: Any, screenshot_id: Any) -> tuple[float, float]:
        cached = self._validate_desktop_screenshot(screenshot_id)
        rel_x, rel_y = float(x), float(y)
        width = float(cached["imageWidth"])
        height = float(cached["imageHeight"])
        if not (0 <= rel_x < width and 0 <= rel_y < height):
            raise ToolError(f"Desktop point ({rel_x}, {rel_y}) is outside {width}x{height}; re-observe")
        bounds = cached["bounds"]
        return (
            float(bounds["x"]) + rel_x * float(bounds["width"]) / width,
            float(bounds["y"]) + rel_y * float(bounds["height"]) / height,
        )

    def _activate(self, window: dict[str, Any]) -> None:
        A = self.AppKit
        app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(window["pid"]))
        if app is None:
            raise ToolError("The target app is no longer running")
        options = int(getattr(A, "NSApplicationActivateIgnoringOtherApps", 1 << 1)) | int(
            getattr(A, "NSApplicationActivateAllWindows", 1 << 0)
        )
        activated = app.activateWithOptions_(options)
        if activated is False:
            raise ToolError("macOS refused to activate the target app; re-observe before sending input")
        target_ax_window = self._ax_window(window)
        self._ax_perform(target_ax_window, self._ax_attr("kAXRaiseAction", "AXRaise"))
        workspace = self.AppKit.NSWorkspace.sharedWorkspace()
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            frontmost = workspace.frontmostApplication()
            front_pid = int(frontmost.processIdentifier()) if frontmost is not None else 0
            if front_pid == int(window["pid"]):
                app_element = self.ApplicationServices.AXUIElementCreateApplication(int(window["pid"]))
                focused = self._ax_copy(
                    app_element,
                    self._ax_attr("kAXFocusedWindowAttribute", "AXFocusedWindow"),
                )
                focused_number = self._ax_copy(
                    focused,
                    self._ax_attr("kAXWindowNumberAttribute", "AXWindowNumber"),
                ) if focused is not None else None
                try:
                    focused_matches = int(focused_number) == int(window["id"])
                except (TypeError, ValueError):
                    focused_matches = focused is not None and focused == target_ax_window
                if focused_matches:
                    return
            time.sleep(0.02)
        raise ToolError("The target window did not become frontmost; no input was sent")

    def _activate_current(self, value: Any) -> dict[str, Any]:
        window = self._get_window(value)
        self._activate(window)
        # Activation can switch Spaces, reveal a sheet, or let an app reposition
        # its window. Rehydrate before converting any observed coordinate.
        return self._get_window(window)

    def tool_activate_window(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._get_window(arguments["window"])
        self._activate(window)
        refreshed = self._get_window(window)
        self._invalidate_window_observations(window)
        return {"ok": True, "window": refreshed}

    def _validate_screenshot(
        self, screenshot_id: str | None, window: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not screenshot_id:
            return None
        cached = self._screenshot_cache.get(str(screenshot_id))
        key = self._window_key(window)
        if cached is None or cached["windowKey"] != key:
            raise ToolError("screenshotId is unknown or belongs to another window; re-observe before acting")
        if time.monotonic() - cached["created"] > 300:
            raise ToolError("screenshotId is stale; re-observe before acting")
        for field in ("x", "y", "width", "height"):
            if abs(float(cached["bounds"][field]) - float(window["bounds"][field])) > 1:
                raise ToolError("The target window moved or resized after this screenshot; re-observe before acting")
        return cached

    def _invalidate_window_observations(self, window: dict[str, Any]) -> None:
        key = self._window_key(window)
        self._element_cache.pop(key, None)
        for screenshot_id, cached in list(self._screenshot_cache.items()):
            if cached["windowKey"] == key:
                self._screenshot_cache.pop(screenshot_id, None)
                self._delete_cached_screenshot(cached)

    def _invalidate_all_observations(self) -> None:
        self._element_cache.clear()
        for cached in self._screenshot_cache.values():
            self._delete_cached_screenshot(cached)
        self._screenshot_cache.clear()

    @staticmethod
    def _delete_cached_screenshot(cached: dict[str, Any]) -> None:
        try:
            Path(str(cached.get("path", ""))).unlink(missing_ok=True)
        except OSError:
            pass

    def _relative_point(
        self, window: dict[str, Any], x: Any, y: Any, screenshot_id: str | None = None
    ) -> tuple[float, float]:
        cached = self._validate_screenshot(screenshot_id, window)
        rel_x, rel_y = float(x), float(y)
        bounds = window["bounds"]
        source_width = float(cached["imageWidth"]) if cached else float(bounds["width"])
        source_height = float(cached["imageHeight"]) if cached else float(bounds["height"])
        if source_width <= 0 or source_height <= 0:
            raise ToolError("The latest screenshot has invalid dimensions; re-observe before acting")
        if not (0 <= rel_x < source_width and 0 <= rel_y < source_height):
            raise ToolError(
                f"Window-relative point ({rel_x}, {rel_y}) is outside {source_width}x{source_height}; re-observe"
            )
        return (
            bounds["x"] + rel_x * float(bounds["width"]) / source_width,
            bounds["y"] + rel_y * float(bounds["height"]) / source_height,
        )

    def _cached_element(self, window: dict[str, Any], index: Any) -> Any:
        key = self._window_key(window)
        cached = self._element_cache.get(key)
        if not cached:
            raise ToolError("No Accessibility observation exists for this window; call get_window_state with include_text=true")
        if time.monotonic() - float(cached["created"]) > 300:
            self._element_cache.pop(key, None)
            raise ToolError("The Accessibility observation is stale; call get_window_state again")
        elements = cached["elements"]
        element_index = int(index)
        if element_index < 0 or element_index >= len(elements):
            raise ToolError(f"element_index {element_index} is outside the latest Accessibility tree")
        return elements[element_index]

    def _element_center(self, element: Any) -> tuple[float, float]:
        position = self._point_components(
            self._ax_value(
                self._ax_copy(element, self._ax_attr("kAXPositionAttribute", "AXPosition")),
                self._ax_attr("kAXValueCGPointType", 1),
            )
        )
        size = self._point_components(
            self._ax_value(
                self._ax_copy(element, self._ax_attr("kAXSizeAttribute", "AXSize")),
                self._ax_attr("kAXValueCGSizeType", 2),
            )
        )
        if not position or not size:
            raise ToolError("The Accessibility element has no usable position and size")
        return position[0] + size[0] / 2, position[1] + size[1] / 2

    def _button(self, value: str) -> tuple[Any, Any, Any, Any]:
        Q = self.Quartz
        normalized = value.lower()
        if normalized in {"left", "l"}:
            return Q.kCGMouseButtonLeft, Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGEventLeftMouseDragged
        if normalized in {"right", "r"}:
            return Q.kCGMouseButtonRight, Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGEventRightMouseDragged
        if normalized in {"middle", "m"}:
            return Q.kCGMouseButtonCenter, Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp, Q.kCGEventOtherMouseDragged
        raise ToolError(f"Unsupported mouse button: {value}")

    def _post_mouse(self, event_type: Any, button: Any, x: float, y: float, click_count: int = 1) -> None:
        Q = self.Quartz
        event = Q.CGEventCreateMouseEvent(None, event_type, (float(x), float(y)), button)
        if event is None:
            raise ToolError("macOS could not create a mouse event")
        if click_count > 1:
            Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, click_count)
        Q.CGEventPost(Q.kCGHIDEventTap, event)

    def _click_pointer(
        self,
        button: Any,
        down: Any,
        up: Any,
        dragged: Any,
        x: float,
        y: float,
        count: int,
    ) -> None:
        if button in self._held_buttons:
            raise ToolError("The requested mouse button is already held; release it with mouse_up before clicking")
        for click_number in range(1, count + 1):
            self._post_mouse(down, button, x, y, click_number)
            self._held_buttons[button] = (up, dragged, x, y)
            try:
                self._post_mouse(up, button, x, y, click_number)
            except Exception:
                # Retry release once now; if it still fails, close() retains
                # the registered button and releases it on MCP shutdown.
                try:
                    self._post_mouse(up, button, x, y, click_number)
                except Exception:
                    pass
                else:
                    self._held_buttons.pop(button, None)
                raise
            else:
                self._held_buttons.pop(button, None)
            if click_number < count:
                time.sleep(0.08)

    def tool_click(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        button_name = str(arguments.get("mouse_button", "left"))
        count = int(arguments.get("click_count", 1))
        if not 1 <= count <= 4:
            raise ToolError("click_count must be between 1 and 4")
        if "element_index" in arguments and arguments.get("element_index") is not None:
            element = self._cached_element(window, arguments["element_index"])
            if button_name in {"left", "l"} and count == 1 and self._ax_perform(
                element, self._ax_attr("kAXPressAction", "AXPress")
            ):
                self._invalidate_window_observations(window)
                return {"ok": True, "method": "accessibility", "element_index": int(arguments["element_index"])}
            x, y = self._element_center(element)
        else:
            if arguments.get("x") is None or arguments.get("y") is None:
                raise ToolError("click requires either element_index or both x and y")
            x, y = self._relative_point(window, arguments["x"], arguments["y"], arguments.get("screenshotId"))
        button, down, up, dragged = self._button(button_name)
        self._click_pointer(button, down, up, dragged, x, y, count)
        self._invalidate_window_observations(window)
        return {"ok": True, "method": "coordinate", "screenPoint": {"x": x, "y": y}, "click_count": count}

    def _modifier_flags(self, modifiers: Iterable[str]) -> int:
        Q = self.Quartz
        mapping = {
            "command": Q.kCGEventFlagMaskCommand,
            "control": Q.kCGEventFlagMaskControl,
            "shift": Q.kCGEventFlagMaskShift,
            "option": Q.kCGEventFlagMaskAlternate,
        }
        flags = 0
        for modifier in modifiers:
            flags |= int(mapping[modifier])
        return flags

    def tool_press_key(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        self._send_key(str(arguments["key"]))
        self._invalidate_window_observations(window)
        return {"ok": True, "key": arguments["key"]}

    def _send_key(self, key: str) -> None:
        key_code, modifiers = parse_key_chord(key)
        flags = self._modifier_flags(modifiers)
        Q = self.Quartz
        down = Q.CGEventCreateKeyboardEvent(None, key_code, True)
        up = Q.CGEventCreateKeyboardEvent(None, key_code, False)
        if down is None or up is None:
            raise ToolError("macOS could not create a keyboard event")
        Q.CGEventSetFlags(down, flags)
        Q.CGEventSetFlags(up, flags)
        Q.CGEventPost(Q.kCGHIDEventTap, down)
        Q.CGEventPost(Q.kCGHIDEventTap, up)

    def tool_type_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        text = str(arguments["text"])
        self._send_text(text)
        self._invalidate_window_observations(window)
        return {"ok": True, "characters": len(text)}

    def _send_text(self, text: str) -> None:
        Q = self.Quartz
        for offset in range(0, len(text), 32):
            chunk = text[offset : offset + 32]
            utf16_length = len(chunk.encode("utf-16-le")) // 2
            down = Q.CGEventCreateKeyboardEvent(None, 0, True)
            up = Q.CGEventCreateKeyboardEvent(None, 0, False)
            if down is None or up is None:
                raise ToolError("macOS could not create a Unicode keyboard event")
            Q.CGEventKeyboardSetUnicodeString(down, utf16_length, chunk)
            Q.CGEventKeyboardSetUnicodeString(up, utf16_length, chunk)
            Q.CGEventPost(Q.kCGHIDEventTap, down)
            Q.CGEventPost(Q.kCGHIDEventTap, up)

    def tool_scroll(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        x, y = self._relative_point(window, arguments["x"], arguments["y"], arguments.get("screenshotId"))
        Q = self.Quartz
        event = Q.CGEventCreateScrollWheelEvent(
            None,
            Q.kCGScrollEventUnitPixel,
            2,
            int(round(-float(arguments["scrollY"]))),
            int(round(-float(arguments["scrollX"]))),
        )
        if event is None:
            raise ToolError("macOS could not create a scroll event")
        Q.CGEventSetLocation(event, (x, y))
        Q.CGEventPost(Q.kCGHIDEventTap, event)
        self._invalidate_window_observations(window)
        return {"ok": True, "screenPoint": {"x": x, "y": y}}

    def tool_set_value(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        element = self._cached_element(window, arguments["element_index"])
        value_attr = self._ax_attr("kAXValueAttribute", "AXValue")
        value = str(arguments["value"])
        if self._ax_set(element, value_attr, value):
            self._invalidate_window_observations(window)
            return {"ok": True, "method": "accessibility", "element_index": int(arguments["element_index"])}
        x, y = self._element_center(element)
        button, down, up, dragged = self._button("left")
        self._click_pointer(button, down, up, dragged, x, y, 1)
        self.tool_press_key({"window": window, "key": "Command+a"})
        self.tool_type_text({"window": window, "text": value})
        self._invalidate_window_observations(window)
        return {"ok": True, "method": "focus-select-type", "element_index": int(arguments["element_index"])}

    def tool_drag(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        start = self._relative_point(window, arguments["from_x"], arguments["from_y"], arguments.get("screenshotId"))
        end = self._relative_point(window, arguments["to_x"], arguments["to_y"], arguments.get("screenshotId"))
        duration = max(0.0, min(30.0, float(arguments.get("duration", 0.35))))
        self._drag_pointer(start, end, duration)
        self._invalidate_window_observations(window)
        return {"ok": True, "from": {"x": start[0], "y": start[1]}, "to": {"x": end[0], "y": end[1]}}

    def _drag_pointer(
        self, start: tuple[float, float], end: tuple[float, float], duration: float
    ) -> None:
        button, down, up, dragged = self._button("left")
        if button in self._held_buttons:
            raise ToolError("The left mouse button is already held; release it with mouse_up before dragging")
        self._post_mouse(down, button, *start)
        self._held_buttons[button] = (up, dragged, start[0], start[1])
        steps = max(1, min(300, int(duration * 60)))
        delay = duration / steps if steps else 0
        last = start
        try:
            for step in range(1, steps + 1):
                fraction = step / steps
                last = (
                    start[0] + (end[0] - start[0]) * fraction,
                    start[1] + (end[1] - start[1]) * fraction,
                )
                self._post_mouse(dragged, button, *last)
                self._held_buttons[button] = (up, dragged, last[0], last[1])
                if delay:
                    time.sleep(delay)
        except BaseException:
            # A failed/interrupted drag must not leave the physical button held.
            try:
                self._post_mouse(up, button, *last)
            except Exception:
                pass
            else:
                self._held_buttons.pop(button, None)
            raise
        try:
            self._post_mouse(up, button, *end)
        except Exception:
            # Retain the held state so close() can retry the release.
            raise
        else:
            self._held_buttons.pop(button, None)

    def tool_perform_secondary_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        element = self._cached_element(window, arguments["element_index"])
        requested = re.sub(r"[\s_-]+", "", str(arguments["action"]).lower().removeprefix("ax"))
        actions = self._ax_actions(element)
        matched = next(
            (
                action
                for action in actions
                if re.sub(r"[\s_-]+", "", action.lower().removeprefix("ax")) == requested
            ),
            None,
        )
        if matched is None:
            raise ToolError(f"Action {arguments['action']!r} is unavailable; supported actions: {actions}")
        if not self._ax_perform(element, matched):
            raise ToolError(f"Accessibility action failed: {matched}")
        self._invalidate_window_observations(window)
        return {"ok": True, "action": matched, "element_index": int(arguments["element_index"])}

    def tool_desktop_click(self, arguments: dict[str, Any]) -> dict[str, Any]:
        x, y = self._desktop_relative_point(arguments["x"], arguments["y"], arguments["screenshotId"])
        button_name = str(arguments.get("mouse_button", "left"))
        count = int(arguments.get("click_count", 1))
        if not 1 <= count <= 4:
            raise ToolError("click_count must be between 1 and 4")
        button, down, up, dragged = self._button(button_name)
        self._click_pointer(button, down, up, dragged, x, y, count)
        self._invalidate_all_observations()
        return {"ok": True, "screenPoint": {"x": x, "y": y}, "click_count": count}

    def tool_desktop_press_key(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_desktop_screenshot(arguments["screenshotId"])
        self._send_key(str(arguments["key"]))
        self._invalidate_all_observations()
        return {"ok": True, "key": arguments["key"]}

    def tool_desktop_type_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_desktop_screenshot(arguments["screenshotId"])
        text = str(arguments["text"])
        self._send_text(text)
        self._invalidate_all_observations()
        return {"ok": True, "characters": len(text)}

    def tool_desktop_scroll(self, arguments: dict[str, Any]) -> dict[str, Any]:
        x, y = self._desktop_relative_point(arguments["x"], arguments["y"], arguments["screenshotId"])
        Q = self.Quartz
        event = Q.CGEventCreateScrollWheelEvent(
            None,
            Q.kCGScrollEventUnitPixel,
            2,
            int(round(-float(arguments["scrollY"]))),
            int(round(-float(arguments["scrollX"]))),
        )
        if event is None:
            raise ToolError("macOS could not create a desktop scroll event")
        Q.CGEventSetLocation(event, (x, y))
        Q.CGEventPost(Q.kCGHIDEventTap, event)
        self._invalidate_all_observations()
        return {"ok": True, "screenPoint": {"x": x, "y": y}}

    def tool_desktop_drag(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start = self._desktop_relative_point(
            arguments["from_x"], arguments["from_y"], arguments["screenshotId"]
        )
        end = self._desktop_relative_point(
            arguments["to_x"], arguments["to_y"], arguments["screenshotId"]
        )
        duration = max(0.0, min(30.0, float(arguments.get("duration", 0.35))))
        self._drag_pointer(start, end, duration)
        self._invalidate_all_observations()
        return {"ok": True, "from": {"x": start[0], "y": start[1]}, "to": {"x": end[0], "y": end[1]}}

    def _cursor(self) -> tuple[float, float]:
        Q = self.Quartz
        event = Q.CGEventCreate(None)
        if event is None:
            raise ToolError("macOS could not read the current cursor position")
        point = Q.CGEventGetLocation(event)
        return float(point.x), float(point.y)

    def _optional_point(self, arguments: dict[str, Any]) -> tuple[float, float]:
        has_x = arguments.get("x") is not None
        has_y = arguments.get("y") is not None
        if has_x != has_y:
            raise ToolError("x and y must be supplied together")
        if not has_x:
            if arguments.get("window") is not None or arguments.get("screenshotId") is not None:
                raise ToolError("window and screenshotId require both x and y")
            return self._cursor()
        if arguments.get("window") is not None:
            if not arguments.get("screenshotId"):
                raise ToolError("Window-relative pointer input requires a fresh screenshotId")
            window = self._get_window(arguments["window"])
            return self._relative_point(
                window, arguments["x"], arguments["y"], arguments.get("screenshotId")
            )
        if arguments.get("screenshotId") is not None:
            return self._desktop_relative_point(
                arguments["x"], arguments["y"], arguments["screenshotId"]
            )
        return float(arguments["x"]), float(arguments["y"])

    def tool_move_mouse(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._optional_point(arguments)
        duration = max(0.0, min(30.0, float(arguments.get("duration", 0))))
        start = self._cursor()
        steps = max(1, min(300, int(duration * 60)))
        delay = duration / steps if steps else 0
        Q = self.Quartz
        if self._held_buttons:
            routes = list(self._held_buttons.items())
        else:
            routes = [
                (
                    Q.kCGMouseButtonLeft,
                    (None, Q.kCGEventMouseMoved, start[0], start[1]),
                )
            ]
        for step in range(1, steps + 1):
            fraction = step / steps
            x = start[0] + (target[0] - start[0]) * fraction
            y = start[1] + (target[1] - start[1]) * fraction
            for held_button, (held_up, event_type, _held_x, _held_y) in routes:
                self._post_mouse(event_type, held_button, x, y)
                if held_up is not None:
                    self._held_buttons[held_button] = (held_up, event_type, x, y)
            if delay:
                time.sleep(delay)
        self._invalidate_all_observations()
        return {"ok": True, "position": {"x": target[0], "y": target[1]}}

    def tool_mouse_down(self, arguments: dict[str, Any]) -> dict[str, Any]:
        point = self._optional_point(arguments)
        button, down, up, dragged = self._button(str(arguments.get("mouse_button", "left")))
        if button in self._held_buttons:
            raise ToolError("The requested mouse button is already held; call mouse_up before mouse_down again")
        self._post_mouse(down, button, *point)
        self._held_buttons[button] = (up, dragged, point[0], point[1])
        self._invalidate_all_observations()
        return {"ok": True, "position": {"x": point[0], "y": point[1]}}

    def tool_mouse_up(self, arguments: dict[str, Any]) -> dict[str, Any]:
        point = self._optional_point(arguments)
        button, _down, up, _dragged = self._button(str(arguments.get("mouse_button", "left")))
        self._post_mouse(up, button, *point)
        self._held_buttons.pop(button, None)
        self._invalidate_all_observations()
        return {"ok": True, "position": {"x": point[0], "y": point[1]}}

    def tool_get_cursor_position(self, arguments: dict[str, Any]) -> dict[str, Any]:
        x, y = self._cursor()
        return {"x": x, "y": y}

    def tool_clipboard_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        A = self.AppKit
        pasteboard = A.NSPasteboard.generalPasteboard()
        value = pasteboard.stringForType_(A.NSPasteboardTypeString)
        return {"text": str(value) if value is not None else ""}

    def tool_clipboard_set(self, arguments: dict[str, Any]) -> dict[str, Any]:
        A = self.AppKit
        pasteboard = A.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        ok = bool(pasteboard.setString_forType_(str(arguments["text"]), A.NSPasteboardTypeString))
        if not ok:
            raise ToolError("macOS rejected the clipboard update")
        return {"ok": True, "characters": len(str(arguments["text"]))}
