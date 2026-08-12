"""
VS Code Skill — open a file or folder in VS Code.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from operator_mode.skills.base_skill import BaseSkill
from operator_mode.operator_types import OperatorAction, VerifySpec, VerifyMethod
import operator_mode.operator_actions as A

logger = logging.getLogger(__name__)


class VSCodeSkill(BaseSkill):

    async def build_actions(
        self, params: dict[str, Any], trace_id: str
    ) -> Sequence[OperatorAction]:
        path = params.get("path", "")
        logger.info("[TRACE %s] [SKILL_START] skill=vscode path=%r", trace_id, path)

        actions: list[OperatorAction] = [
            OperatorAction(
                action_type="tool",
                params={"tool_name": "launch_application", "params": {"name": "code"}},
                description="Launch VS Code",
                delay_after_ms=2000,
            ),
            OperatorAction(
                action_type="wait_for_window",
                params={"app_name": "Visual Studio Code", "timeout_s": 8.0},
                description="Wait for VS Code to open",
                delay_after_ms=500,
                verify=VerifySpec(VerifyMethod.WINDOW_EXISTS,
                                  expected="Visual Studio Code", timeout_ms=8000),
            ),
        ]

        if path:
            # Ctrl+O to open file/folder
            actions.append(A.hotkey("Open file dialog", "Ctrl", "o", wait_ms=500))
            actions.append(A.type_text(path, description=f"Type path {path}", wait_ms=300))
            actions.append(A.press("Return", description="Open path", wait_ms=800))

        return actions

    async def get_success_response(self, params: dict[str, Any]) -> str:
        path = params.get("path", "")
        return f"VS Code is open{' with ' + path if path else ''}."

    async def get_failure_response(self, params: dict[str, Any]) -> str:
        return "I couldn't open VS Code. Is it installed?"
