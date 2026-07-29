# macOS Computer Use for ZCode

[简体中文](./README.zh-CN.md)

A local ZCode plugin and Skill for controlling macOS like a human: observe real
windows, read screenshots and Accessibility trees, move/click/drag, type,
scroll, launch apps, and verify the visible result.

The project mirrors the useful Codex Computer Use loop—select a real returned
window, observe, act once, refresh, verify—without adding Codex's Windows app
restrictions or action-confirmation taxonomy. Its primary backend controls
background windows without moving the user's pointer or stealing focus. A
direct Quartz/PyObjC backend is available when the primary backend cannot act.

The plugin adds no app allowlist, risky-action classifier, approval phrase,
remote vision service, or target deny list. ZCode **Full Access** and macOS's
one-time Accessibility/Screen Recording TCC grants are the remaining
authorization boundaries.
The plugin-owned daemon also enables Cua Driver's optional legacy `page`
mutations, so Full Access does not silently retain a second dependency-level
ceiling on JavaScript, DOM clicks, or text delivery.

## Architecture

| Layer | Role |
| --- | --- |
| `$macos-computer-use` Skill | Routes the agent through a fresh observe → act → verify loop |
| `macos-computer-use` MCP | Cua Driver 0.13.1, background AX/pixel input, dedicated unrestricted daemon |
| `macos-computer-use-fallback` MCP | Repository-owned 28-tool Quartz/PyObjC direct window/desktop input server |
| ZCode plugin + marketplace | Installs the Skill and both local stdio MCP servers |

The primary delivery ladder is background Accessibility → background pixels →
temporary foreground delivery → direct native fallback. This design is informed
by the existing Hermes macOS Computer Use Skill, Open Computer Use's native
macOS implementation, and the open-source Cua Driver, while the ZCode packaging,
unrestricted launcher, fallback runtime, and tests live in this repository.

## Install in ZCode

1. Open **Settings → Plugins → Marketplace** in ZCode.
2. Click **+** and add `BraveCowNoFear/computer-use-for-zcode-macos` or this repository's
   Git URL.
3. Install and enable **macos-computer-use**.
4. Select **Full Access** for a task that should run without ZCode command
   confirmations.
5. Start a new task, type `/`, choose **macos-computer-use** from the **Skills**
   group, then ask:

   ```text
   Check macOS permissions, open Notes, create a note called Trip checklist, and verify it is visible
   ```

6. Grant Accessibility and Screen Recording to `CuaDriver.app` when macOS asks,
   then restart ZCode. If its signed setup panel does not appear, run
   `bash plugins/macos-computer-use/scripts/install.sh` once; Cua Driver 0.13.1
   intentionally keeps prompt-capable TCC setup out of the agent-callable MCP
   and that human-run command uses its trusted LaunchServices grant route. If
   the direct fallback is used, macOS may also ask for the Python/ZCode
   responsible app.

On first start, the primary launcher downloads the pinned universal Cua Driver
release archive, verifies its SHA-256, atomically publishes the signed app in
the plugin data directory, and checks Gatekeeper plus the expected Cua AI Team
ID and signing authority. Every reuse also requires the main driver and cursor
helper bytes to match their exact pinned-release SHA-256 values, so another
validly signed, same-version bundle is not accepted as the tested release. It
proves a telemetry preference persisted only under
plugin data is off, disables the separate update check, and launches a plugin-owned daemon with
`--no-permissions-gate --permission-mode unrestricted
--dangerously-bypass-approvals`. Disabling Cua Driver's startup permission
onboarding prevents its background service from reopening UI or restarting
after the MCP socket is live; it does not grant or bypass macOS TCC. Run the
human-owned install/grant command once for those system permissions. Reuse requires
the exact tested app version and tool surface, plus a live status readback of
`permission mode: unrestricted` with no user, managed, or session policy
configured; reuse also requires the lightweight inventory and session-effective
configuration tools. The socket is private, per-user, and versioned. The fallback
requires CPython 3.10 through 3.15, creates a private
environment, and installs the complete exact-tested five-package PyObjC 12.2.1
binary-wheel closure without dependency re-resolution.
All published CPython 3.10–3.15 wheel variants are allowlisted by SHA-256, and
pip runs in hash-required mode.
First-run dependencies are built and self-tested in a staging environment,
then atomically published to a dependency-closure-versioned runtime directory
without writing the user's shared pip cache. Skill, documentation, and other
plugin-only updates reuse that path; it changes only when the tested native
wheel closure changes, avoiding needless reinstalls and Python TCC path churn.

The plugin never replaces a global `/Applications/CuaDriver.app` or stops the
user's unrelated Cua daemons. macOS TCC cannot be bypassed by any plugin.
Accessibility-only tasks can continue without Screen Recording by explicitly
omitting screenshots; pixel and desktop routes still require it.
Public `check_permissions` calls are read-only; `prompt:true` is rejected even
in unrestricted mode because the macOS approval UI must remain user-owned.
This TCC rule does not add an app allowlist, action classifier, or per-action
approval to ordinary Computer Use.

This layout follows ZCode's current
[plugin and marketplace specification](https://zcode.z.ai/en/docs/plugin),
including `.zcode-plugin/plugin.json`, `.mcp.json`, and the supported plugin
root/data template variables.

## What agents can do

- Discover and launch native apps and select exact returned windows.
- Take a fast content-free inventory of running apps and visible windows before
  binding one returned pid/window to the full screenshot-plus-AX loop. This
  read uses AppKit/WindowServer without a TCC prompt; its text summary is an
  exact rendering of the same structured identities, not a second source of
  target truth.
- Atomically select a locally installed cursor theme and reduced-motion mode in
  `start_session`, so the run never flashes the default theme first.
- Temporarily opt one MCP connection into uncapped native-size window PNGs for
  pixel-perfect verification, prove a peer connection retains its own
  effective setting, and restore without changing the persisted global default.
- Consume the signed primary's real observation shape: AX `tree_markdown` and
  screenshot arrive together by default, screenshot omission is the only
  modality performance knob, and file output preserves returned image geometry
  without pretending the compatibility-only `capture_mode` changes capture.
- Hand an existing local file/folder or resource URL to an exact native app
  through `launch_app.urls`, with structured missing-path errors, returned
  pid/window binding, focus-suppression readback, fresh observation, and exact
  test-window cleanup instead of shell `open` or AppleScript activation.
- Diagnose the pinned driver through its stable schema-v1 health report before
  control, separating core runtime failure, signed bundle attribution, TCC
  grants, AX readiness, and read-only screen-capture status without prompting.
- Launch directly into a matched pid/window set, then capture a screenshot and
  indexed Accessibility tree together on both backends.
- Bind fallback observations to app, pid, CGWindowID, and AXWindowNumber when
  available; refuse ambiguous Accessibility windows instead of guessing.
- Enable Chromium/Electron Accessibility visibility best-effort, merge window,
  menu-bar, row, contents, and visible-child AX sources, and expose adjustable
  1,200-node/64-level observation budgets for large pages and lists. Compact
  lines retain subroles, non-default selected/expanded/disabled/settable state,
  value types, help, placeholders, identifiers, and advertised actions.
- Click AX elements or window-local pixels, double/right-click, drag, and
  scroll. Direct fallback clicks, drag starts, and raw mouse-down actions first
  move the pointer to the grounded point, matching the physical hover/down
  sequence used by a person. Releasing a held button at a new point posts a
  final drag event before mouse-up and retains unconfirmed releases for cleanup.
  The signed primary backend's double- and right-click tools are also mandatory
  runtime capabilities and are exercised against visible state by the live gate.
  The same gate now verifies a timed foreground slider drag and a targeted
  background wheel event against newly observed pixels and visible readback.
- Crop a small region from a fresh primary window screenshot, inspect the
  returned JPEG, and bind one immediate click/type coordinate back to the same
  pid/window; the live gate verifies that zoom-space translation hits a visible
  AppKit target before discarding the per-pid context.
- Verify primary literal text, a pid-bound Command-Shift-K focused-field shortcut,
  and an element-bound Space key independently before the final mouse click.
- Require a snapshot-bound `set_value` to return confirmed AX readback and a
  fresh slider element to expose the requested value independently of dragging.
- Exercise the primary session's real `auto` to desktop transition with a
  required reason, verify `get_session_state`, move the real pointer from fresh
  primary-display pixels, restore its original multi-display position, and
  re-observe the desktop. Emergency cleanup also restores the pointer on error.
- Launch a new isolated Calculator instance by bundle ID, reject any pid that
  existed before the call, bind a window owned by the returned pid, then close
  that instance with foreground Command-Q; `kill_app` is reserved for bounded
  cleanup if the cooperative exit does not land.
- Type Unicode, press Mac shortcuts with real modifier down/up transitions,
  accept common X11/macOS and keypad-navigation key aliases, and set
  Accessibility values.
- Keep normal app work in the background; escalate to foreground only after a
  verified delivery failure. Foreground fallback first asks WindowServer to
  front the exact returned pid/window pair through the same local SkyLight path
  used by Cua Driver, then falls back to public AppKit activation. It raises the
  exact AX window, tries its main/focused attributes when `AXRaise` is absent,
  and confirms WindowServer's foreground PSN (or the public frontmost pid) plus
  the focused AXWindowNumber, CoreFoundation identity, uniquely bound
  title/geometry signature, or the pid's sole exposed AX window before input.
- Persistently front one exact returned window only for an explicit frontmost
  outcome or a focus-proxy surface such as remote desktop. The primary live gate
  verifies the returned activation path, the app's fresh `active` readback, and
  the same re-listed window before any further input; one-off foreground input
  keeps the normal front-act-restore route.
- Drive native app menus semantically with a fresh frontmost menu-bar item,
  `pick` to open it, a second snapshot for the new menu item, and a final visible
  verification. The live gate proves this two-snapshot AX route on the
  disposable fixture instead of guessing at hidden menu children.
- Give every declared primary session its own colored semantic cursor overlay.
  Click, drag, scroll, text, key, navigation, and app actions animate visibly
  without moving the user's real pointer; the cursor can be hidden and read
  back per session, and `end_session` removes it. A compact task-oriented
  session label is shown in its local badge, making concurrent agents easy to
  distinguish without exposing secrets or copied content.
  Its per-session Bezier path, arc, spring, speed/timing, and idle visibility
  can be tuned for a human-readable demo, read back, and restored without
  changing the real input target or physical-pointer semantics.
  Its installed artwork and reduced-motion mode can likewise be selected,
  independently read back, and restored per session; Agent calls cannot inject
  paths, URLs, source artwork, or inline animations.
  A window-scoped `move_cursor` can seed a clearly visible demo glide without
  touching the real pointer; desktop scope remains an explicit real-pointer
  operation grounded in fresh full-desktop pixels.
- Bind one returned Chromium/Electron native window to an exact page target,
  then use snapshot-scoped semantic refs for navigation, trusted or explicit
  DOM-event clicks, replacement-aware typing, pointer gestures, page dialogs,
  uploads, and downloads. Every mutation invalidates old refs and is followed
  by a fresh page snapshot; browser chrome, native pickers/dialogs, unsupported
  webviews, and dependency-refused routes stay on the native control ladder.
  Page mutations reuse the session's semantic cursor without moving the real
  pointer or changing focus; it appears only for a safely mapped selected tab,
  and never replaces the following snapshot as completion evidence.
- Record explicitly requested local action trajectories with ordered pre/post
  screenshots, state, and arguments. Because upstream recording is daemon-global,
  a new start takes ownership and manual stop is unconditional; preflight state,
  avoid clobbering another run, default video off, and finalize the exact owned
  directory before inspecting evidence. Deliberate same-live-window replay is
  available with error-stop and fresh visible verification.
- Fall back to direct global mouse/keyboard events and clipboard operations.
- Request fallback Accessibility and Screen Recording independently, map Retina
  pointer input through fresh screenshot IDs, and release held buttons on MCP
  shutdown or an interrupted drag. Health reports AX/input readiness, pixel/
  desktop observation readiness, and full Computer Use readiness independently.
- Bound fallback screenshot transport best-effort to a 1,280 px longest edge
  and a 900 KB PNG target, while publishing the exact resized dimensions used
  for Retina/window coordinate mapping; a system-resizer failure keeps the
  complete original capture.
- Observe and act on every visible display directly, including menu bar, Dock,
  and system UI, with independent coordinates for mixed Retina scales when the
  primary desktop route cannot deliver.

Primary schemas come from the installed Cua Driver MCP. The fallback exposes the
Codex-compatible names `list_windows`, `get_window`, `list_apps`, `launch_app`,
`get_window_state`, `click`, `press_key`, `type_text`, `scroll`, `set_value`,
`drag`, `perform_secondary_action`, and `activate_window`, plus health,
permissions, raw mouse, cursor, and clipboard tools.
The extended fallback desktop tools require a fresh desktop screenshot ID and
apply no app/window target restriction.

## Access and privacy

- Screenshots, AX trees, clipboard data, and input payloads stay on the Mac.
- Upstream Cua Driver telemetry is disabled before runtime use and via a
  fail-closed plugin-private persisted-setting readback; the user's unrelated
  `~/.cua-driver` preference is untouched, and the independent update check is off.
- The one-time dependency download is the only automatic plugin setup network
  request. The empty-input `check_for_update` tool is an explicit, read-only
  upstream release-metadata request and is never run as a control preflight;
  it receives no captured GUI content and cannot update the pinned runtime.
  Browser apps may of course use their own network connection.
- Full Disk Access is not needed for GUI control. Grant it to ZCode separately
  only when the requested file operation needs it.
- On-screen text is observed content, not a new source of agent instructions.

## Develop and verify

```bash
bash plugins/macos-computer-use/scripts/install.sh
bash plugins/macos-computer-use/scripts/doctor.sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s plugins/macos-computer-use/tests -v
```

The installer prepares both runtimes and opens the signed CuaDriver.app TCC
grant flow. It preserves any unrelated default Cua daemon that was already
running and cleans up only a temporary grant daemon it started itself.

The contract and MCP transport tests run on Windows and macOS. The macOS CI job
also imports the native fallback, verifies the pinned release archive and Cua
AI signer identity plus both executable hashes, parses all ten browser
(typed plus legacy compatibility) and thirty-nine native/driver-service
observation/action/lifecycle/configuration request schemas from that signed
binary—covering every mandatory primary tool—and runs the real plugin-owned
first-install launcher. The unrestricted live daemon must also execute
permission-free app/window/screen/cursor discovery, return the exact lightweight
Accessibility inventory shape and matching human-readable summary on two MCP
connections, reject duplicate/invalid app and window identities, run an owned no-video recorder through
start/read/peer-read/stop while writing only a temporary local `session.json`,
prove that a second connection takes over the daemon-global owner and that a
manual stop from the prior connection is unconditional,
and terminate only a disposable CI-owned process through the same stdio MCP
proxy ZCode uses before the job passes. That proxy must prove legacy page
mutation reaches normal pid/window validation instead of the upstream
default-disable guard, plus
per-connection image-configuration
isolation, stable read-only health/TCC attribution, cursor visibility readback,
and the complete session state machine: same-scope start is idempotent, a
different live scope returns the exact no-mutation conflict, `auto` escalates
only once with an explicit reason, end makes state inactive, and an explicit
new start can revive the same ID under a fresh scope before a final clean end.
App and window discovery also pins the exact nine-field app
record and its running-only summary, ten-field WindowServer record and count
summary, integer cursor position and exact position text, and logical main-display
geometry plus exact point/scale text. It cold-launches a stopped Calculator or TextEdit,
requires the exact background-launch/window summary to be reconstructed from
the structured result, validates that launch-window projection against a fresh full window
inventory, kills only that owned pid, and waits for both the
process and the eventually consistent app inventory to drop it. The public
MCP `prompt:true` path must fail at the trusted-host
macOS TCC boundary without opening permission UI. The stdio gate also rejects
malformed, duplicate, missing, or unexpected tool names, so ZCode receives
exactly the audited 49-tool surface. It also pins the tools-list envelope,
descriptions, standard MCP annotations, capability labels, and risk metadata.
The initialize handshake is equally exact: protocol `2025-06-18`, the tools-only
capability, Cua Driver `0.13.1` identity, and its macOS workflow instructions.
The same live connection must return the exact JSON-RPC parse-error,
method-not-found, and missing-tool-name invalid-params envelopes, keep all
notifications silent, and still serve the complete tools list afterward.
Every mandatory MCP
`inputSchema` must exactly match the same
signed binary's direct `describe` contract, and a strict window session must
start, read back, and end cleanly without TCC.
The same signed-primary gate locks ordinary tool-error semantics:
`APP_NOT_INSTALLED` and `FILE_NOT_FOUND` remain structured MCP errors without
starting the selected app, successful `kill_app` remains a text-only SIGKILL
acknowledgement followed by observed process disappearance, and every nullable
session-state field plus each exact lifecycle summary/error remains stable
through idempotency, conflict, one-way escalation, end, the public daemon's
post-end resurrection guard, and explicit lifecycle-exempt revival.
Successful `launch_app` presentation is pinned too: its app name, positive pid,
quoted or untitled returned windows, IDs, and next-step hint must exactly mirror
the same structured payload without becoming an alternate source of authority.
Its schema-v1 health report is now response-pinned as well: all eight macOS
checks remain in canonical order, every entry obeys the exact status/hint/data
shape, `overall` agrees with the check statuses, and an include-only probe
returns the other seven checks as explicit skips without touching TCC.
Configuration responses are pinned end to end too: the seven-field read shape,
release identity/default PiP fields, both accepted image-cap setter shapes,
exact session-scoped acknowledgement, peer-connection isolation, restoration,
and the no-mutation structured error for retired `capture_scope` are verified.
The permission preflight is fully response-pinned as well: empty and explicit
read-only calls must agree, source identity must bind to the live signed daemon
pid/path/bundle, the human-readable summary must match the booleans, and
`prompt:true` must remain the exact trusted-host TCC refusal.
The fresh-session semantic cursor is also response-pinned: its complete state,
embedded `cua.default` theme identity, nine human-like motion values, idle
or in-flight bounded visual semantics, idempotent theme/motion setters, enabled
toggle, and restored readback must agree without moving the real pointer or
requiring TCC.
The ordinary repository suite derives that same 49-tool union from the pinned
native/browser contracts and requires every exact tool name to be discoverable
through the Skill documentation. This keeps service helpers such as the
diagnostic-only `get_screen_size` and explicit `check_for_update` advisory from
being schema-visible but operationally hidden from ZCode agents.
It also rejects the fallback-only `include_text` field in the primary workflow
and locks the structured macOS browser recovery codes plus exact local
trajectory evidence layout into the agent-facing references.
A real background click/type/screenshot loop requires an unlocked interactive
Mac with TCC grants. Hosted runners do not guarantee those grants, so each
macOS job runs the disposable direct-fallback GUI smoke after its native TCC
readiness check, records the signed driver's exact TCC readback, and additionally
runs the primary GUI smoke only when both signed-app grants are genuinely present.

On such a Mac, run the disposable end-to-end gate below. It creates its own
temporary AppKit window, verifies signed-driver identity plus background
screenshot/type/click on the primary backend, then exercises full-desktop
shortcut/text input plus real Quartz coordinate click, slider drag, raw
held-button sequence, and scroll through the direct fallback. That pixel path
captures after each state change and verifies the fixture's local, atomically
published control state instead of assuming a bare Python process exposes a
complete app-bundle AX tree. Both paths re-observe the visible result. The gate restores the original
pointer position before closing its fixture. To tolerate a real foreground focus
race without weakening the result gate, the fallback first confirms typed text
through the fixture's atomically published local field state after a fresh pixel
observation; if absent, it re-observes, refocuses, and types once more. This does
not assume that a bare Python process exposes its text value through AX. If the first physical submit click has no local visible effect,
it takes another screenshot, recomputes the bound coordinates, and clicks once
more. A second missing effect still fails the gate.
It touches no user document:

```bash
bash plugins/macos-computer-use/scripts/live-smoke.sh
```

## Project layout

```text
marketplace.json
plugins/macos-computer-use/
  .zcode-plugin/plugin.json
  .mcp.json
  macos_cua/                  # direct native fallback MCP
  scripts/                    # primary/fallback launch and diagnostics
  skills/macos-computer-use/  # ZCode agent workflow
  tests/
```

## Upstream and license

Primary background control depends on [Cua Driver](https://github.com/trycua/cua)
(MIT). The routing model was informed by the
[historical Hermes macOS Computer Use Skill](https://github.com/NousResearch/hermes-agent/blob/17dfc6bec4a8b7fd840d479c33e9a7b2449f805d/skills/apple/macos-computer-use/SKILL.md)
and the MIT-licensed
[Open Computer Use macOS Skill and runtime](https://github.com/iFurySt/open-codex-computer-use/tree/a265277f6677ef00a1c597f54616cc3410d8d297/skills/open-computer-use).
This project is MIT licensed; see [LICENSE](./LICENSE) and
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
