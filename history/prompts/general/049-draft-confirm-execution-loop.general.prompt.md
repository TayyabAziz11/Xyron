---
id: "049"
title: "Draft Confirm Execution Loop"
stage: general
date: "2026-04-07"
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 001-gold-tier-full
user: Personal AI Employee
command: build conversational action execution loop — draft → review → confirm → execute
labels: ["draft", "confirmation", "linkedin", "email", "voice", "execution-loop", "command-center", "desktop"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/api/services/draft_service.py
  - backend/api/services/draft_executor.py
  - backend/api/routers/drafts.py
  - backend/api/main.py
  - backend/api/schemas/command.py
  - backend/api/services/command_service.py
  - backend/voice/response_generator.py
  - web/src/lib/types.ts
  - web/src/lib/api.ts
  - web/src/hooks/useDrafts.ts
  - web/src/components/command/DraftPreview.tsx
  - web/src/app/app/command-center/page.tsx
  - desktop/src/renderer/index.html
tests: []
---

## Prompt

Build a conversational action execution loop for AI Operator:
1. User says "Draft a LinkedIn post about AI Operator"
2. AI generates the draft using OpenAI, shows it in dashboard
3. AI speaks: "Your LinkedIn draft is ready. Say 'post it' to publish."
4. User says "post it" (or clicks Publish)
5. AI calls the LinkedIn API and posts it
6. AI speaks: "Done! Your LinkedIn post is now live."
Same for email: draft → review → "send it" → sends via Gmail API.

## Response

Built the full draft-confirm-execute loop across 13 files:

**Backend:**
- `draft_service.py` — in-memory draft store (DraftService singleton) with full lifecycle: draft → executing → executed/rejected/failed
- `draft_executor.py` — routes by draft_type to real API helpers (LinkedIn, Gmail, Instagram) with dry-run fallback when credentials missing
- `routers/drafts.py` — 6 REST endpoints: GET /drafts, /pending, /{id}, PATCH /{id}, POST /{id}/confirm, /{id}/reject
- `command_service.py` — INTENT_PATTERNS now has confirm/cancel patterns FIRST; `_run_linkedin_skill` and `_run_email_skill` now return dicts with draft_id; `_execute` handles dict returns and passes draft_id to update_status
- `schemas/command.py` — added `draft_id: Optional[str]` field
- `response_generator.py` — updated signature to accept `draft_id` and `action_hint`; appends "Say 'post it' to confirm" when draft exists
- `main.py` — mounts drafts router

**Frontend (web):**
- `types.ts` — added Draft interface and DraftStatus type; Command.draft_id field
- `api.ts` — added drafts namespace with list/pending/get/confirm/reject/edit methods; added apiPatch helper
- `useDrafts.ts` — hook with getDraft, confirmDraft, rejectDraft, editDraft, executing state
- `DraftPreview.tsx` — card component with edit/confirm/reject, all status states (draft/executing/executed/rejected), voice hint
- `command-center/page.tsx` — integrates DraftPreview; fetches draft on lastResult.draft_id; speaks spoken_response on confirm

**Desktop:**
- `index.html` — draft panel with type/content/hint/confirm/cancel; showDraft/hideDraft/confirmDraft/rejectDraft JS; pollStart checks d.draft_id and fetches + displays draft

**Verification:**
- 29 backend routes (6 draft routes confirmed)
- TypeScript: clean (0 errors)
- Intent patterns: all 6 test cases pass (post it → confirm, send it → confirm, cancel it → cancel, linkedin → linkedin, email → email, never mind → cancel)
