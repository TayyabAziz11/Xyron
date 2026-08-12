"""
Phase 1 — Operator Engine.

Main entry point for the operator layer.
Checks OPERATOR_MODE from pydantic settings (backend/.env) before doing anything.
When disabled, the caller falls through to existing _run_tool() behavior.

Usage (inside voice_ws._run_tool):
    from operator_mode.operator_engine import operator_engine as _op_eng
    if _op_eng.can_handle(tool_name):
        result = await _op_eng.execute(tool_name, tool_params, goal=goal)
        if result:
            return result          # operator handled it
        # else: fall through to direct tool
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Module-level state exposed to debug endpoint
_last_fallback_reason: str = ""
_last_operator_trace:  str = ""
_last_intercept_tool:  str = ""
_last_skill_selected:  str = ""


def _operator_mode_enabled() -> bool:
    """
    Check OPERATOR_MODE using pydantic settings (reads backend/.env).
    Falls back to os.getenv for environments where settings is unavailable.
    """
    try:
        from api.config import settings
        return bool(settings.operator_mode)
    except Exception:
        pass
    return os.getenv("OPERATOR_MODE", "").lower() in ("1", "true", "yes")


def get_debug_info() -> dict:
    from operator_mode.agent_loop import _SKILL_MAP, OPERATOR_BLOCKLIST
    return {
        "enabled":              _operator_mode_enabled(),
        "registered_skills":    list(_SKILL_MAP.keys()),
        "blocked_tools":        list(OPERATOR_BLOCKLIST),
        "last_operator_trace":  _last_operator_trace,
        "last_fallback_reason": _last_fallback_reason,
        "last_intercept_tool":  _last_intercept_tool,
        "last_skill_selected":  _last_skill_selected,
    }


class OperatorEngine:
    """
    Isolated facade for the operator layer.
    All operator work runs through here, async, behind OPERATOR_MODE flag.
    """

    def can_handle(self, tool_name: str) -> bool:
        """Return True only if OPERATOR_MODE is on AND a skill exists for this tool."""
        if not _operator_mode_enabled():
            return False
        from operator_mode.agent_loop import agent_loop, OPERATOR_BLOCKLIST
        if tool_name in OPERATOR_BLOCKLIST:
            logger.debug("[OPERATOR_CAN_HANDLE] tool=%s → BLOCKED", tool_name)
            return False
        result = agent_loop.can_handle(tool_name)
        logger.debug("[OPERATOR_CAN_HANDLE] tool=%s → %s", tool_name, result)
        return result

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        goal: str = "",
    ) -> str:
        """
        Run the operator skill for tool_name with params.
        Returns a non-empty spoken response string on success.
        Returns "" on failure so the caller falls through to direct tool.
        Never raises.
        """
        global _last_operator_trace, _last_fallback_reason, _last_intercept_tool, _last_skill_selected

        _last_intercept_tool = tool_name

        if not _operator_mode_enabled():
            _last_fallback_reason = "operator_mode_disabled"
            logger.info("[OPERATOR_FALLBACK_DIRECT] reason=operator_mode_disabled tool=%s",
                        tool_name)
            return ""

        from operator_mode.agent_loop import agent_loop, _SKILL_MAP
        skill_path = _SKILL_MAP.get(tool_name, "")
        skill_name = skill_path.split(".")[-1] if skill_path else "none"

        logger.info("[OPERATOR_START] tool=%s goal=%r skill=%s",
                    tool_name, (goal or tool_name)[:60], skill_name)
        if skill_name and skill_name != "none":
            logger.info("[OPERATOR_SKILL_MATCH] tool=%s → skill=%s", tool_name, skill_name)
            _last_skill_selected = skill_name

        tid = "?"
        try:
            logger.info("[OPERATOR_EXECUTE_START] tool=%s skill=%s", tool_name, skill_name)
            result = await agent_loop.run(tool_name, params, goal or tool_name)
            tid = result.trace_id
            _last_operator_trace = tid

            logger.info("[OPERATOR_EXECUTE_END] tool=%s trace=%s success=%s",
                        tool_name, tid, result.success)

            if result.success:
                logger.info("[TRACE %s] [OPERATOR_SUCCESS] tool=%s skill=%s response=%r",
                            tid, tool_name, skill_name, result.response[:60])
                return result.response
            else:
                _last_fallback_reason = f"skill_failed:{result.error or 'unknown'}"
                logger.warning("[TRACE %s] [OPERATOR_FAIL] tool=%s reason=%s",
                               tid, tool_name, _last_fallback_reason)
                logger.warning("[TRACE %s] [OPERATOR_FALLBACK_DIRECT] reason=%s tool=%s",
                               tid, _last_fallback_reason, tool_name)
                return ""   # caller falls through to direct tool

        except Exception as exc:
            _last_fallback_reason = f"exception:{type(exc).__name__}:{exc}"
            logger.warning("[OPERATOR_FALLBACK_DIRECT] reason=exception tool=%s error=%s: %s",
                           tool_name, type(exc).__name__, exc)
            return ""


operator_engine = OperatorEngine()
