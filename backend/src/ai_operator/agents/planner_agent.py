"""
Planner Agent — breaks multi-step goals into ordered tasks.

Handles commands like:
  "plan how to finish Xyron Phase 7"
  "break down my goal into steps"
  "what are the tasks for launching the app?"
  "create a plan for building the API"
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_PLAN_KW = [
    "plan", "break down", "break it down", "steps for",
    "tasks for", "how do i", "create a plan", "make a plan",
    "what are the steps", "help me plan", "planning",
    "schedule for", "roadmap for",
]

# Common multi-step connectors used when expanding via LLM
_STEP_RE = re.compile(r'^\s*(?:\d+[\.\)]\s*|-\s*|\*\s*)', re.MULTILINE)


class PlannerAgent(BaseAgent):
    """Breaks goals into ordered, actionable tasks."""

    name = "planner_agent"
    keywords = _PLAN_KW

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        # Try to extract a goal description from the command
        goal_text = self._extract_goal(command)

        # Try LLM-based decomposition first
        steps = self._llm_decompose(goal_text or command)
        if not steps:
            steps = self._heuristic_decompose(goal_text or command)

        if not steps:
            return self._result(
                success=False,
                output="I couldn't break that down. Try phrasing it as a clear goal.",
                command=command,
            )

        # Persist as sub-goals under an active goal if one exists
        self._maybe_persist(goal_text or command, steps)

        numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        spoken   = f"Here's your plan: {'; '.join(steps[:4])}."
        if len(steps) > 4:
            spoken += f" And {len(steps) - 4} more steps."

        return self._result(
            success=True,
            output=spoken,
            command=command,
            metadata={"steps": steps, "plan_text": numbered, "goal": goal_text},
        )

    # ── Decomposition strategies ──────────────────────────────────────────────

    @staticmethod
    def _extract_goal(command: str) -> str:
        """Strip planning verbs to get the core goal description."""
        lower = command.lower()
        for prefix in ("plan how to ", "plan ", "break down ", "steps for ",
                       "tasks for ", "help me plan ", "create a plan for ",
                       "make a plan for ", "what are the steps for ",
                       "schedule for ", "roadmap for "):
            if lower.startswith(prefix):
                return command[len(prefix):].strip()
        return command.strip()

    @staticmethod
    def _llm_decompose(goal: str) -> list[str]:
        """Ask gpt-4o-mini to produce numbered steps. Returns [] on failure."""
        try:
            from api.services.openai_client import openai_client
            if not openai_client.available:
                return []
            messages = [
                {"role": "system", "content": (
                    "You are a practical task planner. "
                    "Given a goal, return 3-6 concrete, ordered action steps. "
                    "Format: one step per line, numbered. No extra text."
                )},
                {"role": "user", "content": f"Goal: {goal}"},
            ]
            raw = openai_client.generate(messages, model="gpt-4o-mini", max_tokens=200, temperature=0.3)
            if not raw:
                return []
            lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
            # Strip numbering/bullets
            steps = [re.sub(r'^\d+[\.\)]\s*|-\s*|\*\s*', '', ln).strip() for ln in lines]
            return [s for s in steps if len(s) > 5][:6]
        except Exception as exc:
            logger.debug("[PlannerAgent] LLM decompose error: %s", exc)
            return []

    @staticmethod
    def _heuristic_decompose(goal: str) -> list[str]:
        """Minimal fallback — split on conjunctions or return 3 generic steps."""
        parts = re.split(r'\s+(?:then|and then|after that|next)\s+', goal, flags=re.IGNORECASE)
        if len(parts) >= 2:
            return [p.strip().capitalize() for p in parts if p.strip()]
        return [
            f"Define the requirements for: {goal}",
            f"Implement: {goal}",
            f"Test and verify: {goal}",
        ]

    @staticmethod
    def _maybe_persist(goal_desc: str, steps: list[str]) -> None:
        """Store steps as sub-goals on the current active goal if one exists."""
        try:
            from cognition.goals import goal_tracker
            active = goal_tracker.prioritize()
            if active and active.description.lower() in goal_desc.lower():
                # Update sub_goals list on the active goal
                import json, sqlite3
                sub = [s[:120] for s in steps]
                with goal_tracker._conn() as conn:
                    conn.execute(
                        "UPDATE goals SET sub_goals=? WHERE id=?",
                        (json.dumps(sub), active.id),
                    )
        except Exception as exc:
            logger.debug("[PlannerAgent] persist sub-goals error: %s", exc)
