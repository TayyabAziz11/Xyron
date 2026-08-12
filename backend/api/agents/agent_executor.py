"""
Step executor for the Phase 3 agent runtime.

AgentExecutor.execute_step() wraps the specialist agent's executor_fn with:
  - Per-step timeout (60s default, 120s for HIGH-risk steps)
  - Automatic timing (started_at / completed_at on the AgentStep)
  - Verifier check after successful execution
  - Recovery attempt on any failure or timeout
  - Clean error propagation as StepResult

The executor_fn signature expected from specialist agents:
  async def executor_fn(task: AgentTask, step: AgentStep) -> StepResult
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Awaitable

from api.agents.agent_types import AgentStep, AgentTask, RiskLevel, StepResult, StepStatus
from api.agents.agent_verifier import verify_step
from api.agents.agent_recovery import attempt_recovery
from api.agents.world_state_check import check_condition

logger = logging.getLogger("api.agents.executor")

# Default timeouts per risk level (seconds)
_TIMEOUT_DEFAULT: float = 60.0
_TIMEOUT_HIGH_RISK: float = 120.0


def _choose_timeout(step: AgentStep) -> float:
    if step.risk == RiskLevel.HIGH:
        return _TIMEOUT_HIGH_RISK
    return _TIMEOUT_DEFAULT


class AgentExecutor:
    """
    Runs one AgentStep at a time using a caller-supplied executor function.

    The executor is stateless — it holds no task-specific data, so the same
    AgentExecutor instance is shared across all tasks.
    """

    async def execute_step(
        self,
        task: AgentTask,
        step: AgentStep,
        executor_fn: Callable[[AgentTask, AgentStep], Awaitable[StepResult]],
    ) -> StepResult:
        """
        Execute one step with timeout, verification, and automatic recovery.

        Flow:
          1. Mark step as RUNNING with timestamp
          2. Await executor_fn under asyncio.wait_for (timeout)
          3. Run verifier on success
          4. If step.success_condition is set, also check it against World
             State (before/after snapshots) — the Phase 3 observation loop.
             Both the tool-specific verifier AND the World State condition
             must pass; either failing triggers recovery.
          5. If verification fails → treat as failure and attempt recovery
          6. If timeout/exception → attempt recovery
          7. If recovery succeeds → return a "retry" StepResult
          8. If recovery fails → return failure StepResult

        Returns the final StepResult for this step attempt.  The caller
        (agent_runtime) inspects result.should_retry to decide whether to
        loop back.
        """
        timeout = _choose_timeout(step)
        step.mark_started()

        world_state_before: dict = {}
        if step.success_condition:
            try:
                from api.services.world_state import world_state
                world_state_before = world_state.get_context()
            except Exception:
                pass

        logger.info(
            "[EXECUTOR] task=%s step=%d tool=%s timeout=%.0fs",
            task.task_id,
            step.index,
            step.tool or "-",
            timeout,
        )

        # ── Execute ───────────────────────────────────────────────────────────
        try:
            result: StepResult = await asyncio.wait_for(
                executor_fn(task, step),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            error_msg = (
                f"Step {step.index} timed out after {timeout:.0f}s "
                f"(tool={step.tool or 'none'})"
            )
            logger.warning("[EXECUTOR] task=%s %s", task.task_id, error_msg)
            step.mark_failed(error_msg)
            return await self._handle_failure(task, step, error_msg)

        except asyncio.CancelledError:
            # Propagate cancellation — do not swallow it
            step.mark_failed("cancelled")
            raise

        except Exception as exc:  # noqa: BLE001
            error_msg = f"Step {step.index} raised {type(exc).__name__}: {exc}"
            logger.exception("[EXECUTOR] task=%s %s", task.task_id, error_msg)
            step.mark_failed(error_msg)
            return await self._handle_failure(task, step, error_msg)

        # ── Verify ────────────────────────────────────────────────────────────
        if not result.success:
            error_msg = result.output or f"Step {step.index} returned failure"
            step.mark_failed(error_msg)
            return await self._handle_failure(task, step, error_msg)

        verified = await verify_step(task, step, result)
        if not verified:
            error_msg = f"Step {step.index} verification failed (output={result.output[:80]!r})"
            logger.warning("[EXECUTOR] task=%s %s", task.task_id, error_msg)
            step.mark_failed(error_msg)
            return await self._handle_failure(task, step, error_msg)

        # ── Observe World State (Phase 3 observation loop) ──────────────────────
        if step.success_condition:
            try:
                from api.services.world_state import world_state
                world_state_after = world_state.get_context(refresh=True)
                condition_ok, reason = check_condition(
                    step.success_condition, world_state_before, world_state_after
                )
            except Exception:
                logger.debug("[EXECUTOR] world state condition check failed", exc_info=True)
                condition_ok, reason = True, "check errored — not blocking"
            if not condition_ok:
                error_msg = f"Step {step.index} success_condition not met ({reason})"
                logger.warning("[EXECUTOR] task=%s %s", task.task_id, error_msg)
                step.mark_failed(error_msg)
                return await self._handle_failure(task, step, error_msg)

        # ── Success ───────────────────────────────────────────────────────────
        step.mark_completed(result.output)
        logger.info(
            "[EXECUTOR] task=%s step=%d DONE in %.2fs",
            task.task_id,
            step.index,
            step.duration_s() or 0.0,
        )
        return result

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _handle_failure(
        self,
        task: AgentTask,
        step: AgentStep,
        error: str,
    ) -> StepResult:
        """
        Attempt recovery for a failed step.

        Returns a StepResult with should_retry=True if recovery provided a
        path forward (retry or skip), or success=False if all strategies
        are exhausted.
        """
        from api.agents import agent_logger  # noqa: PLC0415

        agent_logger.log_recovery_start(task)

        recovered = await attempt_recovery(task, step, error)

        if recovered:
            agent_logger.log_recovery_success(task)

            # If the step was skipped by recovery, mark as success so runtime advances
            if step.status == StepStatus.SKIPPED:
                return StepResult(
                    success=True,
                    output=step.result or "step skipped",
                    should_retry=False,
                )

            # Step was reset to PENDING for a retry
            return StepResult(
                success=False,
                output=error,
                should_retry=True,
            )

        # Recovery gave up — return terminal failure
        return StepResult(
            success=False,
            output=error,
            should_retry=False,
        )


# Module-level singleton
agent_executor = AgentExecutor()
