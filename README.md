# Xyron

**A voice-driven, agentic AI operator for Windows.** Speak a command, and a coordinator dispatches it across specialized agents — browser automation, coding, filesystem, system administration — that plan, act, verify, and narrate the result back to you.

[![License](https://img.shields.io/badge/license-see--LICENSE-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](backend/pyproject.toml)
[![Next.js](https://img.shields.io/badge/next.js-15-black.svg)](web/package.json)
[![Tauri](https://img.shields.io/badge/desktop-tauri%202-24C8DB.svg)](desktop-app/src-tauri/tauri.conf.json)

---

## Overview

Xyron is not a chatbot with tool access bolted on. It's built around a **perceive → plan → act → verify** loop: a world-state service continuously tracks what's on screen, which windows and browser tabs are open, and recent activity; a coordinator agent breaks incoming requests into a task graph and delegates to domain agents; every risky action passes through a human-in-the-loop approval gate before it executes.

The system runs as three independently deployable layers that talk only over HTTP:

```
desktop-app (Tauri + React)  ─┐
web (Next.js dashboard)      ─┼──▶  backend (FastAPI)  ──▶  external APIs / OS
```

## Core Capabilities

- **Voice pipeline** — local Whisper STT, wake-word detection, streaming TTS (OpenAI + local pyttsx3 fallback), multilingual routing and response localization
- **Multi-agent coordination** — a `CoordinatorAgent` builds a task graph and delegates across a browser agent, coding agent (product planning → frontend/backend engineering → QA → visual review → git), automation agent (disk/downloads/duplicates/startup cleanup), and a personality layer (tone, humor guard, emotional planning)
- **Perception & world state** — CDP-based browser observation, desktop window/selection tracking, and periodic vision analysis feed a single `WorldState` that agents query instead of guessing
- **Operator mode** — direct desktop control (mouse/keyboard/window) with an observe → think → act → verify loop, currently covering VS Code, Chrome, Explorer, and YouTube skills
- **Filesystem intelligence** — an indexed, watched, incrementally-updated filesystem search (`fs_index` + `fs_watcher`) with a non-blocking worker queue so heavy scans never stall the voice runtime
- **Memory** — short-term rolling context plus long-term fact extraction, and an episodic SQLite log of every turn used for proactive suggestions
- **MCP integrations** — JSON-RPC servers for WhatsApp (Playwright-driven) and Odoo, plus a first-party MCP server exposing Xyron's own system tools to external MCP clients
- **Approval gate (HITL)** — any action classified as risky writes a plan to `Pending_Approval/` and halts; nothing executes until a human moves it to `Approved/`

## Architecture

| Layer | Responsibility | Stack |
|---|---|---|
| **Backend** | Voice pipeline, intent routing, agent orchestration, perception, tool execution | Python 3.10+, FastAPI, 34 routers under `/api/v1` |
| **Web Dashboard** | Command center, activity timeline, approvals queue, stats, integrations | Next.js 15, React 19, TypeScript, Tailwind |
| **Desktop App** | System-tray presence, global wake-word listening, native automation bridge | Tauri 2 (Rust shell) + React renderer |

Intent routing is three-layered: fast keyword matching, semantic classification via `gpt-4o-mini` function calling, and a sentence-transformer embedding router — falling back gracefully as confidence drops.

For deep-dives, see [`Docs/architecture/`](Docs/architecture/):

| Doc | Covers |
|---|---|
| [SYSTEM_OVERVIEW.md](Docs/architecture/SYSTEM_OVERVIEW.md) | High-level system map |
| [VOICE_PIPELINE.md](Docs/architecture/VOICE_PIPELINE.md) | STT → routing → TTS |
| [PERCEPTION_ENGINE.md](Docs/architecture/PERCEPTION_ENGINE.md) | Browser/desktop/vision observation |
| [WORLD_STATE.md](Docs/architecture/WORLD_STATE.md) | Shared state model agents query |
| [PLANNING_ENGINE.md](Docs/architecture/PLANNING_ENGINE.md) | Task graph + delegation planning |
| [TOOL_ORCHESTRATOR.md](Docs/architecture/TOOL_ORCHESTRATOR.md) | Tool registry + execution |
| [MEMORY_SYSTEM.md](Docs/architecture/MEMORY_SYSTEM.md) | Short-term + episodic memory |
| [FILESYSTEM.md](Docs/architecture/FILESYSTEM.md) | Indexed fs search + watcher |
| [DATABASE.md](Docs/architecture/DATABASE.md) | SQLite schemas |
| [PROJECT_STRUCTURE.md](Docs/architecture/PROJECT_STRUCTURE.md) | Directory-by-directory map |
| [TECHNICAL_DEBT.md](Docs/architecture/TECHNICAL_DEBT.md) | Known gaps and cleanup targets |

## Getting Started

### Prerequisites

| Tool | Why |
|---|---|
| Python 3.10+ | Backend runtime |
| Node.js 18+ / npm | Web dashboard + desktop app |
| Rust toolchain | Building the Tauri desktop shell |
| WSL2 (Windows) | Backend and voice pipeline target WSL2; `PULSE_SERVER` wiring assumes it |
| `espeak-ng` | Local TTS fallback (`sudo apt-get install espeak-ng`) |
| OpenAI API key | Powers STT correction, intent classification, response generation, TTS |

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
cp .env.example .env        # then fill in OPENAI_API_KEY at minimum
python3 -m uvicorn api.main:app --reload --port 8000
```

Config is read from `backend/.env` by absolute path — your working directory doesn't matter.

### 3. Web Dashboard

```bash
cd web
npm install
npm run dev        # http://localhost:3001
```

### 4. Desktop App (optional)

```bash
cd desktop-app
npm install
npm run dev:wsl     # WSL2 — wires PULSE_SERVER for audio
# or
npm run dev         # native Linux/Mac
```

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/                  # full suite (50+ files)
pytest tests/test_worker_queue.py   # single file
```

```bash
cd web && npm run type-check
cd desktop-app && npm run type-check
```

## Repository Layout

```
Xyron/
  backend/
    api/
      agents/          ← coordinator, browser/coding/automation agents, personality layer
      routers/         ← 34 FastAPI routers mounted under /api/v1
      services/        ← intent routing, perception, memory, fs index/search/watch
      tools/           ← registry of things agents can actually do
    operator_mode/      ← direct desktop control (observe-think-act-verify)
    mcp_servers/         ← WhatsApp / Odoo / Xyron MCP servers (JSON-RPC over stdio)
    voice/               ← STT, TTS, multilingual voice services
    cognition/            ← emotion engine, reflection
    src/ai_operator/       ← business-automation agent skills (silver/gold/platinum tiers)
    tests/                  ← pytest suite

  web/
    src/app/app/             ← command-center, dashboard, activity, approvals, history, stats

  desktop-app/
    src-tauri/                 ← Rust/Tauri shell, native bridge
    src/                        ← React renderer

  shared/                        ← types/hooks shared across web + desktop
  Docs/architecture/               ← system design docs
```

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
