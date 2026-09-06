# Xyron — Pakistan's Local-First Roman Urdu Desktop Voice Assistant

**Speak in Roman Urdu, Urdu script, English, or a mix of all three — Xyron routes it, acts on it, and talks back.** A voice-driven, agentic AI operator for Windows: a coordinator dispatches spoken commands across specialized agents — browser automation, coding, filesystem, WhatsApp, system administration — that plan, act, verify, and narrate the result back to you.

[![License](https://img.shields.io/badge/license-see--LICENSE-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](backend/pyproject.toml)
[![Next.js](https://img.shields.io/badge/next.js-15-black.svg)](web/package.json)
[![Tauri](https://img.shields.io/badge/desktop-tauri%202-24C8DB.svg)](desktop-app/src-tauri/tauri.conf.json)

---

## Overview

Xyron is not a chatbot with tool access bolted on. It's built around a **perceive → plan → act → verify** loop: a world-state service continuously tracks what's on screen, which windows and browser tabs are open, and recent activity; a coordinator agent breaks incoming requests into a task graph and delegates to domain agents; every risky action passes through a human-in-the-loop approval gate before it executes.

What makes it different for Pakistani users specifically: the entire voice pipeline — intent understanding, response generation, and speech synthesis — is built to handle Roman Urdu ("Chrome kholo", "awaz kam karo"), Urdu script, and code-mixed English/Urdu natively, not as an afterthought translation layer.

## Key Features

- **Roman Urdu / Urdu-script / mixed-language voice commands** — a 3-tier intent router (regex/keyword match in <1ms → sentence-embedding classifier → LLM fallback for anything novel) understands commands in whichever language or script the user actually speaks
- **116 registered tool handlers across 15 modules** — system control, browser automation, filesystem, calendar, Gmail, screen reading, WhatsApp, and more, all exposed through one tool registry
- **5 AI agents (coordinator, browser, coding, automation, personality) spanning 87 specialist sub-modules** — a `CoordinatorAgent` builds a task graph and delegates instead of one monolithic prompt trying to do everything
- **Local, on-device STT** via `faster-whisper`, with GPU acceleration where available and an INT8 CPU fallback — no audio leaves the machine for transcription
- **Dual TTS** — Kokoro (English, fully offline) and Edge-TTS (native Pakistani Urdu voices, `ur-PK-AsadNeural`/`ur-PK-UzmaNeural`) selected automatically by response language
- **Human-in-the-loop approval gate** — any action classified as risky writes a plan to `Pending_Approval/` and halts; nothing executes until a human moves it to `Approved/`
- **23-step autonomous coding agent** — product planning → design → frontend/backend implementation → QA → visual review → git, end to end from a spoken request
- **File organization workflow** — plan → confirm → execute with a full undo manifest, gated behind the same approval flow
- **WhatsApp integration** — Baileys and open-wa transports, contact/identity resolution, voice-driven send/reply, incoming-message announcements

## Architecture

```
🎤 Mic → Wake Word → STT (faster-whisper) → Intent Router (3-tier) → Agents / Tools → TTS (Kokoro / Edge-TTS) → 🔊 Audio
```

The system runs as three independently deployable layers that talk only over HTTP:

```
desktop-app (Tauri + React)  ─┐
web (Next.js dashboard)      ─┼──▶  backend (FastAPI)  ──▶  external APIs / OS
```

| Layer | Responsibility | Stack |
|---|---|---|
| **Backend** | Voice pipeline, intent routing, agent orchestration, perception, tool execution | Python 3.10+, FastAPI |
| **Web Dashboard** | Command center, activity timeline, approvals queue, stats, integrations | Next.js 15, React 19, TypeScript, Tailwind |
| **Desktop App** | System-tray presence, global wake-word listening, native automation bridge | Tauri 2 (Rust shell) + React renderer |

For deep-dives, see [`Docs/architecture/`](Docs/architecture/) — system overview, voice pipeline, perception engine, world state, planning engine, tool orchestrator, memory system, filesystem intelligence, database schemas, and project structure.

## Tech Stack

| Component | Technology |
|---|---|
| Backend API | Python, FastAPI |
| Speech-to-text | faster-whisper (local, GPU/INT8) |
| Text-to-speech | Kokoro (offline, English) + Edge-TTS (Urdu) |
| LLM (cloud) | OpenAI (`gpt-4o-mini` intent classification + response generation) |
| LLM (local fallback) | Ollama (`qwen2.5:1.5b`) |
| Intent routing | Regex/keyword + sentence-transformer embeddings + LLM fallback |
| Web dashboard | Next.js 15, React 19, Tailwind, Framer Motion |
| Desktop shell | Tauri 2 (Rust) + React |
| Browser automation | Playwright (CDP) |
| WhatsApp integration | Baileys / open-wa (Node.js sidecar) |
| Memory | SQLite (episodic log) + JSON fact store |

## Getting Started

### Prerequisites

| Tool | Why |
|---|---|
| Python 3.10+ | Backend runtime |
| Node.js 18+ / npm | Web dashboard, desktop app, WhatsApp sidecar |
| Rust toolchain | Building the Tauri desktop shell |
| WSL2 (Windows) | Backend and voice pipeline target WSL2; `PULSE_SERVER` wiring assumes it |
| `espeak-ng` | Local TTS fallback (`sudo apt-get install espeak-ng`) |
| OpenAI API key | Powers STT correction, intent classification, response generation |

### 1. Clone

```bash
git clone https://github.com/TayyabAziz11/Xyron.git
cd Xyron
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in OPENAI_API_KEY at minimum — see .env.example for every var
python3 -m uvicorn api.main:app --reload --port 8000
```

Config is read from `backend/.env` by absolute path — your working directory doesn't matter.

### 3. Web Dashboard

```bash
cd web
npm install
cp .env.local.example .env.local
npm run dev        # http://localhost:3001
```

### 4. Desktop App (optional)

```bash
cd desktop-app
npm install
cp .env.example .env       # optional — Clerk auth; without it the app runs in dev-auth mode
npm run dev:wsl            # WSL2 — wires PULSE_SERVER for audio
# or
npm run dev                # native Linux/Mac
```

### 5. WhatsApp integration (optional)

```bash
cd backend/integrations/whatsapp/sidecar
npm install
cp .env.example .env       # generate a real WA_SIDECAR_API_KEY
node server.js
```

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/                        # full suite
pytest tests/test_wa_identity.py     # single file
```

## Demo Commands

All of these are live-verified through the real intent-routing pipeline (see `backend/tests/manual_50_command_matrix.py`):

| Say (Roman Urdu / English) | What happens |
|---|---|
| "Chrome kholo." | Opens Google Chrome |
| "Chrome band karo." | Closes Chrome |
| "Display settings kholo." | Opens Windows display settings |
| "E drive kholo." | Opens the E: drive in File Explorer |
| "Screen pe kya hai?" | Reads and describes what's currently on screen |
| "Google pe Pakistan weather search karo." | Searches Google for the query |
| "YouTube pe Atif Aslam ka koi famous gana chalao." | Finds and plays a YouTube video |
| "Gana pause karo." | Pauses current media playback |
| "Awaz thori kam karo." | Lowers system volume |
| "Recycle bin khali karo." | Empties the Recycle Bin (routed through the approval gate) |

## Built for Alibaba Cloud AI Hackathon Pakistan 2026

Xyron started as a general-purpose voice operator; during the hackathon we focused it specifically on Roman Urdu / Urdu users. Added during the hackathon build phase:

- Native Roman Urdu and Urdu-script command understanding across the full intent-routing pipeline, including mixed-language (code-switched) utterances
- Native Pakistani Urdu TTS voices via Edge-TTS, auto-selected based on detected response language
- WhatsApp integration (Baileys + open-wa sidecar) — voice-driven send/reply, contact resolution, incoming-message announcements
- File organization tool with a plan → confirm → execute → undo workflow
- Business-automation reporting skills (accounting audit, weekly briefings)
- STT accuracy and latency benchmarking across Whisper model sizes for low-spec hardware
- Assorted reliability fixes to wake-word detection, STT retries, and TTS/STT thread-pool contention

## Team

- **Muhammad Qasim** — Backend, Memory, Agents, Documentation
- **Tayyab Aziz** — Voice Engine, Orchestration, UI, Lead Developer

## Links

- Instagram: [@xyron_ai](https://instagram.com/xyron_ai)
- GitHub: [TayyabAziz11/Xyron](https://github.com/TayyabAziz11/Xyron)

## Contributing

This repo protects `main` — all work happens on feature branches:

```bash
git checkout main && git pull origin main
git checkout -b feat/your-feature
# ...
git push origin feat/your-feature
gh pr create --base main --head feat/your-feature
```

See [`CLAUDE.md`](./CLAUDE.md) for the full development guide (config reference, intent-routing internals, key files).

## License

See [LICENSE](./LICENSE).
