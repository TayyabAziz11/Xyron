# World State Engine

## Purpose

The single, continuously-updated reasoning context for Xyron. Before this
existed, consumers (file resolution, follow-up command resolution, agent
planning) each queried individual sensors — `window_context`,
`workspace_context`, `explorer_context`, `active_context`, `context_stack` —
directly and separately. `world_state.py` aggregates them into one publish/
subscribe store so new consumers read one thing
(`world_state.get_context()`) instead of importing five modules.

**Scope boundary, stated explicitly because it's easy to assume otherwise:**
this phase did *not* rip out or replace `active_context.py` /
`context_stack.py` / `memory_service.py` / `context_memory.py` — those are
live, tested, and wired into the production voice pipeline
(`voice_ws.py`, `follow_up_resolver_v2.py`) in multiple places. World State
*aggregates* them (`context_stack` is used directly as the Entity Tracker,
not duplicated) and is where new context (workspace/Explorer/goal/
activity-timeline/focus) lives going forward. Full consolidation of the four
legacy systems is deliberately deferred — see TECHNICAL_DEBT.md.

## Architecture

```
world_state.py — WorldStateService (singleton)
 ├─ publish(field, value, source)  — diff-only; no-op if value unchanged
 ├─ subscribe(field, callback)     — "*" subscribes to every field;
 │                                   dispatched off-thread, never blocks the publisher
 ├─ refresh_sensors()              — window/workspace/Explorer/project,
 │                                   moved here from file_resolver.py in Phase 1.6
 ├─ record_action(...)             — feeds ActivityTimeline + GoalTracker together
 ├─ FocusGraph (dataclass)         — active_application/active_window/
 │                                   focused_object/selected_object
 └─ get_context(refresh=bool)      — THE Reasoning Context API

activity_timeline.py — bounded (200) chronological action log, distinct
                        from the persistent SQLite history/episodic_memory

goal_tracker.py — categorizes fine-grained tool/workspace signals into
                   broad domains (coding/writing/shopping/travel/research/
                   media/design/...), keeps a short history

context_stack.py (pre-existing, reused as-is) — the Entity Tracker:
                   typed, ordered entity history with verb-aware pronoun
                   resolution ("it", "that", "the same folder")
```

## Data flow

```
Sensors (window_context, workspace_context, explorer_context)
   │  polled every ~2.5s by the Perception Engine's event loop
   ▼
world_state.refresh_sensors() ──publish()──> _fields dict (diff-only)
   │
Action-triggered publishers (smart_open, voice_ws.py post-tool-execution
block, active_context's goal bridge) ──publish()/record_action()───┐
                                                                     ▼
                                                          get_context(refresh=?)
                                                                     │
                                        ┌────────────────────────────┼─────────────┐
                                        ▼                            ▼             ▼
                                 file_resolver.py            Planning Engine   Reasoning API
                                (tiers 1/2/6, single           (agent_runtime   (GET /api/v1/world/*)
                                 sensor call shared)            .launch() enrichment)
```

## Reasoning Context API shape

`get_context()` returns: `current_application`, `current_foreground_window`,
`current_workspace`, `current_project`, `current_explorer_folder`,
`current_browser`/`current_url`/`current_tab`/`current_product` (Phase 2/3
publishers), `current_document`, `current_file`, `current_selection`,
`current_visible_error`, `monitors`, `current_conversation_entities`/
`active_entities` (from `context_stack`), `current_task`, `current_goal` +
`goal_history`, `current_intent`, `recent_actions`, `recent_files`/
`recent_folders`, `current_focus_object`, `focus_graph` — plus backward-compat
keys (`window`, `active_app`, `active_folder`, `active_project`, `hour`,
`weekday`) consumed unchanged by `file_resolver.py` and
`fs_index.get_usage_affinity()`.

## Why `refresh` is a parameter, not always-on

`refresh=True` forces a fresh sensor query (what `file_resolver.resolve()`
needs — it can't work off a snapshot that might be seconds stale while
actively resolving a command). `refresh=False` (the default) returns
whatever the background loop last published, for lower-priority reads
(dashboards, logging) that shouldn't pay even a cached-lookup cost.
Measured: a cold `refresh=True` call costs 1-3s (dominated by the
PowerShell window-context subprocess — see PERCEPTION_ENGINE.md); once
`window_context`'s own 2s cache is warm, subsequent calls are sub-5ms.

## Extension points

- New owned fields: add to `_OWNED_FIELDS` in `world_state.py`, publish via
  `world_state.publish()`, surface in `get_context()`.
- New publishers: any subsystem can call `world_state.publish()` — the
  intended pattern (see `perception_engine.py`'s docstring) is "observe and
  publish," never "observe, decide, and act" in the same module.
- New subscribers: `world_state.subscribe(field_or_"*", callback)` — useful
  for a future reasoning layer that reacts to specific state transitions.
