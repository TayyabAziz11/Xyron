# Personal AI Employee - Gold Tier Architecture

**Version**: 1.0
**Date**: 2026-02-16
**Status**: Production-Ready (Mock Mode)
**Platform**: Windows 11 WSL2 (Ubuntu 22.04), Python 3.10+

---

## Executive Summary

The Personal AI Employee is an autonomous, multi-channel AI system that monitors communications (Gmail, WhatsApp, LinkedIn, Twitter), integrates with Odoo accounting, and executes approved actions while maintaining strict human-in-the-loop (HITL) controls. The system operates locally, requires no cloud dependencies, and leaves comprehensive audit trails.

**Key Characteristics**:
- **Constitutional Architecture**: Perception → Plan → Approval → Action → Logging (immutable)
- **Human-in-the-Loop**: File-based approval gates prevent unauthorized execution
- **Bounded Autonomy**: Ralph loop orchestrator with strict safety controls
- **Multi-Domain**: Personal (Gmail), Social (WhatsApp/LinkedIn/Twitter), Business (Odoo accounting)
- **Audit-First**: Append-only logs (system_log.md + JSON) for full traceability

---

## System Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        CONSTITUTIONAL PIPELINE                              │
│                                                                            │
│  Perception → Plan → Approval → Action → Logging                          │
└────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────┐       ┌─────────────────────┐       ┌───────────────┐
│   PERCEPTION LAYER  │       │   PLANNING LAYER    │       │ APPROVAL LAYER│
│   (Read-Only)       │       │   (Create Plans)    │       │  (Human Gate) │
├─────────────────────┤       ├─────────────────────┤       ├───────────────┤
│ • Gmail Watcher     │──────▶│ brain_create_plan   │──────▶│ File-Based    │
│ • WhatsApp Watcher  │       │   (Silver Skill)    │       │ Approval Queue│
│ • LinkedIn Watcher  │       │                     │       │               │
│ • Twitter Watcher   │       │ • Parses intake     │       │ User moves:   │
│ • Odoo Watcher      │       │ • Risk assessment   │       │ Pending_      │
│                     │       │ • Action structured │       │ Approval/     │
│ Creates:            │       │   in YAML           │       │   ↓           │
│ • Intake wrappers   │       │                     │       │ Approved/     │
│   (Social/Inbox/,   │       │ Output:             │       │   OR          │
│    Needs_Action/)   │       │ • Plans/*.md        │       │ Rejected/     │
│ • PII redacted      │       │ • Pending_Approval/ │       │               │
│ • Checkpointing     │       │   (approval request)│       │               │
└─────────────────────┘       └─────────────────────┘       └───────────────┘
         │                             │                             │
         │                             │                             │
         ▼                             ▼                             ▼
┌─────────────────────┐       ┌─────────────────────┐       ┌───────────────┐
│  ORCHESTRATION      │       │   EXECUTION LAYER   │       │ LOGGING LAYER │
│  (Ralph Loop)       │       │   (Dry-Run Default) │       │ (Append-Only) │
├─────────────────────┤       ├─────────────────────┤       ├───────────────┤
│ brain_ralph_loop_   │       │ • Social Executor   │       │ system_log.md │
│ orchestrator        │       │   (LinkedIn/Twitter │       │ (Human-        │
│                     │       │    /WhatsApp)       │       │  Readable)     │
│ • Scans vault state │       │ • Odoo Executor     │       │               │
│ • Decides actions   │       │   (Create invoice,  │       │ Logs/         │
│ • Creates plans     │       │    Post invoice,    │       │ ├─ mcp_       │
│ • Bounded:          │       │    Register payment)│       │ │  actions.log│
│   - Max iterations  │       │ • Gmail Executor    │       │ │  (JSON)     │
│   - Max plans/iter  │       │   (Silver Tier)     │       │ ├─ whatsapp_  │
│   - Timeout 5 min   │       │                     │       │ │  watcher.log│
│   - Halts if        │       │ Requirements:       │       │ ├─ linkedin_  │
│     approval pending│       │ • Approved plan     │       │ │  watcher.log│
│                     │       │ • --execute flag    │       │ ├─ twitter_   │
│ NEVER executes      │       │   (dry-run default) │       │ │  watcher.log│
│ actions directly    │       │                     │       │ ├─ odoo_      │
│ (plan-first always) │       │ Logs:               │       │ │  watcher.log│
│                     │       │ • mcp_actions.log   │       │ ├─ ralph_     │
└─────────────────────┘       │   (JSON)            │       │ │  loop.log   │
                              └─────────────────────┘       │ └─ mcp_tools_ │
                                        │                    │    snapshot_  │
                                        │                    │    <server>.  │
                                        ▼                    │    json       │
                              ┌─────────────────────┐       │               │
                              │   MCP INTEGRATION   │       │ • No secrets  │
                              │   (4 Servers)       │       │ • PII redacted│
                              ├─────────────────────┤       │ • Timestamped │
                              │ • mcp-whatsapp      │       └───────────────┘
                              │ • mcp-linkedin      │
                              │ • mcp-twitter       │
                              │ • mcp-odoo          │
                              │                     │
                              │ Graceful Degradation│
                              │ (1 down → 3 continue│
                              └─────────────────────┘
```

---

## Directory Structure (Vault + Code)

### Vault Structure (Data + Files)

```
/mnt/e/.../Personal AI Employee/
├── Social/                          # Social channel data
│   ├── Inbox/                       # Intake wrappers (WhatsApp, LinkedIn, Twitter)
│   │   ├── inbox__whatsapp__YYYYMMDD-HHMM__<sender_id>.md
│   │   ├── inbox__linkedin__YYYYMMDD-HHMM__<sender_id>.md
│   │   └── inbox__twitter__YYYYMMDD-HHMM__<handle>.md
│   ├── Summaries/                   # Daily/weekly social summaries
│   └── Analytics/                   # Social performance metrics
├── Business/                        # Business/accounting data
│   ├── Goals/                       # Strategic objectives
│   ├── Briefings/                   # CEO weekly briefings
│   │   └── CEO_Briefing__YYYY-WW.md
│   ├── Accounting/                  # Odoo accounting data
│   │   ├── Intake/                  # Invoice/payment wrappers
│   │   └── Reports/                 # Accounting audits, revenue summaries
│   ├── Clients/                     # Customer data
│   └── Invoices/                    # Invoice records
├── Inbox/                           # Gmail intake wrappers (Silver tier)
├── Needs_Action/                    # Tasks requiring attention (all channels)
│   ├── inbox__*                     # Intake wrappers
│   └── remediation__*               # Failure remediation tasks
├── Plans/                           # Structured action plans
│   └── plan__<action>__YYYYMMDD.md
├── Pending_Approval/                # Approval requests (HITL gate)
│   └── approval__YYYYMMDD-HHMM.md
├── Approved/                        # User-approved plans
├── Rejected/                        # User-rejected plans
├── Done/                            # Completed tasks archive
├── Logs/                            # JSON logs + MCP tool snapshots
│   ├── mcp_actions.log              # All MCP tool invocations (JSON)
│   ├── whatsapp_watcher.log
│   ├── linkedin_watcher.log
│   ├── twitter_watcher.log
│   ├── odoo_watcher.log
│   ├── ralph_loop.log
│   └── mcp_tools_snapshot_<server>.json  # MCP tool discovery cache
├── MCP/                             # MCP server configurations + notes
├── Scheduled/                       # Scheduled task XML templates
├── system_log.md                    # Human-readable append-only log
└── Dashboard.md                     # Obsidian dashboard (single-pane overview)
```

### Code Structure (Implementation)

```
/mnt/e/.../Personal AI Employee/
├── scripts/                         # Backwards-compatible entrypoint wrappers
│   ├── README.md                    # Wrapper documentation
│   ├── scheduler_runner.py          # Task scheduler runner
│   ├── gmail_api_helper.py          # Gmail API auth helper
│   │
│   ├── [Silver Tier Wrappers]
│   ├── gmail_watcher_skill.py
│   ├── brain_create_plan_skill.py
│   ├── brain_request_approval_skill.py
│   ├── brain_monitor_approvals_skill.py
│   ├── brain_execute_with_mcp_skill.py
│   ├── brain_email_query_with_mcp_skill.py
│   ├── watcher_skill.py
│   │
│   └── [Gold Tier Wrappers]
│       ├── whatsapp_watcher_skill.py
│       ├── linkedin_watcher_skill.py
│       ├── twitter_watcher_skill.py
│       ├── odoo_watcher_skill.py
│       ├── brain_execute_social_with_mcp_skill.py
│       ├── brain_execute_odoo_with_mcp_skill.py
│       ├── brain_odoo_query_with_mcp_skill.py
│       ├── brain_mcp_registry_refresh_skill.py
│       ├── brain_handle_mcp_failure_skill.py
│       ├── brain_generate_daily_summary_skill.py
│       ├── brain_generate_weekly_ceo_briefing_skill.py
│       ├── brain_generate_accounting_audit_skill.py
│       └── brain_ralph_loop_orchestrator_skill.py
│
├── src/                             # Real implementations (package structure)
│   └── personal_ai_employee/
│       ├── __init__.py
│       ├── core/                    # Core utilities
│       │   ├── __init__.py
│       │   ├── mcp_helpers.py       # MCP client + PII redaction + path resolution
│       │   └── gmail_api_helper.py  # Gmail API wrapper
│       └── skills/                  # Agent skills by tier
│           ├── __init__.py
│           ├── silver/              # Silver tier skills
│           │   ├── __init__.py
│           │   ├── gmail_watcher_skill.py
│           │   ├── brain_create_plan_skill.py
│           │   ├── brain_request_approval_skill.py
│           │   ├── brain_monitor_approvals_skill.py
│           │   ├── brain_execute_with_mcp_skill.py
│           │   ├── brain_email_query_with_mcp_skill.py
│           │   └── watcher_skill.py
│           └── gold/                # Gold tier skills
│               ├── __init__.py
│               ├── whatsapp_watcher_skill.py
│               ├── linkedin_watcher_skill.py
│               ├── twitter_watcher_skill.py
│               ├── odoo_watcher_skill.py
│               ├── brain_execute_social_with_mcp_skill.py
│               ├── brain_execute_odoo_with_mcp_skill.py
│               ├── brain_odoo_query_with_mcp_skill.py
│               ├── brain_mcp_registry_refresh_skill.py
│               ├── brain_handle_mcp_failure_skill.py
│               ├── brain_generate_daily_summary_skill.py
│               ├── brain_generate_weekly_ceo_briefing_skill.py
│               ├── brain_generate_accounting_audit_skill.py
│               └── brain_ralph_loop_orchestrator_skill.py
│
├── tests/                           # Automated test suite
│   ├── test_gold_e2e_smoke.py       # Gold tier E2E smoke tests (pytest)
│   └── test_silver_unit.py          # Silver tier unit tests
│
├── templates/                       # Mock data + templates
│   ├── mock_emails.json             # Gmail mock data
│   ├── mock_whatsapp.json           # WhatsApp mock data
│   ├── mock_linkedin.json           # LinkedIn mock data
│   ├── mock_twitter.json            # Twitter mock data
│   ├── mock_odoo_invoices.json      # Odoo mock data
│   ├── ceo_briefing_template.md     # CEO briefing structure
│   └── accounting_audit_template.md # Accounting audit structure
│
├── Docs/                            # Documentation
│   ├── test_report_gold_e2e.md      # E2E test results
│   ├── gold_demo_script.md          # 5-7 min judge demo
│   ├── gold_completion_checklist.md # All requirements mapped
│   ├── architecture_gold.md         # This file
│   ├── lessons_learned_gold.md      # Post-mortem insights
│   ├── mcp_whatsapp_setup.md        # WhatsApp MCP server setup
│   ├── mcp_linkedin_setup.md        # LinkedIn MCP server setup
│   ├── mcp_twitter_setup.md         # Twitter MCP server setup
│   └── mcp_odoo_setup.md            # Odoo MCP server setup
│
├── .secrets/                        # Credentials (gitignored)
│   ├── whatsapp_token.txt
│   ├── linkedin_oauth2.json
│   ├── twitter_api_keys.json
│   └── odoo_config.json
│
├── .gitignore                       # Excludes .secrets, Logs, checkpoints
├── pyproject.toml                   # Python package config (editable install)
├── requirements.txt                 # Python dependencies
├── README.md                        # Professional overview
└── Dashboard.md                     # Obsidian dashboard
```

---

## Safety Gates + Constitutional Guarantees

### 1. Perception-Only Watchers

**Rule**: Watchers NEVER execute actions. They observe and create intake wrappers only.

**Enforcement**:
- Code review: No watcher calls MCP action tools (only query tools allowed)
- Naming convention: All watchers end with `_watcher_skill.py`
- Logging: Watchers log "perception complete" (never "action executed")

**Example**:
```python
# ✅ ALLOWED in watchers
def whatsapp_watcher():
    messages = whatsapp_api.list_messages()  # Query only
    for msg in messages:
        create_intake_wrapper(msg)  # Write file locally
        log("Perception complete")  # No execution

# ❌ FORBIDDEN in watchers
def bad_watcher():
    messages = whatsapp_api.list_messages()
    whatsapp_api.send_message("Reply")  # VIOLATION: Action in watcher
```

### 2. Plan-First Workflow

**Rule**: All external actions (social posts, emails, invoices) require a structured plan.

**Enforcement**:
- Executors MUST read from `Approved/*.md` (cannot accept inline commands)
- Plans MUST have YAML frontmatter: `task`, `objective`, `risk_level`, `status`, `actions`
- Plans MUST be approved before execution (status: `Pending_Approval` → `Approved`)

**Example Plan** (`Plans/plan__whatsapp_reply__20260216.md`):
```yaml
---
task: inbox__whatsapp__20260216-1000__+1234567890.md
objective: Reply to customer inquiry about product availability
risk_level: Low
status: Approved
created_at: 2026-02-16T10:00:00Z
approved_at: 2026-02-16T10:15:00Z
approved_by: user@example.com
---

# Actions

1. **WhatsApp Reply**
   - Tool: `whatsapp.reply_message`
   - Params:
     - `recipient`: `+1234567890`
     - `message`: "Product XYZ is in stock. Estimated delivery: 3-5 business days."
```

### 3. Human-in-the-Loop Approvals (File-Based)

**Rule**: NO external action executes without human approval.

**Enforcement**:
- Executor checks for file in `Approved/` directory
- If not found → creates remediation task in `Needs_Action/`, exits
- Approval cannot be bypassed programmatically (user must physically move file or use Obsidian UI)

**File-Based Workflow**:
```
1. brain_create_plan → Plans/plan__action__YYYYMMDD.md
2. brain_request_approval → Pending_Approval/approval__YYYYMMDD.md
3. USER ACTION (manual): mv Pending_Approval/approval__*.md Approved/
   (OR: Click "Approve" button in Obsidian dashboard)
4. brain_monitor_approvals → Updates plan status to "Approved"
5. brain_execute_* --execute → Reads Approved/, executes actions
```

**Why File-Based?**:
- Non-bypassable: AI cannot move files without shell access (which skills don't have)
- Observable: User can see `Pending_Approval/` queue in Obsidian
- Auditable: Git tracks file movements (`git log --follow approval__*.md`)

### 4. Dry-Run Default

**Rule**: All executors default to `--dry-run`. Real execution requires `--execute` flag.

**Enforcement**:
- CLI argument parsing: `dry_run = not args.execute`
- Executors log preview actions but DON'T call MCP tools unless `--execute`
- Error if `--execute` without approved plan

**Example**:
```bash
# ✅ SAFE: Preview only
python3 scripts/brain_execute_social_with_mcp_skill.py --dry-run

# ✅ SAFE: Executes ONLY with approved plan
python3 scripts/brain_execute_social_with_mcp_skill.py --execute

# ❌ FAILS: --execute without approved plan
# Output: "Error: No approved plan found. Remediation task created."
```

### 5. Bounded Autonomy (Ralph Loop)

**Rule**: Autonomous orchestration MUST have safety bounds.

**Enforcement**:
```python
class RalphLoopOrchestrator:
    def __init__(self):
        self.max_iterations = 10      # Hard cap on loops
        self.max_plans_per_iteration = 5  # Prevent plan flooding
        self.iteration_timeout = 300  # 5 minutes per iteration

    def run(self):
        for iteration in range(self.max_iterations):
            # CRITICAL: Check approval gate FIRST
            if self._approval_pending():
                logger.warning("Approval pending - halting loop")
                return  # STOP immediately

            # Decide next actions
            decisions = self._decide_actions()

            if self.dry_run:
                logger.info(f"DRY-RUN: Would create {len(decisions)} plans")
                # DO NOT create plans in dry-run
            else:
                # Create plans (NEVER execute actions)
                for decision in decisions[:self.max_plans_per_iteration]:
                    self._create_plan(decision)  # Plan-first always

    def _create_plan(self, decision):
        # Ralph creates plans, does NOT execute actions
        # Plans still require approval before execution
        pass
```

**Guarantees**:
- Max 10 iterations (prevents infinite loops)
- Max 5 plans per iteration (prevents approval queue flooding)
- 5-minute timeout per iteration
- HALTS if any approval pending (HITL gate)
- Dry-run default (requires `--execute` to create plans)
- NEVER executes actions directly (plan-first always)

### 6. Append-Only Audit Trails

**Rule**: Logs are append-only. No deletions or modifications.

**Enforcement**:
- `system_log.md`: Always append (`>>` in bash, `mode='a'` in Python)
- `Logs/*.log`: JSON lines, timestamped, immutable
- Git tracks log files (changes visible in `git diff`)

**Example Log Entry** (`Logs/mcp_actions.log`):
```json
{
  "timestamp": "2026-02-16T15:30:00Z",
  "action": "whatsapp.reply_message",
  "tool": "mcp-whatsapp",
  "params": {"recipient": "[REDACTED]", "message": "Product XYZ..."},
  "status": "success",
  "plan_id": "plan__whatsapp_reply__20260216.md",
  "executor": "brain_execute_social_with_mcp_skill.py"
}
```

**PII Redaction**: All logs redact emails (`[REDACTED]`) and phones (`[REDACTED]`) via `redact_pii()`.

---

## MCP Integration Architecture

### MCP Servers (4 Total)

| Server | Purpose | Tools (Action) | Tools (Query) | Status |
|--------|---------|---------------|---------------|--------|
| **mcp-whatsapp** | WhatsApp Business API | `send_message`, `reply_message`, `broadcast_message` | `list_messages`, `get_message` | Mock/Real |
| **mcp-linkedin** | LinkedIn API | `create_post`, `reply_comment`, `send_message`, `schedule_post` | `list_notifications`, `get_post_analytics` | Mock/Real |
| **mcp-twitter** | Twitter API v2 | `create_post` (tweet), `reply_post`, `send_dm`, `schedule_post` | `search_mentions`, `get_post_metrics` | Mock/Real |
| **mcp-odoo** | Odoo JSON-RPC | `create_customer`, `create_invoice`, `post_invoice`, `register_payment`, `create_credit_note` | `list_unpaid_invoices`, `get_invoice`, `get_customer`, `revenue_summary`, `ar_aging_summary` | Mock/Real |

### MCP Tool Discovery

**Process**:
1. `brain_mcp_registry_refresh_skill.py` queries each server's tool registry
2. Parses JSON schema for each tool (name, description, parameters, required fields)
3. Saves snapshot to `Logs/mcp_tools_snapshot_<server>.json`
4. Dashboard shows "MCP Registry Status" (last refresh time, server reachability)

**Snapshot Format** (`Logs/mcp_tools_snapshot_whatsapp.json`):
```json
{
  "server": "mcp-whatsapp",
  "refreshed_at": "2026-02-16T15:00:00Z",
  "reachable": true,
  "tools": [
    {
      "name": "whatsapp.send_message",
      "type": "action",
      "description": "Send a new WhatsApp message",
      "parameters": {
        "recipient": {"type": "string", "required": true},
        "message": {"type": "string", "required": true}
      }
    },
    {
      "name": "whatsapp.list_messages",
      "type": "query",
      "description": "List recent WhatsApp messages",
      "parameters": {
        "limit": {"type": "integer", "default": 10}
      }
    }
  ]
}
```

### Graceful Degradation

**Scenario**: Twitter MCP server is down.

**System Behavior**:
1. `twitter_watcher_skill.py` tries to connect → connection timeout
2. Logs error to `Logs/twitter_watcher.log`
3. Creates remediation task: `Needs_Action/remediation__mcp__twitter_watcher__20260216-1500.md`
4. Dashboard marks Twitter as "unreachable" (red status indicator)
5. WhatsApp/LinkedIn/Odoo watchers continue running normally
6. CEO briefing includes note: "Twitter data unavailable (last refresh: 3 hours ago)"

**No Cascading Failures**: Each MCP server is isolated. One server down → others unaffected.

---

## Tier Progression (Bronze → Silver → Gold)

### Bronze Tier (✅ Complete)

**Capabilities**:
- File-based task management (Inbox, Needs_Action, Done)
- Static dashboard (Dashboard.md)
- Manual workflows (user creates tasks, moves files)
- Basic logging (system_log.md)

**Architecture**: Human-driven, file-based workflow.

### Silver Tier (✅ Complete)

**Additions**:
- Gmail perception (watcher creates intake wrappers)
- Gmail execution (sends emails via Gmail API or MCP)
- Plan-first workflow (structured plans in Plans/)
- HITL approvals (Pending_Approval/ → Approved/)
- Checkpointing (no duplicate processing)
- Scheduled automation (Task Scheduler)
- Daily summaries (email digest)

**Architecture**: Perception → Plan → Approval → Action → Logging

### Gold Tier (✅ Complete)

**Additions**:
- Multi-channel social (WhatsApp, LinkedIn, Twitter watchers + executors)
- Odoo accounting integration (watcher + queries + actions)
- MCP registry + graceful degradation
- CEO briefing (weekly, 8 sections, multi-domain synthesis)
- Accounting audit (AR aging, unpaid invoices)
- Ralph loop (bounded autonomous orchestration)
- Cross-domain vault (Social/, Business/)

**Architecture**: Same constitutional pipeline, extended to 4 channels + accounting.

### Platinum Tier (Planned, Not Implemented)

**Future Additions**:
- Cloud deployment (AWS/Azure/GCP)
- UI layer (Next.js/React dashboard)
- Vector DB + RAG (embeddings for search)
- Multi-agent orchestration (CrewAI)
- Advanced analytics (Grafana dashboards)

---

## Security Architecture

### 1. Secrets Management

**Storage**: `.secrets/` directory (gitignored)

**Files**:
- `whatsapp_token.txt`: WhatsApp Business API token
- `linkedin_oauth2.json`: LinkedIn OAuth2 credentials (client ID, secret, refresh token)
- `twitter_api_keys.json`: Twitter API keys (consumer key/secret, access token/secret)
- `odoo_config.json`: Odoo server URL, database name, username, API key

**Access Control**:
- Skills read from `.secrets/` at runtime
- Never logged or committed to git
- File permissions: `chmod 600 .secrets/*` (owner read/write only)

### 2. PII Redaction

**Function**: `redact_pii(text: str) -> str` in `mcp_helpers.py`

**Patterns**:
- Emails: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` → `[REDACTED]`
- Phones: `\+?1?\d{9,15}` → `[REDACTED]`
- Custom: User can extend with additional regex patterns

**Applied To**:
- All log files (`Logs/*.log`)
- Intake wrapper excerpts (`excerpt:` field in YAML frontmatter)
- System log entries (`system_log.md`)
- Dashboard previews (first 100 chars only)

**Example**:
```python
text = "Contact me at john.doe@example.com or +1-234-567-8900"
redacted = redact_pii(text)
# Result: "Contact me at [REDACTED] or [REDACTED]"
```

### 3. Git Security

**.gitignore** (critical entries):
```
.secrets/
Logs/*.log
Logs/mcp_tools_snapshot_*.json
.checkpoint_*
__pycache__/
*.pyc
.env
```

**Validation**:
```bash
# Check no secrets in git history
git log --all --source --full-history -- .secrets/ | wc -l  # Should be 0

# Check no PII in committed logs
git grep -E '\+1[0-9]{10}|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}' -- Logs/  # Should be 0
```

---

## Performance Characteristics

| Operation | Target (Spec) | Actual (Mock) | Actual (Real Mode Est.) |
|-----------|---------------|---------------|-------------------------|
| Watcher intake creation | < 5 min | < 1 sec | 10-30 sec (API latency) |
| Odoo query (1000 invoices) | < 3 sec | < 0.01 sec | 1-2 sec (PostgreSQL) |
| CEO briefing generation | < 60 sec | ~0.05 sec | 5-10 sec (file I/O) |
| Ralph loop iteration | < 2 min | ~0.05 sec | 30-60 sec (vault scan + decision) |
| Plan creation | N/A | < 0.01 sec | < 0.1 sec |
| Approval processing | N/A | < 0.05 sec | < 0.5 sec (file move + log) |

**Bottlenecks (Real Mode)**:
- API rate limits (Twitter: 50 requests/15 min; LinkedIn: 100 requests/day)
- Odoo JSON-RPC (network latency ~100-200ms/query)
- Gmail API (quota: 1 billion requests/day, but 250 requests/user/sec)

**Optimization Strategies**:
- Checkpointing (skip already-processed items)
- Batch queries (Odoo: `search_read` with `limit` and `offset`)
- Caching (MCP tool snapshots refresh daily, not per-run)
- Backoff/retry (exponential backoff on 429 rate limit errors)

---

## Testing Strategy

### Mock Mode (Default)

**Purpose**: Development, CI/CD, demo without credentials.

**Implementation**:
- All watchers accept `--mode mock` flag
- Read from `templates/mock_*.json` files
- No external API calls
- Deterministic outputs (same mock data every run)

**Example**:
```bash
python3 scripts/whatsapp_watcher_skill.py --mode mock --once
# Loads templates/mock_whatsapp.json (5 messages)
# Creates 0-5 intake wrappers (depending on checkpointing)
# No real WhatsApp API call
```

### Real Mode

**Purpose**: Production use with real credentials.

**Implementation**:
- Watchers default to real mode (or `--mode real` explicit)
- Read credentials from `.secrets/`
- Call external APIs (WhatsApp Business, LinkedIn, Twitter, Odoo)
- Handle rate limits, errors, retries

**Example**:
```bash
python3 scripts/whatsapp_watcher_skill.py --once
# Reads .secrets/whatsapp_token.txt
# Calls WhatsApp Business API (GET /messages)
# Creates intake wrappers for new messages
```

### Automated Testing

**Test Suite**: `tests/test_gold_e2e_smoke.py` (pytest)

**Coverage**:
- All watchers (mock mode)
- MCP registry refresh
- Odoo queries
- CEO briefing generation
- Ralph loop (dry-run)
- Path resolution (scripts/ wrappers work)
- Security (no secrets in git, PII redaction)

**Run**:
```bash
python -m pytest tests/test_gold_e2e_smoke.py -v
```

---

## Deployment Architecture

### Local Development (Current)

**Environment**: Windows 11 WSL2 (Ubuntu 22.04)
**Scheduler**: Windows Task Scheduler
**Database**: Odoo Community Edition (PostgreSQL)
**Storage**: Local filesystem (Markdown vault)

**Scheduled Tasks** (7 total):
1. `gmail_watcher` (every 10 minutes)
2. `whatsapp_watcher` (every 10 minutes)
3. `linkedin_watcher` (every 15 minutes)
4. `twitter_watcher` (every 15 minutes)
5. `odoo_watcher` (daily at 00:00 UTC)
6. `mcp_registry_refresh` (daily at 01:00 UTC)
7. `weekly_ceo_briefing` (weekly on Sunday at 23:59 UTC)

**Setup**:
```bash
# Install dependencies
pip install -r requirements.txt

# Install package (editable mode)
pip install -e .

# Run watcher manually
python3 scripts/gmail_watcher_skill.py --mock --once

# Import scheduled task (Windows Task Scheduler)
schtasks /Create /XML "Scheduled/gmail_watcher_task.xml" /TN "PersonalAIEmployee\GmailWatcher"
```

### Production (Platinum Tier - Future)

**Cloud Deployment**:
- Containerization (Docker + Kubernetes)
- Cloud storage (S3/Azure Blob for vault)
- Managed database (RDS PostgreSQL for Odoo)
- Serverless watchers (AWS Lambda/Azure Functions)
- Web UI (Next.js/React + API gateway)

---

## Lessons Learned

See `Docs/lessons_learned_gold.md` for detailed post-mortem.

**Key Insights**:
1. **File-based approvals are brilliant**: Non-bypassable, observable, auditable
2. **Constitutional architecture scales**: Same pipeline (Perception → Plan → Approval → Action → Logging) works for 1 channel or 10
3. **Mock mode is mandatory**: Development velocity 10x faster without real API dependencies
4. **Checkpointing prevents chaos**: Without it, watchers create duplicate wrappers on every run
5. **Bounded autonomy requires discipline**: Ralph loop tempting to bypass approvals → strict enforcement needed

---

**Architecture Version**: 1.0
**Document Owner**: Claude Sonnet 4.5 (Personal AI Employee)
**Last Updated**: 2026-02-16
