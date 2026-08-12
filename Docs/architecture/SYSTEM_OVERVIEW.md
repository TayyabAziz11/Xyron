# System Overview

## What Xyron is

A voice-first AI operating system layer for Windows (via WSL2): wake word →
speech → intent → action, evolving from a command-execution assistant
toward a continuously-aware system that understands what's on screen, what
the user is working on, and can plan and execute multi-step goals.

## The five layers, in the order they were built this development arc

```
1. Filesystem Intelligence   — semantic index of the local disk (FILESYSTEM.md)
2. Context-Aware Resolution  — priority-cascade "what does the user mean"
                                (folded into FILESYSTEM.md's file_resolver section)
3. World State Engine        — the shared reasoning-context hub (WORLD_STATE.md)
4. Perception Engine         — observes desktop/browser/UI, publishes into
                                World State (PERCEPTION_ENGINE.md)
5. Planning Engine +          — Intent -> Goal -> Plan -> Execute -> Observe ->
   Tool Orchestrator            Verify -> Continue, now World-State-aware
                                (PLANNING_ENGINE.md, TOOL_ORCHESTRATOR.md)
```

Each layer was deliberately built by **extending existing production
infrastructure** rather than adding a parallel system, after explicit
research into what already existed — this pattern repeats throughout: World
State aggregates four pre-existing context systems rather than replacing
them; Perception reuses `browser_workspace`/`ps_session`/
`screen_context_service` rather than new CDP/PowerShell/vision code; the
Planning Engine extends the pre-existing `api/agents/` Planner/Executor/
Coordinator stack rather than building a new one.

**Known exception, surfaced by the stabilization audit, not resolved by
it:** `backend/brain/` is a structurally-parallel planning + memory system
that predates all five layers above and was not integrated with World
State during this arc. See PLANNING_ENGINE.md's correction note and
TECHNICAL_DEBT.md.

## Three independent runtimes, one HTTP contract

```
desktop-app (Electron/Tauri)  ──┐
web (Next.js :3001)           ──┤──▶  backend (FastAPI :8000)  ──▶  external APIs
```

The backend is the only layer with real logic; the two frontends are
thin clients. See PROJECT_STRUCTURE.md for the full directory map.

## Golden rules (apply to every phase of this arc, verified before each change)

1. **Perception observes. World State stores. Reasoning consumes.** Never
   mixed in one module.
2. **No eager resource warmup** — Chrome, heavy models, PowerShell
   subprocesses are started on first real need, not speculatively.
3. **Every new capability is proven against the full test suite before and
   after** — this arc's baseline has been a stable 59 pre-existing,
   unrelated test failures (documented per-file in each phase's report)
   across every phase; any deviation from that count is investigated
   before being called done.
4. **Extend, don't duplicate** — confirmed by reading the actual code, not
   assumed from file names.

## Where to go next

- New to the codebase? Read PROJECT_STRUCTURE.md, then the layer doc for
  whatever you're touching.
- Debugging a slow path? See the "Measured performance" sections in
  FILESYSTEM.md, PERCEPTION_ENGINE.md, and this pass's overall numbers in
  TECHNICAL_DEBT.md.
- Adding a new dependency or removing one? See DEPENDENCIES.md.
- Changing a config default? See CONFIGURATION.md.
