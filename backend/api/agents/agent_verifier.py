"""
Post-step verification for the Phase 3 agent runtime.

After a specialist agent executes a step, the verifier applies lightweight
heuristic checks to decide whether the result is trustworthy enough to advance
to the next step.  No LLM calls here — keep this synchronous and fast.

Return contract:
  True  → step is verified; proceed
  False → step verification failed; executor will trigger recovery
"""
from __future__ import annotations

import logging

from api.agents.agent_types import AgentStep, AgentTask, RiskLevel, StepResult

logger = logging.getLogger("api.agents.verifier")


# ── Tool-specific heuristics ───────────────────────────────────────────────────

# Maps tool names (lowercase) to a callable that examines the StepResult and
# returns True/False.  Each check should be cheap and side-effect-free.
_TOOL_CHECKS: dict[str, object] = {}  # populated below via decorator


def _tool_check(tool_name: str):
    """Decorator to register a per-tool verifier function."""

    def decorator(fn):
        _TOOL_CHECKS[tool_name.lower()] = fn
        return fn

    return decorator


@_tool_check("open_application")
def _check_open_app(result: StepResult, step: AgentStep) -> bool:
    # Expect output to mention the app name or "opened"
    output_lower = result.output.lower()
    return "error" not in output_lower and "failed" not in output_lower


@_tool_check("take_screenshot")
def _check_screenshot(result: StepResult, step: AgentStep) -> bool:
    # Expect a file path or "screenshot" in output
    return bool(result.output.strip()) and "error" not in result.output.lower()


@_tool_check("write_file")
def _check_write_file(result: StepResult, step: AgentStep) -> bool:
    return result.success and "error" not in result.output.lower()


@_tool_check("read_file")
def _check_read_file(result: StepResult, step: AgentStep) -> bool:
    # A read that returns empty output for a non-empty path is suspicious
    return result.success and len(result.output.strip()) > 0


@_tool_check("run_command")
def _check_run_command(result: StepResult, step: AgentStep) -> bool:
    output_lower = result.output.lower()
    # Treat non-zero exit mentions as failure indicators
    if "exit code" in output_lower and "exit code 0" not in output_lower:
        return False
    return result.success


# ── Core verifier ──────────────────────────────────────────────────────────────


async def verify_step(
    task: AgentTask,
    step: AgentStep,
    result: StepResult,
) -> bool:
    """
    Verify whether a step result is acceptable.

    Decision order:
    1. Explicit failure → False (no point verifying further)
    2. Approval required → True (approval gates are handled by the executor)
    3. High-risk step → strict: output must be non-empty
    4. Tool-specific heuristic (if registered) → result of that check
    5. Default → True (trust the specialist agent's success flag)

    Never raises; logs warnings for failed checks.
    """
    task_id = task.task_id
    step_idx = step.index

    # 1. Hard failure
    if not result.success:
        logger.debug(
            "[VERIFIER] task=%s step=%d — result.success=False → NOT verified",
            task_id,
            step_idx,
        )
        return False

    # 2. Needs approval — let the runtime handle it; consider the step verified
    if result.needs_approval:
        logger.debug(
            "[VERIFIER] task=%s step=%d — needs_approval → verified (pending gate)",
            task_id,
            step_idx,
        )
        return True

    # 3. High-risk steps must produce non-empty output
    if step.risk == RiskLevel.HIGH:
        if not result.output.strip():
            logger.warning(
                "[VERIFIER] task=%s step=%d — HIGH risk step returned empty output → NOT verified",
                task_id,
                step_idx,
            )
            return False

    # 4. Tool-specific check
    if step.tool:
        checker = _TOOL_CHECKS.get(step.tool.lower())
        if checker is not None:
            try:
                ok: bool = checker(result, step)  # type: ignore[call-arg]
                if not ok:
                    logger.warning(
                        "[VERIFIER] task=%s step=%d tool=%r — tool-specific check failed",
                        task_id,
                        step_idx,
                        step.tool,
                    )
                return ok
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[VERIFIER] task=%s step=%d tool=%r — check raised %s; defaulting to True",
                    task_id,
                    step_idx,
                    step.tool,
                    exc,
                )
                return True

    # 5. Default: trust the agent
    logger.debug(
        "[VERIFIER] task=%s step=%d — default pass",
        task_id,
        step_idx,
    )
    return True
