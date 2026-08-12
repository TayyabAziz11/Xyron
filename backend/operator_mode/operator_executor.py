"""
Executes a sequence of OperatorActions for a skill step.
"""
from __future__ import annotations

import logging
from typing import Sequence

from operator_mode.operator_types import OperatorAction, OperatorResult
from operator_mode.operator_actions import execute_action

logger = logging.getLogger(__name__)


class OperatorExecutor:

    async def run(
        self,
        actions: Sequence[OperatorAction],
        trace_id: str = "?",
        goal: str = "",
    ) -> OperatorResult:
        logger.info("[TRACE %s] [OPERATOR_START] goal=%r steps=%d",
                    trace_id, goal[:60], len(actions))
        executed = 0
        failed   = 0
        for action in actions:
            ok = await execute_action(action, trace_id)
            executed += 1
            if not ok:
                failed += 1
                logger.warning("[TRACE %s] [OPERATOR_ERROR] step=%d desc=%r FAILED",
                               trace_id, executed, action.description[:60])
                # Non-fatal: continue to next step
        result_text = "Done." if not failed else f"Completed with {failed} step(s) failing."
        logger.info("[TRACE %s] [OPERATOR_END] steps=%d failed=%d",
                    trace_id, executed, failed)
        return OperatorResult(
            success=(failed == 0),
            response=result_text,
            trace_id=trace_id,
            steps_executed=executed,
            steps_failed=failed,
        )


operator_executor = OperatorExecutor()
