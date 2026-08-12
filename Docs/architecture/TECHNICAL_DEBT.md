# Technical Debt Report

Platform-stabilization pass, 2026-07. Every item below is backed by direct
code inspection or measurement performed during this pass — none are
speculative. Two items were fixed during this pass (marked ✅); everything
else is reported, not acted on, per the phase's explicit "do not make
speculative changes" / "do not redesign unless there is a measurable
architectural benefit" constraints.

---

## High Priority

### H1. Three structurally-parallel planning/memory systems (`api/agents/`, `brain/`, and — for memory specifically — a 5th cluster)
**Why it matters**: `backend/brain/` (`planner.py`, `orchestrator.py`,
`task_state.py`, `memory_system.py`, `memory_manager.py`,
`entity_stack.py`, `capability_registry.py`) structurally mirrors both the
`api/agents/` Planner/Executor/Coordinator stack extended this development
arc *and* the memory-tracking cluster (`memory_service`/`active_context`/
`context_stack`/`context_memory`). It's live — imported by `voice_ws.py`,
has its own mounted router — meaning some command paths get World State
awareness (via `api/agents/`) and some don't (via `brain/`), with no
documented rule for which path a given command takes. This is the largest
architectural risk in the codebase for anyone trying to reason about "what
happens when the user says X."
**Recommended solution**: a dedicated investigation (not a code change) to
map which command paths route through `brain/` vs `api/agents/`, followed
by either (a) extending `brain/` with the same World State hooks, or (b)
migrating its command paths onto `api/agents/` and retiring it. Do not
attempt a mechanical merge — the two systems' internals differ enough that
this needs a human product/architecture decision first.
**Estimated effort**: Large (investigation: 1-2 days; migration, if
chosen: multi-week).

### H2. `voice_ws.py`'s `ws_session()` is 3302 lines; three more functions in `voice.py`/`voice_ws.py` exceed 2000 lines each
**Why it matters**: these four functions total ~10,000 lines in the two
hottest, most latency-sensitive files in the codebase. Every bug fix or
feature addition to the voice pipeline this arc has had to reason about
functions too large to hold in context at once — directly slows down
every future change to voice handling, and increases the risk of an
unintended interaction between unrelated tiers/branches inside the same
function.
**Recommended solution**: do **not** attempt a big-bang refactor — this is
exactly the kind of live, heavily-latency-tuned, behavior-sensitive code
where a "safe" refactor easily introduces a regression that's invisible
until a specific voice-command tier is hit in production. Instead: extract
one clearly-bounded, independently-testable sub-flow at a time (e.g. the
pending-confirmation handler already has clear start/end markers), write a
characterization test for it first, then extract. Treat as an ongoing
tax paid down incrementally, not a project.
**Estimated effort**: Large, ongoing (each extraction: small; the whole
effort: multi-month if done safely).

### H3. `SentenceTransformer("all-MiniLM-L6-v2")` loaded independently by `semantic_index.py` and `intent_router.py`
**Why it matters**: measured cold load time is **~19 seconds**, paid twice
in a full backend boot (once for fs semantic search, once for intent
routing's Tier 3 classifier) — and, if either runs on GPU, two independent
VRAM allocations for the identical model. This is the single largest
measured startup-latency and duplicate-resource-usage finding in the audit.
**Recommended solution**: extract a shared `embedding_model_service.py`
singleton both `semantic_index.py` and `intent_router.py` call into,
loaded once, lazily, on first use by either. Low risk — both current call
sites already handle "model not ready yet" gracefully (degraded mode / GPU
fallback), so consolidating the load doesn't change either's failure mode.
**Estimated effort**: Small-Medium (one new file, two call-site edits, one
regression pass focused on both semantic search and intent classification).

---

## Medium Priority

### M1. Three inconsistent SQLite connection-lifecycle patterns across 8 databases
Persistent-single-connection (`fs_index.py`, `history_service.py`,
`macro_service.py`, `notes_service.py`, `brain/memory_system.py` — the
good pattern) vs. fresh-connection-never-closed
(`episodic_memory.py`, `learning_service.py`, `cognition/goals.py` — a
leak, low severity at current scale but wasteful) vs.
fresh-connection-always-closed (`collaboration_memory.py` — no leak, worst
throughput). **Fix**: standardize on pattern 1. Medium effort (8 files),
zero behavior change, real throughput/robustness improvement. See
DATABASE.md.

### M2. `fs_index.db` filename substring search does a full table scan
`LIKE '%text%'` over ~500K rows measured at 90-300ms (worst case, no
match: up to 900ms+) because a leading-wildcard pattern can't use the
`lowercase_name` B-tree index. This is Tier 9 of `file_resolver.py`'s
cascade — reached whenever tiers 0-8 don't clear, i.e. exactly the "I don't
recognize this file by context, just find it by name" case, which is
common. **Fix**: add an SQLite FTS5 virtual table for filename search,
mirroring the pattern `history.db`/`notes.db` already use correctly.
Medium effort, meaningful latency improvement for a hot path, low risk
(additive index, doesn't change the existing LIKE-based fallback's
correctness).

### M3. 24 files spawn `powershell.exe` per-call instead of using the warm `ps_session.py`
Cold spawn measured at 400-800ms (observed up to 2.7-3.2s under
concurrent load); the warm persistent session measured at 20-90ms. This
session already migrated `explorer_context.py` to the warm session as
part of Phase 2 (Perception Engine) for exactly this reason — the pattern
is proven, just not applied everywhere yet. **Fix**: migrate the remaining
24 call sites. Medium effort (mechanical but must verify each script is
single-line-safe first — see PERCEPTION_ENGINE.md's documented `ps_session`
multi-line hang bug), meaningful latency win for anything using PowerShell
on a user-facing path.

### M4. Three separate "memory" API surfaces + `/state` naming collision across 4 routers
`memory.py`, `cognition.py`, and `brain.py` each expose their own
memory-related endpoints; `dashboard.py`, `world_state.py`, `brain.py`,
`cognition.py` each expose a `/state` endpoint meaning something different.
Not a functional bug (each is internally consistent), but a real
discoverability/maintainability cost for anyone new to the API surface.
**Fix**: document the distinction clearly (partially done — see
MEMORY_SYSTEM.md) and consider renaming for clarity in a future pass;
not a behavior change, low risk, but touches frontend call sites so needs
coordination.

### M5. Broad `except Exception: pass` pattern, ~15+ sites
Found while investigating the `entity_corrector.py` bug (H-adjacent, now
fixed) — the same broad-except pattern that silently swallowed a stale
schema reference for an unknown period appears in ~15 other locations.
Not inherently wrong (defensive code in a voice pipeline that must never
crash on a single bad input is a reasonable default), but it means a
future regression in any of those 15 sites will also fail silently.
**Fix**: audit each site individually — most should stay silent-by-design,
but each should log at `debug` level minimum so the failure is visible in
logs even if it doesn't surface to the user. Medium effort (spot-check
each site's intent), low risk (adding a log line doesn't change behavior).

### M6. Response-shape inconsistency across routers
~10 of 35 routers use a generic `ApiResponse[T]` envelope
(`api/schemas/common.py`); the other ~25 return bare, router-specific
dicts. No documented rule for which a new router should follow. **Fix**:
pick one convention, document it, apply to new routers going forward — do
not retrofit existing routers (breaking change for any frontend code
depending on the current bare-dict shapes). Low effort to document, the
retrofit itself is out of scope (breaking change).

---

## Low Priority

- **L1. `api/routers/audio.py` and `debug.py` are built but never mounted**
  in `main.py`. `debug.py` looks intentionally standalone (manual
  inspection tooling); `audio.py`'s WSLg/PulseAudio health check looks like
  it might be an oversight — worth a quick human check, not a code
  change on its own.
- **L2. `api/services/response_validator.py` is a confirmed orphan** — has
  a docstring describing exactly how it should plug into
  `model_router.py`'s retry logic, but nothing imports it. Either wire it
  in (if the retry behavior it implements is still wanted) or delete it
  (if superseded) — a 10-minute decision once someone with product context
  looks at it.
- **L3. Three prefix-declaration styles for FastAPI routers** — some bake
  the full `/api/v1/x` prefix into the router file, `auth.py`/`monitor.py`
  split it with `main.py`, `automation.py` relies on `main.py` entirely.
  Cosmetic, zero functional impact, worth standardizing opportunistically
  (touch-when-touched, not a dedicated pass).
- **L4. `operator_mode/` (18 files, own test suite) is unreachable from any
  live route** — only referenced from the unmounted `debug.py` router, no
  `OPERATOR_MODE` gate found anywhere under `api/`. Either it's
  intentionally dormant (staged for a future integration) or genuinely
  dead — needs a product decision, not a mechanical fix.
- **L5. `cognition/language_detector.py`/`response_language.py` share names
  with `api/services/language_detector.py`/`response_language.py`** —
  unconfirmed whether this is intentional layering (cognition-level vs
  service-level) or accidental duplication; a diff was out of scope for
  this pass.
- **L6. Configuration split across a typed `Settings` class and ~48
  scattered `os.getenv()` calls** with no documented boundary between the
  two mechanisms. See CONFIGURATION.md for the full catalog and the
  recommended (not executed) consolidation.
- **L7. Docstring coverage is highly inconsistent** (this arc's new code:
  80-100%; `tools/system_tools.py`: ~23%). Type hints are consistently
  present regardless of docstring presence — that convention already
  holds. Fix opportunistically when touching a file, not as a dedicated pass.

---

## Fixed This Pass

- **`entity_corrector.py:167`** — `SELECT name FROM paths WHERE
  type='dir'` referenced a table/schema that hasn't existed since
  `fs_index.py`'s rewrite to `entries`/`type IN ('file','folder')`.
  Silently swallowed by a bare `except`, meaning "recent folders from
  fs_index" contributed nothing to entity correction for an unknown
  period. Fixed to the correct table/column; verified live (168 folder
  entries now correctly populate where zero did before).
- **`requirements.txt`** — added `requests>=2.28.0`, a real runtime
  dependency of `src/ai_operator/` (imported at backend startup) that was
  declared in `pyproject.toml` but missing from the file an actual
  fresh-install follows.
- **`pyproject.toml`'s `[voice]` extra** — removed `openai-whisper` and
  `pyaudio`, both confirmed to have zero import sites anywhere in the
  codebase (`faster-whisper`, a different package, is the real STT engine).

---

## Architectural Risks

- **Command-path ambiguity between `api/agents/` and `brain/`** (see H1) —
  the single biggest risk to predictable behavior.
- **No load-bearing regression suite for `brain/`'s command paths visible
  from this pass** — this arc's testing focused on `api/agents/` (which
  this arc actively extended); `brain/`'s test coverage wasn't audited.
- **Live, latency-tuned voice pipeline megafunctions (H2)** make every
  future voice-pipeline change higher-risk than it should be, by
  construction (large blast radius per edit, hard to unit-test in
  isolation).

## Known Limitations

- No true OS-level event hooks for Perception (documented, accepted
  trade-off — see PERCEPTION_ENGINE.md).
- No local vision model backend (Qwen2.5-VL) — GPU budget constraint,
  documented as a one-function swap when needed.
- No database backup strategy — accepted for a local desktop assistant
  with rebuildable/low-value-per-row caches (see DATABASE.md).
- Rollback in the Planning Engine is single-tool, not a multi-step undo
  sequence (see PLANNING_ENGINE.md).

## Future Improvements (not urgent, worth tracking)

- Migrate scattered `os.getenv()` calls into typed `Settings` fields (L6).
- FTS5 for `fs_index.db` filenames (M2) and `brain/memory_system.py`/
  `learning_service.py`'s text search, before either grows enough to hurt.
- Consolidate the four-system context-tracking cluster
  (`memory_service`/`active_context`/`context_stack`/`context_memory`)
  behind World State's Entity Tracker, once `follow_up_resolver_v2.py`'s
  benchmarked path can be safely touched (see MEMORY_SYSTEM.md).
