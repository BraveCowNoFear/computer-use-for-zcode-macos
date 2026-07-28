# Tool API and routing

## Primary: Cua Driver background MCP

The `macos-computer-use` MCP is the primary surface. It is pinned to the tested
Cua Driver 0.12.6 contract at install time. An existing signed app is reused
only when it is that exact version and exposes every required session,
window, desktop, and input tool. Inspect `tools/list` for the live schemas
instead of guessing optional fields.

Common flow:

| Intent | Tool and key fields |
| --- | --- |
| Permission status | `check_permissions({prompt:false})` |
| Begin task | `start_session({session, capture_scope})` |
| Launch app | `launch_app({bundle_id})` |
| List app windows | `list_windows({pid})` |
| Snapshot | `get_window_state({session, pid, window_id})` |
| Snapshot desktop | `get_desktop_state({session})` in a desktop-scoped session |
| AX click | `click({session, pid, window_id, element_index})` |
| Pixel click | `click({session, pid, window_id, x, y})` |
| Desktop click | `click({session, x, y, scope:"desktop"})` with no pid/window |
| Enter text | `type_text({session, pid, window_id, element_index, text})` |
| Shortcut | `hotkey({session,pid,window_id,keys:["cmd","c"]})` |
| One key | `press_key({session,pid,window_id,key:"return",modifiers:[]})` |
| Non-text AX value | `set_value({session, pid, window_id, element_index, value})` |
| Finish task | `end_session({session})` |

Primary window coordinates use the screenshot's window-local pixel space. AX
indexes are cached against one `(pid, window_id)` observation and go stale on
the next snapshot. Most input tools accept `delivery_mode:"background"`
(default) or `"foreground"` (last resort).

Desktop state and desktop-scope input are also part of this primary MCP, not a
third server. For menu bar/Dock/system UI, pair a fresh `get_desktop_state`
with a windowless `scope:"desktop"` action. Browser tools such as
`get_browser_state`, `browser_click`, and `browser_type`—when advertised by the
live primary `tools/list`—also belong to this same MCP.

The plugin launches a dedicated daemon with:

```text
serve --permission-mode unrestricted --dangerously-bypass-approvals
```

This removes Cua Driver's runtime human-approval prompts. It does not and cannot
forge macOS TCC consent or remove capability limits compiled into a dependency.
The launcher uses a per-user private, versioned socket and accepts the daemon
only after `status` reports `permission mode: unrestricted`; a stale, standard,
bounded, incompatible, or unknown daemon is stopped only on that plugin socket
and replaced. The same status gate requires user, managed, and bounded-session
policy configuration to be absent. The dedicated launch clears inherited Cua
policy environment variables so another tool cannot silently narrow this
plugin's advertised full-access mode.
`check_permissions({prompt:false})` is the read-only MCP inspection call. A
staged `check_permissions({prompt:true,probe_direct_capture:false})` requests
Accessibility and Screen Recording; a later `check_permissions({prompt:true})`
also verifies direct ScreenCaptureKit readiness and may raise Tahoe's separate
capture consent. The signed app's startup onboarding remains an equivalent
first-run path.

## Fallback: direct Quartz/PyObjC MCP

The `macos-computer-use-fallback` MCP ships 28 local tools. Its Codex-compatible
core is:

| Tool | Required input | Purpose |
| --- | --- | --- |
| `list_windows` | none | Return targetable windows front-to-back. |
| `get_window` | `id`, optional `app` | Rehydrate a returned window. |
| `list_apps` | none | Return installed/running apps and windows. |
| `launch_app` | `app` | Launch and return matched pid plus current windows. |
| `get_window_state` | `window` | Return screenshot and AX by default; either can be disabled explicitly. |
| `click` | `window`, element index or `x`/`y` | Click by AX or pixels. |
| `press_key` | `window`, `key` | Press a key or `+`-separated chord. |
| `type_text` | `window`, `text` | Send literal Unicode. |
| `scroll` | `window`, `x`, `y`, `scrollX`, `scrollY` | Pixel scroll. |
| `set_value` | `window`, `element_index`, `value` | Set editable AX value. |
| `drag` | `window`, start/end coordinates | Left-button drag. |
| `perform_secondary_action` | `window`, index, `action` | Run listed AX action. |
| `activate_window` | `window` | Bring app/window forward. |

Extended fallback tools: `computer_use_health`, `permission_status`,
`request_permissions`, `move_mouse`, `mouse_down`, `mouse_up`,
`get_cursor_position`, `clipboard_get`, `clipboard_set`, plus the unrestricted
desktop family `get_desktop_state`, `desktop_click`, `desktop_press_key`,
`desktop_type_text`, `desktop_scroll`, and `desktop_drag`.
`get_desktop_state` returns one image and screenshot ID per active display so
mixed Retina/non-Retina layouts do not share an incorrect global scale.

Fallback startup sequence:

```text
computer_use_health → permission_status → launch_app/list_windows → get_window
→ get_window_state → one action → get_window_state
```

The fallback is stateless and has no session cleanup tool. If its permissions
are missing, read the granular health fields first. Accessibility alone makes
`axControlReady=true`; use `get_window_state` with
`include_screenshot:false` for an AX-completable task. Screen Recording is
required for `pixelObservationReady`, window screenshots, coordinate grounding,
and every desktop-state route. Call `request_permissions` only for the grant the
requested task actually needs, then wait for the user to grant the Python/ZCode
responsible app before restarting ZCode.

Do not pass primary handles to fallback tools. A fallback window looks like:

```json
{
  "id": 123,
  "app": "com.apple.TextEdit",
  "title": "Untitled",
  "pid": 456,
  "bounds": {"x": 100, "y": 80, "width": 900, "height": 700}
}
```

Fallback screenshot IDs remain valid for five minutes and only for their
window. Fallback AX indexes belong to the latest text observation for that
window.

For menu bar, Dock, desktop, or other system UI after the primary desktop route
fails, use the fallback's own strict loop:

```text
get_desktop_state → one desktop_* action with its screenshotId
→ get_desktop_state
```

For a coordinate action, use the screenshot ID of the display image containing
the target and read x/y from that same image. Keyboard-only desktop actions may
use any fresh returned desktop screenshot ID.

The direct desktop actions are screen-wide and deliberately have no app or
window allowlist. The screenshot ID is required and becomes invalid after the
action, including keyboard/text actions.

## Installation and permissions

The primary first-run launcher downloads the versioned upstream installer,
verifies its pinned SHA-256, installs signed `/Applications/CuaDriver.app`, and
disables upstream telemetry. It may need the current macOS user to have write
access to `/Applications`. The fallback requires CPython 3.10 or newer, creates
a private Python environment, and installs the exact tested PyObjC 12.2.1
binary wheels.

Required macOS grants:

1. Privacy & Security → Accessibility for `CuaDriver.app` (and Python/ZCode if
   the direct fallback is used).
2. Privacy & Security → Screen Recording for the same responsible app.

Restart ZCode after changing a TCC grant. Full Disk Access is unrelated to GUI
control; grant it to ZCode only when the user's file operation itself needs it.

Source-checkout diagnostics:

```bash
bash plugins/macos-computer-use/scripts/install.sh
bash plugins/macos-computer-use/scripts/doctor.sh
```

`doctor.sh` starts or reuses only the versioned plugin daemon, then prints its
actual permission mode before checking the direct MCP runtime.
