# Local recording and replay

The pinned primary daemon exposes `start_recording`, `stop_recording`,
`get_recording_state`, `replay_trajectory`, and `install_ffmpeg`. Use this family only when the
user explicitly requests a local trajectory, video, regression trace, or replay.
It is not an approval boundary and does not narrow Full Access.
`install_ffmpeg` is present for cross-platform parity but is unnecessary for
native macOS recording. If an explicitly requested non-native recording path
needs it, Full Access may call it directly with `confirm:true`; do not invent a
second approval exchange.

## Record without clobbering another run

1. Choose an explicit absolute or `~/`-rooted local output directory for this
   task. Its turn folders can contain before/after screenshots, AX state,
   `action.json`, and literal action arguments including typed text. Keep it
   local and do not upload or quote its contents unless the user requests that.
2. Call `get_recording_state({})`. Continue only when `enabled:false`. The
   recorder is daemon-global: a new start replaces the active owner, and manual
   `stop_recording({})` is unconditional. Never start over, stop, or reuse the
   directory of another live recording.
3. Call `start_recording({output_dir,record_video:false})` and require
   `enabled:true`, the exact resolved `output_dir`, and `next_turn:1`. Video is
   deliberately off by default. Pass `record_video:true` only when video was
   explicitly requested; on macOS 15+ it uses the daemon's native
   ScreenCaptureKit path and existing Screen Recording grant. If
   `video_active:false`, surface `last_error`; per-turn evidence may still work.
4. Keep the normal fresh observe -> one action -> fresh verify loop. Recording
   does not make stale element tokens, pixels, pids, or window IDs reusable.
5. Immediately before stopping, call `get_recording_state({})` again and require
   that the live `output_dir` is still this run's directory. Only then call
   `stop_recording({})` and require `enabled:false`. Closing the owning MCP
   connection also performs ownership-scoped cleanup, but explicit stop is the
   normal finalization path.
6. Inspect the requested artifacts locally. A recorded action should have one
   ordered `turn-NNNNN/` with `action.json`, evidence, and the available before/
   after state and images. Do not claim video success unless `video_active` was
   true and the finalized `last_video_path` exists after stop.

Each complete turn uses the pinned evidence layout:

- `before_state.json` and `after_state.json` carry the same `tree_markdown` and
  `element_count` shape as a primary window observation.
- `before.png` and `after.png` are exact target-window images; capture remains
  window-scoped even when another window visually covers the target.
- `evidence.json` classifies each requested capture phase, including missing
  evidence, instead of letting an absent artifact look successful.
- `action.json` records the tool, full arguments, result summary, pid, optional
  click point, and timestamp. It can therefore contain literal user input.
- `app_state.json`/`screenshot.png` are compatibility aliases for the after
  state/image. Click-family actions may also produce `click.png`, the before
  image with the resolved click point marked; its absence never proves that a
  click landed.

## Replay deliberately

`replay_trajectory` re-invokes recorded mutating calls; it does not replay the
read-only observations that grounded them. Before replay, inspect every local
`turn-NNNNN/action.json` and confirm its tools and arguments match the user's
request. Continue only while every recorded pid/window is still the same live
process/window and the relevant layout/geometry has not changed. Element
indices and tokens are snapshot-scoped and must not be replayed; limit replay
to a same-live-window pixel/keyboard trajectory whose coordinates remain valid.

Make sure recording is disabled, then call
`replay_trajectory({dir,delay_ms,stop_on_error:true})`. Use a human-observable
delay when the user wants to watch. Read its attempted/succeeded/failed counts,
then re-list the exact window and take a fresh state to verify the visible final
outcome. Never infer success from the replay count alone, and never replay into
an active recorder or a restarted/reused pid.
