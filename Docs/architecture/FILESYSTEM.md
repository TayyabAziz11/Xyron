# Filesystem Intelligence

## Purpose

Gives Xyron a continuously-updated, semantically-searchable index of the local
filesystem so `smart_open` and voice commands like "open my tax file" resolve
in milliseconds instead of running a live disk crawl, and so vague references
("open my tax file", "open the logo") resolve without an exact filename.

## Architecture

```
fs_index.py (SQLite, singleton)
 ├─ entries table          — OS-wide filename/path index, all drives
 │    columns: path, name, lowercase_name, type, drive, size,
 │    modified_time, accessible, content_hash, keywords, has_content,
 │    last_opened, open_count
 ├─ open_events table       — usage-learning events (hour/weekday/app/
 │                            folder/project) feeding usage-affinity scoring
 ├─ learned_resolutions table — (query_norm -> path) pairs the user has
 │                            confirmed, promoted above every other tier
 ├─ _rebuild()               — full filename scan, 6h cadence + on boot
 └─ _content_pass()          — second pass, SEMANTIC_ROOTS only: extracts
                              text (content_extractor.py) and embeds it
                              (semantic_index.py)

content_extractor.py — PyMuPDF/python-docx/openpyxl/python-pptx text
                        extraction, 25MB cap, 8000-char output cap

semantic_index.py — SentenceTransformer(all-MiniLM-L6-v2) + FAISS
                     IndexIDMap2(IndexFlatIP), vector IDs == entries.id

fs_watcher.py — real-time PollingObserver on SEMANTIC_ROOTS only (see
                Scope Decision below), idle-gated content-embedding queue

file_resolver.py — the priority-cascade resolution engine (see below)
```

## Scope decision: SEMANTIC_ROOTS

Content extraction, embedding, and real-time watching are **not** applied to
every file on every drive — only to Desktop/Documents/Downloads/Pictures/
Videos/Music/OneDrive, git repo roots, and the Xyron repo itself
(`fs_index._discover_semantic_roots()`). Two reasons:

1. WSL2's `/mnt/*` Windows drives are DrvFs-mounted and don't support
   inotify — a full-drive real-time watch would need expensive polling
   across potentially millions of files.
2. Embedding every OS/cache/installer file would bloat the FAISS index with
   noise for no benefit.

Plain filename/path search still covers the whole OS, unchanged, on the
existing 6-hour rebuild cadence.

## Resolution priority cascade (`file_resolver.py`)

When `smart_open` needs to resolve "open X", it tries, in order, stopping at
the first tier whose best candidate clears that tier's confidence threshold:

| Tier | Source | Notes |
|---|---|---|
| 0 | `learned_resolutions` | A query the user has explicitly confirmed before — always wins if the path still exists |
| 1 | Current workspace | VS Code/Visual Studio window title → project root (`workspace_context.py`) |
| 2 | Current Explorer folder | Focused Explorer window's real path via Shell.Application COM (`explorer_context.py`) |
| 3 | Recent files | `entries.last_opened DESC` |
| 4 | Frequently opened | `entries.open_count DESC` |
| 5 | Recent conversation | `memory_service` last_file/last_folder slots |
| 6 | Active application | Foreground app's typical file extensions (`workspace_context.APP_EXTENSIONS`) |
| 7 | Screen context | Hook reserved for Phase 2 (Perception Engine); wired but currently a no-op |
| 8 | Semantic index | FAISS cosine similarity over embedded content |
| 9 | Filename/path index | OS-wide fuzzy substring match (`search_ranked`) |
| 10 | Slow disk crawl | `find`, last resort, ~8s deadline (`system_tools.py`) |

### Confidence model

```
confidence = tier_prior[tier] + match_score * 0.5 + learned_boost + usage_affinity * 0.1
  >= 0.75  -> open immediately
  0.45-0.75 -> ask "did you mean X?" (reuses voice_ws.py's confirm_required gate)
  < 0.45   -> present ranked choices (reuses the multiple_matches disambiguation gate)
```

Confirmed choices (accepted confirmation or picked-from-a-list) write back to
`learned_resolutions`, so a repeated query resolves instantly next time via
Tier 0.

## Measured performance

- Warm `file_resolver.resolve()` (workspace tier hit): ~150-200ms, dominated by
  the PowerShell window-context query (see PERCEPTION_ENGINE.md for why).
- Raw indexed SQLite lookups (`last_opened`, `open_count`): sub-millisecond.
- `LIKE '%substring%'` filename search over ~500K entries: 90-300ms — cannot
  use the `lowercase_name` B-tree index for infix patterns (only prefix
  patterns benefit). This is the largest known filesystem-search bottleneck;
  see TECHNICAL_DEBT.md for the recommended fix (FTS5).
- SentenceTransformer model load: ~19s cold, one-time per process (also
  loaded independently by `intent_router.py` — see TECHNICAL_DEBT.md).
- Semantic search once warm: 40-55ms.

## Extension points

- New content-worthy file types: add to `content_extractor.SUPPORTED_EXTS`
  and `_extract_*` functions.
- New resolution tiers: insert into `file_resolver.resolve()`'s cascade at
  the appropriate priority position — tier 7 (screen context) is the
  designated slot for Phase 2 perception data once wired.
- New usage-affinity signals: extend `fs_index.get_usage_affinity()`'s
  weighted blend.
