# FIXES.md — Xyron Bug Fix Log
> Running log of all bugs found and fixed. Updated by both Qasim and Tayyab.
> Format: Date | Bug | Cause | Fix | File

---

## 2026-05-17 | Missing frontend emotion files
**Error:** compile error — useEmotionState and emotionState not found
**Cause:** Tayyab added emotion orb to page.tsx but forgot to create hook/state files
**Fix:** Created web/src/state/emotionState.ts + web/src/hooks/useEmotionState.ts
         Added mood_state field to cognition API response
**Files:** web/src/state/emotionState.ts, web/src/hooks/useEmotionState.ts, backend/api/routers/cognition.py

---

## 2026-05-17 | Wrong time (1 hour off)
**Error:** "what time is it" returned wrong time
**Cause:** PowerShell reading Windows misconfigured timezone
**Fix:** Switched to Python ZoneInfo("Asia/Karachi") using WSL2 NTP-synced clock
         Added PKT abbreviation to response
**File:** backend/api/tools/system_tools.py:3128

---

## 2026-05-17 | "upgrading memory" triggered system specs
**Error:** "I am thinking about upgrading memory" returned PC specs
**Cause:** voice/emotion_tts_mapper.py missing → emotional guard crashed → LLM picked system_info
**Fix:** Created missing emotion_tts_mapper.py → emotional guard runs correctly
         system_info description updated to exclude conversational "memory"
**File:** backend/voice/emotion_tts_mapper.py (new), backend/api/tools/system_tools.py:2008

---

## 2026-05-17 | page.tsx console warnings
**Error:** unused imports + debug console.log firing every emotion change
**Fix:** Removed MicOff import, wakeSupported, debug console.log calls
**File:** web/src/app/app/command-center/page.tsx
