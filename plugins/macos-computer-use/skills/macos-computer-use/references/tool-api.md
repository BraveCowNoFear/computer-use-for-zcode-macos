# Tool API and routing

## Primary: Cua Driver background MCP

The `macos-computer-use` MCP is the primary surface. It is pinned to the tested
Cua Driver 0.13.1 contract at install time. An existing signed app is reused
only when it is that exact version and exposes every required session,
window, desktop, and input tool. Inspect `tools/list` for the live schemas
instead of guessing optional fields.

Common flow:

| Intent | Tool and key fields |
| --- | --- |
| Stable driver diagnosis | `health_report({})`; require schema v1/darwin/pinned version and inspect named check statuses/hints |
| Fast app/window inventory | `get_accessibility_tree({})`; discovery only, then bind through exact window state |
| Read session-effective image cap | `get_config({session})` |
| Temporarily request native-size PNGs | `set_config({session, max_image_dimension:0})`; restore the prior value or end the session |
| Permission status | `check_permissions({prompt:false})` |
| Begin task | `start_session({session, capture_scope})` |
| Inspect visible session cursor | `get_agent_cursor_state({session})` |
| Hide/show session cursor | `set_agent_cursor_enabled({session, enabled})` |
| Select installed cursor artwork | `set_agent_cursor_theme({session, theme_id, reduced_motion})` |
| Move only the visible session cursor | `move_cursor({session, x, y, scope:"window"})` |
| Unlock desktop for an `auto` session | `escalate_session({session, reason, detail})` after verified window-ladder exhaustion |
| Launch app | `launch_app({bundle_id})` |
| Open native file/folder/URL | `launch_app({bundle_id, urls:[target]})`; require an exact returned target window and refresh focus state |
| Isolated concurrent app | `launch_app({bundle_id, creates_new_application_instance:true})` when advertised |
| Force exact process exit | `kill_app({pid})` only after verified cooperative quit failure or an explicit force-quit request |
| List app windows | `list_windows({pid})` |
| Persist exact foreground | `bring_to_front({pid, window_id})` only when the returned window must remain frontmost across calls |
| Snapshot | `get_window_state({session, pid, window_id})` |
| Snapshot desktop | `get_desktop_state({session})` in a desktop-scoped session |
| AX click | `click({session, pid, window_id, element_index})` |
| Pixel click | `click({session, pid, window_id, x, y})` |
| Native menu opener | `click({session, pid, window_id, element_token, action:"pick"})`, then refresh and press the returned menu item |
| Double-click | `double_click({session, pid, window_id, element_token})` or fresh x/y |
| Context click | `right_click({session, pid, window_id, element_token})` or fresh x/y |
| Drag | `drag({session, pid, window_id, from_x, from_y, to_x, to_y, delivery_mode:"foreground"})` after the macOS background-unavailable response |
| Targeted scroll | `scroll({session, pid, window_id, x, y, direction, by, amount})` from fresh pixels |
| Desktop click | `click({session, x, y, scope:"desktop"})` with no pid/window |
| Enter text | `type_text({session, pid, window_id, element_index, text})` |
| Shortcut | `hotkey({session,pid,window_id,keys:["cmd","c"]})` |
| One key | `press_key({session,pid,window_id,key:"return",modifiers:[]})` |
| Non-text AX value | `set_value({session, pid, window_id, element_index, value})` |
| Finish task | `end_session({session})` |

The public session ID is also the local cursor-badge label and color seed. Use
a short task slug plus a compact uniqueness suffix (for example,
`mail-triage-a1b2`), keep it within 28 visible characters, and never include
secrets or copied user content. Reuse that exact ID throughout one run.

`escalate_session` requires a truthful `reason`: `ax_tree_pixel_mismatch`,
`background_delivery_failed`, `foreground_ineffective`, `no_window_target`, or
`other`. Optional `detail` is a short local diagnostic, never secrets or page
content. Read back `get_session_state({session})`, require effective desktop
scope, and then take a new desktop snapshot before acting.

`health_report` is always a successful diagnostic call, even when its
structured `overall` is `degraded` or `failed`. Under schema version `"1"`,
consume named checks rather than parsing its decorative text and tolerate new
names. On macOS, `binary_version`, `platform_supported`, `session_active`, and
`bundle_identity` must pass before control. TCC and capability failures carry
local remediation hints; `screen_capture_capability` may be `skip` because the
read-only report deliberately does not trigger ScreenCaptureKit consent.

For isolated app lifecycles, snapshot existing pids before launch, require a
new positive pid plus a window owned by that pid, and clean up only that exact
process. Prefer pid/window-bound Command-Q and verify exit; `kill_app` is the
forceful second rung for a still-live exact pid, never a name-wide cleanup.

Pinned macOS `bring_to_front` has the exact schema `{pid, window_id?}` with no
session field. With a returned window ID it first requests exact WindowServer
activation and may fall back to app-level Cocoa activation; success returns the
same pid/window, `activated:true`, and `path:"skylight"` or `"cocoa"`. It is a
persistent focus-proxy tool, chiefly for remote-desktop clients or an explicit
frontmost-state outcome. Normal `delivery_mode:"foreground"` briefly fronts,
acts, and restores, so prefer it for one-off delivery. After persistent
activation, require the pid's `active:true` readback from fresh `list_apps`,
re-list the exact window, and take a new snapshot before acting.

Declared sessions receive an enabled, colored semantic cursor overlay by
default. Primary actions animate it without moving the real OS pointer. The
cursor follows its session across windows and is reclaimed by `end_session`;
anonymous actions remain cursor-less. Use the state tool to verify any cursor
configuration change. For concurrent runs on the same app, use distinct
sessions and `creates_new_application_instance:true` only when that optional
field is advertised by the live macOS `launch_app` schema.

Window-scoped `move_cursor` takes screen-point coordinates, changes only the
overlay, and does not deliver input. It is useful to seed a long visible glide
before a demo action. Desktop-scoped `move_cursor` instead moves the real OS
pointer and therefore requires an effective desktop session plus coordinates
from that session's fresh `get_desktop_state`; do not confuse the two spaces.

Keep primary keyboard actions pid/window-bound for background app work. A
windowless `scope:"desktop"` shortcut or key deliberately targets the current
foreground app and therefore starts from a fresh desktop observation. Both
paths still require a new state read before any follow-up action.

For `set_value`, prefer the current element token and check both layers: the
pinned driver should return `effect:"confirmed"` with `verified:true`, then a
new snapshot must expose the requested non-text control value. This is the AX
equivalent of action plus visible-state verification, not a license to reuse
the now-stale token.

Primary window coordinates use the screenshot's window-local pixel space. AX
indexes and opaque `element_token` handles are cached against one
`(app, pid, window_id)` observation and go stale on the next snapshot. Prefer
the token when the live response and action schema advertise it; otherwise use
the index. Most input tools accept `delivery_mode:"background"`
(default) or `"foreground"` (last resort).

Primary `click.action` maps `press`, `show_menu`, `pick`, `confirm`, `cancel`,
and `open` to the corresponding advertised AX operation. Native app menus are
frontmost-only and use two fresh snapshots: `pick` the returned menu-bar item,
then act on a newly returned open-menu item. Do not carry a closed-menu token
into the open state.

Pinned 0.13.1 action responses expose a structured delivery verdict. Interpret
`effect:"confirmed"` plus `verified:true` as an AX post-condition read-back;
`effect:"unverifiable"` plus `verified:false` as dispatched but still requiring
fresh-state verification; and `effect:"suspected_noop"` as the signal to leave
the AX rung. `effect:"partial"` with `code:"type_text_incomplete"` reports
`delivered_chars` and `retry_from_character`; observe, then submit only the
remaining suffix. Follow `escalation.recommended:"px"` or `"foreground"` by
re-observing and changing delivery once. A degraded state response likewise
routes to screenshot-grounded pixels. These fields optimize routing but never
replace the post-action `get_window_state` completion evidence.

For Electron/Catalyst or an AX text path recommending `px`, primary
`type_text({session,pid,window_id,x,y,text})` pixel-focuses the fresh screenshot
coordinate and types in one action. Its x/y form is mutually exclusive with
`element_index`. Open a closed control first and re-observe before this call;
otherwise its focus click can leave the text in the old field.

Desktop state and desktop-scope input are also part of this primary MCP, not a
third server. For menu bar/Dock/system UI, pair a fresh `get_desktop_state`
with a windowless `scope:"desktop"` action. The complete typed browser family
(`browser_prepare`, `get_browser_state`, navigation, click/type/pointer,
page-dialog, upload, and download tools) belongs to this same MCP and is a
required capability of the pinned primary launcher. Follow
[browser-workflow.md](browser-workflow.md) for its exact binding, snapshot,
invalidation, input-route, upload, and dependency-owned download contracts.

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
`check_permissions({prompt:false})` is the read-only MCP inspection call.
Pinned 0.13.1 refuses public `prompt:true` calls before platform dispatch in
every permission mode. This is a macOS TCC boundary, not a plugin action-risk
approval layer. The signed app's startup panel or the explicit human-run
`scripts/install.sh` route invokes upstream `permissions grant` through
LaunchServices, so the dialogs and Tahoe direct-capture probe are attributed to
the plugin-owned signed CuaDriver.app. Re-run the read-only check after the user
grants access and restarts ZCode.

## Fallback: direct Quartz/PyObjC MCP

The `macos-computer-use-fallback` MCP ships 28 local tools. Its Codex-compatible
core is:

Its stdio transport follows JSON-RPC 2.0 notification and error semantics:
notifications execute without a response, invalid request shapes are distinct
from JSON parse failures, and invalid initialize parameters do not terminate the
server.

| Tool | Required input | Purpose |
| --- | --- | --- |
| `list_windows` | none | Return targetable windows front-to-back. |
| `get_window` | `id`, optional `app`/`pid` | Rehydrate a returned window; carry `pid` for exact process binding. |
| `list_apps` | none | Return installed/running apps and windows. |
| `launch_app` | `app` | Launch and return matched pid plus current windows. |
| `get_window_state` | `window` | Return a screenshot by default; request AX text explicitly, with optional `max_tree_nodes`/`max_tree_depth`, when needed. |
| `click` | `window`, element index or `x`/`y` | Click by AX or pixels. |
| `press_key` | `window`, `key` | Press a key or `+`-separated chord; use keysym names such as `plus`, `colon`, or `ISO_Left_Tab` when the symbol conflicts with the separator. |
| `type_text` | `window`, `text` | Send literal Unicode. |
| `scroll` | `window`, `x`, `y`, `scrollX`, `scrollY` | Pixel scroll; `deliveredDelta` reports symmetric integer quantization, an all-zero quantized event is rejected, and success settles for 100 ms before refresh. |
| `set_value` | `window`, `element_index`, `value` | Set editable AX value. |
| `drag` | `window`, start/end coordinates | Left-button drag. |
| `perform_secondary_action` | `window`, index, `action` | Run listed AX action. |
| `activate_window` | `window` | Bring app/window forward. |

Fallback `press_key` covers F1-F20, arrows, punctuation, shifted keysyms, and
the numeric keypad. It also accepts common aliases (`Spacebar`, `Del`,
`Insert`, `Prior`, `Next`, `Caps_Lock`) and `KP_*` navigation/equal/delete
variants. Modifier chords post physical modifier down/up events around the
primary key, retain exact pending releases for shutdown cleanup, and never
same-call replay a primary release that already exhausted its retry.
After a completely released chord, fallback waits 100 ms before returning so a
fresh observation does not race the target app's shortcut handler.

Fallback `launch_app` accepts a bundle ID, display name, or `.app` path. User
paths such as `~/Applications/Foo.app` are expanded and resolved before both
the macOS launch request and process matching.

Extended fallback tools: `computer_use_health`, `permission_status`,
`request_permissions`, `move_mouse`, `mouse_down`, `mouse_up`,
`get_cursor_position`, `clipboard_get`, `clipboard_set`, plus the unrestricted
desktop family `get_desktop_state`, `desktop_click`, `desktop_press_key`,
`desktop_type_text`, `desktop_scroll`, and `desktop_drag`.
Window and desktop scroll results expose the integer `deliveredDelta`; if both
requested axes quantize to zero, no event is posted and the call asks for a
larger delta instead of reporting a false delivery.
`get_desktop_state` returns one image and screenshot ID per active display so
mixed Retina/non-Retina layouts do not share an incorrect global scale.
Fallback screenshots are published only after complete capture/encoding inside
a current-user-owned 0700 temporary directory. Timeouts and failed window or
desktop captures delete any unpublished partial PNG immediately.
Large fallback PNGs are best-effort resampled with macOS `sips` to a 1,280 px
longest edge and a 900 KB transport target. The returned and cached
`width`/`height` are read from the final published PNG, so screenshot-bound
coordinates continue to map exactly across Retina and mixed-scale displays. If
the system resizer fails, the complete original remains the observation.
Window screenshot plus AX text, and all screens in one desktop call, are each
transactional observations: if any requested channel or display fails, every
new cache handle and PNG from that failed call is removed.
Fallback AX observations default to 1,200 rendered nodes and 64 levels, enable
Chromium/Electron manual/enhanced Accessibility best-effort, and merge the
window, menu bar, `AXRows`, `AXContents`, and `AXVisibleChildren` into one
actionable generation. `max_tree_nodes` accepts 1–10,000 and
`max_tree_depth` accepts 1–256. Extra selected rows/cells/children remain capped
at 64; `truncated:true` plus `truncation_reasons` means re-observe with the
specific larger budget or a narrower target instead of assuming omitted items
are actionable.
Each rendered AX line keeps the raw role/index plus compact, non-default
semantics: subrole, distinct description/help/placeholder/identifier, value,
`selected`/`expanded`/`disabled`/`settable` traits, editable value type, and up
to eight advertised actions. Duplicate defaults are omitted to keep the tree
groundable at the 1,200-node default.
For raw mouse tools, a `screenshotId` without `window` binds the supplied
coordinates to that exact fresh desktop image; it is never silently ignored.
When `move_mouse`, `mouse_down`, or `mouse_up` uses window-image coordinates,
pass that window's fresh `screenshotId` too so Retina pixels map to logical
Quartz points. The core `click`, `scroll`, and `drag` tools accept the same
field; always include it when x/y came from a returned screenshot.
Fallback `click`, `drag`, and `mouse_down` post `MouseMoved` at the resolved
point before button-down. Multi-click repeats this move/down/up sequence for
each click, preserving native hover behavior and click counts. Direct clicks
leave 30 ms between move, down, and up so foreground apps can consume each
physical transition before the next one; timed moves and drags publish their
last interpolated frame at the requested duration boundary. Drag endpoints,
raw mouse down/up transitions, and final pointer read-back use the same 30 ms
app-event-loop settlement interval. All fallback mouse sequences reuse one
Quartz `hidSystemState` event source so move/down/drag/up share hardware state.

For a held button, raw `mouse_up` at a changed point posts the corresponding
dragged event before release; the same endpoint does not invent a drag. It
retries release once and retains an unconfirmed release for shutdown cleanup.

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
The two readiness paths are independent: Screen Recording alone makes
`pixelObservationReady`/`desktopObservationReady` true for screenshots, while
`inputControlReady` stays false; `fullComputerUseReady` becomes true only when
both permissions are active.

Fallback `type_text` and `desktop_type_text` publish structured
`code:"type_text_incomplete"`/`effect:"partial"` errors if a later Quartz chunk
cannot be delivered. The response carries `delivered_chars` and
`retry_from_character`; all prior observation handles are expired, so refresh
state and send only that remaining suffix.
Each successfully completed Unicode key pair settles for 20 ms before the next
chunk. Chunks pack at most 64 UTF-16 units without splitting a Unicode code
point, matching the reference runtime's event size while preserving literal
text and the partial-delivery replay boundary.

Fallback `clipboard_set` verifies an exact pasteboard read-back before returning
`effect:"confirmed"`. A clear-then-write failure is reported as
`code:"clipboard_update_incomplete"`, `effect:"partial"`, and
`clipboard_cleared:true`; call `clipboard_get` before any retry. A successful
write whose read-back differs uses `clipboard_verification_mismatch` with the
same partial-effect semantics.

Every fallback input attempt conservatively expires its prior window or desktop
observation even when native delivery raises. Key-down and mouse-down events are
registered before posting; matching releases are retried immediately and again
during MCP shutdown if interruption occurs between the pair. Shutdown makes up
to three bounded attempts with 10 ms gaps; duplicate releases are safe and
never replay the matching down event.
If both immediate release attempts fail, structured
`click_release_incomplete`, `key_release_incomplete`, `drag_incomplete`, or
`drag_release_incomplete` errors report a partial effect and whether a release
remains pending. Re-observe instead of replaying the full click/key/drag.
Native post failures that cannot prove whether macOS accepted the event use a
`*_delivery_unknown` code with `effect:"unverifiable"`.

Fallback action results mirror the primary verdict vocabulary. Quartz input and
AX actions without a generic post-condition return `effect:"unverifiable"` and
`verified:false`; `ok:true` means dispatch succeeded, not that the UI outcome is
complete. Fallback `set_value` returns `confirmed` only after an exact AX value
read-back; missing or mismatched read-back recommends a freshly grounded pixel
route and never claims completion.
An advertised `AXPress` or secondary AX action that does not report success
returns `suspected_noop` with a pixel recommendation; the same tool call never
also emits a coordinate click. Refresh state before crossing that delivery rung
so an AX action that actually landed cannot become a double click.
`set_value` likewise crosses to focus/select/type in the same call only when AX
explicitly marks the value attribute non-settable. A failed write to a settable
or unknown attribute returns `suspected_noop`; refresh before any retyping.
App launch and foreground activation conservatively invalidate prior fallback
observations even when their completion probe fails. A launch timeout or an
accepted launch that cannot be matched returns a structured unknown effect;
re-list apps/windows before deciding whether another launch is needed.
Exact-window activation first performs `AXRaise`. If unsupported, it sets the
bound window's `AXMain=true` and then `AXFocused=true`; frontmost pid and focused
AXWindowNumber must still match before any input is sent.

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
Native permission requests invalidate all fallback observations because a TCC
prompt or System Settings can alter focus/layout. AX native-object caches are
also bounded to 32 observed windows; older window indexes may be evicted sooner
after broad enumeration, so always observe the final target immediately before
using an index.

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
proves telemetry persisted inside plugin data is disabled without changing the
user's unrelated `~/.cua-driver` preference, turns off the separate update check,
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
atomically publishing it to a dependency-closure-versioned runtime directory,
so an interrupted install is never reused as healthy. Plugin-only releases
reuse the same tested PyObjC path; the identifier changes only with the native
wheel closure, limiting reinstall and TCC executable-path churn.

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

`install.sh` also runs the trusted signed-app permission grant flow. It
preserves any default Cua daemon that was already running and stops only a
temporary default daemon created by that grant command.

`doctor.sh` starts or reuses only the versioned plugin daemon, then prints its
actual permission mode before checking the direct MCP runtime.
