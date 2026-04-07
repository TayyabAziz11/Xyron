# AI Operator — Architecture

## Overview

AI Operator is structured as three independent layers that communicate through well-defined interfaces:

```
┌─────────────────────────────────────────┐
│           User Interfaces               │
│  ┌──────────────┐  ┌──────────────────┐ │
│  │ Desktop App  │  │  Web Dashboard   │ │
│  │  (Electron)  │  │   (Next.js)      │ │
│  └──────┬───────┘  └────────┬─────────┘ │
└─────────┼────────────────────┼──────────┘
          │ voice/text command  │ HTTP API
          ▼                    ▼
┌─────────────────────────────────────────┐
│              Backend                    │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │       Command Router            │    │
│  │  (parse intent → pick agent)   │    │
│  └──────────────┬──────────────────┘    │
│                 │                        │
│  ┌──────────────▼──────────────────┐    │
│  │       Agent Orchestrator        │    │
│  │   (plan → approve → execute)   │    │
│  └──────────────┬──────────────────┘    │
│                 │                        │
│  ┌──────────────▼──────────────────┐    │
│  │          Agents / Skills        │    │
│  │  Gmail | LinkedIn | WhatsApp   │    │
│  │  Instagram | Odoo | ...        │    │
│  └──────────────┬──────────────────┘    │
│                 │                        │
│  ┌──────────────▼──────────────────┐    │
│  │         MCP Servers             │    │
│  │  (WhatsApp Web, Odoo API)      │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
          │
          ▼ real external actions
   Gmail API / LinkedIn API / WhatsApp Web / Odoo / Instagram Graph API
```

---

## Layer Breakdown

### 1. Desktop App (`desktop/`)
- Electron app — system tray icon + popup assistant window
- Push-to-talk button → captures audio → sends to backend STT pipeline
- Displays latest command result
- **Status:** Planned (Phase 2)

### 2. Web Dashboard (`web/`)
- Next.js + TypeScript + Tailwind CSS
- Pages: approvals, command history, logs, integrations, settings
- Connects to backend via REST API
- **Status:** To be built fresh

### 3. Backend (`backend/`)

#### `src/ai_operator/core/`
Shared utilities used by all agents:
- `gmail_api_helper.py` — Gmail OAuth2 auth and API calls
- `linkedin_api_helper.py` — LinkedIn OAuth2 + API
- `instagram_api_helper.py` — Instagram Graph API
- `odoo_api_helper.py` — Odoo JSON-RPC client
- `whatsapp_web_helper.py` — WhatsApp Web via Playwright
- `content_generator.py` — OpenAI GPT-4o for drafting
- `audit_logger.py` — Append-only JSON + Markdown log
- `mcp_helpers.py` — PII redaction, rate limiting

#### `src/ai_operator/skills/`
Agent logic organised by tier (legacy structure — will be reorganised by domain):
- `silver/` — Gmail watcher, plan creation, approval workflow, MCP execution
- `gold/` — Multi-channel watchers, social executors, orchestrator, reporting
- `platinum/` — Cloud/local split worker (archived for now)

#### `mcp_servers/`
Custom JSON-RPC MCP servers that wrap external services:
- `whatsapp_mcp/server.py` — WhatsApp Web automation (Playwright, persistent browser)
- `odoo_mcp/server.py` — Odoo accounting API (invoices, payments, reports)

#### `scripts/`
CLI entry points — each script runs a single skill or daemon:
- `agent_daemon.py` — Long-running agent process
- `brain_*.py` — Orchestration and brain skills
- `*_watcher_skill.py` — Per-channel perception scripts

---

## Key Design Principles

### Approval Gates (HITL)
Every risky action (send email, post to LinkedIn, create invoice) goes through an approval gate. The agent creates a plan file, moves it to `Pending_Approval/`, and waits. Nothing executes without explicit human approval. This cannot be bypassed.

### Voice Pipeline (V1)
```
Push-to-talk button
  → record audio (PyAudio / sounddevice)
  → Whisper STT (local, free)
  → text command
  → Command Router
  → Agent picks up the task
```
Wake word (openWakeWord) is a Phase 2 addition.

### Audit Trail
All agent activity is logged to:
- JSON structured log (machine-readable, queryable)
- Markdown log (human-readable, PII-redacted)

### MCP Integration
Agents talk to external services through MCP servers (JSON-RPC). This keeps the external integration code isolated and testable independently of the agent logic.

---

## Data Flow Example: "Send a follow-up email to John"

```
1. User speaks → Whisper transcribes → "Send follow-up email to John"
2. Command Router identifies intent: email action, contact: John
3. Gmail agent searches inbox for John's last email (perception)
4. Brain creates a plan: draft follow-up, subject, body
5. Plan moved to Pending_Approval/ → dashboard shows it
6. User approves in web dashboard
7. Gmail executor sends the email via Gmail API
8. Audit logger records the action
9. Dashboard updates command history
```

---

## Shared Types (`shared/`)

Placeholder for TypeScript + Python type definitions that will be shared between the web dashboard and backend API. To be populated when the web dashboard is built.

---

## What Is Not In Scope for V1

- Wake word activation (Phase 2)
- Text-to-speech responses (Phase 2)
- Mobile app (Phase 3)
- Multi-user / team support (Phase 3)
- Self-hosted LLM (not planned)
