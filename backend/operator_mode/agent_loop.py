"""
Phase 10 — Observe-Think-Act-Verify agent loop.

For each operator goal:
  1. OBSERVE — capture current screen state
  2. THINK   — select the right skill and build the action plan
  3. ACT     — execute each action step
  4. VERIFY  — check the goal was achieved
  Retry on failure (max 3 times).
"""
from __future__ import annotations

import logging
from typing import Any

from operator_mode.operator_state import GoalState, GoalStatus
from operator_mode.operator_types import OperatorResult

logger = logging.getLogger(__name__)

# Maps tool_name → skill class path
# Keys are the ACTUAL tool names that IntentRouter/Orchestrator produce.
# This is what voice_ws sees after routing — must match exactly.
_SKILL_MAP: dict[str, str] = {
    # ── Explorer / filesystem ──────────────────────────────────────────────
    "open_drive":      "operator_mode.skills.explorer_skill.ExplorerSkill",
    "smart_open":      "operator_mode.skills.explorer_skill.ExplorerSkill",
    "open_directory":  "operator_mode.skills.explorer_skill.ExplorerSkill",
    "open_folder":     "operator_mode.skills.explorer_skill.ExplorerSkill",

    # ── YouTube / media ────────────────────────────────────────────────────
    "search_youtube":  "operator_mode.skills.youtube_skill.YouTubeSkill",

    # ── App launch ─────────────────────────────────────────────────────────
    # "launch_application" and "open_application" are generic — only intercept
    # for YouTube specifically; handled in ExplorerSkill / YouTubeSkill.
    # Do NOT intercept launch_application globally — would break every app open.

    # ── Explicit operator aliases (future voice commands can use these) ────
    "operator_youtube":  "operator_mode.skills.youtube_skill.YouTubeSkill",
    "operator_explorer": "operator_mode.skills.explorer_skill.ExplorerSkill",
    "operator_chrome":   "operator_mode.skills.chrome_skill.ChromeSkill",
    "operator_vscode":   "operator_mode.skills.vscode_skill.VSCodeSkill",
}

# Hard-blocked tools — operator NEVER executes these regardless of flag
OPERATOR_BLOCKLIST: frozenset[str] = frozenset({
    "delete_file",
    "format_drive",
    "shutdown_system",
    "hibernate_system",
    "drop_table",
    "run_admin",
    "lock_system",       # requires approval
    "sleep_system",
})


def _load_skill(class_path: str):
    module_path, class_name = class_path.rsplit(".", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)()


class AgentLoop:

    def can_handle(self, tool_name: str) -> bool:
        if tool_name in OPERATOR_BLOCKLIST:
            return False
        return tool_name in _SKILL_MAP

    async def run(
        self,
        tool_name: str,
        params: dict[str, Any],
        goal: str = "",
    ) -> OperatorResult:
        state = GoalState(
            goal=goal or tool_name,
            tool_name=tool_name,
            params=params,
        )
        state.mark_running()
        tid = state.trace_id

        logger.info("[TRACE %s] [AGENT_OBSERVE] tool=%s params=%s",
                    tid, tool_name, {k: v for k, v in params.items() if k != "openai_key"})

        # Safety: blocklist check (belt-and-suspenders — engine also checks)
        if tool_name in OPERATOR_BLOCKLIST:
            logger.warning("[TRACE %s] [OPERATOR_ERROR] BLOCKLISTED tool=%s", tid, tool_name)
            state.mark_failed("blocked")
            return OperatorResult(
                success=False,
                response="That action is not permitted in operator mode.",
                trace_id=tid,
            )

        skill_path = _SKILL_MAP.get(tool_name)
        if not skill_path:
            state.mark_failed("no_skill")
            return OperatorResult(
                success=False,
                response="",
                trace_id=tid,
                error="no_skill",
            )

        while True:
            logger.info("[TRACE %s] [AGENT_THINK] skill=%s attempt=%d",
                        tid, skill_path.split(".")[-1], state.retries + 1)
            try:
                skill = _load_skill(skill_path)
                skill_name = type(skill).__name__
                logger.info("[TRACE %s] [SKILL_SELECTED] skill=%s tool=%s",
                            tid, skill_name, tool_name)
                actions = await skill.build_actions(params, tid)

                logger.info("[TRACE %s] [AGENT_ACT] steps=%d", tid, len(actions))
                from operator_mode.operator_executor import operator_executor
                exec_result = await operator_executor.run(actions, tid, state.goal)

                logger.info("[TRACE %s] [AGENT_VERIFY] success=%s", tid, exec_result.success)
                if exec_result.success:
                    response = await skill.get_success_response(params)
                    state.mark_success(response)
                    return OperatorResult(
                        success=True,
                        response=response,
                        trace_id=tid,
                        steps_executed=exec_result.steps_executed,
                        steps_failed=0,
                    )

                if state.can_retry():
                    state.increment_retry()
                    logger.info("[TRACE %s] [OPERATOR_RETRY] attempt=%d", tid, state.retries)
                    continue
                else:
                    state.mark_failed("max_retries_exceeded")
                    response = await skill.get_failure_response(params)
                    return OperatorResult(
                        success=False,
                        response=response,
                        trace_id=tid,
                        steps_executed=exec_result.steps_executed,
                        steps_failed=exec_result.steps_failed,
                    )

            except Exception as exc:
                logger.warning("[TRACE %s] [OPERATOR_ERROR] error=%s", tid, exc)
                if state.can_retry():
                    state.increment_retry()
                    logger.info("[TRACE %s] [OPERATOR_RETRY] attempt=%d", tid, state.retries)
                    continue
                state.mark_failed(str(exc))
                return OperatorResult(
                    success=False,
                    response="",
                    trace_id=tid,
                    error=str(exc),
                )


agent_loop = AgentLoop()
