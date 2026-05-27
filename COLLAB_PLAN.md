# Xyron — Collaboration Plan

> **For Tayyab's collaborator** — everything you need to understand the system,
> what exists, what each layer does, and what to work on.
> Last updated: 2026-05-27

---

## What Is Xyron?

Xyron is a **local-first AI operating assistant** — runs entirely on your machine.

- **Wakes on voice** ("Hey Jarvis" or custom wake word)
- **Understands natural language** — not keyword matching
- **Controls your PC** — opens files/folders/apps, system settings, volume, brightness, etc.
- **Speaks back** using Kokoro TTS (local, no internet needed)
- **Remembers context** across the conversation
- **Works offline** — no internet needed for most features

---

## Repository Structure

```
Xyron/
├── backend/              FastAPI Python backend (the brain)
│   ├── api/
│   │   ├── main.py       Entry point — mounts all routers
│   │   ├── config.py     All env config (reads backend/.env)
│   │   ├── routers/      19+ API routers (voice, tools, auth, dashboard…)
│   │   ├── services/     Core services (intent router, memory, fs_index…)
│   │   └── tools/        Tool registry + system_tools.py (~4000 lines)
│   ├── brain/            Orchestrator, emotion, autonomy, task state
│   ├── voice/            Whisper STT, Kokoro TTS, wake word, emotion
│   └── tests/            pytest test suite
│
├── desktop-app/          Tauri (Rust + React) desktop app — primary UI
│   ├── src/              React frontend
│   │   ├── views/        Dashboard, CommandCenter, Settings, ActivityTimeline
│   │   ├── hooks/        useVoiceSession, useWakeWord, useSystemMonitor…
│   │   └── components/   Orb, voice UI, charts, cards
│   ├── src-tauri/        Rust side (Tauri commands, window management)
│   └── package.json
│
├── shared/               Shared React hooks + components (used by both apps)
│   ├── hooks/            useVoiceWS, useVoice, useSystemMetrics, useWakeWord…
│   └── components/       Reusable UI components
│
├── web/                  Next.js web dashboard (optional, secondary)
│   └── src/app/          Pages: dashboard, command-center, settings…
│
└── Docs/                 Architecture docs and audit reports
```

---

## Three-Layer Architecture

```
┌───────────────────────────────────────────────┐
│  Desktop App (Tauri + React)                  │
│  • Wake word detection (always listening)     │
│  • Voice recording + WebSocket streaming      │
│  • Real-time dashboard (CPU/GPU/RAM/Net)      │
│  • Settings, command center, activity log     │
└──────────────────────┬────────────────────────┘
                       │ HTTP + WebSocket
                       │ localhost:8000
┌──────────────────────▼────────────────────────┐
│  Backend (FastAPI — Python)                   │
│  • STT: Whisper (faster-whisper, GPU)         │
│  • Intent: 4-tier router (cache→regex→        │
│    semantic→LLM)                              │
│  • Orchestrator: decides what to do           │
│  • Tools: opens files, apps, settings, etc.  │
│  • TTS: Kokoro (local ONNX, GPU)             │
│  • Memory: session + long-term facts          │
└───────────────────────────────────────────────┘
```

---

## Voice Pipeline (the critical path)

Every voice utterance goes through this pipeline:

```
Microphone
  → Wake word detected (OpenWakeWord)
  → Audio captured (VAD — silence filtered out)
  → Whisper STT → transcript
  → Context resolver (replace "it" / "the folder" with actual entity)
  → Ordinal resolver ("open the second one" → stored match path)
  → Tier 0: Local clock? → instant response (no LLM)
  → Tier 0b: System metrics? → instant response (no LLM)
  → Tier 2: Regex intent match? → route to tool
  → Tier 3: Semantic (sentence-transformers)? → route to tool
  → Tier 4: LLM (GPT-4o-mini or Ollama) → generate response
  → Tool execution → verification (path exists? app launched?)
  → Kokoro TTS → audio streamed back
```

**Performance targets:**
- Local clock / system metrics: <50ms total
- Tool command (regex match): <500ms
- LLM response: 1-3s

---

## Key Files to Know

| File | What it does |
|------|-------------|
| `backend/api/main.py` | FastAPI entry point — all 19+ routers mounted here |
| `backend/api/config.py` | Reads `backend/.env`, all computed paths |
| `backend/api/routers/voice_ws.py` | Main voice WebSocket — the hot path |
| `backend/api/services/intent_router.py` | 4-tier hybrid intent router (~950 lines) |
| `backend/api/tools/system_tools.py` | All OS automation tools (~4000 lines) |
| `backend/api/services/fs_index.py` | SQLite filesystem index (drive-aware) |
| `backend/api/services/context_resolver.py` | Pronoun + ordinal disambiguation |
| `backend/api/services/memory_service.py` | Session + long-term memory |
| `backend/brain/orchestrator.py` | Decides: TOOL / LLM / CLARIFY / STOP |
| `backend/voice/whisper_service.py` | Faster-Whisper STT (GPU) |
| `backend/voice/tts_service.py` | Kokoro TTS (local ONNX) |
| `desktop-app/src/hooks/useVoiceSession.ts` | WebSocket voice session hook |
| `desktop-app/src/hooks/useWakeWord.ts` | Wake word listener hook |
| `desktop-app/src/hooks/useSystemMonitor.ts` | Real-time metrics WebSocket |
| `shared/hooks/useVoiceWS.ts` | Shared voice WS hook (used by both apps) |

---

## What Is Already Built (as of 2026-05-27)

### Backend
- [x] FastAPI with 19+ routers (voice, auth, brain, dashboard, monitor, takeover…)
- [x] 4-tier intent router: exact cache → regex → sentence-transformers → LLM
- [x] Local clock responses (instant, offline, no LLM)
- [x] Drive-aware filesystem routing — all letters A-Z supported
- [x] Dynamic drive discovery (auto-detects all /mnt/<letter> mounts)
- [x] SQLite filesystem index with rapidfuzz fuzzy search
- [x] Multi-match disambiguation ("I found 3 folders named Python — which one?")
- [x] Ordinal resolution ("open the second one" → correct path)
- [x] Verify-before-speak (never says "Opened X" until verified)
- [x] Tool registry with 50+ tools
- [x] System tools: files, folders, apps, system settings, volume, brightness…
- [x] Whisper STT (GPU, faster-whisper)
- [x] Kokoro TTS (local ONNX, GPU)
- [x] Wake word service (OpenWakeWord)
- [x] Session + long-term memory
- [x] Emotion detection + mood state machine
- [x] Orchestrator (brain decision layer)
- [x] Real-time system monitor (CPU/GPU/RAM/Network/Disk via WebSocket)
- [x] Clerk auth integration
- [x] Pre-STT silence gate (RMS energy filter — skips Whisper on silence)
- [x] Multi-step command pipeline
- [x] Context resolver (pronoun + ordinal disambiguation)
- [x] PERF logs: [PERF_STT], [PERF_INTENT], [PERF_TOOL], [PERF_TOTAL]

### Desktop App (Tauri)
- [x] Tauri 2.x shell (Rust + WebView2)
- [x] React + Vite frontend
- [x] Wake word always-on background listener
- [x] Voice session (full duplex WebSocket)
- [x] Real-time dashboard (CPU/GPU/RAM/Network graphs)
- [x] Reactive orb (pulsing, emotion-aware)
- [x] Settings page (voice, appearance, behavior)
- [x] Command center (text commands)
- [x] Activity timeline
- [x] Clerk auth (signup/login)
- [x] Tailwind CSS styling
- [x] Exponential backoff WebSocket reconnect

---

## What Still Needs Work

### High priority
- [ ] **Live testing Phase 16** — manual end-to-end test of all voice commands
- [ ] **RVC live streaming** — disabled; per-chunk latency is inconsistent
- [ ] **Clerk webhooks** — user profile sync to backend not fully wired
- [ ] **Work Mode** — workflow defined but multi-app orchestration needs testing
- [ ] **Settings persistence** — some settings saved locally but not synced

### Lower priority
- [ ] **Mobile companion** — future scope
- [ ] **Proactive suggestions** — partially built, needs tuning
- [ ] **Web dashboard parity** — some desktop-only features not on web

---

## Development Workflow

### Branch strategy
```
main                      <- stable, always passing tests
feat/your-feature-name    <- all work goes here
fix/your-fix-name         <- bug fixes
```

**Never push directly to `main`.** Always open a PR.

### Running the full stack

Terminal 1 — Backend:
```bash
cd backend
PYTHONPATH=/mnt/e/Xyron/backend python3 -m uvicorn api.main:app --reload --port 8000
```

Terminal 2 — Desktop app:
```bash
cd desktop-app
npm run dev:wsl
```

### Running tests
```bash
cd backend
pytest tests/ -v
# Expected: 112+ passed, 0 failed, 2 skipped
```

### Adding a new tool

1. Add `_exec_mytool(params, ctx) -> ToolResult` in `system_tools.py`
2. Register: `registry.register(name="my_tool", definition={...}, executor=_exec_mytool, ...)`
3. Add routing in `intent_router.py` `_build_rules()`
4. Add a test in `tests/test_tools_routing.py`

### Adding a new API route

1. Create `backend/api/routers/myrouter.py`
2. Mount in `backend/api/main.py`: `app.include_router(myrouter.router, prefix="/api/v1/myroute")`

---

## Environment Variables Quick Reference

| Variable | Required | Notes |
|----------|----------|-------|
| `OPENAI_API_KEY` | Yes | LLM fallback + embeddings |
| `CLERK_SECRET_KEY` | Yes | Auth validation |
| `CLERK_PUBLISHABLE_KEY` | Yes | Frontend auth |
| `ONNX_PROVIDER` | No | `CUDAExecutionProvider` for GPU, else `CPUExecutionProvider` |
| `WHISPER_MODEL` | No | `tiny`/`base`/`small`/`medium`/`large` |
| `ENABLE_RVC` | No | Voice conversion (keep `false` for live) |
| `LOCAL_ONLY_MODE` | No | Skips HuggingFace download (for offline dev) |
| `FS_SCAN_ROOTS` | No | Override auto-detected drive scan roots |

---

## Common Log Tags (for debugging)

| Tag | Meaning |
|-----|---------|
| `[DRIVE_DISCOVERY]` | Filesystem index scanning drives |
| `[DRIVE_FOUND]` | A drive was indexed |
| `[FS_INDEX_HIT]` | Found in filesystem index |
| `[FS_MATCH]` | Fuzzy match succeeded |
| `[FS_TOP_RESULT]` | Best ranked result |
| `[SETTINGS_RESOLVER]` | Settings intent matched |
| `[CTX_RESOLVED]` | Pronoun replaced with entity |
| `[CTX_ORDINAL]` | "the second one" resolved |
| `[PERF_STT]` | Whisper latency ms |
| `[PERF_INTENT]` | Orchestrator latency ms |
| `[PERF_TOOL]` | Tool execution latency ms |
| `[PERF_TOTAL]` | Full turn latency ms |
| `[STALE_RESPONSE_DROPPED]` | New utterance arrived, old dropped |
| `[LOCAL_CLOCK_RESPONSE]` | Time/date answered without LLM |
| `[TTS_STARTED]` / `[TTS_STOPPED]` | Audio streaming lifecycle |
| `[WakeWord]` | Wake word service events |

---

## API Endpoints (Key ones)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |
| WS | `/api/v1/voice/ws` | Main voice WebSocket |
| WS | `/api/v1/voice/ws/wake` | Wake word WebSocket |
| WS | `/api/v1/monitor/ws` | Real-time metrics WebSocket |
| GET | `/api/v1/monitor/snapshot` | Current system metrics (REST fallback) |
| POST | `/api/v1/command` | Text command (non-voice) |
| GET | `/api/v1/brain/state` | Brain/mood state |
| POST | `/api/v1/auth/verify` | Clerk token verification |

---

## PR History

| PR | What was done |
|----|---------------|
| #21 `feat/filesystem-routing-reliability` | Drive-aware routing, verify-before-speak, fuzzy match, silence gate, dynamic drive discovery, rapidfuzz, multi-match disambiguation, ordinal resolution, Urdu settings fixes, PERF logs |
| #18 `feat/system-tools-voice-routing` | System tools core layer, voice routing accuracy |
| Prior PRs | Emotion engine, memory, wake word, Tauri migration |

---

## Contacts

| Person | Role |
|--------|------|
| Tayyab (owner) | Brain, cognition, routing, memory, emotion, voice pipeline, backend architecture |
| Collaborator | Tools, OS control, execution layer, frontend features |

---

## Quick Glossary

| Term | Meaning |
|------|---------|
| **Tier 0** | Local clock — instant, offline, no AI |
| **Tier 2** | Regex routing — sub-millisecond, no model |
| **Tier 3** | Semantic routing — sentence-transformers (~80ms) |
| **Tier 4** | LLM fallback — GPT-4o-mini or Ollama |
| **Kokoro** | Local text-to-speech model (ONNX, runs on GPU) |
| **OWW** | OpenWakeWord — always-on wake word detector |
| **fs_index** | SQLite index of your filesystem for fast file search |
| **ToolResult** | Structured result: `success`, `text`, `spoken`, `data` |
| **HITL** | Human-in-the-loop: risky actions go to Pending_Approval/ first |
| **RVC** | Real Voice Cloning — voice conversion (disabled for live) |
| **VAD** | Voice Activity Detection — filters silence before Whisper |
| **rapidfuzz** | Fast fuzzy string matching library used for file search |
| **Tauri** | Rust + WebView2 framework for native desktop app |
