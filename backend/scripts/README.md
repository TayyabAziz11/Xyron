# Scripts Directory

This directory contains **backwards-compatible entrypoint wrappers** for all Personal AI Employee skills.

## Purpose

Each script in this directory is a thin wrapper that:
- Adds `src/` to Python path
- Imports the actual implementation from `src/personal_ai_employee/`
- Calls the skill's `main()` function

## Usage

All scripts are designed to run from the **repository root**:

```bash
# From repo root
python3 scripts/gmail_watcher_skill.py --mock --once
python3 scripts/brain_ralph_loop_orchestrator_skill.py --dry-run
python3 scripts/odoo_watcher_skill.py --mode mock --once
```

## Organization

**Silver Tier Skills (7 scripts):**
- `watcher_skill.py` → Bronze tier watcher
- `gmail_watcher_skill.py` → Gmail perception layer
- `brain_create_plan_skill.py` → Plan creation
- `brain_request_approval_skill.py` → Approval workflow
- `brain_monitor_approvals_skill.py` → Approval monitoring
- `brain_execute_with_mcp_skill.py` → MCP execution (Gmail)
- `brain_email_query_with_mcp_skill.py` → Email queries

**Gold Tier Skills (13 scripts):**
- `whatsapp_watcher_skill.py` → WhatsApp perception
- `linkedin_watcher_skill.py` → LinkedIn perception
- `twitter_watcher_skill.py` → Twitter perception
- `odoo_watcher_skill.py` → Odoo perception
- `brain_execute_social_with_mcp_skill.py` → Social MCP execution
- `brain_execute_odoo_with_mcp_skill.py` → Odoo MCP execution
- `brain_odoo_query_with_mcp_skill.py` → Odoo queries
- `brain_generate_daily_summary_skill.py` → Daily summaries
- `brain_generate_weekly_ceo_briefing_skill.py` → Weekly CEO briefing
- `brain_generate_accounting_audit_skill.py` → Accounting audit
- `brain_ralph_loop_orchestrator_skill.py` → Ralph loop orchestrator
- `brain_mcp_registry_refresh_skill.py` → MCP registry refresh
- `brain_handle_mcp_failure_skill.py` → MCP failure handling

**Infrastructure (1 script):**
- `scheduler_runner.py` → Scheduled task runner

## Implementation Details

Each wrapper follows this pattern:

```python
#!/usr/bin/env python3
"""Backwards compatibility wrapper for <skill_name>.py"""
import sys
from pathlib import Path
repo_root = Path(__file__).parent.parent  # Go up from scripts/ to repo root
sys.path.insert(0, str(repo_root / 'src'))
from personal_ai_employee.skills.<tier>.<skill_name> import main
if __name__ == '__main__':
    main()
```

## Scheduled Tasks

Scheduled tasks in `Scheduled/*.xml` reference these scripts:

```xml
<Command>python3</Command>
<Script>scripts/scheduler_runner.py</Script>
```

## Development

For development, install the package in editable mode:

```bash
pip install -e .
```

This allows direct imports without path manipulation:
```python
from personal_ai_employee.skills.gold.brain_ralph_loop_orchestrator_skill import main
```

---

**Note:** These wrappers maintain backwards compatibility with existing documentation, scheduled tasks, and CLI workflows.
