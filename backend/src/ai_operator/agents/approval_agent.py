"""
Approval Agent — manages the human-in-the-loop approval pipeline.

Capabilities:
  - List pending approvals
  - Summarize what needs review
  - Escalate urgent approvals

Delegates to: ApprovalMonitor (from silver skills)
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_APPROVAL_KEYWORDS = [
    "approval", "approve", "pending", "review", "reject", "waiting for",
    "needs approval", "pending actions", "approval queue",
]


class ApprovalAgent(BaseAgent):
    """Agent that surfaces and manages the approval pipeline."""

    name = "approval_agent"
    keywords = _APPROVAL_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        try:
            from ..approvals.approval_monitor import ApprovalMonitor
            monitor = ApprovalMonitor()
            summary = monitor.get_summary()
            return self._result(True, summary, command, metadata={"action": "list_approvals"})
        except Exception as exc:
            logger.exception("ApprovalAgent run failed")
            return self._result(False, f"Approval check failed: {exc}", command, error=str(exc))
