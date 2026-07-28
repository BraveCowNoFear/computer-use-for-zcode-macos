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

## Architecture

| Layer | Role |
| --- | --- |
| `$macos-computer-use` Skill | Routes the agent through a fresh observe → act → verify loop |
| `macos-computer-use` MCP | Cua Driver 0.12.6, background AX/pixel input, dedicated unrestricted daemon |
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
   then restart ZCode. If the direct fallback is used, macOS may also ask for
   the Python/ZCode responsible app.

On first start, the primary launcher downloads the pinned universal Cua Driver
release archive, verifies its SHA-256, atomically publishes the signed app in
the plugin data directory, and checks Gatekeeper plus the expected Cua AI Team
ID and signing authority. It proves a telemetry preference persisted only under
plugin data is off, disables the separate update check, and launches a plugin-owned daemon with
`--permission-mode unrestricted --dangerously-bypass-approvals`. Reuse requires
the exact tested app version and tool surface, plus a live status readback of
`permission mode: unrestricted` with no user, managed, or session policy
configured; the socket is private, per-user, and versioned. The fallback
requires CPython 3.10 through 3.15, creates a private
environment, and installs the complete exact-tested five-package PyObjC 12.2.1
binary-wheel closure without dependency re-resolution.
All published CPython 3.10–3.15 wheel variants are allowlisted by SHA-256, and
pip runs in hash-required mode.
First-run dependencies are built and self-tested in a staging environment,
then atomically published to a plugin-versioned runtime directory without
writing the user's shared pip cache.

The plugin never replaces a global `/Applications/CuaDriver.app` or stops the
user's unrelated Cua daemons. macOS TCC cannot be bypassed by any plugin.
Accessibility-only tasks can continue without Screen Recording by explicitly
omitting screenshots; pixel and desktop routes still require it.

This layout follows ZCode's current
[plugin and marketplace specification](https://zcode.z.ai/en/docs/plugin),
including `.zcode-plugin/plugin.json`, `.mcp.json`, and the supported plugin
root/data template variables.

## What agents can do

- Discover and launch native apps and select exact returned windows.
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
- Type Unicode, press Mac shortcuts with real modifier down/up transitions,
  accept common X11/macOS and keypad-navigation key aliases, and set
  Accessibility values.
- Keep normal app work in the background; escalate to foreground only after a
  verified delivery failure. Foreground fallback raises the exact bound AX
  window, then tries its main/focused attributes for apps without `AXRaise`,
  and confirms the focused pid/window ID before sending input.
- Bind supported Chromium/Electron pages to typed browser tools while retaining
  native control for browser chrome, file pickers, and permission dialogs.
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
- The one-time dependency download is the only plugin setup network request;
  browser apps may of course use their own network connection.
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

The contract and MCP transport tests run on Windows and macOS. The macOS CI job
also imports the native fallback, verifies the pinned release archive and Cua
AI signer identity, and runs the real plugin-owned first-install launcher.
A real background click/type/screenshot loop requires an unlocked interactive
Mac with TCC grants, which hosted CI runners do not provide.

On such a Mac, run the disposable end-to-end gate below. It creates its own
temporary AppKit window, verifies signed-driver identity plus background
screenshot/type/click on the primary backend, then exercises full-desktop
shortcut/text input through the direct fallback. Both paths re-observe and
verify the visible result before the fixture is closed; no user document is
touched:

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
