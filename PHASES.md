# Xyron — Development Phases

A living record of every implementation phase. Updated as work ships.

---

## Phase 0 — Cognitive State Engine
**Status:** ✅ Complete  
**Author:** Muhammad Qasim ([@Psqasim](https://github.com/Psqasim))  
**PR:** #7 — merged into `main`

**What was built:**

- `backend/cognition/cognitive_state.py` — canonical `CognitiveState` dataclass with:
  - `AttentionLevel` enum: `IDLE / LISTENING / PROCESSING / SPEAKING / FOCUSED`
  - `MoodBias` enum: `NEUTRAL / ALERT / CALM / STRESSED`
  - `RLock` thread safety — all mutations go through `update()` while holding the lock
  - `turn_count` — increments on every processing cycle
  - `last_updated` timestamp
  - `snapshot()` — returns a serializable dict of all public fields
- `backend/cognition/state_transitions.py` — the only sanctioned way to advance `AttentionLevel`:
  - `transition_to_listening()` — call when mic goes live
  - `transition_to_processing()` — call on transcript received; increments `turn_count`
  - `transition_to_speaking()` — call when TTS starts streaming
  - `transition_to_idle()` — call when response ends
- `backend/cognition/__init__.py` — clean package exports
- `backend/api/services/cognitive_state.py` converted to a **shim** — re-exports from `cognition` package; all existing routers keep working unchanged
- `backend/api/routers/cognition.py` updated — response includes `mood_bias`, `context_summary`, `turn_count`, `last_updated`
- `backend/api/services/command_service.py` — wired: `transition_to_processing()` on command start, `transition_to_idle()` on completion/failure
- `backend/brain/orchestrator.py` — reads `active_goal` + `mood_bias` from state before routing decisions

**Test results:** 230 pass, 23 pre-existing failures (Odoo/LinkedIn fixtures — unrelated)

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

## Phase 8 — Code Assistant Mode
**Status:** ✅ Complete  
**Author:** Tayyab Aziz

**What it does:** When a coding editor (VS Code, Cursor, vim, nvim, etc.) is the active window, Xyron automatically enters Code Mode. The UI transforms into a focused developer interface, voice routing becomes coding-aware, and Xyron behaves like an autonomous AI developer living inside the machine.

**Backend — Cognitive State**
- `backend/cognition/cognitive_state.py` — added 3 new fields:
  - `code_mode: bool` — true when a code editor is the active window
  - `active_project: Optional[str]` — detected VS Code workspace name
  - `active_file: Optional[str]` — currently open filename from window title

**Backend — Environment Monitor** (`backend/api/routers/environment.py`)
- Background loop reduced from 10s → **3s** for faster editor detection
- `CODE_EDITORS` list: `code, cursor, vim, nvim, pycharm, webstorm, intellij, sublime, atom, emacs`
- `_is_code_editor_active(active_window)` — keyword match against active window title
- `_detect_vscode_workspace()` — reads VS Code `storage.json` + parses window title to extract project/file; cached 5s, non-blocking
- On editor focus: sets `code_mode=True`, `active_ui_mode="focus"`, workspace info
- On editor blur: sets `code_mode=False` (does not reset `active_ui_mode`)

**Backend — Dev Agent** (`backend/src/ai_operator/agents/dev_agent.py`)
- New `DevAgent` extending `BaseAgent` — 7 supported intents: `explain`, `write`, `test`, `debug`, `refactor`, `architect`, `optimize`
- `phi3:mini` for intent classification, `mistral:7b` for reasoning responses — fully local via Ollama
- Senior-engineer personality: concise, direct, production-oriented, no fluff
- `run()` for single-shot queries, `stream()` generator for token streaming
- Context-aware system prompt includes active project + file per request

**Backend — Dev Router** (`backend/api/routers/dev.py`)
- `POST /api/v1/dev/query` — non-streaming query, returns full response
- `POST /api/v1/dev/stream` — SSE token streaming for progressive UI updates
- `GET /api/v1/dev/status` — current code_mode, active_project, active_file
- Registered in `main.py`

**Backend — Voice Router** (`backend/voice/voice_command_router.py`)
- When `code_mode=True` AND command is code-related: routes directly to `/api/v1/dev/query`
- System commands (volume, music, browser, OS) are never intercepted regardless of code_mode
- Falls back to standard `/api/v1/commands` for everything non-code

**Backend — Command Service** (`backend/api/services/command_service.py`)
- `_CODE_INTENT_PATTERNS` — 7 patterns checked first when `code_mode=True`
- Normal routing behavior unchanged outside code mode

**Frontend — CodeAssistantPanel** (`web/src/components/code/CodeAssistantPanel.tsx`)
- Polls `/api/v1/dev/status` every 2s for live context
- **Live context display:** project name, active file, detected language, UI mode
- **6 Quick Actions:** Explain / Write / Debug / Refactor / Architect / Optimize — inject prompts into the input pipeline
- **Passive intelligence hints:** random low-frequency (45–90s) observations like "Detected repeated error pattern", "Large render cycle detected" — non-invasive, context-aware
- **Code rain background:** subtle matrix-style code characters behind the panel (canvas, opacity 0.4) — visible only in code_mode
- **Streaming response panel:** renders tokens progressively as they arrive via SSE, with code block syntax highlighting, auto-scroll to bottom
- **DEV MODE ACTIVE** badge with pulse animation when code_mode is live

**Frontend — UI Mode** (`web/src/hooks/useUIMode.ts`)
- Priority updated: `sentinel → overdrive → calm → focus (code_mode OR focus goal) → default`
- `code_mode=true` now maps to `'focus'` mode

**Frontend — CognitiveState interface** (`web/src/hooks/useCognitiveState.ts`)
- Extended with all Phase 0 + Phase 8 fields: `mood_bias`, `context_summary`, `turn_count`, `last_updated`, `code_mode`, `active_project`, `active_file`

**Frontend — Command Center** (`web/src/app/app/command-center/page.tsx`)
- `<CodeAssistantPanel>` added to right column — animates in/out with `AnimatePresence` when code_mode changes
- `DEV MODE ACTIVE` animated badge in page header when code_mode is true
- Subtle focus-mode red overlay applied to page root when in code_mode

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
| Phase 9 | Workflow automation — multi-step skill chains with approval gates | Planned |
| Phase 11 | Mobile companion app | Planned |
| Phase 12 | Multi-user / multi-agent coordination | Planned |
