"""
Recovery strategies for the Phase 3 agent runtime.

When a step fails, attempt_recovery() is called before marking the whole
task as FAILED.  Recovery is tried in priority order:

  1. Retry with exponential backoff  (if step.retries < 2)
  2. Skip the step                   (only for LOW-risk steps)
  3. Modify step args and retry once (tool-specific tweaks)
  4. Rollback                        (if step.rollback_tool is set — undo
                                       partial effects before giving up)
  5. Propagate failure               (return False → runtime marks task FAILED)

The function is async so strategies can sleep for backoff without blocking
the event loop, but it never calls the specialist agent directly — that
responsibility stays with AgentExecutor. Rollback is the one exception:
it does call into the tool registry directly, since undoing a partial
effect (e.g. deleting a file this step half-created) is inherently a tool
action, not something the specialist agent needs to be involved in.
"""
from __future__ import annotations

import asyncio
import logging

from api.agents.agent_types import AgentStep, AgentTask, RiskLevel, StepStatus

logger = logging.getLogger("api.agents.recovery")

# Maximum automatic retry count before giving up
_MAX_RETRIES: int = 2

# Backoff schedule per retry count (seconds to wait before retry)
_BACKOFF_S: dict[int, float] = {0: 1.0, 1: 3.0}


# ── Strategy implementations ───────────────────────────────────────────────────


async def _retry_with_backoff(step: AgentStep) -> bool:
    """
    Increment retry counter and sleep before the next attempt.

    Returns True so the executor knows to re-run the step.
    """
    delay = _BACKOFF_S.get(step.retries, 5.0)
    logger.info(
        "[RECOVERY] step=%d retries=%d → retrying in %.1fs",
        step.index,
        step.retries,
        delay,
    )
    step.retries += 1
    step.status = StepStatus.PENDING  # reset so executor will re-run
    step.error = None
    await asyncio.sleep(delay)
    return True


async def _skip_step(step: AgentStep) -> bool:
    """
    Mark the step as SKIPPED and report success so execution continues.

    Only safe for LOW-risk steps whose failure is non-blocking.
    """
    logger.info(
        "[RECOVERY] step=%d risk=low → skipping (non-critical)",
        step.index,
    )
    step.mark_skipped("skipped by recovery: low-risk step failure")
    return True


async def _attempt_rollback(step: AgentStep) -> None:
    """
    Best-effort rollback via the tool registry — undoes partial effects
    before a step is marked FAILED for good. Only runs when a step
    explicitly declares rollback_tool (opt-in — steps that don't set it are
    completely unaffected by this strategy). Never raises; a rollback
    failure doesn't change the outcome (the step still ends up FAILED
    either way), it's just logged.
    """
    if not step.rollback_tool:
        return
    try:
        from api.tools.registry import registry
        logger.info("[RECOVERY_ROLLBACK] step=%d tool=%s", step.index, step.rollback_tool)
        result = await asyncio.to_thread(registry.execute, step.rollback_tool, step.rollback_args, {})
        logger.info("[RECOVERY_ROLLBACK] step=%d success=%s", step.index, result.success)
    except Exception as exc:
        logger.warning("[RECOVERY_ROLLBACK] step=%d failed: %s", step.index, exc)


def _tweak_args(step: AgentStep) -> bool:
    """
    Apply tool-specific argument tweaks to increase the chance of success.

    Returns True if a tweak was applied (so the caller can retry),
    False if no known tweak exists for this tool.
    """
    if not step.tool:
        return False

    tool = step.tool.lower()

    if tool == "open_application":
        # Try launching via shell if direct launch failed
        if not step.tool_args.get("via_shell"):
            step.tool_args["via_shell"] = True
            logger.info(
                "[RECOVERY] step=%d tool=open_application → enabling via_shell=True",
                step.index,
            )
            return True

    if tool == "run_command":
        # Add timeout kwarg if missing
        if "timeout" not in step.tool_args:
            step.tool_args["timeout"] = 30
            logger.info(
                "[RECOVERY] step=%d tool=run_command → adding timeout=30",
                step.index,
            )
            return True

    if tool in ("write_file", "read_file"):
        # Try alternate encoding
        if step.tool_args.get("encoding", "utf-8") == "utf-8":
            step.tool_args["encoding"] = "latin-1"
            logger.info(
                "[RECOVERY] step=%d tool=%s → switching encoding to latin-1",
                step.index,
                tool,
            )
            return True

    return False


# ── Public API ─────────────────────────────────────────────────────────────────


async def attempt_recovery(
    task: AgentTask,
    step: AgentStep,
    error: str,
) -> bool:
    """
    Try to recover from a step failure.

    Returns:
      True  → recovery succeeded; caller should re-run (or skip) the step
      False → all strategies exhausted; task should be marked FAILED

    Never raises.
    """
    task_id = task.task_id
    logger.info(
        "[RECOVERY] task=%s step=%d error=%r — evaluating strategies",
        task_id,
        step.index,
        error[:120],
    )

    # ── Strategy 1: Retry with backoff ────────────────────────────────────────
    if step.retries < _MAX_RETRIES:
        return await _retry_with_backoff(step)

    # ── Strategy 2: Skip low-risk steps ───────────────────────────────────────
    if step.risk == RiskLevel.LOW:
        return await _skip_step(step)

    # ── Strategy 3: Modify args and retry once ────────────────────────────────
    if step.retries == _MAX_RETRIES and _tweak_args(step):
        # Give it one more chance with tweaked args
        step.retries += 1
        step.status = StepStatus.PENDING
        step.error = None
        logger.info(
            "[RECOVERY] task=%s step=%d → args tweaked, attempting one more run",
            task_id,
            step.index,
        )
        await asyncio.sleep(1.0)
        return True

    # ── Strategy 4: Rollback (best-effort, opt-in) ────────────────────────────
    if step.rollback_tool:
        await _attempt_rollback(step)

    # ── Strategy 5: Give up ───────────────────────────────────────────────────
    logger.warning(
        "[RECOVERY] task=%s step=%d — all strategies exhausted, propagating failure",
        task_id,
        step.index,
    )
    return False
