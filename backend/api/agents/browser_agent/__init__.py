"""
Phase 3.1 — Browser Agent for Xyron.

Playwright-backed autonomous browser agent that can research, read, fill
forms, download files, and guard against unsafe purchases. All destructive
actions (form submission, payment, booking) require explicit human approval
before execution.

Public surface:
    from api.agents.browser_agent.browser_agent import run
"""
from api.agents.browser_agent.browser_agent import run

__all__ = ["run"]
