# PR #18 Integration Audit

**PR:** feat: system tools refactor — core layer + routing improvements  
**Auditor:** Claude Code  
**Date:** 2026-05-20  
**Current branch:** docs/collab-architecture

---

## What PR #18 Fixes

### New Core Infrastructure Layer (`backend/api/tools/core/`)
| File | Purpose |
|---|---|
| `ps_runner.py` | Unified async PowerShell runner + `ToolResult` dataclass |
| `drives.py` | Dynamic drive detection via WMI, 30s cache, dead-mount filtering |
| `path_translator.py` | wslpath-based translation, edge-case handling |
| `file_search.py` | 3-tier search: Everything → Windows Index → PowerShell GCI |
| `media_control.py` | Volume/brightness via NirCmd + WMI |
| `app_finder.py` | 267 apps indexed, 4-source discovery + fuzzy matching |

### Routing Fixes (HTTP REST path — `voice.py`)
- `_OPEN_FOLDER_IN_DRIVE_RE` — "open music folder in D drive" preserves drive letter
- `_FIND_ITEM_RE` + `_FIND_DRIVE_RE` — "find IOS folder" → smart_open
- `_RENAME_RE` / `_CHANGE_NAME_RE` / `_CHANGE_NAME_OLD_NEW_RE` / `_CALL_IT_RENAME_RE` — rename routing
- `_IMPLICIT_OPEN_FOLDER_RE` — "beta folder in D drive" with no open verb
- Multi-turn pending_action: "where?" → "D drive" continues prior tool call

### Tool Additions
- `rename_file` — newly registered with `_exec_rename_file` executor
- `copy_file` — newly registered with `_exec_copy_file` executor
- `smart_open` improvements: suffix normalization, junk path filtering, `_found_path` in last_action

### Context Resolver Fix
- Skip coreference resolution for rename commands ("rename it to alpha" — "it" should NOT be resolved to a path)

### STT Corrections (`whisper_service.py`)
- "indeed drive/derived", "individually drive", "deep drive" → "in D drive"
- "indie drive" → "in D drive"

### FS Index Improvements (`fs_index.py`)
- `_is_readable()` filters dead WSL mounts before scanning
- `_detect_win_user_home()` discovers actual Windows user home
- startup_delay 30s → 5s

### Cognitive State
- `pending_action: Optional[dict]` field added — stores tool waiting for location clarification

---

## What Conflicts With Current Architecture

### 1. CRITICAL: Routing fixes live in the WRONG file for WS voice path

**PR #18 routing fixes are in `backend/api/routers/voice.py` (HTTP REST endpoint).**  
Our active voice path is `backend/api/routers/voice_ws.py` (WebSocket).

- `voice.py` serves `/api/v1/voice/speak` — HTTP streaming endpoint, used by old dashboard command box
- `voice_ws.py` serves `/api/v1/voice/ws/session` — WebSocket, used by wake-word → greeting → full voice session

**The PR's `_OPEN_FOLDER_IN_DRIVE_RE`, `_FIND_ITEM_RE`, `_RENAME_RE`, `_IMPLICIT_OPEN_FOLDER_RE`, and pending_action resume do NOT apply to the WS voice path at all.**

When user says "Create a folder called alpha in C drive" via voice, it goes through:
```
voice_ws.py → process_utterance() → orchestrator.decide() → intent_router.route()
```
None of the PR's voice.py routing additions are in this path.

**Resolution:** The intent_router already has our Tier-2 regex for `create_folder` (added last session). For rename/find-in-drive, we need the same patterns added to `intent_router.py` — NOT to `voice.py`.

### 2. MODERATE: `system.py` app_finder Priority 0 change is risky

The PR inserts `app_finder._search_index()` BEFORE the existing `_APP_MAP` fast path. If app_finder index hasn't loaded yet (still building at startup), `_search_index()` returns None and falls through correctly. But if the fuzzy match returns a wrong entry, it could launch the wrong application.

**Current working:** "open settings" → `_APP_MAP["settings"]` → `ms-settings://` URI  
**PR change:** "open settings" → first checks `app_finder._search_index("settings")` → could match something else from Start Menu

**Resolution:** Keep app_finder but only as a tertiary fallback (after `_APP_MAP` and start-menu), not Priority 0.

### 3. MINOR: `core/ps_runner.py` defines its own `ToolResult`

`backend/api/tools/core/ps_runner.py` re-defines `ToolResult` as a dataclass. The main `system_tools.py` already has its own `ToolResult`. These must remain separate — `core/` tools use their own; `system_tools.py` uses its own.

**Resolution:** Already handled — `core/__init__.py` exports only `core.ToolResult`. No collision.

### 4. MINOR: `fs_index.py` startup_delay 30s → 5s

This accelerates the initial FS scan. On systems where `/mnt/d`, `/mnt/e` are large drives, a 5s startup may begin scanning before the backend is fully ready. The `_is_readable()` filter helps, but large drives still take time.

**Resolution:** Acceptable tradeoff. Keep as-is.

---

## What Should Be Merged

| Change | File | Status | Reason |
|---|---|---|---|
| `core/` entire directory | `backend/api/tools/core/` | ✅ MERGE | Purely additive, no conflicts |
| `core_tools.py` | `backend/api/tools/core_tools.py` | ✅ MERGE | New registrations, additive |
| Import core_tools | `backend/api/tools/__init__.py` | ✅ MERGE | 1-line import |
| `pending_action` field | `backend/cognition/cognitive_state.py` | ✅ MERGE | Additive field |
| Rename skip | `backend/api/services/context_resolver.py` | ✅ MERGE | Fixes rename corruption |
| Drive detection + 5s startup | `backend/api/services/fs_index.py` | ✅ MERGE | Safer mount handling |
| STT corrections | `backend/voice/whisper_service.py` | ✅ MERGE | More correct transcriptions |
| `rename_file` + `copy_file` tools | `backend/api/tools/system_tools.py` | ✅ MERGE | Fill tool matrix gaps |
| `smart_open` improvements | `backend/api/tools/system_tools.py` | ✅ MERGE | Junk filter + suffix strip |
| App warmup | `backend/api/main.py` | ✅ MERGE | Non-blocking background |
| HTTP routing regex blocks | `backend/api/routers/voice.py` | ✅ MERGE | HTTP path improvements |
| Pending_action resume | `backend/api/routers/voice.py` | ✅ MERGE | Multi-turn clarification |
| intent_router rename patterns | `backend/api/services/intent_router.py` | ✅ MERGE (new work) | Port rename/find-in-drive to WS path |

## What Should NOT Be Merged As-Is

| Change | File | Risk | Fix |
|---|---|---|---|
| app_finder Priority 0 | `backend/api/routers/system.py` | Medium — could override known-good `_APP_MAP` entries | Demote to tertiary fallback |
| voice.py routing (for WS) | `backend/api/routers/voice.py` | Not a merge risk, just not applicable | Port to intent_router instead |

---

## Risk Analysis

### Regression Risks
- **App launch** (`open settings`, `open chrome`): If app_finder Priority 0 is inserted wrong, it could match fuzzy entries before the ms-settings:// fast path. **Mitigation:** Keep Priority 0 only for apps NOT in `_APP_MAP`.
- **Smart_open junk filter**: The filter drops paths containing "appdata" etc. A user file inside AppData subfolders would be missed. **Mitigation:** Acceptable — user content rarely lives in appdata.
- **5s startup scan**: Fast scan begins before large drives are fully mounted. **Mitigation:** `_is_readable()` check guards against this.

### Non-Risks
- All `core/` files are imported lazily (only on tool call), so startup cost is zero.
- `rename_file` and `copy_file` are medium-risk tools — they don't auto-execute on first call without explicit user command.
- STT corrections are additive regex substitutions — they cannot corrupt existing matches.

---

## Voice/WS Compatibility

The PR changes to `voice.py` (HTTP) have **zero impact** on `voice_ws.py` (WS). They are completely separate codepaths.

Our session's WS path fixes (greeting audio, tts_done timing, command-list rejection, create_folder Tier-2, LLM routing policy) are all in `voice_ws.py` and `intent_router.py`, untouched by PR #18.

The WS voice session flow remains intact:
```
Wake → Greeting → listening → STT → normalizer → intent_router → orchestrator → tool execution → TTS response
```

---

## Frontend Compatibility

PR #18 has **zero frontend changes**. Our session's `useVoiceWS.ts` changes (greeting log tags, tts_done 150ms delay, greeting chat message) are fully compatible.

The `pending_action` field in cognitive_state could surface in the future dashboard state if needed — no immediate frontend impact.

---

## Merge Plan (Staged Commits)

1. **Stage 1:** `core/` infrastructure + `core_tools.py` + `__init__.py` import
2. **Stage 2:** `cognitive_state.py` + `context_resolver.py` + `fs_index.py` + `whisper_service.py`
3. **Stage 3:** `system_tools.py` — rename_file + copy_file + smart_open improvements
4. **Stage 4:** `voice.py` — routing patterns + pending_action resume
5. **Stage 5:** `main.py` — warmup + (system.py with demoted app_finder priority)
6. **Stage 6:** `intent_router.py` — port rename/find-in-drive patterns to WS Tier-2

---

## Verification Checklist (Before PR)

- [ ] `python3 -c "from api.tools.core import ps_runner"` — no import error
- [ ] "open settings" still opens Windows Settings
- [ ] "open chrome" still opens Chrome
- [ ] "open C drive" still works
- [ ] "create folder called alpha in C drive" → `create_folder` tool, not OpenAI
- [ ] "rename alpha to beta" → `rename_file` tool
- [ ] Greeting audio plays full, appears in conversation
- [ ] Garbage transcript rejected, not normalized to shutdown
