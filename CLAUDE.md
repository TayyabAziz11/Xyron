# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Before Starting Any Work

Always sync with main first:
```bash
git checkout main && git pull origin main
git checkout - && git merge main
```

## Running the Project

### Backend (FastAPI on port 8000)
```bash
cd backend
source .venv/bin/activate
python3 -m uvicorn api.main:app --reload --port 8000
```
Config is read from `backend/.env` via absolute path — CWD does not matter. Required key: `OPENAI_API_KEY`.

### Web Dashboard (Next.js on port 3001)
```bash
cd web
npm run dev
```

### Desktop App (Electron)
```bash
cd desktop-app
npm run dev:wsl   # WSL2
npm run dev       # native Linux/Mac
```
The `dev:wsl` script sets `PULSE_SERVER=unix:/mnt/wslg/PulseServer` for WSL2 audio.

### Backend Tests
```bash
cd backend
source .venv/bin/activate
pytest tests/                        # all tests
pytest tests/test_mcp_helpers.py     # single file
```

### Type checking (web)
```bash
cd web && npm run type-check
```

---

## Architecture

Three independent layers communicate only through HTTP:

```
desktop-app (Electron)  ──┐
web (Next.js :3001)     ──┤──▶  backend (FastAPI :8000)  ──▶  external APIs
```

### Backend (`backend/`)

**Entry point:** `backend/api/main.py` — mounts all 19 routers under `/api/v1`, initializes the agent registry as a side-effect import on startup.

**Config:** `backend/api/config.py` — `pydantic-settings` `Settings` singleton. Reads `backend/.env` by absolute path. Key computed properties: `repo_root`, `pending_approval_dir`, `approved_dir`, `logs_dir`, `mcp_servers_dir`.

**Intent routing — two paths:**
1. **Keyword (fast):** `classify_intent()` in `command_service.py` — `INTENT_PATTERNS` list maps keyword tuples → `(agent, skill)`.
2. **Semantic (AI):** `classify_intent_ai()` in `command_service.py` — calls `gpt-4o-mini` with tool definitions from the registry when an OpenAI key is present.
3. **Sentence-transformer:** `intent_router.py` — embedding-based routing as a third path.

**Tool registry:** `backend/api/tools/__init__.py` auto-registers 10 tool modules at import time. The registry exposes `get_definitions()` for OpenAI function-calling format. `system_tools.py` (~3000 lines) is the largest — covers all OS/Windows automation via a PowerShell WSL2 bridge.

**Memory — two layers:**
- `memory_service.py` — in-session `deque(maxlen=40)` + long-term facts persisted to `~/.ai-operator/memory.json`. Regex extraction auto-populates name/profession/location/etc. from user text.
- `episodic_memory.py` — SQLite at `~/.ai-operator/episodes.db`. Stores every turn with `tool_name` and `success`; tracks `tool_patterns` (tool × hour) for proactive suggestions.

**Command queue:** `command_service.py` — in-memory `OrderedDict` + `ThreadPoolExecutor`. Cleared on restart (intentional for v1).

**Voice pipeline:**
- STT: `backend/voice/whisper_service.py` — `faster-whisper` (local), lazy-loaded.
- TTS (two paths): `backend/voice/tts_service.py` uses `pyttsx3`/espeak-ng (local); `backend/api/routers/voice.py` uses OpenAI TTS API directly for the `/api/v1/voice/synthesize` endpoint.
- Response generation: `backend/voice/response_generator.py` — tries OpenAI first, falls back to per-agent template strings; max 150 chars, no markdown.

**MCP servers:** JSON-RPC 2.0 over stdin/stdout, run as subprocesses.
- `backend/mcp_servers/whatsapp_mcp/server.py` — Playwright browser automation, file lock at `/tmp/wa_mcp.lock`.
- `backend/mcp_servers/odoo_mcp/server.py` — Odoo JSON-RPC client.

**Approval gate (HITL):** Any risky action writes a plan file to `Pending_Approval/` and halts. Nothing executes until the file is moved to `Approved/` via the dashboard. This cannot be bypassed in agent code.

**Background services** (start only if `OPENAI_API_KEY` is valid):
- `screen_context_service` — periodic screenshot analysis for proactive context.
- `proactive_service` — time-based proactive suggestion engine.

**Agent skills** (`backend/src/ai_operator/skills/`): organized into `silver/`, `gold/`, `platinum/` tiers. Registered at startup via `ai_operator.agents.registry`.

### Web Dashboard (`web/`)

Next.js 15 App Router, React 19, Tailwind, Framer Motion. No state management library — all data fetching via raw `fetch` in custom hooks under `web/src/hooks/`. Pages live under `web/src/app/app/`: `command-center`, `dashboard`, `activity`, `approvals`, `history`, `integrations`, `settings`, `stats`, `workflows`.

### Desktop App (`desktop-app/`)

Electron + electron-vite + React. Structure: `src/main/` (Electron main process), `src/preload/` (IPC bridge), `src/renderer/` (React UI). Pages: `CommandCenter`, `Home`, `ActivityTimeline`, `Settings`. Voice hooks in `src/renderer/src/hooks/`.

---

## Key Files to Know

| File | Purpose |
|---|---|
| `backend/api/main.py` | FastAPI entry, all 19 routers registered here |
| `backend/api/config.py` | All env config + computed paths |
| `backend/api/services/command_service.py` | Intent patterns, command queue, AI routing |
| `backend/api/services/intent_router.py` | Semantic embedding-based routing |
| `backend/api/tools/registry.py` | Tool registry, OpenAI definitions |
| `backend/api/tools/system_tools.py` | All OS automation (PowerShell bridge) |
| `backend/api/services/memory_service.py` | Short + long-term memory, fact extraction |
| `backend/api/services/episodic_memory.py` | SQLite conversation history |
| `backend/voice/tts_service.py` | Local TTS (pyttsx3) |
| `backend/api/routers/voice.py` | STT + OpenAI TTS API endpoint |
| `backend/.env` | Secrets — never committed |

---

## Environment Notes

- **WSL2:** Desktop audio requires `PULSE_SERVER=unix:/mnt/wslg/PulseServer`. The `dev:wsl` script sets this automatically.
- **`openai` package:** Already installed in `.venv` (`openai==2.32.0`). `openai-whisper` is declared in `pyproject.toml` but not in `requirements.txt` — install separately if needed.
- **`HF_TOKEN`:** Not set by default. Set in `backend/.env` to avoid HuggingFace Hub rate limiting on model downloads.
- **espeak-ng:** Required for local TTS. Install with `sudo apt-get install espeak-ng`.
- **CORS:** Allowed origins are `localhost:3000, 3001, 5173, 4173, 5174`. Override via `CORS_ORIGINS` in `.env`.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
