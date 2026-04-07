"""
Integration Agent — checks and reports on integration health.

Capabilities:
  - List which integrations are connected
  - Check status of a specific integration
  - Report on credential file presence

Delegates to: IntegrationStatusSkill
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_INTEGRATION_KEYWORDS = [
    "integration", "connected", "status", "credentials", "configured",
    "whatsapp status", "gmail status", "linkedin status", "odoo status",
    "instagram status", "openai status", "check integration",
]


class IntegrationAgent(BaseAgent):
    """Agent that reports on integration health and connectivity."""

    name = "integration_agent"
    keywords = _INTEGRATION_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        try:
            from ..skills.reporting.integration_status_skill import get_integration_status
            output = get_integration_status()
            return self._result(True, output, command, metadata={"action": "status_check"})
        except Exception as exc:
            logger.exception("IntegrationAgent run failed")
            return self._result(False, f"Status check failed: {exc}", command, error=str(exc))
