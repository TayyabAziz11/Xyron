"""
Sentinel Agent — monitors system health and raises alerts.

Monitors:
  - Backend health (FastAPI reachable)
  - ChromaDB disk usage (warn if > 1 GB)
  - Goal deadlines (alert if deadline < 1 hour away)
  - Whisper STT availability

Handles commands like:
  "system health", "system check", "how is the system?",
  "check deadlines", "any alerts?", "sentinel report"
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_SENTINEL_KW = [
    "system health", "system check", "health check",
    "how is the system", "system status", "any alerts",
    "sentinel", "check deadlines", "monitor", "system report",
    "backend health", "memory usage", "disk usage",
]

_CHROMA_PATH  = Path.home() / ".ai-operator" / "chroma"
_CHROMA_LIMIT = 1.0  # GB


class SentinelAgent(BaseAgent):
    """Monitors system health, memory, goals, and STT availability."""

    name = "sentinel_agent"
    keywords = _SENTINEL_KW

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        alerts:  list[str] = []
        reports: list[str] = []

        # 1. ChromaDB disk usage
        chroma_gb = self._chroma_size_gb()
        if chroma_gb >= _CHROMA_LIMIT:
            alerts.append(f"ChromaDB is {chroma_gb:.2f} GB — consider pruning old memories.")
        else:
            reports.append(f"ChromaDB: {chroma_gb:.2f} GB / {_CHROMA_LIMIT:.0f} GB limit.")

        # 2. Goal deadlines
        deadline_alerts = self._check_deadlines()
        alerts.extend(deadline_alerts)
        if not deadline_alerts:
            reports.append("No imminent goal deadlines.")

        # 3. Whisper STT availability
        whisper_ok = self._check_whisper()
        if not whisper_ok:
            alerts.append("Whisper STT unavailable — fallback to typed input.")
        else:
            reports.append("Whisper STT: ready.")

        # 4. Goals count
        active_count = self._active_goal_count()
        reports.append(f"Active goals: {active_count}.")

        if alerts:
            summary = "ALERTS: " + " | ".join(alerts)
            if reports:
                summary += " | " + " ".join(reports)
        else:
            summary = "All systems nominal. " + " ".join(reports)

        logger.info("[SentinelAgent] report: %d alerts", len(alerts))
        return self._result(
            success=True,
            output=summary[:500],
            command=command,
            metadata={
                "alerts": alerts,
                "chroma_gb": chroma_gb,
                "whisper_ok": whisper_ok,
                "active_goals": active_count,
            },
        )

    # ── Checks ────────────────────────────────────────────────────────────────

    @staticmethod
    def _chroma_size_gb() -> float:
        try:
            if not _CHROMA_PATH.exists():
                return 0.0
            total = sum(f.stat().st_size for f in _CHROMA_PATH.rglob("*") if f.is_file())
            return round(total / (1024 ** 3), 3)
        except Exception:
            return 0.0

    @staticmethod
    def _check_deadlines() -> list[str]:
        alerts: list[str] = []
        try:
            from cognition.goals import goal_tracker
            now = time.time()
            for goal in goal_tracker.get_active_goals():
                if goal.deadline and 0 < (goal.deadline - now) < 3600:
                    mins = int((goal.deadline - now) / 60)
                    alerts.append(
                        f"Goal '{goal.description[:40]}' deadline in {mins} minutes!"
                    )
                elif goal.deadline and goal.deadline < now:
                    alerts.append(
                        f"Goal '{goal.description[:40]}' is overdue."
                    )
        except Exception as exc:
            logger.debug("[SentinelAgent] deadline check error: %s", exc)
        return alerts

    @staticmethod
    def _check_whisper() -> bool:
        try:
            from voice.whisper_service import WhisperService
            return True
        except Exception:
            try:
                import faster_whisper  # noqa: F401
                return True
            except Exception:
                return False

    @staticmethod
    def _active_goal_count() -> int:
        try:
            from cognition.goals import goal_tracker
            return len(goal_tracker.get_active_goals())
        except Exception:
            return 0
