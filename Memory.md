# Project Memory

## Goal

Create a ZCode plugin for macOS that mirrors the practical Codex Computer Use loop while granting the agent the broad local access selected by the user.

## Current architecture

- Distribution: repository-backed ZCode marketplace.
- Plugin: `plugins/macos-computer-use`.
- Agent guidance: `skills/macos-computer-use/SKILL.md`.
- Tool transport: two local stdio MCP servers.
- Primary implementation: signed `CuaDriver.app` 0.12.6, launched on a private per-user/version socket in unrestricted mode for background AX/pixel control without focus stealing; startup verifies exact app version, required tools, and the daemon's reported permission mode.
- Fallback implementation: repository-owned Python/PyObjC MCP using AppKit, Quartz, and macOS Accessibility for unrestricted direct foreground window and full-desktop control when the driver is unavailable or refuses an operation.
- Authorization boundary: ZCode Full Access plus macOS Accessibility and Screen Recording TCC grants.
- Privacy boundary: runtime screen, Accessibility, clipboard, and input payloads remain local; upstream Cua Driver telemetry is disabled by environment and persisted preference.

## Compatibility contract

The core tool names mirror Codex Computer Use: `list_windows`, `get_window`, `list_apps`, `launch_app`, `get_window_state`, `click`, `press_key`, `type_text`, `scroll`, `set_value`, `drag`, `perform_secondary_action`, and `activate_window`.

## Product decisions

- Ship both a ZCode plugin manifest and a Codex-compatible manifest so the same source can be imported by either host.
- Do not implement OpenAI's action confirmation taxonomy or Windows target denials in this plugin.
- Retain correctness constraints: select exactly one returned window, invalidate stale Accessibility indexes after observation changes, validate screenshot IDs when supplied, and refresh after actions.
- Add raw cursor, mouse button, clipboard, health, permission, and fresh-screenshot-bound full-desktop tools beyond the compatibility core.
- Prefer the Cua Driver background ladder (`AX -> pixel -> foreground`) and use the repository-owned direct backend as the final no-approval fallback.
- Pin the upstream universal release by tag and SHA-256; publish it only in the plugin data directory, bind reuse to the Cua AI signer identity and tested tool surface, and prove the dedicated daemon reports unrestricted mode.

## Verification status

- 2026-07-28: official ZCode Skill, Plugin, marketplace, MCP, and Full Access documentation reviewed.
- 2026-07-28: bundled Codex Computer Use `guidance.md`, `api.md`, and `confirmations.md` reviewed; tool contract frozen.
- 2026-07-28: historical Hermes `macos-computer-use` Skill at revision `17dfc6bec4a8b7fd840d479c33e9a7b2449f805d` and Cua Driver 0.12.6 implementation reviewed; background control adopted as the primary architecture.
- 2026-07-28: independent first-use Agent forward test passed after clarifying desktop/window routing, permission order, shortcut shapes, and sessionless fallback behavior.
- 2026-07-28: local Skill/contract/MCP stdio tests and GitHub Actions on Windows plus macOS passed, including native AppKit/ApplicationServices/Quartz imports and the pinned release checksum.
- 2026-07-28: optimization audit hardened crash recovery and primary daemon identity/mode verification, then added a 28-tool direct fallback with full-desktop screenshot-bound mouse/keyboard control.
- 2026-07-28: added an opt-in disposable AppKit live smoke gate covering signed-driver attribution, primary background screenshot/type/click, fallback desktop keyboard delivery, native MCP image blocks, exact window binding, re-observation, visible verification, and cleanup without user-document mutation.
- 2026-07-28: pinned the 51 MB universal release archive and required a valid macOS code signature/Gatekeeper assessment before reuse; the later plugin-owned installer removes the upstream installer's global `/Applications` and daemon lifecycle side effects.
- 2026-07-28: direct desktop fallback now captures each active display separately and binds coordinates to that display's own screenshot dimensions, avoiding mixed Retina/non-Retina global-scale drift and rejecting layout changes before input.
- 2026-07-28: released the multi-display/runtime reproducibility batch as plugin 0.3.0; the fallback now requires CPython 3.10+, pins PyObjC 12.2.1 exactly, and accepts binary wheels only in install, first-run, and CI paths.
- YAML plain scalars ending a colon-bearing CLI token immediately before whitespace can invalidate an Actions workflow before jobs are created; quote the complete `run` value when using `--only-binary=:all:`.
- Apple's `lipo -verify_arch` syntax requires the input file before the command and architecture list (`lipo "$binary" -verify_arch arm64 x86_64`).
- 2026-07-28: comparison with the current Hermes/cua-driver macOS Skill exposed that `unrestricted` is still below configured Cua user/managed policy ceilings. Plugin 0.3.1 clears inherited policy variables for its dedicated launch and accepts the daemon only when status proves user, managed, and session policies are all absent.
- 2026-07-28: plugin 0.4.0 removed a fallback parity detour by returning matched pid/window handles from `launch_app`; plugin 0.8.0 later aligned `get_window_state` with the current Codex screenshot-only default while retaining explicit AX-only or combined observations.
- 2026-07-28: plugin 0.4.1 separates Accessibility readiness from pixel readiness. AX-only tasks can continue with `include_screenshot:false` when Screen Recording is absent; permission UX is deferred until the requested route genuinely needs screenshots, coordinates, or desktop state.
- 2026-07-28: plugin 0.4.2 closes the remaining PyObjC drift path discovered in the real macOS CI install log: `pyobjc-core` and `CoreText` are now explicit 12.2.1 pins alongside the three top-level frameworks, and every install uses `--no-deps --only-binary=:all:`.
- 2026-07-28: plugin 0.4.3 validates the actual PyObjC wheel range, CPython 3.10 through 3.15, before creating an environment; future unsupported interpreters fail with a precise diagnostic instead of a late pip resolution error.
- 2026-07-28: plugin 0.4.4 pins the 45 official PyPI SHA-256 values covering all normal and free-threaded CPython 3.10–3.15 variants for the complete five-package PyObjC closure; install, first-run, and CI now require hashes.
- 2026-07-28: plugin 0.5.0 makes fallback first-run crash-safe: a version-specific staging venv must install, import, and pass the MCP self-test before an atomic rename publishes it; interrupted environments are never treated as reusable.
- 2026-07-28: plugin 0.5.1 applies the screenshot handle's five-minute freshness ceiling to fallback AX element indexes too; expired native references are evicted and require re-observation instead of remaining actionable indefinitely.
- 2026-07-28: plugin 0.6.0 binds fallback screenshot IDs and AX element indexes to `(app, pid, window_id)`, so an app restart cannot reuse an old observation even if macOS recycles the numeric window ID.
- 2026-07-28: the agent workflow borrows three reliability lessons from existing macOS Computer Use skills without importing their safety gates: visible pixels override optimistic AX echoes, closed controls are re-observed before selection input, and global menus are addressed only after confirming the intended frontmost app.
- 2026-07-29: plugin 0.7.0 closes the next exact-delivery gaps: pid-aware rehydration, activation-then-rehydrate, AXWindowNumber matching with ambiguity refusal, half-open Retina bounds, independently requested TCC grants, selected/focused AX handles that remain target-window-bound, shutdown cleanup for held buttons/screenshots, and process-local handle recovery guidance.
- 2026-07-28: plugin 0.8.0 isolates the signed Cua runtime under plugin data, pins and verifies its Cua AI Team ID and signing authority, persists and proves telemetry opt-out, disables update checks, atomically repairs unhealthy fallback runtimes, refreshes app process state on every listing, validates MCP arguments at the boundary, and tests the exact PyObjC closure across normal and free-threaded CPython 3.10-3.15.
- 2026-07-28: plugin 0.8.1 makes install, fallback, doctor, and the interactive live gate share one native-runtime probe and plugin-data environment; it also injects telemetry/update opt-outs into the LaunchServices-launched signed daemon instead of assuming shell environment inheritance.
- 2026-07-28: plugin 0.8.2 makes fresh final-state evidence an explicit Skill completion gate and corrects the fallback permission recovery wording; action responses alone are never treated as proof of a visible result.
- 2026-07-28: plugin 0.8.3 moves hosted macOS coverage to macOS 15 and runs the complete contract, native fallback, signed universal Cua install, and unrestricted policy proof on both Apple Silicon and Intel runners.
- 2026-07-28: plugin 0.8.4 completes fallback keyboard parity for common X keysym-style shifted punctuation (including `plus`, `colon`, and `ISO_Left_Tab`) and left/right Command aliases.
- 2026-07-28: plugin 0.8.5 makes raw mouse screenshot IDs fail closed: window IDs use window-relative pixel mapping, desktop IDs use their exact display mapping, and supplied IDs are never ignored.
- 2026-07-28: plugin 0.8.6 routes SIGTERM through MCP backend cleanup and tracks the latest delivered position for every held mouse button, so interrupted raw drags release at the real endpoint reached.
- 2026-07-28: plugin 0.8.7 enforces the exact five-package PyObjC 12.2.1 closure inside the backend itself, so a stale checkout `.venv` cannot bypass the pinned runtime contract; health output reports the resolved versions.
- 2026-07-28: plugin 0.8.8 fails every fallback synthetic-input and Accessibility action before dispatch when Accessibility TCC is absent, while preserving screenshot-only observation; macOS-silently-discarded input is never returned as success.
- 2026-07-28: plugin 0.8.9 preserves macOS front-to-back window enumeration while normalizing screenshot `zIndex` to the Codex convention that larger values are visually above smaller ones.
- 2026-07-28: plugin 0.8.10 elevates fallback screenshot binding into the main Skill: every image-derived window coordinate carries that exact fresh `screenshotId`, preventing Retina pixels from being misread as logical Quartz points.
- 2026-07-28: macOS CI now launches the verified Cua 0.12.6 binary on a disposable socket with the permissions onboarding disabled only for this non-GUI proof, checks its real status for unrestricted mode plus absent user/managed/session policies, then stops and reaps it.
- Remaining hardware gate: run a live background screenshot/action/re-screenshot loop on an unlocked user Mac after granting CuaDriver.app Accessibility and Screen Recording. Hosted GitHub runners cannot receive interactive TCC grants.
