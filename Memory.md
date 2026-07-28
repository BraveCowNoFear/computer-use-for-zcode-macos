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
- Remaining hardware gate: run a live background screenshot/action/re-screenshot loop on an unlocked user Mac after granting CuaDriver.app Accessibility and Screen Recording. Hosted GitHub runners cannot receive interactive TCC grants.
