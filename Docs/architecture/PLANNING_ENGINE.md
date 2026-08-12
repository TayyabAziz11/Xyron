# Planning Engine & Tool Orchestrator

## Purpose

Turns a user goal into `Intent -> Goal -> Plan -> Execute -> Observe ->
Verify -> Continue until complete`, instead of a flat
`request -> tool call` mapping.

## Architecture decision — read this before assuming anything is missing

A full **Planner -> Executor -> Verifier -> Recovery -> DAG Orchestrator**
stack already existed before this phase and is **live in production**,
wired into `voice_ws.py` for both single-domain direct dispatch and
multi-domain coordinated workflows:

```
Planner:      agent_planner.py (single-agent, LLM)
              coordinator/delegation_planner.py (multi-agent, LLM + rule-based)
Executor:     agent_executor.py
Verifier:     agent_verifier.py (tool-specific heuristics)
Recovery:     agent_recovery.py (retry/backoff, skip, arg-tweak, rollback)
Orchestrator: coordinator/coordinator_agent.py + coordinator/task_graph.py (DAG)
```

The gap this phase closed — confirmed by grep before writing any code —
was that **nothing under `api/agents/` referenced World State**. Building a
second parallel planner/orchestrator would have repeated the exact
fragmentation the World State phase existed to fix. This document describes
the extended system, not a new one.

**Correction surfaced by the subsequent platform-stabilization audit,
recorded here rather than silently left out:** `backend/brain/` is a
*third*, live, structurally parallel system —
`brain/planner.py`/`orchestrator.py`/`task_state.py` mirror
`agent_planner.py`/`coordinator_agent.py`/`task_graph.py`, and
`brain/memory_system.py`/`memory_manager.py`/`entity_stack.py` mirror the
memory-system cluster (see MEMORY_SYSTEM.md). It's imported by
`voice_ws.py`, has its own mounted router (`brain.py`), and is referenced
by `dashboard.py`/`takeover.py`. This phase did **not** extend `brain/`
with World State awareness — only `api/agents/` — meaning any command path
that routes through `brain/` instead of `api/agents/` does not yet benefit
from the observation loop or rollback described below. Determining which of
the two owns which command paths, and whether they should be merged, is a
product/architecture decision flagged in TECHNICAL_DEBT.md, not made here.

## Step/Node contract

```python
@dataclass
class AgentStep:              # single-agent plans
    tool, tool_args            # required tool + expected input
    expected_output             # free text, surfaced to the LLM planner
    success_condition           # NEW — declarative World State check
    rollback_tool, rollback_args  # NEW — undo action if all retries fail
    retries                     # retry policy (existing)

@dataclass
class TaskNode:                # coordinator DAG nodes — same shape
    success_condition, rollback_goal   # NEW, mirrors AgentStep
    dependencies, can_run_parallel, requires_approval, retries/max_retries
```

All new fields are optional, default `None`/empty — every existing plan or
graph that doesn't set them is completely unaffected.

## Observation loop

```
world_state_check.py — check_condition(condition, before_snapshot, after_snapshot)
```

A tiny, pure, side-effect-free comparator. **No `eval()`, no arbitrary code
execution** — condition shape is a plain dict:
`{"field": "current_document", "op": "changed"|"not_none"|"equals"|"contains"|..., "value": ...}`.
Tested explicitly with an injection-attempt payload as the field name — it's
treated as a literal (nonexistent) key, never executed.

`agent_executor.execute_step()` and `coordinator_agent._run_agent_node()`
both: snapshot `world_state.get_context()` before dispatch, and — only if
the step/node declared a `success_condition` — snapshot again
(`refresh=True`) after the tool/sub-agent claims success, and check the
declared condition. If it fails, the *existing* retry/recovery machinery
handles it exactly as it would any other failure (both the tool-specific
verifier AND the World State condition must pass).

## Rollback

New, opt-in fourth recovery strategy in `agent_recovery.py` (single-agent)
and a matching `_handle_node_condition_failure()` in `coordinator_agent.py`
(multi-agent DAG). Only triggers once retries are exhausted, and only if
`rollback_tool`/`rollback_goal` was declared. Rollback cleans up but never
turns a failure into a success — the step/node still ends FAILED; rollback
just executes the declared undo action first (via the tool registry for
steps, via a fire-and-forget rollback agent dispatch for DAG nodes, so a
slow cleanup never blocks the rest of the workflow).

## World State integration point

**One shared injection point**, not scattered call sites:
`agent_runtime.launch()` — the single entry point both the direct-dispatch
path and every coordinator sub-agent launch go through — enriches
`task.metadata["context"]` with a curated World State subset
(`current_product`, `current_document`, `current_file`, `current_url`,
`current_browser`, `current_selection`, `current_project`,
`current_explorer_folder`, `current_goal`), placed first in the dict so it
survives `agent_planner.py`'s prompt-length truncation. This is what lets
"review this product" resolve `current_product` without every planner
needing to know `world_state.py` exists.

`delegation_planner.py`'s `context` parameter existed before this phase but
was **entirely unused** in `plan()` — a real, confirmed-by-reading dead
parameter, now wired into the LLM prompt (`_multi_agent_plan_llm`).

## Extensibility

New specialist agent types (shopping/coding/research/writing/travel/desktop
automation/browser automation) plug into `coordinator/agent_registry.py`
and `_type_map` in `coordinator_agent.py` without touching the planner,
executor, verifier, or recovery layers — the DAG orchestrator only cares
about `agent_type` as a string key.

## Regression evidence

Ran the pre-existing `test_phase4_coordinator.py` (35 tests exercising the
exact production coordinator this phase modified): 34/35 pass; the one
failure is a pre-existing, unrelated issue present in every baseline run
this session (booking-approval logic intentionally relocated in an earlier
commit; the test predates that change).
