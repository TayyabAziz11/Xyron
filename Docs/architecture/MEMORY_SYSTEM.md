# Memory System

## Purpose

Lets Xyron resolve pronouns and follow-up references ("open it", "delete
them", "the same folder") and recall facts/history across turns and
sessions, without an LLM call on every command.

## Architecture reality: four overlapping systems, by history not by design

This is the single most important thing to understand about Xyron's memory
layer, and it's documented here explicitly because it isn't obvious from
any one file:

| System | File | Scope | What it tracks |
|---|---|---|---|
| Typed slots | `memory_service.py` | session + persisted facts | `last_app`/`last_file`/`last_folder`/`last_url` typed slots, `deque(maxlen=40)` short-term turns, long-term facts (`~/.ai-operator/memory.json`), disambiguation-match storage |
| Goal/platform inference | `active_context.py` | session, 15-min TTL | `current_platform`, `current_goal` (fine-grained: `file_management`/`app_installation`/...), `current_folder`, `current_app` — inferred from tool executions |
| Entity Tracker | `context_stack.py` | session, last 50 | Ordered, **typed** entity history (app/folder/file/url/drive/store_app/media/search_query) with verb-aware resolution (`resolve()` picks the right entity for "close it" vs "open it" vs "play it") — reused directly as World State's Entity Tracker (see WORLD_STATE.md) |
| Action memory | `context_memory.py` | session, persisted (`~/.xyron/context_memory.json`) | Last action + entities/paths, purpose-built for "delete them"/"do it again" |

**A fifth cluster exists in parallel:** `brain/memory_system.py`,
`brain/memory_manager.py`, and `brain/entity_stack.py` structurally mirror
this table (a memory store, a manager, and an entity tracker) but live
under `backend/brain/`, a separately-evolved system also wired into
`voice_ws.py` (see PLANNING_ENGINE.md's correction note for the fuller
picture — `brain/` parallels `api/agents/` too, not just the memory layer).
Not audited in depth this pass; flagged in TECHNICAL_DEBT.md.

All four systems in the table above are **live** and wired into `voice_ws.py`'s post-tool-execution
block (three parallel `asyncio.create_task(asyncio.to_thread(...))` calls —
`active_context.update_from_tool`, `context_stack.update_from_tool`, and
now `world_state.record_action` — plus `context_memory.record_action` fed
separately). None has been removed or is safe to remove without a dedicated
consolidation pass — see TECHNICAL_DEBT.md.

## Two separate pronoun-resolution pipelines

- **`context_resolver.py`** — simple regex substitution, called early
  "before any routing" per its own docstring. Priority: typed memory slot
  (command-type aware) → episodic session-history regex scan. Also owns
  ordinal disambiguation ("open the second one" → rewrites to an explicit
  path command) and, as of the Filesystem phase, the learning hook that
  writes confirmed choices back to `fs_index.learned_resolutions`.
- **`follow_up_resolver_v2.py`** — a proper 5-tier cascade (ContextStack →
  ScreenContext → SessionState → MemoryService typed slots → V1 fallback),
  explicitly benchmarked at <50ms total. This is the more capable of the
  two and is what most follow-up commands actually go through.

Both are load-bearing. Neither was modified or unified during the World
State phase — deliberately: touching `follow_up_resolver_v2.py`'s
already-benchmarked path was judged too risky to bundle into a phase that
was primarily about adding new (workspace/Explorer/goal/timeline)
context, not migrating existing conversational UX.

## Persistent stores

- `~/.ai-operator/memory.json` — long-term facts (name/profession/location/
  etc., regex-extracted from user text), personality style.
- `~/.xyron/context_memory.json` — last action + recent entities.
- `~/.ai-operator/episodes.db` — every conversation turn with
  `tool_name`/`success`, plus `tool_patterns` (tool × hour) for proactive
  suggestions.

## Extension points

- New typed context slot: `memory_service._TOOL_SLOT` mapping + `set_context_slot`/`get_context_slot`.
- New entity type for the Entity Tracker: `context_stack.ENTITY_TYPES` +
  `_TOOL_ENTITY_MAP` + a case in `_make_entity()`.
- A genuine consolidation (recommended future work, not attempted here):
  make World State's Entity Tracker (`context_stack`) the single source of
  truth, with `active_context`/`memory_service` slots/`context_memory`
  becoming thin read-through views over it rather than independently
  updated stores. This is a real, evidence-backed opportunity — see
  TECHNICAL_DEBT.md for scope and risk.
