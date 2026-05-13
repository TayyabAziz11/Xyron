"""
Focus Agent — manages attention level and filters low-priority interruptions.

Handles commands like:
  "enter focus mode", "do not disturb", "I'm concentrating"
  "exit focus mode", "stop focusing", "ambient mode"
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_FOCUS_ON_KW = [
    "focus mode", "focused mode", "do not disturb", "dnd",
    "concentrate", "concentrating", "block distractions",
    "enter focus", "start focus", "i'm focusing", "i am focusing",
]
_FOCUS_OFF_KW = [
    "exit focus", "stop focus", "leave focus", "end focus",
    "ambient mode", "relax mode", "unfocus",
]


class FocusAgent(BaseAgent):
    """Manages AI attention level — activates focused or ambient mode."""

    name = "focus_agent"
    keywords = _FOCUS_ON_KW + _FOCUS_OFF_KW + ["focus", "distraction"]

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        lower = command.lower()

        if any(kw in lower for kw in _FOCUS_OFF_KW):
            return self._exit_focus(command)
        return self._enter_focus(command)

    def _enter_focus(self, command: str) -> AgentResult:
        try:
            from cognition.cognitive_state import cognitive_state
            from cognition.state_transitions import transition_to_processing
            cognitive_state.update(
                active_ui_mode="focus",
                mood_bias="focused" if hasattr(__import__("cognition.cognitive_state", fromlist=["MoodBias"]).MoodBias, "FOCUSED") else "alert",
            )
        except Exception:
            pass
        try:
            from cognition.cognitive_state import cognitive_state, MoodBias
            cognitive_state.update(mood_bias=MoodBias.ALERT)
        except Exception:
            pass
        logger.info("[FocusAgent] entered focus mode")
        return self._result(
            success=True,
            output="Focus mode activated. Low-priority interruptions suppressed.",
            command=command,
            metadata={"mode": "focus", "action": "enter"},
        )

    def _exit_focus(self, command: str) -> AgentResult:
        try:
            from cognition.cognitive_state import cognitive_state, MoodBias
            cognitive_state.update(active_ui_mode="default", mood_bias=MoodBias.NEUTRAL)
        except Exception:
            pass
        logger.info("[FocusAgent] exited focus mode")
        return self._result(
            success=True,
            output="Focus mode off. Back to ambient mode.",
            command=command,
            metadata={"mode": "default", "action": "exit"},
        )

    def is_focused(self) -> bool:
        """Return True if currently in focus mode — used by other agents to check priority."""
        try:
            from cognition.cognitive_state import cognitive_state
            return cognitive_state.active_ui_mode == "focus"
        except Exception:
            return False
