"""
AgentRuntime — central orchestrator for the Phase 3 autonomous agent system.

Responsibilities:
  - Launch specialist agents as background asyncio.Tasks (non-blocking)
  - Stream progress to the caller via an async WebSocket send function
  - Manage task lifecycle: pause / resume / cancel
  - Route AgentType to the correct specialist agent module (lazy import)
  - Survive specialist-agent import failures gracefully

Specialist agents must expose a module-level object whose .run() coroutine
matches this signature:
    async def run(
        task: AgentTask,
        runtime: AgentRuntime,
        cancel_event: asyncio.Event,
        pause_event: asyncio.Event,
    ) -> StepResult

pause_event semantics: CLEARED = paused, SET = running (default).
cancel_event semantics: SET = cancel requested.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from api.agents.agent_types import (
    AgentPlan,
    AgentStatus,
    AgentStep,
    AgentTask,
    AgentType,
    StepResult,
    StepStatus,
)

logger = logging.getLogger("api.agents.runtime")

# ── Lazy import map ────────────────────────────────────────────────────────────
# Each value is "module_path.attribute_name".
# The runtime does `importlib.import_module(module_path)` then
# `getattr(module, attribute_name)` to retrieve the agent object.

_AGENT_MODULES: dict[AgentType, str] = {
    AgentType.BROWSER:      "api.agents.browser_agent.browser_agent",
    AgentType.CODING:       "api.agents.coding_agent.coding_builder_agent",
    AgentType.AUTOMATION:   "api.agents.automation_agent.automation_agent",
    AgentType.PERSONALITY:  "api.agents.personality.personality_engine",
    AgentType.COORDINATOR:  "api.agents.coordinator.coordinator_agent",  # Phase 4
}


def _load_agent(agent_type: AgentType) -> Optional[Any]:
    """
    Dynamically import and return the specialist agent object.

    Returns None if:
      - The agent_type has no registered module path (e.g. GENERIC)
      - The module does not exist yet (not yet implemented)
      - The module exists but raises on import (bug in specialist agent)
    """
    dotted = _AGENT_MODULES.get(agent_type)
    if dotted is None:
        return None

    # Split "a.b.c.attr_name" → module="a.b.c" attr="attr_name"
    *mod_parts, attr_name = dotted.rsplit(".", 1)
    module_path = ".".join(mod_parts)

    try:
        mod = importlib.import_module(module_path)
        agent_obj = getattr(mod, attr_name)
        return agent_obj
    except ModuleNotFoundError:
        logger.warning(
            "[RUNTIME] Specialist agent module not found: %s (agent_type=%s) — will use generic executor",
            module_path,
            agent_type.value,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[RUNTIME] Failed to import specialist agent %s: %s",
            module_path,
            exc,
            exc_info=True,
        )
        return None


# ── Generic fallback executor ─────────────────────────────────────────────────

async def _generic_executor_fn(task: AgentTask, step: AgentStep) -> StepResult:
    """
    Fallback executor used when no specialist agent is registered/importable.

    Reports the step as completed with a placeholder output so the runtime
    can advance without crashing.
    """
    logger.info(
        "[RUNTIME] generic_executor task=%s step=%d desc=%r",
        task.task_id,
        step.index,
        step.description,
    )
    await asyncio.sleep(0.1)  # yield to event loop
    return StepResult(
        success=True,
        output=f"[generic] step {step.index} executed: {step.description}",
    )


# ── WebSocket helpers ─────────────────────────────────────────────────────────

async def _safe_ws_send(
    ws_send_fn: Optional[Callable[[dict[str, Any]], Awaitable[bool]]],
    payload: dict[str, Any],
) -> None:
    """Call ws_send_fn safely — ignores None, logs but swallows errors."""
    if ws_send_fn is None:
        return
    try:
        await ws_send_fn(payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RUNTIME] ws_send_fn raised (connection closed?): %s", exc)


# ── AgentRuntime ──────────────────────────────────────────────────────────────


class AgentRuntime:
    """
    Singleton runtime that manages all active long-running agent tasks.

    Data structures:
      _tasks          task_id → AgentTask          (all tasks, including terminal)
      _asyncio_tasks  task_id → asyncio.Task        (only live tasks)
      _pause_events   task_id → asyncio.Event       (SET=running, CLEAR=paused)
      _cancel_events  task_id → asyncio.Event       (SET=cancel requested)
    """

    def __init__(self) -> None:
        self._tasks: dict[str, AgentTask] = {}
        self._asyncio_tasks: dict[str, asyncio.Task] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    # ── Public launch API ──────────────────────────────────────────────────────

    async def launch(
        self,
        goal: str,
        agent_type: AgentType,
        ws_send_fn: Optional[Callable[[dict[str, Any]], Awaitable[bool]]] = None,
        context: dict[str, Any] | None = None,
    ) -> AgentTask:
        """
        Create an AgentTask and start it as a background asyncio.Task.

        Returns immediately with the newly created AgentTask.  The task runs
        concurrently — callers track progress via ws_send_fn callbacks.
        """
        from api.agents import agent_logger  # noqa: PLC0415

        if context is None:
            context = {}

        task_id = str(uuid.uuid4())[:8]  # short ID for readability in logs
        task = AgentTask(
            task_id=task_id,
            agent_type=agent_type,
            goal=goal,
            ws_send_fn=ws_send_fn,
            metadata={"context": context},
        )
        self._tasks[task_id] = task

        # Per-task asyncio primitives
        pause_event = asyncio.Event()
        pause_event.set()  # start in RUNNING state (cleared = paused)
        cancel_event = asyncio.Event()
        self._pause_events[task_id] = pause_event
        self._cancel_events[task_id] = cancel_event

        agent_logger.log_created(task)

        # Load specialist agent (may return None)
        specialist = _load_agent(agent_type)

        # Kick off the background coroutine
        asyncio_task = asyncio.create_task(
            self._run_agent_task(task, specialist, pause_event, cancel_event),
            name=f"agent:{task_id}",
        )
        self._asyncio_tasks[task_id] = asyncio_task

        # Cleanup asyncio.Task reference when done (not the AgentTask itself)
        asyncio_task.add_done_callback(
            lambda _: self._asyncio_tasks.pop(task_id, None)
        )

        logger.info(
            "[RUNTIME] Launched task=%s agent_type=%s goal=%r",
            task_id,
            agent_type.value,
            goal[:80],
        )
        return task

    # ── Control API ────────────────────────────────────────────────────────────

    async def cancel(self, task_id: str) -> bool:
        """
        Request cancellation of a running task.

        Returns True if the task was found (regardless of terminal state).
        """
        from api.agents import agent_logger  # noqa: PLC0415
        from api.agents.agent_state import agent_state_machine  # noqa: PLC0415

        task = self._tasks.get(task_id)
        if task is None:
            logger.warning("[RUNTIME] cancel: task_id=%s not found", task_id)
            return False

        if task.is_terminal():
            logger.debug(
                "[RUNTIME] cancel: task=%s already terminal (%s)", task_id, task.status
            )
            return True

        # Signal the background coroutine
        cancel_ev = self._cancel_events.get(task_id)
        if cancel_ev is not None:
            cancel_ev.set()

        # Also hard-cancel the asyncio.Task (it checks cancel_event but this
        # ensures it stops even if stuck in a tool call)
        asyncio_task = self._asyncio_tasks.get(task_id)
        if asyncio_task is not None and not asyncio_task.done():
            asyncio_task.cancel()

        # Update state
        try:
            agent_state_machine.transition(task, AgentStatus.CANCELLED)
        except ValueError:
            task.status = AgentStatus.CANCELLED  # force if already transitioning

        agent_logger.log_cancelled(task)
        await self._send_progress(task, "Task cancelled.", pct=task.progress_pct)
        return True

    async def pause(self, task_id: str) -> bool:
        """
        Pause a running task between steps.

        The task will finish the current step and then wait until resumed.
        Returns True if the task exists and is in a pausable state.
        """
        from api.agents.agent_state import agent_state_machine  # noqa: PLC0415

        task = self._tasks.get(task_id)
        if task is None:
            return False
        if not agent_state_machine.can_interrupt(task.status):
            return False
        if task.status == AgentStatus.PAUSED:
            return True  # already paused

        pause_ev = self._pause_events.get(task_id)
        if pause_ev is not None:
            pause_ev.clear()  # CLEAR = paused

        try:
            agent_state_machine.transition(task, AgentStatus.PAUSED)
        except ValueError:
            return False

        await self._send_progress(task, "Task paused.", pct=task.progress_pct)
        logger.info("[RUNTIME] task=%s paused at step=%d", task_id, task.current_step)
        return True

    async def resume(self, task_id: str) -> bool:
        """
        Resume a paused task.

        Returns True if the task was found and was paused.
        """
        from api.agents import agent_logger  # noqa: PLC0415
        from api.agents.agent_state import agent_state_machine  # noqa: PLC0415

        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status != AgentStatus.PAUSED:
            return False

        try:
            agent_state_machine.transition(task, AgentStatus.RUNNING)
        except ValueError:
            return False

        pause_ev = self._pause_events.get(task_id)
        if pause_ev is not None:
            pause_ev.set()  # SET = running

        agent_logger.log_resumed(task)
        await self._send_progress(task, "Task resumed.", pct=task.progress_pct)
        return True

    # ── Query API ──────────────────────────────────────────────────────────────

    def get_active(self) -> Optional[AgentTask]:
        """
        Return the most recently created non-terminal task, or None.
        """
        # Iterate insertion order (Python 3.7+) in reverse
        for task in reversed(list(self._tasks.values())):
            if not task.is_terminal():
                return task
        return None

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Return an AgentTask by ID, or None if not found."""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[AgentTask]:
        """Return all tracked tasks (including terminal ones)."""
        return list(self._tasks.values())

    def get_progress(self, task_id: Optional[str] = None) -> str:
        """
        Human-readable progress string suitable for voice output.

        If task_id is None, reports on the active task.
        """
        task = (
            self._tasks.get(task_id) if task_id else self.get_active()
        )
        if task is None:
            return "No active agent task."
        pct = task.progress_pct
        status = task.status.value
        goal_short = task.goal[:50]
        if task.is_terminal():
            summary = task.result_summary or task.error_message or status
            return f"Task '{goal_short}' {status}: {summary}"
        return f"Task '{goal_short}' — {status}, {pct}% complete. {task.progress_text()}"

    # ── Internal progress sender ───────────────────────────────────────────────

    async def _send_progress(
        self,
        task: AgentTask,
        message: str,
        pct: Optional[int] = None,
    ) -> None:
        """Send a progress update to the WebSocket client."""
        if pct is not None:
            task.progress_pct = pct
        payload: dict[str, Any] = {
            "type": "agent_progress",
            "task_id": task.task_id,
            "message": message,
            "pct": task.progress_pct,
            "status": task.status.value,
        }
        await _safe_ws_send(task.ws_send_fn, payload)

    # ── Core execution loop ────────────────────────────────────────────────────

    async def _run_agent_task(
        self,
        task: AgentTask,
        specialist: Optional[Any],
        pause_event: asyncio.Event,
        cancel_event: asyncio.Event,
    ) -> None:
        """
        Background coroutine that drives an AgentTask from PENDING to terminal.

        Phases:
          1. PLANNING — generate plan via LLM
          2. WAITING_APPROVAL — if plan requires confirmation
          3. RUNNING — execute steps one by one
          4. COMPLETED / FAILED / CANCELLED — terminal cleanup
        """
        from api.agents import agent_logger  # noqa: PLC0415
        from api.agents.agent_planner import generate_plan, _fallback_plan  # noqa: PLC0415
        from api.agents.agent_executor import agent_executor  # noqa: PLC0415
        from api.agents.agent_state import agent_state_machine  # noqa: PLC0415
        from api.agents.agent_memory import agent_memory  # noqa: PLC0415

        context: dict[str, Any] = task.metadata.get("context", {})

        try:
            # ── Phase 1: PLANNING ─────────────────────────────────────────────
            # CoordinatorAgent runs its own DelegationPlanner and gates individual
            # nodes for approval (see coordinator_agent._execute_node). Running the
            # generic single-shot risk planner on top of that double-gates the task
            # and can block it before CoordinatorAgent (or any of its delegated
            # sub-agents — identified by a "coordinator_task_id" in context, set by
            # coordinator_agent._run_agent_node) ever starts. That WAITING_APPROVAL
            # also cannot be resumed here — resume() requires PAUSED, not
            # WAITING_APPROVAL — so once it fires the task can only time out.
            # Phase 5: any agent_type launched directly with a real specialist
            # (BROWSER/CODING/AUTOMATION/PERSONALITY, now reachable straight
            # from voice_ws.py's DIRECT_AGENT dispatch, not just through
            # Coordinator) also skips the generic gate — the plan/confirmation
            # built here would be discarded the moment specialist.run() takes
            # over in Phase 3 below anyway (see the "Determine executor
            # function" branch), so building it first only costs an LLM call
            # and risks a spurious WAITING_APPROVAL stall before the
            # specialist ever starts.
            skip_generic_gate = (
                task.agent_type == AgentType.COORDINATOR
                or context.get("coordinator_task_id") is not None
                or (specialist is not None and hasattr(specialist, "run"))
            )

            agent_state_machine.transition(task, AgentStatus.PLANNING)
            await self._send_progress(task, f"Planning: {task.goal[:60]}...", pct=0)

            # Check cancel before any LLM call
            if cancel_event.is_set():
                raise asyncio.CancelledError

            plan: AgentPlan
            if skip_generic_gate:
                plan = _fallback_plan(task.goal)
            else:
                plan = await generate_plan(
                    goal=task.goal,
                    agent_type=task.agent_type,
                    context=context,
                )
            task.plan = plan
            agent_logger.log_plan(task)

            # ── Phase 2: CONFIRMATION gate ────────────────────────────────────
            if plan.requires_confirmation and not skip_generic_gate:
                agent_state_machine.transition(task, AgentStatus.WAITING_APPROVAL)
                approval_prompt = (
                    f"This task involves {plan.risk_level.value}-risk steps. "
                    f"Approve to continue? ({len(plan.steps)} steps, "
                    f"~{plan.estimated_duration_s}s)"
                )
                agent_logger.log_approval_wait(task, approval_prompt)
                await self._send_progress(
                    task,
                    approval_prompt,
                    pct=0,
                )
                # Wait for approval or cancellation (poll cancel_event every second)
                # In production: the approval router calls runtime.resume(task_id)
                max_wait_s = 300  # 5 minute approval window
                waited = 0
                while task.status == AgentStatus.WAITING_APPROVAL and waited < max_wait_s:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    await asyncio.sleep(1.0)
                    waited += 1

                if task.status == AgentStatus.WAITING_APPROVAL:
                    # Timed out waiting for approval — cancel
                    logger.warning(
                        "[RUNTIME] task=%s approval timed out after %ds",
                        task.task_id,
                        max_wait_s,
                    )
                    raise asyncio.CancelledError

            # ── Phase 3: RUNNING ──────────────────────────────────────────────
            agent_state_machine.transition(task, AgentStatus.RUNNING)
            task.started_at = task.started_at or time.time()

            # Determine executor function
            specialist_result: Optional[str] = None
            if specialist is not None and hasattr(specialist, "run"):
                # Specialist provides its own run() coroutine — capture its return value
                specialist_result = await specialist.run(task, self, cancel_event, pause_event)
            else:
                # Generic step-by-step execution
                await self._execute_steps_loop(
                    task,
                    agent_executor,
                    _generic_executor_fn,
                    cancel_event,
                    pause_event,
                )

            # ── Phase 4: COMPLETED ────────────────────────────────────────────
            if not task.is_terminal():
                agent_state_machine.transition(task, AgentStatus.COMPLETED)

            # Use specialist's crafted response if provided (e.g. CoordinatorAgent),
            # else fall back to generic plan-step count summary.
            if specialist_result and isinstance(specialist_result, str):
                task.result_summary = specialist_result
            else:
                completed_steps = task.plan.completed_count() if task.plan else 0
                total_steps = task.plan.total_steps() if task.plan else 0
                task.result_summary = (
                    f"Completed {completed_steps}/{total_steps} steps in "
                    f"{task.elapsed_s():.1f}s."
                )
            agent_logger.log_finished(task)
            await self._send_progress(
                task,
                f"Done! {task.result_summary}",
                pct=100,
            )

        except asyncio.CancelledError:
            if not task.is_terminal():
                try:
                    agent_state_machine.transition(task, AgentStatus.CANCELLED)
                except ValueError:
                    task.status = AgentStatus.CANCELLED
            agent_logger.log_cancelled(task)
            await self._send_progress(task, "Task was cancelled.", pct=task.progress_pct)
            # Do not re-raise — this is expected behaviour

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[RUNTIME] task=%s unhandled exception: %s", task.task_id, exc
            )
            if not task.is_terminal():
                try:
                    agent_state_machine.transition(task, AgentStatus.FAILED)
                except ValueError:
                    task.status = AgentStatus.FAILED
            task.error_message = str(exc)
            agent_logger.log_finished(task)
            await self._send_progress(
                task,
                f"Task failed: {exc}",
                pct=task.progress_pct,
            )

        finally:
            # Clean up event references (keep AgentTask for history queries)
            self._pause_events.pop(task.task_id, None)
            self._cancel_events.pop(task.task_id, None)
            agent_memory.clear(task.task_id)

    # ── Generic step execution loop ────────────────────────────────────────────

    async def _execute_steps_loop(
        self,
        task: AgentTask,
        executor: Any,
        executor_fn: Any,
        cancel_event: asyncio.Event,
        pause_event: asyncio.Event,
    ) -> None:
        """
        Iterate through task.plan.steps and execute each one in order.

        Handles:
          - Cancellation check between steps
          - Pause / resume (waits on pause_event between steps)
          - Per-step retry if StepResult.should_retry is True
          - Approval gate if StepResult.needs_approval is True
          - Task FAILED if a step fails without recovery
        """
        from api.agents import agent_logger  # noqa: PLC0415
        from api.agents.agent_state import agent_state_machine  # noqa: PLC0415

        if task.plan is None:
            return

        steps = task.plan.steps

        for step in steps:
            # Skip already-processed steps (e.g. after recovery skipped one)
            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                continue

            task.current_step = step.index

            # ── Cancellation check ────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            # ── Pause check ───────────────────────────────────────────────────
            if not pause_event.is_set():
                logger.info("[RUNTIME] task=%s pausing before step=%d", task.task_id, step.index)
                await self._send_progress(
                    task, f"Paused before step {step.index + 1}.", pct=task.progress_pct
                )
                await pause_event.wait()  # blocks until resumed
                if cancel_event.is_set():
                    raise asyncio.CancelledError

            # ── Progress update ───────────────────────────────────────────────
            pct = task.plan.progress_pct()
            await self._send_progress(
                task,
                f"Step {step.index + 1}/{task.plan.total_steps()}: {step.description}",
                pct=pct,
            )
            agent_logger.log_step_start(task, step)

            # ── Step execution (with retry loop) ─────────────────────────────
            max_attempts = 4  # initial + up to 3 retries
            for attempt in range(max_attempts):
                result: StepResult = await executor.execute_step(task, step, executor_fn)

                if result.needs_approval:
                    # Gate: pause for human approval
                    agent_state_machine.transition(task, AgentStatus.WAITING_APPROVAL)
                    agent_logger.log_approval_wait(task, result.approval_prompt)
                    await self._send_progress(
                        task,
                        result.approval_prompt or "Waiting for approval…",
                        pct=pct,
                    )
                    # Wait; approval resumes the task (sets pause_event + transitions RUNNING)
                    await pause_event.wait()
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    agent_state_machine.transition(task, AgentStatus.RUNNING)
                    # Re-execute the step now that we have approval
                    result = await executor.execute_step(task, step, executor_fn)

                if result.should_retry:
                    logger.info(
                        "[RUNTIME] task=%s step=%d retry attempt=%d",
                        task.task_id,
                        step.index,
                        attempt + 1,
                    )
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    continue  # loop back for next attempt

                # Not a retry — break out of attempt loop
                break

            # ── Post-step result handling ──────────────────────────────────────
            if step.status == StepStatus.FAILED:
                # Recovery exhausted — fail the whole task
                task.error_message = step.error or "Step failed"
                agent_logger.log_step_fail(task, step, task.error_message)
                agent_state_machine.transition(task, AgentStatus.FAILED)
                await self._send_progress(
                    task,
                    f"Failed at step {step.index + 1}: {task.error_message}",
                    pct=pct,
                )
                return  # exit loop; _run_agent_task will catch and handle

            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                agent_logger.log_step_done(task, step, result)
                task.progress_pct = task.plan.progress_pct()
                await self._send_progress(
                    task,
                    f"Completed step {step.index + 1}: {result.output[:80]}",
                    pct=task.progress_pct,
                )

        # All steps done — final progress
        task.progress_pct = 100


# ── Module-level singleton ────────────────────────────────────────────────────

agent_runtime = AgentRuntime()
