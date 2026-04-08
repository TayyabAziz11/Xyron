---
id: "048"
title: "Voice TTS Response Pipeline"
stage: general
date: "2026-04-07"
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 001-gold-tier-full
user: Personal AI Employee
command: build voice TTS response system
labels: ["voice", "tts", "pyttsx3", "espeak", "audio", "command-center", "settings", "desktop"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/requirements.txt
  - backend/voice/tts_service.py
  - backend/voice/response_generator.py
  - backend/api/routers/voice.py
  - backend/api/schemas/command.py
  - backend/api/services/command_service.py
  - web/src/lib/types.ts
  - web/src/hooks/useVoice.ts
  - web/src/components/ui/VoicePlayer.tsx
  - web/src/components/command/CommandResult.tsx
  - web/src/app/app/command-center/page.tsx
  - web/src/app/app/settings/page.tsx
  - desktop/src/renderer/index.html
  - README.md
  - ARCHITECTURE.md
tests: []
---

## Prompt

Build the voice response (Text-to-Speech) system for AI Operator. This makes the assistant speak its responses back to the user after executing commands.

14 steps covering: pyttsx3 install, TTS service, response generator, POST /api/v1/voice/synthesize endpoint, assistant_response schema field, command service integration, TypeScript types, useVoice hook, VoicePlayer component, CommandResult update, Command Center page, Settings page voice section, Electron desktop voice support, docs update.

## Response

Implemented the complete TTS voice response pipeline end-to-end:

**Backend (Python)**
- `backend/voice/tts_service.py` — pyttsx3 wrapper with thread-safe lazy init, WAV file synthesis, graceful 503 degradation when espeak-ng missing
- `backend/voice/response_generator.py` — maps agent/skill results to short spoken-friendly sentences (strips markdown, ≤150 chars)
- `POST /api/v1/voice/synthesize` + `GET /api/v1/voice/tts-info` added to `backend/api/routers/voice.py`
- `assistant_response: Optional[str]` added to `Command` schema and persisted in command store
- `CommandService._execute()` calls `generate_assistant_response()` after skill dispatch

**Frontend (TypeScript/React)**
- `useVoice.ts` hook — fetches WAV from `/api/v1/voice/synthesize`, plays via `new Audio(objectURL)`, persists settings to localStorage
- `VoicePlayer.tsx` — mute/unmute button + animated wave indicator while speaking
- `CommandResult.tsx` — updated with assistant_response displayed prominently (brand-tinted card), raw result collapsible, voice controls
- Command Center page — auto-plays assistant_response on completion, voice toggle in header
- Settings page — Voice Settings card: enable, auto-play, speed (80-250 WPM), volume, test button

**Desktop (Electron)**
- `speakResponse()` function — fetches WAV and plays via HTMLAudioElement
- `toggleVoice()` — speaker icon in titlebar, persists to localStorage
- Wave indicator in titlebar while TTS plays
- `pollStart()` calls `speakResponse(d.assistant_response)` on completion

**Verification**
- Backend imports clean: 23 routes registered including 3 voice routes
- TypeScript: zero compilation errors
- pyttsx3 installed (Python 3.10 user path)
- TTS returns graceful 503 until `sudo apt-get install espeak-ng` is run

## Outcome

All 14 implementation steps complete. Voice response pipeline is fully wired. Requires `sudo apt-get install espeak-ng` on the host to activate audio synthesis.
---
