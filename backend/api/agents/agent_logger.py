"""
Structured logger for the Phase 3 agent runtime.

Emits tagged log lines that can be grepped or streamed to observability
tools without any external library dependencies.

Log tags emitted:
  [AGENT_CREATED]          [AGENT_PLAN_GENERATED]    [AGENT_STEP_STARTED]
  [AGENT_STEP_COMPLETED]   [AGENT_STEP_FAILED]       [AGENT_RECOVERY_STARTED]
  [AGENT_RECOVERY_SUCCESS] [AGENT_WAITING_APPROVAL]  [AGENT_CANCELLED]
  [AGENT_RESUMED]          [AGENT_FINISHED]
"""
from __future__ import annotations

import logging
from typing import Optional

from api.agents.agent_types import AgentStep, AgentTask, StepResult

logger = logging.getLogger("api.agents.logger")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _task_prefix(task: AgentTask) -> str:
    return f"[task={task.task_id}] [{task.agent_type.value}]"


def _step_prefix(step: AgentStep) -> str:
    return f"[step={step.index}] [{step.tool or 'no-tool'}]"


# ── Public log functions ───────────────────────────────────────────────────────


def log_created(task: AgentTask) -> None:
    logger.info(
        "[AGENT_CREATED] %s goal=%r status=%s",
        _task_prefix(task),
        task.goal,
        task.status.value,
    )


def log_plan(task: AgentTask) -> None:
    if task.plan is None:
        logger.warning("[AGENT_PLAN_GENERATED] %s plan=None", _task_prefix(task))
        return
    step_count = task.plan.total_steps()
    risk = task.plan.risk_level.value
    needs_confirm = task.plan.requires_confirmation
    logger.info(
        "[AGENT_PLAN_GENERATED] %s steps=%d risk=%s requires_confirmation=%s est_duration_s=%d",
        _task_prefix(task),
        step_count,
        risk,
        needs_confirm,
        task.plan.estimated_duration_s,
    )
    for step in task.plan.steps:
        logger.debug(
            "[AGENT_PLAN_GENERATED]   step[%d] %r risk=%s tool=%s",
            step.index,
            step.description,
            step.risk.value,
            step.tool or "-",
        )


def log_step_start(task: AgentTask, step: AgentStep) -> None:
    logger.info(
        "[AGENT_STEP_STARTED] %s %s description=%r",
        _task_prefix(task),
        _step_prefix(step),
        step.description,
    )


def log_step_done(task: AgentTask, step: AgentStep, result: StepResult) -> None:
    duration = step.duration_s()
    logger.info(
        "[AGENT_STEP_COMPLETED] %s %s duration_s=%.2f output=%r",
        _task_prefix(task),
        _step_prefix(step),
        duration if duration is not None else -1.0,
        result.output[:120],
    )


def log_step_fail(task: AgentTask, step: AgentStep, error: str) -> None:
    logger.warning(
        "[AGENT_STEP_FAILED] %s %s retries=%d error=%r",
        _task_prefix(task),
        _step_prefix(step),
        step.retries,
        error[:200],
    )


def log_recovery_start(task: AgentTask) -> None:
    logger.info(
        "[AGENT_RECOVERY_STARTED] %s step=%d",
        _task_prefix(task),
        task.current_step,
    )


def log_recovery_success(task: AgentTask) -> None:
    logger.info(
        "[AGENT_RECOVERY_SUCCESS] %s step=%d",
        _task_prefix(task),
        task.current_step,
    )


def log_approval_wait(task: AgentTask, prompt: str) -> None:
    logger.info(
        "[AGENT_WAITING_APPROVAL] %s step=%d prompt=%r",
        _task_prefix(task),
        task.current_step,
        prompt[:200],
    )


def log_cancelled(task: AgentTask) -> None:
    logger.info(
        "[AGENT_CANCELLED] %s elapsed_s=%.1f",
        _task_prefix(task),
        task.elapsed_s(),
    )


def log_resumed(task: AgentTask) -> None:
    logger.info(
        "[AGENT_RESUMED] %s step=%d",
        _task_prefix(task),
        task.current_step,
    )


def log_finished(task: AgentTask) -> None:
    logger.info(
        "[AGENT_FINISHED] %s status=%s elapsed_s=%.1f summary=%r",
        _task_prefix(task),
        task.status.value,
        task.elapsed_s(),
        (task.result_summary or task.error_message or "")[:200],
    )
