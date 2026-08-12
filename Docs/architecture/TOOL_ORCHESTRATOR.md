# Tool Orchestrator

## Purpose

Executes plans produced by the Planning Engine (see PLANNING_ENGINE.md) —
sequential and parallel execution, retries, cancellation, progress
tracking, and error recovery. This document covers the *execution* layer
specifically; PLANNING_ENGINE.md covers plan generation and World State
observation.

## Tool registry — the execution boundary

```
api/tools/registry.py
  ToolRegistry.execute(name, params, context) -> ToolResult   # never raises
  ToolRegistry.get_definitions(categories=None)                # OpenAI function-calling format
```

Every tool call — from voice-routed intents, from `AgentStep.tool`, from
rollback actions — goes through this one function. `ToolResult` is the
universal contract: `success, text, spoken, data, action_url/app/path, risk,
error`.

**Note for anyone writing tests against this module:** `api/tools/__init__.py`
does `from .registry import registry`, which shadows the `registry` submodule
name inside the `api.tools` package namespace — `import api.tools.registry`
resolves to the `ToolRegistry` *instance*, not the module. Use
`sys.modules['api.tools.registry']` if you need the actual module object
(e.g. to `mock.patch` `ToolRegistry.execute` at the class level). This isn't
a bug, just a naming collision worth knowing about — the same pattern exists
for `api.agents.agent_runtime` (`api/agents/__init__.py` re-exports the
`agent_runtime` singleton under the same name as its submodule).

## Single-agent execution (`agent_executor.py`)

```
execute_step(task, step, executor_fn):
  1. mark RUNNING, start timeout (60s default, 120s HIGH-risk)
  2. await executor_fn(task, step) under asyncio.wait_for
  3. verify_step() — tool-specific heuristic check
  4. IF step.success_condition: check_condition() against World State (NEW)
  5. on any failure -> _handle_failure() -> agent_recovery.attempt_recovery()
  6. recovery: retry w/ backoff -> skip (LOW risk only) -> tweak args ->
     rollback (NEW, opt-in) -> give up
```

Timeouts, verification, and recovery are handled uniformly regardless of
which specialist agent supplied `executor_fn` — a new specialist agent
never needs to reimplement retry/timeout logic.

## Multi-agent DAG execution (`coordinator/coordinator_agent.py` +
## `coordinator/task_graph.py`)

`TaskGraph` is a real dependency DAG: `TaskNode.dependencies` (list of
`node_id`), `get_ready_nodes()` (all dependencies DONE/SKIPPED),
`get_parallel_groups()` (depth-based grouping of `can_run_parallel=True`
siblings). `_execute_graph()` loops: pull ready nodes, split into
parallel/sequential batches, `asyncio.gather()` the parallel batch, run
sequential nodes one at a time, repeat until `is_complete()` or
`is_failed()`.

Per-node execution (`_execute_node()`) routes to one of three handlers by
`agent_type`:
- `"verifier"` — inline check, no agent launch
- `"personality"` — response polish, no agent launch
- anything else — `_run_agent_node()`: launches a specialist agent via
  `agent_runtime.launch()`, polls for completion (5 min cap), checks the
  node's `success_condition` against World State if declared (NEW),
  dispatches a rollback goal if retries are exhausted and `rollback_goal`
  was declared (NEW, fire-and-forget so cleanup never blocks the rest of
  the workflow).

**Approval gates**: a node with `requires_approval=True` blocks (polling
`task.metadata[f"approved_{node_id}"]`, up to 120s) until the user
confirms via the existing `confirm_required`/approval UI flow — this
predates this phase and was not modified.

**Resource locks** (`coordinator/resource_locks.py`): prevents concurrent
nodes from touching the same resource (e.g. two nodes both trying to drive
the one shared browser session) — `async with resource_lock_manager.lock(name)`.

## Performance characteristics

- Retry backoff schedule: 1s, 3s, then a tweaked-args attempt, then
  give-up (`_BACKOFF_S = {0: 1.0, 1: 3.0}`, `_MAX_RETRIES = 2`).
- DAG polling interval while waiting on a sub-agent: 1s (`_run_agent_node`);
  the outer graph loop backs off 0.5s when nothing is ready but something
  is still running.
- Progress updates are deduplicated (`send_progress` skips identical
  `(message, pct)` pairs) — a prior fix (Phase 4.13) for WS flooding during
  polling, not something this phase touched.

## Extension points

- New retry strategy: add a case to `agent_recovery.attempt_recovery()`'s
  priority chain.
- New tool-specific verification: register via `agent_verifier.py`'s
  `@_tool_check("tool_name")` decorator.
- New World-State condition operator: extend `world_state_check._VALID_OPS`
  and the `check_condition()` branch — kept deliberately small and
  eval()-free; resist the temptation to add a generic expression language
  here.
