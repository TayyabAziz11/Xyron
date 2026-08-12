# Perception Engine

## Purpose

Converts current desktop/browser/UI state into structured World State
updates. **Perception observes; it never reasons.** Nothing under
`api/services/perception/` calls into agents, command routing, or an LLM —
it only reads OS/browser/UI state and calls `world_state.publish()`. A
future reasoning layer that wants to say "this looks like an error, offer
help" consumes `world_state.get_context()["current_visible_error"]`; it does
not live in perception code.

## Architecture

```
api/services/perception/
 ├─ browser_perception.py    — Playwright/CDP via the existing
 │                             browser_workspace singleton (not new CDP code)
 ├─ desktop_perception.py    — UI Automation via the ps_session warm
 │                             PowerShell process
 ├─ selection_tracker.py     — browser DOM > desktop TextPattern >
 │                             Explorer SelectedItems > clipboard, in that order
 ├─ vision_perception.py     — LAST resort, on-demand only, 30s-throttled,
 │                             reuses screen_context_service's capture+GPT-4o-mini
 ├─ multi_monitor_manager.py — Screen.AllScreens + Cursor.Position +
 │                             Screen.FromHandle
 ├─ event_dispatcher.py      — the unified async observation loop
 └─ perception_engine.py     — orchestrator: start()/stop()/refresh_now()/
                                request_vision()
```

## Safety-critical design constraint

`browser_workspace.get_or_create_page()` **launches Chrome if it isn't
already running** — that eager-launch behavior was deliberately removed
from the voice critical path in an earlier fix. A perception loop ticking
every 2.5s must not reintroduce it. `browser_perception.refresh()` checks
`browser_workspace.is_healthy` first and does nothing at all if Chrome isn't
already CDP-connected — verified live and by test
(`TestBrowserPerceptionSafety`): calling it with no Chrome session leaves
`is_healthy` unchanged.

## "Event-driven, no polling" — honest scope

WSL2 Linux Python has no accessible hook into native Windows events
(`SetWinEventHook`, clipboard listeners, raw-input idle detection all
require a native Windows message loop — a persistent Windows-side listener
process talking back over a pipe, not attempted here). What's implemented
is short-interval (~2.5s) change-detection: every tick is cheap, and every
write goes through `world_state.publish()`, which is diff-only — nothing
downstream is notified unless something actually changed. Vision, the one
genuinely expensive tier, is excluded from the loop entirely; it only runs
on explicit request (`perception_engine.request_vision()`).

## A bug found and fixed during this build (documented so it isn't
## reintroduced)

`ps_session.py`'s warm PowerShell process treats piped stdin like an
interactive REPL. Genuinely multi-line script text — even without a
here-string — confuses its command-boundary detection and hangs until
read-timeout (reproduced directly with a bare two-line command). Every
PowerShell script routed through `ps_session.run_ps()` must be a single
semicolon-joined logical line, and `Add-Type -TypeDefinition` (custom C#
compilation) throws on redefinition the second time it runs in the same
persistent process — use `Add-Type -MemberDefinition ... -ErrorAction
SilentlyContinue` for simple P/Invoke declarations instead. All four Phase 2
PowerShell scripts (`multi_monitor_manager.py`, `explorer_context.py`,
`selection_tracker.py`, `desktop_perception.py`) follow this pattern; new
PowerShell-bridge code must too.

## Measured performance

- `ps_session.run_ps()` warm call: ~20-90ms.
- Cold `powershell.exe` spawn (first call in a process, or a script that
  still uses per-call `subprocess.run` instead of the warm session): ~400-
  800ms, observed as high as 2.7-3.2s under concurrent load. 24 files
  outside the perception package still spawn PowerShell per-call rather
  than through `ps_session.py` — see TECHNICAL_DEBT.md.
- `desktop_perception.get_ui_automation_snapshot()`: correctly identified
  the live focused VS Code integrated terminal by name in ~24-92ms once warm.
- Multi-monitor enumeration: repeatable without hanging (regression-tested
  for the bug above), ~20-40ms warm.

## Extension points

- New perception source: add a module exposing `refresh() -> dict | None`
  (async if it needs Playwright, sync otherwise), wire it into
  `event_dispatcher.tick()`, publish its fields into World State.
- Local vision backend (Qwen2.5-VL): `vision_perception._describe()` is the
  single swappable function — not installed this phase (GPU already
  carries Whisper+Kokoro+SentenceTransformer; downloading a second
  multi-GB model for the rarest-used tier wasn't justified without a
  concrete need). Swapping backends is a one-function change.
- Per-app Desktop Perception depth: `desktop_perception.py` is a *generic*
  UI Automation extractor (focused control, selected text/item,
  document-from-title), not 15 hand-built per-app parsers. Deep per-app
  content reading is future work.
