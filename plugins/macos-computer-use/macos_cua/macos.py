"""Native macOS implementation backed by Quartz, AppKit, and Accessibility."""

from __future__ import annotations

import base64
import ctypes
from importlib import metadata
import json
import math
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
DEFAULT_AX_TREE_MAX_NODES = 1200
DEFAULT_AX_TREE_MAX_DEPTH = 64
MAX_AX_TREE_MAX_NODES = 10_000
MAX_AX_TREE_MAX_DEPTH = 256
MAX_SCREENSHOT_PNG_BYTES = 900_000
MAX_SCREENSHOT_DIMENSION = 1280
MIN_SCREENSHOT_SCALE = 0.25
SCREENSHOT_RESIZE_STEP = 0.85
SCREENSHOT_RESIZE_TIMEOUT_SECONDS = 5
MODIFIER_KEY_ORDER = ("command", "control", "option", "shift")
CLICK_EVENT_SETTLE_SECONDS = 0.03
MULTICLICK_ADDITIONAL_GAP_SECONDS = 0.05
TEXT_CHUNK_SETTLE_SECONDS = 0.02
KEY_CHORD_SETTLE_SECONDS = 0.1
SCROLL_SETTLE_SECONDS = 0.1
MAX_KEYBOARD_UNICODE_CHUNK_UNITS = 64
SHUTDOWN_RELEASE_ATTEMPTS = 3
SHUTDOWN_RELEASE_RETRY_SECONDS = 0.01


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
NUMERIC_PAD_KEY_CODES = frozenset(
    code for name, code in KEY_CODES.items() if name.startswith("kp_")
)

KEY_ALIASES: dict[str, str] = {
    "enter": "return", "esc": "escape", "backspace": "delete", "back_space": "delete",
    "del": "forwarddelete", "forward_delete": "forwarddelete", "insert": "help",
    "spacebar": "space", "prior": "pageup", "page_up": "pageup",
    "next": "pagedown", "page_down": "pagedown", "caps_lock": "capslock",
    "arrowleft": "left", "arrowright": "right", "arrowup": "up", "arrowdown": "down",
    "period": ".", "comma": ",", "slash": "/", "backslash": "\\", "minus": "-",
    "hyphen": "-", "equal": "=", "equals": "=", "semicolon": ";", "apostrophe": "'",
    "quote": "'", "grave": "`", "backtick": "`", "leftbracket": "[", "rightbracket": "]",
    "numpad_0": "kp_0", "numpad_1": "kp_1", "numpad_2": "kp_2", "numpad_3": "kp_3",
    "numpad_4": "kp_4", "numpad_5": "kp_5", "numpad_6": "kp_6", "numpad_7": "kp_7",
    "numpad_8": "kp_8", "numpad_9": "kp_9", "numpad_add": "kp_add",
    "numpad_subtract": "kp_subtract", "numpad_multiply": "kp_multiply",
    "numpad_divide": "kp_divide", "numpad_decimal": "kp_decimal", "numpad_enter": "kp_enter",
    "numpad_equal": "kp_equals", "numpad_equals": "kp_equals", "numpad_clear": "kp_clear",
    "kp_equal": "kp_equals", "kp_delete": "kp_decimal", "kp_home": "home",
    "kp_left": "left", "kp_up": "up", "kp_right": "right", "kp_down": "down",
    "kp_prior": "pageup", "kp_page_up": "pageup", "kp_next": "pagedown",
    "kp_page_down": "pagedown", "kp_end": "end", "kp_insert": "help",
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


def unicode_text_chunks(
    text: str,
    max_utf16_units: int = MAX_KEYBOARD_UNICODE_CHUNK_UNITS,
) -> Iterable[str]:
    """Pack text without splitting a Unicode code point across key events."""
    if max_utf16_units <= 0:
        raise ValueError("max_utf16_units must be positive")
    start = 0
    current_units = 0
    for index, character in enumerate(text):
        character_units = 2 if ord(character) > 0xFFFF else 1
        if character_units > max_utf16_units:
            raise ValueError("max_utf16_units is too small for one Unicode code point")
        if current_units and current_units + character_units > max_utf16_units:
            yield text[start:index]
            start = index
            current_units = 0
        current_units += character_units
    if start < len(text):
        yield text[start:]


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
        self._held_key_releases: list[Any] = []
        self._hid_mouse_event_source: Any | None = None
        self._skylight_library: Any | None = None
        self._skylight_load_attempted = False
        self._skylight_front_target: tuple[int, int, bytes] | None = None
        self._skylight_last_status = "not-attempted"
        self._cleanup_orphaned_screenshots()

    def _cleanup_orphaned_screenshots(self) -> None:
        # Cleanup must never follow a substituted temp-directory symlink or
        # touch a directory owned by another account.
        if self._screenshot_dir.is_symlink() or not self._screenshot_dir.is_dir():
            return
        try:
            if hasattr(os, "getuid") and self._screenshot_dir.stat().st_uid != os.getuid():
                return
        except OSError:
            return
        cutoff = time.time() - 24 * 60 * 60
        for path in self._screenshot_dir.glob("*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _ensure_screenshot_dir(self) -> None:
        if self._screenshot_dir.is_symlink() or (
            self._screenshot_dir.exists() and not self._screenshot_dir.is_dir()
        ):
            raise ToolError(f"Refusing unsafe screenshot directory: {self._screenshot_dir}")
        try:
            self._screenshot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if hasattr(os, "getuid") and self._screenshot_dir.stat().st_uid != os.getuid():
                raise ToolError(f"Screenshot directory is not owned by the current user: {self._screenshot_dir}")
            os.chmod(self._screenshot_dir, 0o700)
        except ToolError:
            raise
        except OSError as error:
            raise ToolError(f"Could not prepare the private screenshot directory: {error}") from error

    def close(self) -> None:
        for button, (up, _dragged, x, y) in list(self._held_buttons.items()):
            for attempt in range(SHUTDOWN_RELEASE_ATTEMPTS):
                try:
                    self._post_mouse(up, button, x, y)
                except Exception:
                    if attempt + 1 < SHUTDOWN_RELEASE_ATTEMPTS:
                        time.sleep(SHUTDOWN_RELEASE_RETRY_SECONDS)
                else:
                    break
        self._held_buttons.clear()
        if self.Quartz is not None:
            for up in self._held_key_releases:
                for attempt in range(SHUTDOWN_RELEASE_ATTEMPTS):
                    try:
                        self.Quartz.CGEventPost(self.Quartz.kCGHIDEventTap, up)
                    except Exception:
                        if attempt + 1 < SHUTDOWN_RELEASE_ATTEMPTS:
                            time.sleep(SHUTDOWN_RELEASE_RETRY_SECONDS)
                    else:
                        break
        self._held_key_releases.clear()
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
        pixel_ready = bool(native and permissions["screenRecording"])
        full_ready = bool(ax_ready and pixel_ready)
        return {
            "ok": ax_ready,
            "platform": "darwin",
            "nativeDependencies": native,
            "nativeError": self.native_error,
            "pyobjcVersions": self.native_versions,
            **permissions,
            "axControlReady": ax_ready,
            "inputControlReady": ax_ready,
            "pixelObservationReady": pixel_ready,
            "desktopObservationReady": pixel_ready,
            "fullComputerUseReady": full_ready,
            "localOnly": True,
            "extraConfirmationLayer": False,
            "message": (
                "Ready for AX and pixel-based live macOS control."
                if full_ready
                else "Ready for AX-only control; grant Screen Recording only when screenshots or pixels are needed."
                if ax_ready
                else "Ready for screenshot-only observation; grant Accessibility for mouse and keyboard input."
                if pixel_ready
                else "Native runtime ready; grant Accessibility for input and Screen Recording for pixels."
                if native
                else "Install native dependencies, then grant the required macOS permissions."
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
        # Native consent prompts and System Settings can change focus, Spaces,
        # and visible layout before this call returns or even when it raises.
        self._invalidate_all_observations()
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
            resolved_app_path = str(Path(app).expanduser().resolve())
            command = ["/usr/bin/open", resolved_app_path]
        elif re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", app):
            resolved_app_path = None
            command = ["/usr/bin/open", "-b", app]
        else:
            resolved_app_path = None
            command = ["/usr/bin/open", "-a", app]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            raise ToolError(
                f"The launch request for {app!r} timed out; the app may still have opened",
                {
                    "ok": False,
                    "code": "launch_timeout",
                    "effect": "unverifiable",
                    "verified": False,
                    "app": app,
                },
            ) from error
        finally:
            # `open` may alter focus or finish asynchronously even when it
            # returns an error/timeout. No prior desktop/window image is safe.
            self._installed_cache = None
            self._invalidate_all_observations()
        if completed.returncode != 0:
            raise ToolError(completed.stderr.strip() or f"Failed to launch {app}")

        target_bundle_id: str | None = None
        target_path: str | None = None
        target_name: str | None = None
        if app.lower().endswith(".app") or "/" in app:
            assert resolved_app_path is not None
            target_path = resolved_app_path
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
            raise ToolError(
                f"macOS accepted the launch request for {app!r}, but no matching running app appeared",
                {
                    "ok": False,
                    "code": "launch_not_observed",
                    "effect": "unverifiable",
                    "verified": False,
                    "app": app,
                },
            )
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

    def _ax_is_settable(self, element: Any, attribute: Any) -> bool | None:
        try:
            result = self.ApplicationServices.AXUIElementIsAttributeSettable(
                element, attribute, None
            )
        except Exception:
            return None
        if isinstance(result, tuple) and len(result) == 2:
            error, settable = result
            try:
                return bool(settable) if int(error) == 0 else None
            except (TypeError, ValueError):
                return None
        return bool(result) if isinstance(result, bool) else None

    @staticmethod
    def _ax_equal(left: Any, right: Any) -> bool:
        if left is right:
            return True
        try:
            return bool(left == right)
        except Exception:
            return False

    def _ax_array(self, element: Any, attribute: Any) -> list[Any]:
        value = self._ax_copy(element, attribute)
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple)) else [value]

    def _ax_children(self, element: Any) -> list[Any]:
        role = str(
            self._ax_copy(element, self._ax_attr("kAXRoleAttribute", "AXRole"))
            or ""
        )
        rows_attr = self._ax_attr("kAXRowsAttribute", "AXRows")
        visible_attr = "AXVisibleChildren"
        rows = self._ax_array(element, rows_attr)
        visible = self._ax_array(element, visible_attr)
        attributes: list[Any] = []
        row_primary_roles = {"AXOutline", "AXList", "AXTable", "AXBrowser"}
        if not (rows and role in row_primary_roles) and not (
            visible and role == "AXList"
        ):
            attributes.append(self._ax_attr("kAXChildrenAttribute", "AXChildren"))
        attributes.extend((rows_attr, "AXContents", visible_attr))

        children: list[Any] = []
        for attribute in attributes:
            if attribute == rows_attr:
                values = rows
            elif attribute == visible_attr:
                values = visible
            else:
                values = self._ax_array(element, attribute)
            for child in values:
                if child is None or any(
                    self._ax_equal(child, existing) for existing in children
                ):
                    continue
                children.append(child)
        return children

    def _enable_best_effort_accessibility_modes(self, app_element: Any) -> None:
        # Chromium/Electron can withhold descendants until one of these app-level
        # Accessibility modes is enabled. Unsupported attributes fail harmlessly.
        self._ax_set(app_element, "AXManualAccessibility", True)
        self._ax_set(app_element, "AXEnhancedUserInterface", True)

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
        subrole = self._short(self._ax_copy(element, self._ax_attr("kAXSubroleAttribute", "AXSubrole")))
        title = self._short(self._ax_copy(element, self._ax_attr("kAXTitleAttribute", "AXTitle")))
        description = self._short(self._ax_copy(element, self._ax_attr("kAXDescriptionAttribute", "AXDescription")))
        help_text = self._short(self._ax_copy(element, self._ax_attr("kAXHelpAttribute", "AXHelp")))
        raw_value = self._ax_copy(element, self._ax_attr("kAXValueAttribute", "AXValue"))
        value = self._short(raw_value)
        placeholder = self._short(
            self._ax_copy(element, self._ax_attr("kAXPlaceholderValueAttribute", "AXPlaceholderValue"))
        )
        identifier = self._short(
            self._ax_copy(element, self._ax_attr("kAXIdentifierAttribute", "AXIdentifier"))
        )
        parts = [f"[{index}]", role]
        if subrole and subrole != role:
            parts.append(f"subrole={json.dumps(subrole, ensure_ascii=False)}")
        label = title or description
        if label:
            parts.append(json.dumps(label, ensure_ascii=False))
        if title and description and description != title:
            parts.append(f"description={json.dumps(description, ensure_ascii=False)}")
        if help_text and help_text not in {label, description}:
            parts.append(f"help={json.dumps(help_text, ensure_ascii=False)}")
        if value and value != label:
            parts.append(f"value={json.dumps(value, ensure_ascii=False)}")
        if placeholder and placeholder not in {label, description, value}:
            parts.append(f"placeholder={json.dumps(placeholder, ensure_ascii=False)}")
        if identifier and identifier not in {label, description, value, placeholder}:
            parts.append(f"identifier={json.dumps(identifier, ensure_ascii=False)}")

        traits: list[str] = []
        selected = self._ax_copy(element, self._ax_attr("kAXSelectedAttribute", "AXSelected"))
        expanded = self._ax_copy(element, self._ax_attr("kAXExpandedAttribute", "AXExpanded"))
        enabled = self._ax_copy(element, self._ax_attr("kAXEnabledAttribute", "AXEnabled"))
        if selected is not None and bool(selected):
            traits.append("selected")
        if expanded is not None and bool(expanded):
            traits.append("expanded")
        if enabled is not None and not bool(enabled):
            traits.append("disabled")
        settable = self._ax_is_settable(
            element, self._ax_attr("kAXValueAttribute", "AXValue")
        )
        if settable is True:
            traits.append("settable")
            if isinstance(raw_value, bool):
                traits.append("boolean")
            elif isinstance(raw_value, str):
                traits.append("string")
            elif isinstance(raw_value, (int, float)):
                traits.append("float")
        if traits:
            parts.append("traits=" + ",".join(traits))
        actions = [action.removeprefix("AX") for action in self._ax_actions(element)]
        if actions:
            parts.append("actions=" + ",".join(actions[:8]))
        return "  " * depth + " ".join(parts)

    def _accessibility_state(
        self, window: dict[str, Any], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        options = options or {}
        max_nodes = min(
            MAX_AX_TREE_MAX_NODES,
            int(options.get("max_tree_nodes", DEFAULT_AX_TREE_MAX_NODES)),
        )
        max_depth = min(
            MAX_AX_TREE_MAX_DEPTH,
            int(options.get("max_tree_depth", DEFAULT_AX_TREE_MAX_DEPTH)),
        )
        AX = self.ApplicationServices
        app_element = AX.AXUIElementCreateApplication(int(window["pid"]))
        self._enable_best_effort_accessibility_modes(app_element)
        root = self._ax_window(window)
        elements: list[Any] = []
        lines: list[str] = []
        seen: dict[int, list[Any]] = {}
        truncated = False
        truncation_reasons: set[str] = set()

        def walk(element: Any, depth: int) -> None:
            nonlocal truncated
            if depth >= max_depth:
                truncated = True
                truncation_reasons.add("max_tree_depth")
                return
            if len(elements) >= max_nodes:
                truncated = True
                truncation_reasons.add("max_tree_nodes")
                return
            try:
                identity = hash(element)
            except Exception:
                # Keep unhashable wrappers in one equality-checked bucket so a
                # native AX cycle cannot evade detection through fresh proxies.
                identity = 0
            bucket = seen.setdefault(identity, [])
            if any(self._ax_equal(element, existing) for existing in bucket):
                return
            bucket.append(element)
            index = len(elements)
            elements.append(element)
            lines.append(self._format_element(element, index, depth))
            for child in self._ax_children(element):
                walk(child, depth + 1)

        walk(root, 0)
        menu_bar = self._ax_copy(
            app_element, self._ax_attr("kAXMenuBarAttribute", "AXMenuBar")
        )
        if menu_bar is not None and not self._ax_equal(menu_bar, root):
            walk(menu_bar, 0)
        key = self._window_key(window)
        generation = uuid.uuid4().hex
        self._element_cache[key] = {"generation": generation, "elements": elements, "created": time.monotonic()}

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
            if len(selected_elements) >= 64:
                truncated = True
                truncation_reasons.add("selected_elements")
                break
            for attr_name, fallback in (
                ("kAXSelectedChildrenAttribute", "AXSelectedChildren"),
                ("kAXSelectedRowsAttribute", "AXSelectedRows"),
                ("kAXSelectedCellsAttribute", "AXSelectedCells"),
            ):
                if len(selected_elements) >= 64:
                    truncated = True
                    truncation_reasons.add("selected_elements")
                    break
                selected = self._ax_copy(container, self._ax_attr(attr_name, fallback)) or []
                if not isinstance(selected, (list, tuple)):
                    selected = [selected]
                for item in selected:
                    if len(selected_elements) >= 64:
                        truncated = True
                        truncation_reasons.add("selected_elements")
                        break
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
        self._prune_element_cache(key)
        return {
            "tree": "\n".join(lines),
            "focused_element": focused_line,
            "selected_text": selected_text,
            "selected_elements": selected_elements,
            "document_text": document_text,
            "generation": generation,
            "truncated": truncated,
            "truncation_reasons": sorted(truncation_reasons),
            "tree_limits": {
                "max_tree_nodes": max_nodes,
                "max_tree_depth": max_depth,
                "rendered_nodes": len(lines),
            },
        }

    def _capture_window(self, window: dict[str, Any]) -> dict[str, Any]:
        status = self._permission_status()
        if not status["screenRecording"]:
            raise ToolError("Screen Recording permission is not granted. Run request_permissions, grant it, and restart ZCode.")
        self._ensure_screenshot_dir()
        screenshot_id = uuid.uuid4().hex
        path = self._screenshot_dir / f"{window['id']}-{screenshot_id}.png"
        executable = shutil.which("screencapture") or "/usr/sbin/screencapture"
        published = False
        try:
            completed = subprocess.run(
                [executable, "-x", "-o", "-l", str(window["id"]), str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0 or not path.is_file():
                raise ToolError(completed.stderr.strip() or "macOS could not capture the selected window")
            raw, width, height = self._bounded_screenshot_png(
                path,
                int(round(window["bounds"]["width"])),
                int(round(window["bounds"]["height"])),
            )
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
            published = True
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
        except subprocess.TimeoutExpired as error:
            raise ToolError("macOS window capture timed out after 30 seconds; re-observe before retrying") from error
        except ToolError:
            raise
        except OSError as error:
            raise ToolError(f"macOS could not read or publish the window screenshot: {error}") from error
        finally:
            if not published:
                self._screenshot_cache.pop(screenshot_id, None)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _decoded_png_dimensions(self, path: Path) -> tuple[int, int] | None:
        try:
            image_rep = self.AppKit.NSBitmapImageRep.imageRepWithContentsOfFile_(str(path))
            if image_rep is not None:
                width = int(image_rep.pixelsWide())
                height = int(image_rep.pixelsHigh())
                if width > 0 and height > 0:
                    return width, height
        except Exception:
            pass
        return None

    def _png_dimensions(
        self, path: Path, fallback_width: int, fallback_height: int
    ) -> tuple[int, int]:
        return self._decoded_png_dimensions(path) or (
            max(1, int(fallback_width)),
            max(1, int(fallback_height)),
        )

    def _bounded_screenshot_png(
        self, path: Path, fallback_width: int, fallback_height: int
    ) -> tuple[bytes, int, int]:
        """Best-effort bound an MCP screenshot while retaining exact pixel mapping."""
        original = path.read_bytes()
        width, height = self._png_dimensions(path, fallback_width, fallback_height)
        largest = max(width, height)
        if largest <= MAX_SCREENSHOT_DIMENSION and len(original) <= MAX_SCREENSHOT_PNG_BYTES:
            return original, width, height

        # Keep at least one quarter of an ordinary source image, except that a
        # very large Retina/6K capture must still honor the absolute 1280 px
        # transport edge. This avoids the reference implementation's >5K edge
        # case where its relative floor can accidentally return the original.
        minimum_largest = max(
            1,
            min(MAX_SCREENSHOT_DIMENSION, int(round(largest * MIN_SCREENSHOT_SCALE))),
        )
        if largest > MAX_SCREENSHOT_DIMENSION:
            target_largest = MAX_SCREENSHOT_DIMENSION
        else:
            target_largest = max(minimum_largest, int(largest * SCREENSHOT_RESIZE_STEP))

        best = (original, width, height)
        resized = False
        candidate = path.with_name(f".{path.stem}-{uuid.uuid4().hex}.png")
        executable = shutil.which("sips") or "/usr/bin/sips"
        try:
            while target_largest < max(best[1], best[2]):
                candidate.unlink(missing_ok=True)
                completed = subprocess.run(
                    [
                        executable,
                        "--resampleHeightWidthMax",
                        str(target_largest),
                        str(path),
                        "--out",
                        str(candidate),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=SCREENSHOT_RESIZE_TIMEOUT_SECONDS,
                )
                if completed.returncode != 0 or not candidate.is_file():
                    break
                candidate_raw = candidate.read_bytes()
                candidate_dimensions = self._decoded_png_dimensions(candidate)
                if candidate_dimensions is None:
                    break
                candidate_width, candidate_height = candidate_dimensions
                candidate_largest = max(candidate_width, candidate_height)
                if candidate_largest >= max(best[1], best[2]):
                    break
                best = (candidate_raw, candidate_width, candidate_height)
                resized = True
                if len(candidate_raw) <= MAX_SCREENSHOT_PNG_BYTES:
                    break
                if candidate_largest <= minimum_largest:
                    break
                next_target = max(
                    minimum_largest,
                    int(candidate_largest * SCREENSHOT_RESIZE_STEP),
                )
                if next_target >= candidate_largest:
                    break
                target_largest = next_target

            if resized:
                # Publish the selected bytes atomically. If this replacement
                # fails, the original capture remains available to the caller.
                candidate.write_bytes(best[0])
                os.replace(candidate, path)
            return best
        except (OSError, subprocess.TimeoutExpired):
            return original, width, height
        finally:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

    def tool_get_window_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._get_window(arguments["window"])
        self._invalidate_window_observations(window)
        include_screenshot = bool(arguments.get("include_screenshot", True))
        include_text = bool(arguments.get("include_text", False))
        try:
            screenshots = [self._capture_window(window)] if include_screenshot else []
            accessibility = self._accessibility_state(window, arguments) if include_text else None
            return {"window": window, "screenshots": screenshots, "accessibility": accessibility}
        except Exception:
            # Never retain a screenshot or AX generation that the caller did
            # not receive as one complete observation.
            self._invalidate_window_observations(window)
            raise

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
        self._ensure_screenshot_dir()
        screenshot_id = uuid.uuid4().hex
        path = self._screenshot_dir / f"desktop-{screen['displayId']}-{screenshot_id}.png"
        try:
            path.write_bytes(raw)
            raw, width, height = self._bounded_screenshot_png(path, width, height)
        except OSError as error:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ToolError(f"macOS could not publish the desktop screenshot: {error}") from error
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
        try:
            screenshots = [self._capture_desktop_screen(screen, layout) for screen in layout]
            return {
                "scope": "desktop",
                "bounds": self._bounds_for_layout(layout),
                "displays": layout,
                "screenshots": screenshots,
            }
        except Exception:
            # A multi-display state is one observation. Roll back earlier
            # screens when any later screen cannot be captured or encoded.
            self._invalidate_all_observations()
            raise

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

    def _load_skylight(self) -> Any | None:
        """Resolve the exact-window foreground SPI used by Cua Driver."""
        if self._skylight_load_attempted:
            return self._skylight_library
        self._skylight_load_attempted = True
        try:
            self._skylight_library = ctypes.CDLL(
                "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
                mode=getattr(ctypes, "RTLD_GLOBAL", 0) | getattr(os, "RTLD_LAZY", 1),
            )
        except (AttributeError, OSError):
            self._skylight_library = None
        return self._skylight_library

    def _skylight_set_front_process(self, pid: int, window_id: int) -> bool:
        """Ask WindowServer to front one exact process/window, when available."""
        self._skylight_front_target = None
        library = self._load_skylight()
        if library is None:
            self._skylight_last_status = "framework-unavailable"
            return False
        try:
            connection_id = library.CGSMainConnectionID
            get_window_owner = library.SLSGetWindowOwner
            get_connection_psn = library.SLSGetConnectionPSN
            set_front = library.SLPSSetFrontProcessWithOptions
            connection_id.argtypes = []
            connection_id.restype = ctypes.c_uint32
            get_window_owner.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            get_window_owner.restype = ctypes.c_int32
            get_connection_psn.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            get_connection_psn.restype = ctypes.c_int32
            set_front.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
            set_front.restype = ctypes.c_int32

            owner_connection = ctypes.c_uint32()
            owner_status = get_window_owner(
                connection_id(), ctypes.c_uint32(window_id), ctypes.byref(owner_connection)
            )
            if owner_status != 0 or owner_connection.value == 0:
                self._skylight_last_status = (
                    f"window-owner-rejected:{owner_status}:{owner_connection.value}"
                )
                return False
            process_serial_number = (ctypes.c_ubyte * 8)()
            psn_status = get_connection_psn(
                owner_connection.value, ctypes.cast(process_serial_number, ctypes.c_void_p)
            )
            if psn_status != 0:
                self._skylight_last_status = f"connection-psn-rejected:{psn_status}"
                return False
            # kCPSNoWindows keeps the request scoped to the supplied window id
            # instead of broadly raising every window owned by the app.
            front_status = set_front(
                ctypes.cast(process_serial_number, ctypes.c_void_p),
                ctypes.c_uint32(window_id),
                ctypes.c_uint32(0x400),
            )
            if front_status != 0:
                self._skylight_last_status = f"set-front-rejected:{front_status}"
                return False
            self._skylight_front_target = (
                int(pid), int(window_id), bytes(process_serial_number)
            )
            self._skylight_last_status = "set-front-accepted"
            return True
        except (AttributeError, OSError, TypeError, ValueError) as error:
            self._skylight_last_status = f"spi-unavailable:{type(error).__name__}"
            return False

    def _skylight_front_process_matches(self, pid: int, window_id: int) -> bool:
        target = self._skylight_front_target
        if target is None or target[:2] != (int(pid), int(window_id)):
            return False
        library = self._load_skylight()
        if library is None:
            return False
        try:
            get_front_process = library._SLPSGetFrontProcess
            get_front_process.argtypes = [ctypes.c_void_p]
            get_front_process.restype = ctypes.c_int32
            current = (ctypes.c_ubyte * 8)()
            status = get_front_process(ctypes.cast(current, ctypes.c_void_p))
            if status != 0:
                self._skylight_last_status = f"front-readback-rejected:{status}"
                return False
            matches = bytes(current) == target[2]
            self._skylight_last_status = (
                "front-readback-matched" if matches else "front-readback-mismatched"
            )
            return matches
        except (AttributeError, OSError, TypeError, ValueError) as error:
            self._skylight_last_status = f"front-readback-unavailable:{type(error).__name__}"
            return False

    def _activate(self, window: dict[str, Any]) -> None:
        A = self.AppKit
        app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(window["pid"]))
        if app is None:
            raise ToolError("The target app is no longer running")
        skylight_activated = self._skylight_set_front_process(
            int(window["pid"]), int(window["id"])
        )
        if not skylight_activated:
            options = int(getattr(A, "NSApplicationActivateIgnoringOtherApps", 1 << 1)) | int(
                getattr(A, "NSApplicationActivateAllWindows", 1 << 0)
            )
            activated = app.activateWithOptions_(options)
            if activated is False:
                raise ToolError("macOS refused to activate the target app; re-observe before sending input")
        app_element = self.ApplicationServices.AXUIElementCreateApplication(int(window["pid"]))
        # AppKit activation is asynchronous and can be ignored by a process
        # that was launched outside a normal app bundle. A trusted AX client
        # can independently request the same exact pid to become frontmost.
        self._ax_set(
            app_element,
            self._ax_attr("kAXFrontmostAttribute", "AXFrontmost"),
            True,
        )
        target_ax_window = self._ax_window(window)
        raised = self._ax_perform(
            target_ax_window, self._ax_attr("kAXRaiseAction", "AXRaise")
        )
        if not raised:
            made_main = self._ax_set(
                target_ax_window,
                self._ax_attr("kAXMainAttribute", "AXMain"),
                True,
            )
            if not made_main:
                self._ax_set(
                    target_ax_window,
                    self._ax_attr("kAXFocusedAttribute", "AXFocused"),
                    True,
                )
        workspace = self.AppKit.NSWorkspace.sharedWorkspace()
        deadline = time.monotonic() + 2.0
        last_front_pid = 0
        last_skylight_front = False
        last_ax_frontmost: Any = None
        last_focused_number: Any = None
        last_focused_matches = False
        while time.monotonic() < deadline:
            frontmost = workspace.frontmostApplication()
            front_pid = int(frontmost.processIdentifier()) if frontmost is not None else 0
            last_front_pid = front_pid
            last_skylight_front = self._skylight_front_process_matches(
                int(window["pid"]), int(window["id"])
            )
            last_ax_frontmost = self._ax_copy(
                app_element,
                self._ax_attr("kAXFrontmostAttribute", "AXFrontmost"),
            )
            if front_pid == int(window["pid"]) or last_skylight_front:
                focused = self._ax_copy(
                    app_element,
                    self._ax_attr("kAXFocusedWindowAttribute", "AXFocusedWindow"),
                )
                focused_number = self._ax_copy(
                    focused,
                    self._ax_attr("kAXWindowNumberAttribute", "AXWindowNumber"),
                ) if focused is not None else None
                last_focused_number = focused_number
                try:
                    focused_matches = int(focused_number) == int(window["id"])
                except (TypeError, ValueError):
                    focused_matches = focused is not None and focused == target_ax_window
                last_focused_matches = focused_matches
                if focused_matches:
                    return
            time.sleep(0.02)
        detail = {
            "ok": False,
            "code": "activation_not_confirmed",
            "effect": "unverifiable",
            "verified": False,
            "targetPid": int(window["pid"]),
            "targetWindowId": int(window["id"]),
            "frontmostPid": last_front_pid,
            "windowServerFrontmost": last_skylight_front,
            "axFrontmost": bool(last_ax_frontmost),
            "focusedWindowId": (
                int(last_focused_number)
                if isinstance(last_focused_number, (int, float))
                else None
            ),
            "focusedWindowMatches": last_focused_matches,
            "skylightStatus": self._skylight_last_status,
        }
        summary = ", ".join(f"{key}={value}" for key, value in detail.items() if key != "ok")
        raise ToolError(
            f"The target window did not become frontmost; no input was sent ({summary})",
            detail,
        )

    def _activate_current(self, value: Any) -> dict[str, Any]:
        window = self._get_window(value)
        try:
            self._activate(window)
            # Activation can switch Spaces, reveal a sheet, or let an app reposition
            # its window. Rehydrate before converting any observed coordinate.
            return self._get_window(window)
        except BaseException:
            # Even a failed confirmation can follow a real Space/focus change.
            self._invalidate_window_observations(window)
            raise

    def tool_activate_window(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._get_window(arguments["window"])
        try:
            self._activate(window)
            refreshed = self._get_window(window)
            return {"ok": True, "window": refreshed, "effect": "confirmed", "verified": True}
        finally:
            self._invalidate_window_observations(window)

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

    def _prune_element_cache(self, keep: tuple[str, int, int]) -> None:
        """Bound retained AX native objects while preserving the fresh window."""
        if len(self._element_cache) <= 32:
            return
        candidates = sorted(
            (
                (key, cached)
                for key, cached in self._element_cache.items()
                if key != keep
            ),
            key=lambda item: float(item[1].get("created", 0)),
        )
        # Evict to a lower watermark so scanning many windows does not prune on
        # every subsequent observation.
        for key, _cached in candidates[: max(0, len(self._element_cache) - 24)]:
            self._element_cache.pop(key, None)

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

    def _mouse_event_source(self) -> Any | None:
        if self._hid_mouse_event_source is not None:
            return self._hid_mouse_event_source
        Q = self.Quartz
        create = getattr(Q, "CGEventSourceCreate", None)
        state = getattr(Q, "kCGEventSourceStateHIDSystemState", None)
        # Lightweight unit fixtures and older bridge shims may not expose the
        # source API; native PyObjC CI proves the production path does.
        if create is None or state is None:
            return None
        source = create(state)
        if source is None:
            raise ToolError("macOS could not create a HID mouse event source")
        self._hid_mouse_event_source = source
        return source

    def _post_mouse(self, event_type: Any, button: Any, x: float, y: float, click_count: int = 1) -> None:
        Q = self.Quartz
        event = Q.CGEventCreateMouseEvent(
            self._mouse_event_source(),
            event_type,
            (float(x), float(y)),
            button,
        )
        if event is None:
            raise ToolError("macOS could not create a mouse event")
        if click_count > 1:
            Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, click_count)
        try:
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        except Exception as error:
            raise ToolError(
                f"macOS could not confirm mouse event delivery: {error}",
                {
                    "code": "mouse_event_delivery_unknown",
                    "effect": "unverifiable",
                    "verified": False,
                },
            ) from error

    @staticmethod
    def _quantize_scroll_delta(value: Any) -> int:
        number = float(value)
        if not math.isfinite(number):
            raise ToolError("scroll deltas must be finite")
        return int(math.copysign(math.floor(abs(number) + 0.5), number))

    def _post_scroll(
        self,
        x: float,
        y: float,
        scroll_x: Any,
        scroll_y: Any,
        scope: str,
    ) -> tuple[int, int]:
        # The public contract uses positive X/right and positive Y/down.
        # Quartz's wheel axes use the opposite content direction and integer
        # pixel deltas, so quantize symmetrically before reversing both axes.
        pixel_x = self._quantize_scroll_delta(scroll_x)
        pixel_y = self._quantize_scroll_delta(scroll_y)
        if pixel_x == 0 and pixel_y == 0:
            raise ToolError(
                "scrollX and scrollY quantize to a zero-pixel event; use an absolute delta of at least 0.5"
            )
        Q = self.Quartz
        event = Q.CGEventCreateScrollWheelEvent(
            None,
            Q.kCGScrollEventUnitPixel,
            2,
            -pixel_y,
            -pixel_x,
        )
        if event is None:
            raise ToolError(f"macOS could not create a {scope} scroll event")
        Q.CGEventSetLocation(event, (x, y))
        try:
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        except Exception as error:
            raise ToolError(
                f"macOS could not confirm {scope} scroll delivery: {error}",
                {
                    "code": "scroll_delivery_unknown",
                    "effect": "unverifiable",
                    "verified": False,
                },
            ) from error
        time.sleep(SCROLL_SETTLE_SECONDS)
        return pixel_x, pixel_y

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
            self._post_mouse(
                getattr(self.Quartz, "kCGEventMouseMoved", "moved"),
                button,
                x,
                y,
            )
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            self._post_mouse_down(button, down, up, dragged, x, y, click_number)
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            try:
                self._post_mouse(up, button, x, y, click_number)
            except Exception as first_error:
                # Retry release once now; if it still fails, close() retains
                # the registered button and releases it on MCP shutdown.
                try:
                    self._post_mouse(up, button, x, y, click_number)
                except Exception as retry_error:
                    raise ToolError(
                        f"click may have landed but its mouse release could not be confirmed: {retry_error}",
                        {
                            "code": "click_release_incomplete",
                            "effect": "partial",
                            "verified": False,
                            "requested_clicks": count,
                            "completed_clicks": click_number - 1,
                            "release_pending": True,
                        },
                    ) from first_error
                else:
                    self._held_buttons.pop(button, None)
            else:
                self._held_buttons.pop(button, None)
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            if click_number < count:
                time.sleep(MULTICLICK_ADDITIONAL_GAP_SECONDS)

    def _post_mouse_down(
        self,
        button: Any,
        down: Any,
        up: Any,
        dragged: Any,
        x: float,
        y: float,
        click_count: int = 1,
    ) -> None:
        # Register before posting so shutdown can always synthesize the matching
        # release, including an interruption immediately after the native call.
        self._held_buttons[button] = (up, dragged, x, y)
        try:
            self._post_mouse(down, button, x, y, click_count)
        except BaseException:
            try:
                self._post_mouse(up, button, x, y, click_count)
            except Exception:
                pass
            else:
                self._held_buttons.pop(button, None)
            raise

    def tool_click(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        try:
            button_name = str(arguments.get("mouse_button", "left"))
            count = int(arguments.get("click_count", 1))
            if not 1 <= count <= 4:
                raise ToolError("click_count must be between 1 and 4")
            if "element_index" in arguments and arguments.get("element_index") is not None:
                element = self._cached_element(window, arguments["element_index"])
                press_action = self._ax_attr("kAXPressAction", "AXPress")
                actions = self._ax_actions(element)
                advertises_press = any(
                    re.sub(r"[\s_-]+", "", action.lower().removeprefix("ax")) == "press"
                    for action in actions
                )
                if button_name in {"left", "l"} and count == 1 and advertises_press:
                    if self._ax_perform(element, press_action):
                        return {
                            "ok": True,
                            "method": "accessibility",
                            "element_index": int(arguments["element_index"]),
                            "effect": "unverifiable",
                            "verified": False,
                        }
                    return {
                        "ok": True,
                        "method": "accessibility",
                        "element_index": int(arguments["element_index"]),
                        "effect": "suspected_noop",
                        "verified": False,
                        "escalation": {
                            "recommended": "px",
                            "reason": "Advertised AXPress did not report success; refresh before pixel delivery",
                        },
                    }
                x, y = self._element_center(element)
            else:
                if arguments.get("x") is None or arguments.get("y") is None:
                    raise ToolError("click requires either element_index or both x and y")
                x, y = self._relative_point(
                    window, arguments["x"], arguments["y"], arguments.get("screenshotId")
                )
            button, down, up, dragged = self._button(button_name)
            self._click_pointer(button, down, up, dragged, x, y, count)
            return {
                "ok": True,
                "method": "coordinate",
                "screenPoint": {"x": x, "y": y},
                "click_count": count,
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_window_observations(window)

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
        try:
            self._send_key(str(arguments["key"]))
            return {
                "ok": True,
                "key": arguments["key"],
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_window_observations(window)

    def _send_key(self, key: str) -> None:
        key_code, modifiers = parse_key_chord(key)
        Q = self.Quartz
        active_flags = 0
        cleanup_releases: list[tuple[Any, int | None]] = []
        failed_releases: set[int] = set()
        action_error: BaseException | None = None
        cleanup_error: BaseException | None = None

        def event_pair(code: int, description: str) -> tuple[Any, Any]:
            down = Q.CGEventCreateKeyboardEvent(None, code, True)
            up = Q.CGEventCreateKeyboardEvent(None, code, False)
            if down is None or up is None:
                raise ToolError(f"macOS could not create {description} keyboard events")
            return down, up

        try:
            # Post real modifier transitions as well as flags. Some native,
            # Catalyst, and game-style event loops ignore a flags-only chord.
            for modifier in MODIFIER_KEY_ORDER:
                if modifier not in modifiers:
                    continue
                flag = self._modifier_flags({modifier})
                down, up = event_pair(KEY_CODES[modifier], modifier)
                next_flags = active_flags | flag
                Q.CGEventSetFlags(down, next_flags)
                # Event flags describe modifier state at that event. The
                # matching key-up therefore carries the state from before
                # this modifier was pressed, not the still-down state.
                Q.CGEventSetFlags(up, active_flags)
                active_flags = next_flags
                cleanup_releases.append((up, flag))
                self._post_key_down(down, up)

            down, up = event_pair(key_code, "primary")
            primary_modifier = next(
                (
                    modifier
                    for modifier in MODIFIER_KEY_ORDER
                    if KEY_CODES[modifier] == key_code
                ),
                None,
            )
            primary_down_flags = active_flags
            primary_up_flags = active_flags
            if key_code in NUMERIC_PAD_KEY_CODES:
                numeric_pad_flag = int(
                    getattr(Q, "kCGEventFlagMaskNumericPad", 0)
                )
                primary_down_flags |= numeric_pad_flag
                primary_up_flags |= numeric_pad_flag
            if primary_modifier is not None:
                primary_flag = self._modifier_flags({primary_modifier})
                primary_down_flags |= primary_flag
                primary_up_flags &= ~primary_flag
            Q.CGEventSetFlags(down, primary_down_flags)
            Q.CGEventSetFlags(up, primary_up_flags)
            cleanup_releases.append((up, None))
            self._post_key_down(down, up)
            try:
                self._post_key_up(up)
            except BaseException:
                # _post_key_up already retried; retain this exact event for
                # close() rather than silently issuing another immediate up.
                failed_releases.add(id(up))
                raise
        except BaseException as error:
            action_error = error
        finally:
            for up, flag in reversed(cleanup_releases):
                is_held = any(held is up for held in self._held_key_releases)
                release_flags = active_flags & ~flag if flag is not None else active_flags
                if is_held and id(up) not in failed_releases:
                    try:
                        Q.CGEventSetFlags(up, release_flags)
                        self._post_key_up(up)
                    except BaseException as error:
                        failed_releases.add(id(up))
                        if cleanup_error is None:
                            cleanup_error = error
                if flag is not None:
                    active_flags = release_flags

            # A release retained for shutdown is posted after every modifier
            # cleanup attempt, so its flags must describe that final desired
            # state rather than the chord state from the original attempt.
            for pending_up, _flag in cleanup_releases:
                if any(held is pending_up for held in self._held_key_releases):
                    Q.CGEventSetFlags(pending_up, active_flags)

        if action_error is not None:
            raise action_error
        if cleanup_error is not None:
            raise cleanup_error
        # Return only after the target app has had a chance to consume the
        # fully released chord. Failed or retained releases never reach here.
        time.sleep(KEY_CHORD_SETTLE_SECONDS)

    def _forget_key_release(self, up: Any) -> None:
        for index, held in enumerate(self._held_key_releases):
            if held is up:
                self._held_key_releases.pop(index)
                return

    def _post_key_down(self, down: Any, up: Any) -> None:
        Q = self.Quartz
        # Register first so SIGTERM/KeyboardInterrupt between bytecodes still
        # leaves close() enough information to post the matching key-up.
        self._held_key_releases.append(up)
        try:
            Q.CGEventPost(Q.kCGHIDEventTap, down)
        except BaseException as error:
            try:
                Q.CGEventPost(Q.kCGHIDEventTap, up)
            except Exception:
                pass
            else:
                self._forget_key_release(up)
            if isinstance(error, Exception):
                raise ToolError(
                    f"macOS could not confirm keyboard event delivery: {error}",
                    {
                        "code": "key_event_delivery_unknown",
                        "effect": "unverifiable",
                        "verified": False,
                    },
                ) from error
            raise

    def _post_key_up(self, up: Any) -> None:
        Q = self.Quartz
        try:
            Q.CGEventPost(Q.kCGHIDEventTap, up)
        except BaseException as first_error:
            # Retry once immediately; retain the event for close() if the
            # release still cannot be posted.
            try:
                Q.CGEventPost(Q.kCGHIDEventTap, up)
            except Exception as retry_error:
                if isinstance(first_error, Exception):
                    raise ToolError(
                        f"key press may have landed but its release could not be confirmed: {retry_error}",
                        {
                            "code": "key_release_incomplete",
                            "effect": "partial",
                            "verified": False,
                            "release_pending": True,
                        },
                    ) from first_error
                raise
            else:
                self._forget_key_release(up)
                if not isinstance(first_error, Exception):
                    raise first_error
                return
        else:
            self._forget_key_release(up)

    def tool_type_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        text = str(arguments["text"])
        try:
            delivered = self._send_text(text)
        finally:
            # Activation or a partially delivered chunk changes observable UI
            # even when the action raises, so no old screenshot/index is safe.
            self._invalidate_window_observations(window)
        return {
            "ok": True,
            "characters": delivered,
            "effect": "unverifiable",
            "verified": False,
        }

    def _send_text(self, text: str) -> int:
        Q = self.Quartz
        delivered = 0
        for chunk in unicode_text_chunks(text):
            try:
                utf16_length = len(chunk.encode("utf-16-le")) // 2
                down = Q.CGEventCreateKeyboardEvent(None, 0, True)
                up = Q.CGEventCreateKeyboardEvent(None, 0, False)
                if down is None or up is None:
                    raise ToolError("macOS could not create a Unicode keyboard event")
                Q.CGEventKeyboardSetUnicodeString(down, utf16_length, chunk)
                Q.CGEventKeyboardSetUnicodeString(up, utf16_length, chunk)
                self._post_key_down(down, up)
                # Unicode insertion occurs on key-down; count the chunk before
                # key-up so a reported key-up failure cannot cause replay.
                delivered += len(chunk)
                self._post_key_up(up)
            except Exception as error:
                if delivered:
                    raise ToolError(
                        f"type_text incomplete: delivered {delivered} of {len(text)} character(s); "
                        "retry only the remaining suffix",
                        {
                            "code": "type_text_incomplete",
                            "effect": "partial",
                            "requested_chars": len(text),
                            "delivered_chars": delivered,
                            "retryable": True,
                            "retry_from_character": delivered,
                        },
                    ) from error
                if isinstance(error, ToolError):
                    raise
                raise ToolError(f"macOS could not deliver Unicode text: {error}") from error
            # Pacing is not part of native delivery, so it stays outside the
            # partial-delivery handler and can never make a completed chunk
            # look replayable.
            time.sleep(TEXT_CHUNK_SETTLE_SECONDS)
        return delivered

    def tool_scroll(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        try:
            x, y = self._relative_point(
                window, arguments["x"], arguments["y"], arguments.get("screenshotId")
            )
            delivered_x, delivered_y = self._post_scroll(
                x,
                y,
                arguments["scrollX"],
                arguments["scrollY"],
                "window",
            )
            return {
                "ok": True,
                "screenPoint": {"x": x, "y": y},
                "deliveredDelta": {"x": delivered_x, "y": delivered_y},
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_window_observations(window)

    def tool_set_value(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        try:
            element = self._cached_element(window, arguments["element_index"])
            value_attr = self._ax_attr("kAXValueAttribute", "AXValue")
            value = str(arguments["value"])
            settable = self._ax_is_settable(element, value_attr)
            if settable is not False:
                if self._ax_set(element, value_attr, value):
                    result = {
                        "ok": True,
                        "method": "accessibility",
                        "element_index": int(arguments["element_index"]),
                    }
                    observed = self._ax_copy(element, value_attr)
                    if observed is not None and str(observed) == value:
                        return {**result, "effect": "confirmed", "verified": True}
                    if observed is None:
                        return {
                            **result,
                            "effect": "unverifiable",
                            "verified": False,
                            "escalation": {
                                "recommended": "px",
                                "reason": "Accessibility value could not be read back",
                            },
                        }
                    return {
                        **result,
                        "effect": "suspected_noop",
                        "verified": False,
                        "escalation": {
                            "recommended": "px",
                            "reason": "Accessibility value read-back did not match",
                        },
                    }
                return {
                    "ok": True,
                    "method": "accessibility",
                    "element_index": int(arguments["element_index"]),
                    "effect": "suspected_noop",
                    "verified": False,
                    "escalation": {
                        "recommended": "px",
                        "reason": "Settable Accessibility value did not report success; refresh before pixel delivery",
                    },
                }
            x, y = self._element_center(element)
            button, down, up, dragged = self._button("left")
            self._click_pointer(button, down, up, dragged, x, y, 1)
            self.tool_press_key({"window": window, "key": "Command+a"})
            self.tool_type_text({"window": window, "text": value})
            return {
                "ok": True,
                "method": "focus-select-type",
                "element_index": int(arguments["element_index"]),
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_window_observations(window)

    def tool_drag(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        try:
            start = self._relative_point(
                window, arguments["from_x"], arguments["from_y"], arguments.get("screenshotId")
            )
            end = self._relative_point(
                window, arguments["to_x"], arguments["to_y"], arguments.get("screenshotId")
            )
            duration = max(0.0, min(30.0, float(arguments.get("duration", 0.35))))
            self._drag_pointer(start, end, duration)
            return {
                "ok": True,
                "from": {"x": start[0], "y": start[1]},
                "to": {"x": end[0], "y": end[1]},
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_window_observations(window)

    def _drag_pointer(
        self, start: tuple[float, float], end: tuple[float, float], duration: float
    ) -> None:
        button, down, up, dragged = self._button("left")
        if button in self._held_buttons:
            raise ToolError("The left mouse button is already held; release it with mouse_up before dragging")
        self._post_mouse(
            getattr(self.Quartz, "kCGEventMouseMoved", "moved"),
            button,
            *start,
        )
        time.sleep(CLICK_EVENT_SETTLE_SECONDS)
        self._post_mouse_down(button, down, up, dragged, *start)
        steps = max(1, min(300, int(duration * 60)))
        delay = duration / steps if steps else 0
        last = start
        try:
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            for step in range(1, steps + 1):
                fraction = step / steps
                last = (
                    start[0] + (end[0] - start[0]) * fraction,
                    start[1] + (end[1] - start[1]) * fraction,
                )
                if delay:
                    time.sleep(delay)
                self._post_mouse(dragged, button, *last)
                self._held_buttons[button] = (up, dragged, last[0], last[1])
        except BaseException as error:
            # A failed/interrupted drag must not leave the physical button held.
            release_pending = True
            try:
                self._post_mouse(up, button, *last)
            except Exception:
                pass
            else:
                self._held_buttons.pop(button, None)
                release_pending = False
                time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            if isinstance(error, Exception):
                raise ToolError(
                    f"drag was only partially delivered: {error}",
                    {
                        "code": "drag_incomplete",
                        "effect": "partial",
                        "verified": False,
                        "release_pending": release_pending,
                        "last_position": {"x": last[0], "y": last[1]},
                    },
                ) from error
            raise
        try:
            self._post_mouse(up, button, *end)
        except Exception as first_error:
            # A transient release failure is safe to retry once. If both
            # attempts fail, retain the held state so close() can retry it.
            try:
                self._post_mouse(up, button, *end)
            except Exception as retry_error:
                raise ToolError(
                    f"drag reached its endpoint but the mouse release could not be confirmed: {retry_error}",
                    {
                        "code": "drag_release_incomplete",
                        "effect": "partial",
                        "verified": False,
                        "release_pending": True,
                    },
                ) from first_error
            else:
                self._held_buttons.pop(button, None)
        else:
            self._held_buttons.pop(button, None)
        time.sleep(CLICK_EVENT_SETTLE_SECONDS)

    def tool_perform_secondary_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window = self._activate_current(arguments["window"])
        try:
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
                return {
                    "ok": True,
                    "action": matched,
                    "element_index": int(arguments["element_index"]),
                    "effect": "suspected_noop",
                    "verified": False,
                    "escalation": {
                        "recommended": "px",
                        "reason": "Advertised Accessibility action did not report success",
                    },
                }
            return {
                "ok": True,
                "action": matched,
                "element_index": int(arguments["element_index"]),
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_window_observations(window)

    def tool_desktop_click(self, arguments: dict[str, Any]) -> dict[str, Any]:
        x, y = self._desktop_relative_point(arguments["x"], arguments["y"], arguments["screenshotId"])
        button_name = str(arguments.get("mouse_button", "left"))
        count = int(arguments.get("click_count", 1))
        if not 1 <= count <= 4:
            raise ToolError("click_count must be between 1 and 4")
        button, down, up, dragged = self._button(button_name)
        try:
            self._click_pointer(button, down, up, dragged, x, y, count)
            return {
                "ok": True,
                "screenPoint": {"x": x, "y": y},
                "click_count": count,
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_all_observations()

    def tool_desktop_press_key(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_desktop_screenshot(arguments["screenshotId"])
        try:
            self._send_key(str(arguments["key"]))
            return {
                "ok": True,
                "key": arguments["key"],
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_all_observations()

    def tool_desktop_type_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_desktop_screenshot(arguments["screenshotId"])
        text = str(arguments["text"])
        try:
            delivered = self._send_text(text)
        finally:
            self._invalidate_all_observations()
        return {
            "ok": True,
            "characters": delivered,
            "effect": "unverifiable",
            "verified": False,
        }

    def tool_desktop_scroll(self, arguments: dict[str, Any]) -> dict[str, Any]:
        x, y = self._desktop_relative_point(arguments["x"], arguments["y"], arguments["screenshotId"])
        try:
            delivered_x, delivered_y = self._post_scroll(
                x,
                y,
                arguments["scrollX"],
                arguments["scrollY"],
                "desktop",
            )
            return {
                "ok": True,
                "screenPoint": {"x": x, "y": y},
                "deliveredDelta": {"x": delivered_x, "y": delivered_y},
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_all_observations()

    def tool_desktop_drag(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start = self._desktop_relative_point(
            arguments["from_x"], arguments["from_y"], arguments["screenshotId"]
        )
        end = self._desktop_relative_point(
            arguments["to_x"], arguments["to_y"], arguments["screenshotId"]
        )
        duration = max(0.0, min(30.0, float(arguments.get("duration", 0.35))))
        try:
            self._drag_pointer(start, end, duration)
            return {
                "ok": True,
                "from": {"x": start[0], "y": start[1]},
                "to": {"x": end[0], "y": end[1]},
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_all_observations()

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
        try:
            for step in range(1, steps + 1):
                fraction = step / steps
                x = start[0] + (target[0] - start[0]) * fraction
                y = start[1] + (target[1] - start[1]) * fraction
                if delay:
                    time.sleep(delay)
                for held_button, (held_up, event_type, _held_x, _held_y) in routes:
                    self._post_mouse(event_type, held_button, x, y)
                    if held_up is not None:
                        self._held_buttons[held_button] = (held_up, event_type, x, y)
            # CGEventPost is asynchronous with respect to many app event
            # loops; settle before cursor read-back or a following action.
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            try:
                observed = self._cursor()
            except ToolError:
                return {
                    "ok": True,
                    "position": {"x": target[0], "y": target[1]},
                    "effect": "unverifiable",
                    "verified": False,
                }
            verified = abs(observed[0] - target[0]) <= 1 and abs(observed[1] - target[1]) <= 1
            return {
                "ok": True,
                "position": {"x": observed[0], "y": observed[1]},
                "effect": "confirmed" if verified else "suspected_noop",
                "verified": verified,
            }
        finally:
            self._invalidate_all_observations()

    def tool_mouse_down(self, arguments: dict[str, Any]) -> dict[str, Any]:
        point = self._optional_point(arguments)
        button, down, up, dragged = self._button(str(arguments.get("mouse_button", "left")))
        if button in self._held_buttons:
            raise ToolError("The requested mouse button is already held; call mouse_up before mouse_down again")
        try:
            self._post_mouse(
                getattr(self.Quartz, "kCGEventMouseMoved", "moved"),
                button,
                *point,
            )
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            self._post_mouse_down(button, down, up, dragged, *point)
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            return {
                "ok": True,
                "position": {"x": point[0], "y": point[1]},
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_all_observations()

    def tool_mouse_up(self, arguments: dict[str, Any]) -> dict[str, Any]:
        point = self._optional_point(arguments)
        button, _down, up, dragged = self._button(str(arguments.get("mouse_button", "left")))
        try:
            held = self._held_buttons.get(button)
            move_error: BaseException | None = None
            move_attempted = False
            if held is not None:
                held_up, held_dragged, held_x, held_y = held
                up = held_up
                dragged = held_dragged
                if abs(held_x - point[0]) > 0.01 or abs(held_y - point[1]) > 0.01:
                    move_attempted = True
                    # Register the requested endpoint first because a native
                    # post can land even when delivery confirmation raises.
                    self._held_buttons[button] = (up, dragged, point[0], point[1])
                    try:
                        self._post_mouse(dragged, button, *point)
                    except BaseException as error:
                        # Releasing is still mandatory even if the final drag
                        # event was rejected, unverifiable, or interrupted.
                        move_error = error
                    else:
                        time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            else:
                # An untracked mouse_up is an intentional recovery primitive.
                # Retain it until posting succeeds so shutdown can retry an
                # unknown native delivery without inventing another down.
                self._held_buttons[button] = (up, dragged, point[0], point[1])

            try:
                self._post_mouse(up, button, *point)
            except Exception as first_error:
                try:
                    self._post_mouse(up, button, *point)
                except Exception as retry_error:
                    if move_error is not None and not isinstance(move_error, Exception):
                        raise move_error
                    raise ToolError(
                        f"mouse release could not be confirmed: {retry_error}",
                        {
                            "code": "mouse_up_release_incomplete",
                            "effect": "partial",
                            "verified": False,
                            "release_pending": True,
                            "movement_attempted": move_attempted,
                            "position": {"x": point[0], "y": point[1]},
                        },
                    ) from first_error
            self._held_buttons.pop(button, None)
            time.sleep(CLICK_EVENT_SETTLE_SECONDS)
            if move_error is not None:
                if not isinstance(move_error, Exception):
                    raise move_error
                raise ToolError(
                    f"mouse button was released but movement before release could not be confirmed: {move_error}",
                    {
                        "code": "mouse_up_move_incomplete",
                        "effect": "partial",
                        "verified": False,
                        "release_pending": False,
                        "position": {"x": point[0], "y": point[1]},
                    },
                ) from move_error
            return {
                "ok": True,
                "position": {"x": point[0], "y": point[1]},
                "effect": "unverifiable",
                "verified": False,
            }
        finally:
            self._invalidate_all_observations()

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
        text = str(arguments["text"])
        try:
            pasteboard.clearContents()
        except Exception as error:
            raise ToolError(
                f"macOS could not clear the clipboard: {error}",
                {
                    "ok": False,
                    "code": "clipboard_clear_failed",
                    "effect": "unverifiable",
                    "verified": False,
                    "requested_chars": len(text),
                },
            ) from error
        try:
            accepted = bool(pasteboard.setString_forType_(text, A.NSPasteboardTypeString))
        except Exception as error:
            raise ToolError(
                f"macOS cleared the clipboard but could not write the requested text: {error}",
                {
                    "ok": False,
                    "code": "clipboard_update_incomplete",
                    "effect": "partial",
                    "verified": False,
                    "clipboard_cleared": True,
                    "requested_chars": len(text),
                },
            ) from error
        if not accepted:
            raise ToolError(
                "macOS cleared the clipboard but rejected the requested text",
                {
                    "ok": False,
                    "code": "clipboard_update_incomplete",
                    "effect": "partial",
                    "verified": False,
                    "clipboard_cleared": True,
                    "requested_chars": len(text),
                },
            )
        try:
            observed = pasteboard.stringForType_(A.NSPasteboardTypeString)
        except Exception:
            return {
                "ok": True,
                "characters": len(text),
                "effect": "unverifiable",
                "verified": False,
            }
        if observed is None or str(observed) != text:
            raise ToolError(
                "macOS accepted the clipboard write but the read-back did not match",
                {
                    "ok": False,
                    "code": "clipboard_verification_mismatch",
                    "effect": "partial",
                    "verified": False,
                    "clipboard_cleared": True,
                    "requested_chars": len(text),
                    "observed_chars": len(str(observed)) if observed is not None else 0,
                },
            )
        return {"ok": True, "characters": len(text), "effect": "confirmed", "verified": True}
