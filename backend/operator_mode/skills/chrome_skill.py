"""
Chrome Skill — generic Chrome automation.
Open a URL in Chrome, wait for it to load.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from operator_mode.skills.base_skill import BaseSkill
from operator_mode.operator_types import OperatorAction, VerifySpec, VerifyMethod
import operator_mode.operator_actions as A

logger = logging.getLogger(__name__)


class ChromeSkill(BaseSkill):

    async def build_actions(
        self, params: dict[str, Any], trace_id: str
    ) -> Sequence[OperatorAction]:
        url   = params.get("url", "https://google.com")
        title = params.get("expected_title", "")
        logger.info("[TRACE %s] [SKILL_START] skill=chrome url=%r", trace_id, url)

        actions: list[OperatorAction] = [
            OperatorAction(
                action_type="tool",
                params={"tool_name": "launch_application", "params": {"name": "chrome"}},
                description="Launch Chrome",
                delay_after_ms=1500,
            ),
            OperatorAction(
                action_type="navigate_url",
                params={"url": url},
                description=f"Navigate to {url}",
                delay_after_ms=2000,
                verify=VerifySpec(
                    VerifyMethod.WINDOW_TITLE,
                    expected=title or url.split("/")[2],
                    timeout_ms=4000,
                ) if (title or url) else None,
            ),
        ]
        return actions

    async def get_success_response(self, params: dict[str, Any]) -> str:
        url = params.get("url", "the page")
        return f"Opened {url} in Chrome."

    async def get_failure_response(self, params: dict[str, Any]) -> str:
        return "I couldn't open Chrome. Is it installed?"
