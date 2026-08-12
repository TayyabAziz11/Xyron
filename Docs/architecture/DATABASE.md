# Database

Xyron has no central database — each subsystem owns a small SQLite file
under `~/.ai-operator/` or `~/.xyron/`. This document is the map, plus the
audit findings from the platform-stabilization pass (2026-07).

## Inventory

| File | Owner | Connection pattern | Notes |
|---|---|---|---|
| `~/.ai-operator/fs_index.db` | `fs_index.py` | thread-local, reused | Best-designed of the set: WAL mode, indexes matched to actual query patterns, the only DB with any pruning strategy (180-day `open_events` cutoff) |
| `~/.ai-operator/history.db` | `history_service.py` | persistent single connection, `check_same_thread=False` + lock | FTS5 virtual table + `AFTER INSERT` trigger — proper full-text search |
| `~/.ai-operator/macros.db` | `macro_service.py` | persistent single connection | |
| `~/.ai-operator/notes.db` | `notes_service.py` | persistent single connection | FTS5, same pattern as history.db |
| `~/.ai-operator/episodes.db` | `episodic_memory.py` | **fresh connection per call, never closed** | Leak pattern — see Findings |
| (brain memory store) | `brain/memory_system.py` | persistent single connection | `WHERE text LIKE '%query%'` full scans — see Findings |
| (learning store) | `learning_service.py` | fresh connection per call, never closed | Same leak pattern as episodic_memory.py |
| (collaboration memory) | `coordinator/collaboration_memory.py` | fresh connection per call, explicitly closed | No leak, but pays full connection-open cost per key read |

`fs_index.db`'s schema (entries/open_events/learned_resolutions) is
documented in FILESYSTEM.md — not repeated here.

## Findings — connection lifecycle (three inconsistent patterns)

1. **Persistent single connection** (recommended, used by `fs_index.py`,
   `history_service.py`, `macro_service.py`, `notes_service.py`,
   `brain/memory_system.py`) — one connection held for the service's
   lifetime, thread-safe via an explicit lock. Sound.
2. **Fresh connection per call, never closed** (`episodic_memory.py`,
   `learning_service.py`, `cognition/goals.py`) — `with self._conn() as
   conn:` only wraps the transaction (commit/rollback); it does **not**
   call `.close()`. Every read/write leaks a connection object until GC
   collects it. Not catastrophic at current scale (SQLite handles many
   short-lived connections fine; WSL2 file-handle limits are generous) but
   wasteful and inconsistent with pattern 1 used two files over.
3. **Fresh connection per call, explicitly closed**
   (`collaboration_memory.py`) — no leak, but pays full connection-open
   cost (file open + header parse) on every single key read/write, worse
   throughput than pattern 1 for no benefit.

**Recommendation**: standardize on pattern 1 (thread-local or single
persistent connection + lock) across all eight. Medium effort, zero
behavior change, meaningful throughput/robustness improvement.

## Findings — indexing

`history.db` and `notes.db` use FTS5 correctly. `brain/memory_system.py`
and `learning_service.py`'s procedure lookups do `WHERE text LIKE
'%query%'` (leading wildcard) — no B-tree index can serve this pattern;
same class of issue documented in FILESYSTEM.md for `fs_index.db`'s
filename substring search. Not urgent at current row counts, but will
degrade the same way `fs_index.db` did once these tables grow — recommend
FTS5 for both if/when they do.

## Findings — dead code (confirmed bug)

`entity_corrector.py:167` queries `SELECT name FROM paths WHERE
type='dir'` against `fs_index.db` — but that table has been named
`entries` with `type IN ('file','folder')` for the entirety of this
session's Phase 1 work (and likely longer). The query is a stale reference
to a pre-rewrite schema, silently swallowed by a bare `except: pass`. The
"recent folders from fs_index" contribution to entity correction has
therefore been dead code — it always returns nothing, and nothing ever
surfaced the failure. See TECHNICAL_DEBT.md for the fix (one-line, safe,
already-broken feature starts working again).

## Findings — no lifecycle management

No `VACUUM` or scheduled cleanup exists anywhere except `fs_index.db`'s new
`open_events` pruning. `history.db`, `episodes.db`, `notes.db` all grow
unbounded. `main.py` has `@app.on_event("startup")` but no `"shutdown"`
counterpart — none of the eight connections are ever explicitly closed on
backend exit; this relies on process termination, which is fine for SQLite
(no corruption risk with WAL mode) but not best practice.

## Backup strategy

None exists. All eight databases are local-only, unbacked-up, user-profile
files. Given they're rebuildable (`fs_index.db`) or low-value-per-row
(history/notes/episodes are convenience caches, not systems of record),
this is an accepted risk for a local desktop assistant, not a gap requiring
immediate action — noted for completeness per the audit brief.
