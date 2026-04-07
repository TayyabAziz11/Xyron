# AI Operator — Backend Architecture

## Overview

The backend is organized into clear, product-grade layers. Each layer has a single responsibility and a clean interface.

```
backend/
  api/                    ← FastAPI REST API (dashboard backend)
    routers/              ← HTTP route handlers
    services/             ← Business logic (reads files, manages state)
    schemas/              ← Pydantic v2 request/response models

  src/ai_operator/
    agents/               ← Domain agents (email, linkedin, reporting, etc.)
    skills/               ← Reusable skill modules (one function, one job)
      email/              ← Email summarization, drafting
      linkedin/           ← Post drafting, publishing
      reporting/          ← Daily summaries, weekly briefings, status reports
      summarization/      ← Generic text summarization
      gold/               ← Legacy operational skills (watchers, orchestrator)
      silver/             ← Legacy workflow skills (approval, MCP execution)
      platinum/           ← Legacy cloud/sync skills
    integrations/         ← External service adapters (Gmail, LinkedIn, etc.)
    approvals/            ← Approval pipeline logic
    workflows/            ← Workflow lifecycle (future: step-level tracking)
    core/                 ← Shared utilities (audit logger, API helpers)
```

---

## Layer Definitions

### Agents (`agents/`)

Agents accept natural language commands and coordinate one or more skills to fulfill them. Each agent owns a domain.

**Available agents:**
- `EmailAgent` — email summarization, drafting, send-with-approval
- `LinkedInAgent` — post drafting, publish-with-approval
- `ReportingAgent` — daily summaries, weekly CEO briefings
- `IntegrationAgent` — integration health checks
- `ApprovalAgent` — approval queue management

**Adding a new agent:**
1. Subclass `BaseAgent` in `agents/base.py`
2. Implement `can_handle(command)` and `run(command)`
3. Register in `agents/registry.py`

### Skills (`skills/`)

Skills are reusable, single-purpose modules. Each skill does exactly one thing well. They are called by agents (never directly by the API).

**Naming convention:** `{category}/{verb}_{noun}_skill.py`

**Examples:**
- `email/summarize_skill.py` → `summarize_inbox()`
- `email/draft_skill.py` → `draft_email(command)`
- `linkedin/draft_skill.py` → `draft_linkedin_post(command)`
- `reporting/daily_summary_skill.py` → `generate_daily_summary()`
- `reporting/integration_status_skill.py` → `get_integration_status()`

### Integrations (`integrations/`)

Integration adapters wrap external services. Each adapter:
- Loads credentials from `.secrets/` (never hardcoded)
- Handles auth, retry, and error normalization
- Returns plain Python types (no framework coupling)

### Approvals (`approvals/`)

The human-in-the-loop approval pipeline. Files flow through directories:
```
Pending_Approval/ → Approved/ or Rejected/
```
The `ApprovalMonitor` surfaces the queue state. The API `/approvals` endpoints move files.

### API (`api/`)

FastAPI application providing the HTTP layer for the web dashboard. The API:
- Does NOT execute agents directly (v1 queues only)
- Reads filesystem state (logs, approvals) for display
- Returns consistent `{"success": true, "data": ...}` envelopes

---

## Command Flow

```
Dashboard → POST /api/v1/commands
  → command_service.submit(text)         # stored in-memory
  → router.route(text)                   # dispatched to matching agent
  → Agent.can_handle() + Agent.run()     # agent coordinates skills
  → skill function called                # one skill does the actual work
  → AgentResult returned                 # result stored back on Command
  → GET /api/v1/commands/{id}            # dashboard polls for result
```

---

## Adding Future Capabilities

### New agent
```python
# agents/twitter_agent.py
class TwitterAgent(BaseAgent):
    name = "twitter_agent"
    keywords = ["tweet", "twitter", "post on twitter"]

    def can_handle(self, command): ...
    def run(self, command): ...

# agents/registry.py
router.register(TwitterAgent())
```

### New skill
```python
# skills/email/reply_skill.py
def reply_to_email(email_id: str, body: str) -> str:
    """Reply to an email by ID."""
    ...
```

### New integration adapter
```python
# integrations/twitter.py
class TwitterAdapter(BaseIntegration):
    integration_id = "twitter"

    def is_configured(self) -> bool:
        return (self._find_secrets_dir() / "twitter_credentials.json").exists()
```
