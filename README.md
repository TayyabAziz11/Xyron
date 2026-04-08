# AI Operator

> Talk naturally. It does your work.

AI Operator is a voice-first personal AI assistant built for productivity. You give it natural commands — it drafts emails, creates LinkedIn posts, manages approvals, summarises your priorities, and routes tasks to the right agents automatically.

This is not a smart-home assistant. This is an AI that handles real business work.

---

## Product Vision

**Phase 1 — Foundation (current)**
- Push-to-talk voice command input (local Whisper STT)
- Web dashboard: approvals, logs, command history, integrations
- Core agents: email, LinkedIn, task routing
- Approval gates for risky actions (nothing auto-sends without review)

**Phase 2 — Desktop Companion**
- Electron tray app with push-to-talk
- Always-accessible popup assistant
- Wake word support (openWakeWord)

**Phase 2 — Desktop Companion + Voice Responses (current)**
- Electron tray app with push-to-talk
- Always-accessible popup assistant
- Wake word support (openWakeWord)
- **Text-to-speech voice responses** — assistant speaks results after every command (pyttsx3/espeak)
- Voice settings panel: enable/disable, speed, volume, auto-play
- Web dashboard + desktop app both support audio playback

**Phase 3 — Expansion**
- WhatsApp, Instagram, calendar, booking workflows
- Multi-user / team support

---

## Tech Stack

| Layer | Tech |
|---|---|
| Web Dashboard | Next.js + TypeScript + Tailwind CSS |
| Desktop App | Electron + TypeScript |
| Backend / Agents | Python 3.13 |
| Voice (STT) | OpenAI Whisper (local) |
| Voice (TTS) | pyttsx3 / espeak-ng (local, no model download) |
| Wake Word | openWakeWord (Phase 2) |
| Agent Orchestration | Custom Python orchestrator |
| Approval Workflow | File-based HITL (human-in-the-loop) |
| Logging | JSON + Markdown audit trail |

---

## Repository Structure

```
ai-operator/
  backend/          Python backend — agents, orchestrator, MCP servers, core utils
    src/
      ai_operator/  Main Python package
        core/       Shared utilities (Gmail, LinkedIn, Odoo, audit, content)
        skills/     Agent skills by domain
    mcp_servers/    Custom MCP server implementations (WhatsApp, Odoo)
    scripts/        CLI entry points and wrappers
    tests/          Backend tests
    pyproject.toml
    requirements.txt

  web/              Next.js web dashboard (to be built)
  desktop/          Electron desktop companion (to be built)
  shared/           Shared types and schemas
  docs/             Product and integration documentation
    integrations/   Setup guides for Gmail, LinkedIn, WhatsApp, Odoo, etc.
    architecture/   Architecture reference docs
  LICENSE
  README.md
  ARCHITECTURE.md
  CLEANUP_SUMMARY.md
```

---

## What Is Implemented vs Planned

### Implemented (backend — ready to wire up)

- **Gmail agent** — OAuth2 perception + email action execution
- **LinkedIn agent** — OAuth2 + post creation
- **WhatsApp agent** — Web automation via Playwright MCP server
- **Instagram agent** — Graph API integration
- **Odoo agent** — Accounting queries and invoice actions (JSON-RPC MCP server)
- **Approval workflow** — File-based HITL; plans require approval before execution
- **Plan generation** — Structured AI-generated action plans
- **Audit logging** — Append-only JSON + Markdown logs with PII redaction
- **Content generation** — OpenAI GPT-4o integration
- **Agent orchestrator** — Autonomous loop with safety controls

### To Build

- [ ] Web dashboard (new frontend)
- [ ] Desktop Electron app
- [ ] Voice command pipeline (Whisper STT → text → agent router)
- [x] Voice response pipeline (command result → generate_assistant_response → pyttsx3 TTS → WAV → browser Audio playback)
- [ ] Push-to-talk UI
- [ ] Command history and approval UI
- [ ] Settings and integrations management UI

---

## Running

### Backend API

```bash
cd backend
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .

# Copy and fill in environment variables
cp .env.example .env

# Run the FastAPI server
uvicorn api.main:app --reload --port 8000
```

### Web Dashboard

```bash
cd web
npm install
npm run dev      # starts on http://localhost:3001
```

### URLs

| Surface | URL |
|---|---|
| Homepage | http://localhost:3001 |
| Dashboard | http://localhost:3001/app/dashboard |
| Command Center | http://localhost:3001/app/command-center |
| Approvals | http://localhost:3001/app/approvals |
| Activity | http://localhost:3001/app/activity |
| Integrations | http://localhost:3001/app/integrations |
| Workflows | http://localhost:3001/app/workflows |
| Settings | http://localhost:3001/app/settings |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

### Route structure

```
/               → Public landing page (homepage)
/app            → Redirects to /app/dashboard
/app/dashboard  → Main dashboard
/app/command-center  → Command input + history
/app/approvals  → Approval queue
/app/activity   → Audit log
/app/integrations → Integration status
/app/workflows  → Workflow tracker
/app/settings   → System settings
```

### Legacy scripts

```bash
# Run the agent daemon
python scripts/agent_daemon.py

# Run a specific skill
python scripts/gmail_watcher_skill.py
```

---

## Integration Setup Guides

See `docs/integrations/` for step-by-step setup:
- Gmail OAuth2 → `docs/integrations/gmail_oauth_setup.md`
- LinkedIn OAuth → `docs/integrations/linkedin_real_setup.md`
- WhatsApp Web → `docs/integrations/mcp_whatsapp_web_setup.md`
- Odoo → `docs/integrations/mcp_odoo_setup.md`
- Instagram → `docs/integrations/mcp_instagram_setup.md`

---

## License

See `LICENSE`.
