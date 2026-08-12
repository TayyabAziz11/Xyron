"""Abstract base class for all operator skills."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from operator_mode.operator_types import OperatorAction


class BaseSkill(ABC):
    """
    A skill produces a sequence of OperatorActions to accomplish a goal.
    Skills must NOT call voice/TTS/STT/wake systems.
    Skills MUST use existing tools from the registry for low-level actions.
    """

    @abstractmethod
    async def build_actions(
        self, params: dict[str, Any], trace_id: str
    ) -> Sequence[OperatorAction]:
        """Return the ordered list of actions to execute."""

    async def get_success_response(self, params: dict[str, Any]) -> str:
        return "Done."

    async def get_failure_response(self, params: dict[str, Any]) -> str:
        return "I wasn't able to complete that."
