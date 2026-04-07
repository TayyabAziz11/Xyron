"""
Reporting Agent — generates summaries and briefings.

Capabilities:
  - Daily activity summary
  - Weekly CEO briefing
  - Activity report for a date range

Delegates to: DailySummarySkill, WeeklyBriefingSkill
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_REPORTING_KEYWORDS = [
    "summary", "summarize", "report", "briefing", "weekly", "daily",
    "what happened", "activity", "digest", "recap",
]


class ReportingAgent(BaseAgent):
    """Agent that generates summaries and management reports."""

    name = "reporting_agent"
    keywords = _REPORTING_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        lower = command.lower()

        if any(w in lower for w in ("weekly", "week", "ceo")):
            return self._weekly_briefing(command)
        else:
            return self._daily_summary(command)

    def _daily_summary(self, command: str) -> AgentResult:
        """Generate a daily activity summary."""
        try:
            from ..skills.reporting.daily_summary_skill import generate_daily_summary
            output = generate_daily_summary()
            return self._result(True, output, command, metadata={"report_type": "daily"})
        except Exception as exc:
            logger.exception("ReportingAgent daily_summary failed")
            return self._result(False, f"Summary generation failed: {exc}", command, error=str(exc))

    def _weekly_briefing(self, command: str) -> AgentResult:
        """Generate a weekly CEO briefing."""
        try:
            from ..skills.reporting.weekly_briefing_skill import generate_weekly_briefing
            output = generate_weekly_briefing()
            return self._result(True, output, command, metadata={"report_type": "weekly"})
        except Exception as exc:
            logger.exception("ReportingAgent weekly_briefing failed")
            return self._result(False, f"Briefing generation failed: {exc}", command, error=str(exc))
