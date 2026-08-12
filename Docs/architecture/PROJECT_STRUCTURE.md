# Project Structure

## Top level

```
backend/        FastAPI app — the only layer with real logic
web/             Next.js dashboard (:3001) — thin client
desktop-app/     Electron/Tauri desktop client — thin client
docs/            Documentation (this folder: docs/architecture/)
```

## backend/ (517 .py files)

```
api/
 ├─ main.py              — entry point, mounts ~35 routers, startup sequence
 ├─ config.py             — Settings singleton (pydantic-settings), computed paths
 ├─ routers/ (37 files)   — REST/WS endpoints, ~150 routes total. voice.py
 │                          (5026 lines) and voice_ws.py (3808 lines) are the
 │                          two largest files in the codebase — see
 │                          VOICE_PIPELINE.md. audio.py and debug.py are
 │                          built but not mounted in main.py (see
 │                          TECHNICAL_DEBT.md).
 ├─ services/ (79 files)  — the bulk of the logic. See WORLD_STATE.md,
 │                          FILESYSTEM.md, MEMORY_SYSTEM.md for the major
 │                          clusters. response_validator.py is a confirmed
 │                          orphan (documented integration point, never
 │                          wired in — TECHNICAL_DEBT.md).
 ├─ services/perception/  — Perception Engine (PERCEPTION_ENGINE.md)
 ├─ tools/ (12 + 8 core/)  — the tool registry (TOOL_ORCHESTRATOR.md),
 │                          109 registered tools, no dead files, no
 │                          duplicate registrations (audited)
 ├─ agents/                — Planner/Executor/Coordinator stack
 │                          (PLANNING_ENGINE.md) + browser/coding/
 │                          automation/personality specialist agents
 └─ schemas/               — shared Pydantic response models (ApiResponse[T]),
                             used inconsistently across routers (~10 of 35
                             files) — see TECHNICAL_DEBT.md

voice/          (22 files) STT/TTS/wake-word engines — VOICE_PIPELINE.md
cognition/      (15 files) emotion engine, mood state machine, personality,
                           reflection. Has same-named files as api/services/
                           (language_detector.py, response_language.py) —
                           unconfirmed whether intentional layering or
                           accidental duplication, see TECHNICAL_DEBT.md
brain/          (17 files) a structurally-parallel planning + memory system,
                           predates this development arc, still live —
                           see PLANNING_ENGINE.md's correction note
operator_mode/  (18 files) Explorer/Chrome/VS Code/YouTube "skills" —
                           reachable only via the unmounted debug.py
                           router; effectively dead in production despite
                           having its own passing test suite
                           (tests/operator_mode/) — see TECHNICAL_DEBT.md

src/ai_operator/ (69 files) — business-automation agents (LinkedIn/Gmail/
                              Odoo), organized into silver/gold/platinum
                              skill tiers. Confirmed live (imported at
                              startup by api/main.py), a genuinely distinct
                              domain from api/agents/ and brain/ — not a
                              duplication candidate on its own, though its
                              existence alongside two other agent-ish
                              systems is part of the broader picture.

mcp_servers/    JSON-RPC subprocess servers (WhatsApp, Odoo) — MCP protocol
Business/       Generated business documents (accounting/briefings) — data,
                not code; large volume of untracked files, not part of this audit
data/           Runtime data (gitignored)
```

## Data locations (outside the repo, user-profile scoped)

```
~/.ai-operator/   fs_index.db, history.db, macros.db, notes.db, episodes.db,
                  memory.json — see DATABASE.md for the full inventory
~/.xyron/         context_memory.json
backend/.env      secrets (OPENAI_API_KEY etc.) — gitignored, never logged
```

## Test suite

```
backend/tests/   ~90 test files. Baseline: 59 pre-existing failures
                 (documented per-file across every phase of this arc —
                 drive-phonetics/settings-routing/brain-v2/odoo-mock/
                 linkedin/phase4-coordinator's one stale test), unrelated
                 to any change made in this arc. New test files added this
                 arc: test_phase1_system_intelligence.py,
                 test_phase15_context_aware_resolution.py,
                 test_world_state_engine.py, test_perception_engine.py,
                 test_planning_engine.py.
tests/operator_mode/   passes in isolation but exercises a subsystem
                       unreachable from any live route (see above)
```

## Conventions confirmed consistent (audited, no action needed)

- File naming: snake_case throughout, zero exceptions across 517 files.
- Class naming: PascalCase throughout; leading-underscore-PascalCase
  (`_PersistentSession`) is an intentional "private class" convention, not
  an inconsistency.
- Type hints: consistently present on function signatures across sampled
  files, independent of docstring coverage (which is inconsistent — high
  in this arc's new code, low in some older files like
  `tools/system_tools.py`, ~23% docstring coverage there).
