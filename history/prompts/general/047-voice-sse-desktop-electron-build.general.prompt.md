---
id: "047"
title: "Voice SSE Desktop Electron Build"
stage: general
date: "2026-04-08"
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 001-gold-tier-full
user: Personal AI Employee
command: build voice pipeline, SSE, real execution, Electron desktop
labels: ["voice", "whisper", "sse", "electron", "desktop", "command-execution", "realtime"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/requirements.txt
  - backend/voice/__init__.py
  - backend/voice/audio_utils.py
  - backend/voice/whisper_service.py
  - backend/voice/voice_command_router.py
  - backend/voice/recorder.py
  - backend/voice/cli.py
  - backend/api/routers/events.py
  - backend/api/routers/voice.py
  - backend/api/services/command_service.py
  - backend/api/main.py
  - desktop/package.json
  - desktop/tsconfig.json
  - desktop/.gitignore
  - desktop/src/main.ts
  - desktop/src/preload.ts
  - desktop/src/renderer/index.html
  - desktop/assets/README.md
  - web/src/components/command/CommandBar.tsx
  - web/src/hooks/useCommands.ts
  - web/src/lib/api.ts
  - web/src/lib/types.ts
  - scripts/dev.sh
tests:
  - python3 -c "from api.main import app" — passes, 21 routes
  - cd desktop && npm run build — tsc zero errors
  - cd web && npx tsc --noEmit — zero errors
---

## Prompt

Build these in order:
1. Voice Pipeline Backend (faster-whisper, sounddevice, numpy, scipy)
2. SSE Real-time Updates (EventSource stream for command status)
3. Real Command Execution (background ThreadPoolExecutor, skill dispatch)
4. Electron Desktop App (tray icon, Alt+Space hotkey, popup assistant window)
5. Voice Button in Command Center (MediaRecorder, POST to /api/v1/voice/transcribe)
6. Update types and lib/api.ts (VoiceTranscriptionResult, voice.transcribe, useCommandStream)
7. Update documentation (dev.sh hints for desktop + voice CLI)

## Response

All 8 steps completed:

### Packages installed
- faster-whisper==1.2.1, sounddevice==0.5.5, numpy==2.4.4, scipy==1.17.1, python-multipart==0.0.24

### New API endpoints (21 total)
- GET  /api/v1/events/commands/{command_id} — SSE stream (status/done/error/timeout events)
- POST /api/v1/voice/transcribe            — accepts WebM/WAV blob, returns transcript

### Backend changes
- command_service.py: ThreadPoolExecutor background execution, skill dispatch (_run_email_skill, _run_linkedin_skill, _run_reporting_skill, etc.), thread-safe lock on store
- main.py: mounts events.router and voice.router

### Voice pipeline (backend/voice/)
- audio_utils.py: record_audio, record_until_silence, VAD by RMS, list_input_devices
- whisper_service.py: lazy-loaded faster-whisper model, transcribe_audio, transcribe_file, set_model_size
- recorder.py: PushToTalkRecorder — record(), record_and_transcribe()
- voice_command_router.py: VoiceCommandRouter.submit() → POST /api/v1/commands
- cli.py: python -m voice.cli [--no-submit] [--model tiny|base|small] [--list-devices]

### Desktop app (desktop/)
- Electron 36, TypeScript 5.7, zero compile errors
- main.ts: BrowserWindow frameless popup, Tray, globalShortcut Alt+Space, IPC handlers
- preload.ts: contextBridge exposes aiOperator.{submitCommand, getCommands, getHealth, hideWindow, openDashboard}
- renderer/index.html: full dark UI matching web dashboard design tokens, quick chips, result polling, health indicator

### Web frontend changes
- CommandBar.tsx: Mic/MicOff button, MediaRecorder API, POST to voice/transcribe, fills textarea with transcript, pulsing red recording indicator
- useCommands.ts: added useCommandStream(commandId, onUpdate) — EventSource SSE hook with cleanup
- api.ts: added api.voice.transcribe(blob)
- types.ts: added VoiceTranscriptionResult interface

### Verification
- Backend: 21 routes, loads clean
- Desktop TypeScript: zero errors (tsc)
- Web TypeScript: zero errors (tsc --noEmit)
---
outcome: All steps complete. Voice pipeline, SSE streaming, real command execution, and Electron desktop app built and verified.
---
