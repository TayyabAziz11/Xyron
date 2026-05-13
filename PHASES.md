# Xyron — Development Phases

A living record of every implementation phase. Updated as work ships.

---

## Phase 1 — Foundation
**Status:** ✅ Complete

- FastAPI backend scaffolding (`backend/api/main.py`, 19 routers)
- Pydantic-settings config (`backend/api/config.py`)
- Keyword + AI intent routing (`command_service.py`)
- Tool registry with 10 auto-registered modules (`backend/api/tools/`)
- Short-term memory deque + long-term JSON facts (`memory_service.py`)
- Episodic memory SQLite (`episodic_memory.py`)
- HITL approval gate — risky actions write to `Pending_Approval/`, block until moved to `Approved/`

---

## Phase 2 — Voice Pipeline
**Status:** ✅ Complete

- STT: `faster-whisper` local model (`backend/voice/whisper_service.py`)
- Local TTS: `pyttsx3` / espeak-ng (`backend/voice/tts_service.py`)
- OpenAI TTS API endpoint (`backend/api/routers/voice.py` — `/api/v1/voice/synthesize`)
- Response generator with OpenAI + per-agent template fallback (`response_generator.py`)
- Wake word service (`wake_word_service.py`)
- Instant greeting via Kokoro warmup cache + abort-on-timeout
- **Emotion detection from audio** — `AudioEmotionDetector` in `voice/emotion_detector.py` runs after every Whisper transcription; writes `last_user_emotion` + `emotion_intensity` to `CognitiveState` (rule-based: pitch, RMS energy, ZCR, speech rate → 6 emotion labels)

---

## Phase 3 — MCP Integrations
**Status:** ✅ Complete

- WhatsApp MCP server — Playwright browser automation (`backend/mcp_servers/whatsapp_mcp/server.py`)
- Odoo MCP server — JSON-RPC client (`backend/mcp_servers/odoo_mcp/server.py`)
- LinkedIn, Twitter snapshot tooling
- JSON-RPC 2.0 over stdin/stdout subprocess architecture
- File lock at `/tmp/wa_mcp.lock` for browser singleton

---

## Phase 4 — Web Dashboard
**Status:** ✅ Complete

- Next.js 15 App Router, React 19, Tailwind, Framer Motion
- Pages: `command-center`, `dashboard`, `activity`, `approvals`, `history`, `integrations`, `settings`, `stats`, `workflows`
- Custom hooks under `web/src/hooks/` — no state management library
- Desktop Electron app with IPC bridge (`desktop-app/src/preload/`)
- WSL2 audio fix: `PULSE_SERVER=unix:/mnt/wslg/PulseServer`

---

## Phase 5 — Environment Monitor + Cinematic I'm Home UI
**Status:** ✅ Complete  
**Commit:** `d7e33f2`

- Environment monitor: tracks system health, CPU/GPU/RAM in real time
- `ImHomeProtocol.tsx` — full briefing flow (weather, news, calendar, system status)
- `CinematicOrb.tsx` — animated 3D orb with particle effects for I'm Home screen
- `NeuralCanvas.tsx` — neural network canvas background animation
- WSL2 localhost proxy fix for backend connectivity
- Wake word debug tooling

---

## Phase 6 — Adaptive UI Modes + Ambient Cognitive State
**Status:** ✅ Complete

**Ambient Components** (`web/src/components/ambient/`)
- `PassiveHUD.tsx` — top bar showing live emotion, intensity bar, cognitive mode badge
- `ThoughtStream.tsx` — emotion-tagged thought cards with timestamps and spring animations
- `index.ts` — barrel export

**New Hooks** (`web/src/hooks/`)
- `useCognitiveState.ts` — polls `/api/v1/cognition` for live emotional + cognitive state
- `useThoughtGenerator.ts` — generates contextual thought strings driven by emotion type
- `useUIMode.ts` — switches UI layout mode (focus / ambient / cinematic / minimal)

**Backend Extensions**
- `cognitive_state.py` — extended with `emotion_intensity` field + `VALID_EMOTIONS` list
- `cognition.py` router — new `/api/v1/cognition` endpoints, PATCH allowlist, convenience routes

**UI Wiring**
- `AppShell.tsx` — `PassiveHUD` injected above `Header` in main content column
- `command-center/page.tsx` — `ThoughtStream` overlay + thoughts toggle button
- `layout.tsx` — ambient context provider integrated

---

## Phase 7 — Advanced Emotion System
**Status:** ✅ Complete

- Human-like emotional states: `neutral`, `curious`, `excited`, `focused`, `calm`, `joy`, `sad`, `laugh`, `frustrated`, `confident`
- Centralized emotion config module
- `useThoughtGenerator` upgraded — emotion-driven thought templates per state
- `PassiveHUD` upgraded — full emotion display, color-coded intensity bar
- `ThoughtStream` upgraded — emotion-tagged card layout with per-emotion icon + color
- Backend `CognitiveState` extended: `emotion_intensity` (0.0–1.0), validated emotion list
- Cognition router: validation middleware, convenience `POST /api/v1/cognition/emotion` shortcut

---

## Phase 10 — Voice Identity System
**Status:** ✅ Complete

**DSP Engine** (`backend/voice/audio_fx.py`)
- Realtime audio FX on numpy float32 arrays — no subprocesses, no cloud
- Presets: `default`, `cinematic`, `whisper`, `robotic`, `warm`
- Effects: reverb, pitch shift, chorus, EQ, subtle distortion

**Voice Personality Layer** (`backend/voice/voice_personality.py`)
- Per-mode speaking style, pacing, cinematic delivery, text shaping
- Speech mutex + cancel flag for interrupt-safe delivery
- Modes: `assistant`, `narrator`, `companion`, `analyst`

**Emotion-to-Voice Router** (`backend/voice/emotion_router.py`)
- Maps live `CognitiveState` emotion → voice delivery parameters
- Adjusts speed, pitch, FX preset per emotional state

**Local Voice Profiles** (`backend/voice/voice_profiles.json`)
- Persistent named profiles with full parameter sets
- Hot-swappable at runtime

**Voice Identity API** (`backend/api/routers/voice_identity.py`)
- `GET/POST /api/v1/voice/identity/mode` — get/set active voice mode
- `GET/POST /api/v1/voice/identity/profile` — list/activate profiles
- `GET/POST /api/v1/voice/identity/fx` — FX preset control
- `POST /api/v1/voice/identity/preview` — preview mode/profile without persisting
- Router registered in `main.py`

**DSP Integration**
- `audio_fx` injected into `_kokoro_to_wav()` in `voice.py` — processes every Kokoro output before playback

---

---

## Architecture Notes

### cognitive_state.py — canonical location
The canonical `CognitiveState` singleton lives in **`backend/cognition/`** (owned by friend's branch).
`backend/api/services/cognitive_state.py` is a shim that re-exports it — do not modify the shim directly.
All routers import from `..services.cognitive_state` and will keep working once the shim is in place.

---

## Collaboration Rules

**Before starting any new phase — send a message first.**
We built Phases 5/6/7/10 while Phase 0 was being planned. No conflict happened but parallel work wastes time.
The rule: post intent in the repo (or direct message) before writing code for a new phase.

---

## Upcoming

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 8 | Proactive intelligence — context-aware suggestions from episodic patterns | Planned |
| Phase 9 | Workflow automation — multi-step skill chains with approval gates | Planned |
| Phase 11 | Mobile companion app | Planned |
| Phase 12 | Multi-user / multi-agent coordination | Planned |
