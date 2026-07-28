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
indexes are cached against one `(app, pid, window_id)` observation and go stale on
the next snapshot. Most input tools accept `delivery_mode:"background"`
(default) or `"foreground"` (last resort).

Pinned 0.12.6 action responses expose a structured delivery verdict. Interpret
`effect:"confirmed"` plus `verified:true` as an AX post-condition read-back;
`effect:"unverifiable"` plus `verified:false` as dispatched but still requiring
fresh-state verification; and `effect:"suspected_noop"` as the signal to leave
the AX rung. Follow `escalation.recommended:"px"` or `"foreground"` by
re-observing and changing delivery once. A degraded state response likewise
routes to screenshot-grounded pixels. These fields optimize routing but never
replace the post-action `get_window_state` completion evidence.

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
| `get_window` | `id`, optional `app`/`pid` | Rehydrate a returned window; carry `pid` for exact process binding. |
| `list_apps` | none | Return installed/running apps and windows. |
| `launch_app` | `app` | Launch and return matched pid plus current windows. |
| `get_window_state` | `window` | Return a screenshot by default; request AX text explicitly when needed. |
| `click` | `window`, element index or `x`/`y` | Click by AX or pixels. |
| `press_key` | `window`, `key` | Press a key or `+`-separated chord; use keysym names such as `plus`, `colon`, or `ISO_Left_Tab` when the symbol conflicts with the separator. |
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
For raw mouse tools, a `screenshotId` without `window` binds the supplied
coordinates to that exact fresh desktop image; it is never silently ignored.
When `move_mouse`, `mouse_down`, or `mouse_up` uses window-image coordinates,
pass that window's fresh `screenshotId` too so Retina pixels map to logical
Quartz points. The core `click`, `scroll`, and `drag` tools accept the same
field; always include it when x/y came from a returned screenshot.

Fallback startup sequence:

```text
computer_use_health → permission_status → launch_app/list_windows → get_window
→ get_window_state → one action → get_window_state
```

The fallback is sessionless, not stateless: screenshot IDs and AX indexes live
only in its current MCP process, and it has no session cleanup tool. After a
stdio/server restart, enumerate and observe again. If permissions are missing,
read the granular health fields first. Accessibility alone makes
`axControlReady=true`; use `get_window_state` with
`include_screenshot:false` for an AX-completable task. Screen Recording is
required for `pixelObservationReady`, window screenshots, coordinate grounding,
and every desktop-state route. Use
`request_permissions({accessibility:true,screen_recording:false})` for AX alone,
or `{accessibility:false,screen_recording:true}` for pixels alone. Then wait for
the user to grant the Python/ZCode responsible app before restarting ZCode.

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

Preserve that whole object in fallback calls. `pid` is an optional extension to
the Codex-shaped `get_window({id,app})` input, but supplying it prevents an old
handle from binding to a new process if macOS reuses the numeric window ID.

Fallback screenshot IDs remain valid for five minutes and only for their exact
`(app, pid, window_id)` process/window identity. If an app restarts, re-list and
re-observe even if macOS happens to reuse the same numeric window ID. Fallback
AX indexes belong to the latest text observation for that same process/window
identity and expire after the same five-minute ceiling. Any action or
subsequent observation invalidates them sooner.

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

The primary first-run launcher downloads the pinned upstream release archive,
verifies its SHA-256, atomically publishes `CuaDriver.app` inside the plugin data
directory, and accepts only the tested Cua AI Team ID/signing authority. It
proves persisted telemetry is disabled, turns off the separate update check,
and never overwrites a global `/Applications` app. The fallback requires CPython
3.10 through 3.15, creates a private Python environment, and installs the exact tested PyObjC 12.2.1
five-package binary-wheel closure with dependency resolution disabled.
Every supported wheel variant is pinned by its PyPI SHA-256 and installation
uses hash-required mode.
Every fallback synthetic-input or AX action also checks Accessibility before
dispatch. Screenshot-only observation remains independent, but missing TCC is
an explicit tool error rather than a silently discarded input reported as
successful.
The automatic first-run path builds and self-tests a staging environment before
atomically publishing it to a plugin-versioned runtime directory, so an
interrupted install is never reused as healthy.

Required macOS grants:

1. Privacy & Security → Accessibility for the plugin-owned `CuaDriver.app` (and Python/ZCode if
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
