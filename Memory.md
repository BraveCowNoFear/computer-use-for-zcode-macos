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
- Pin the upstream installer by release tag and SHA-256; reuse only the exact tested signed app/tool surface and prove the dedicated daemon reports unrestricted mode.

## Verification status

- 2026-07-28: official ZCode Skill, Plugin, marketplace, MCP, and Full Access documentation reviewed.
- 2026-07-28: bundled Codex Computer Use `guidance.md`, `api.md`, and `confirmations.md` reviewed; tool contract frozen.
- 2026-07-28: existing Hermes `macos-computer-use` Skill and Cua Driver 0.12.6 implementation reviewed; background control adopted as the primary architecture.
- 2026-07-28: independent first-use Agent forward test passed after clarifying desktop/window routing, permission order, shortcut shapes, and stateless fallback behavior.
- 2026-07-28: local Skill/contract/MCP stdio tests and GitHub Actions on Windows plus macOS 14 passed, including native AppKit/ApplicationServices/Quartz imports and the pinned installer checksum.
- 2026-07-28: optimization audit hardened crash recovery and primary daemon identity/mode verification, then added a 28-tool direct fallback with full-desktop screenshot-bound mouse/keyboard control.
- 2026-07-28: added an opt-in disposable AppKit live smoke gate covering signed-driver attribution, primary background screenshot/type/click, fallback desktop keyboard delivery, native MCP image blocks, exact window binding, re-observation, visible verification, and cleanup without user-document mutation.
- 2026-07-28: closed the pinned-installer supply-chain gap by pinning the helper and 51 MB universal release archive too, routing the upstream installer's exact asset request to the verified bytes, and requiring a valid macOS code signature/Gatekeeper assessment before reuse.
- 2026-07-28: direct desktop fallback now captures each active display separately and binds coordinates to that display's own screenshot dimensions, avoiding mixed Retina/non-Retina global-scale drift and rejecting layout changes before input.
- 2026-07-28: released the multi-display/runtime reproducibility batch as plugin 0.3.0; the fallback now requires CPython 3.10+, pins PyObjC 12.2.1 exactly, and accepts binary wheels only in install, first-run, and CI paths.
- YAML plain scalars ending a colon-bearing CLI token immediately before whitespace can invalidate an Actions workflow before jobs are created; quote the complete `run` value when using `--only-binary=:all:`.
- 2026-07-28: comparison with the current Hermes/cua-driver macOS Skill exposed that `unrestricted` is still below configured Cua user/managed policy ceilings. Plugin 0.3.1 clears inherited policy variables for its dedicated launch and accepts the daemon only when status proves user, managed, and session policies are all absent.
- 2026-07-28: plugin 0.4.0 removes a fallback parity detour: direct `launch_app` now returns the matched pid and current window handles, `list_apps` retains the running pid, and `get_window_state` observes screenshot plus AX together by default like the primary/Codex loop.
- 2026-07-28: plugin 0.4.1 separates Accessibility readiness from pixel readiness. AX-only tasks can continue with `include_screenshot:false` when Screen Recording is absent; permission UX is deferred until the requested route genuinely needs screenshots, coordinates, or desktop state.
- 2026-07-28: plugin 0.4.2 closes the remaining PyObjC drift path discovered in the real macOS CI install log: `pyobjc-core` and `CoreText` are now explicit 12.2.1 pins alongside the three top-level frameworks, and every install uses `--no-deps --only-binary=:all:`.
- 2026-07-28: plugin 0.4.3 validates the actual PyObjC wheel range, CPython 3.10 through 3.15, before creating an environment; future unsupported interpreters fail with a precise diagnostic instead of a late pip resolution error.
- 2026-07-28: plugin 0.4.4 pins the 45 official PyPI SHA-256 values covering all normal and free-threaded CPython 3.10–3.15 variants for the complete five-package PyObjC closure; install, first-run, and CI now require hashes.
- 2026-07-28: plugin 0.5.0 makes fallback first-run crash-safe: a version-specific staging venv must install, import, and pass the MCP self-test before an atomic rename publishes it; interrupted environments are never treated as reusable.
- Remaining hardware gate: run a live background screenshot/action/re-screenshot loop on an unlocked user Mac after granting CuaDriver.app Accessibility and Screen Recording. Hosted GitHub runners cannot receive interactive TCC grants.
