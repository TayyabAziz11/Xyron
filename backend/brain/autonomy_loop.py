"""
Autonomy Loop — goal-driven multi-step execution engine.

Accepts a goal, creates a plan, routes each step to the right agent/tool,
verifies step results, stops on failure, and asks confirmation for risky steps.

Autonomy levels:
  0  reactive      — no autonomous execution, return plan only
  1  suggestive    — propose plan, don't execute
  2  assisted      — execute low-risk steps; confirm medium/high
  3  autonomous    — execute all except high-risk; confirm high only
  4  guarded high  — execute all; log everything; never skip confirmation for destructive

Default: 2 (assisted)

Logs:
  [AUTONOMY] goal=...
  [AUTONOMY_PLAN] steps=...
  [AUTONOMY_STEP] index=... agent=... success=...
  [AUTONOMY_DONE] success=true
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class LoopStep:
    index:       int
    description: str
    agent_id:    str
    tool:        Optional[str]      = None
    tool_args:   dict[str, Any]     = field(default_factory=dict)
    risk:        str                = "low"    # "low" | "medium" | "high"
    done:        bool               = False
    success:     bool               = False
    spoken:      str                = ""
    error:       Optional[str]      = None


@dataclass
class LoopResult:
    goal:          str
    success:       bool
    steps_total:   int
    steps_done:    int
    spoken_parts:  list[str]         = field(default_factory=list)
    errors:        list[str]         = field(default_factory=list)
    halted_at:     Optional[int]     = None    # step index where loop stopped
    latency_ms:    float             = 0.0

    @property
    def spoken(self) -> str:
        return " ".join(p for p in self.spoken_parts if p)


class AutonomyLoop:
    """
    Executes a sequence of LoopSteps respecting the current autonomy level.

    Usage:
        result = await autonomy_loop.run(goal, steps, on_spoken=speak_callback)
    """

    def __init__(self) -> None:
        self._log = logging.getLogger("brain.autonomy_loop")

    async def run(
        self,
        goal:       str,
        steps:      list[LoopStep],
        autonomy:   int | None              = None,
        on_spoken:  Optional[Callable]      = None,   # called with spoken text per step
        on_confirm: Optional[Callable]      = None,   # called with (step, prompt) → bool
    ) -> LoopResult:
        """
        Execute steps in order, respecting autonomy level and risk gates.

        Args:
            goal:       Human-readable goal label.
            steps:      Ordered list of LoopStep objects.
            autonomy:   Override autonomy level (else reads brain_state).
            on_spoken:  Callback(text: str) — speak each step's output.
            on_confirm: Async callback(step, prompt) → bool — confirmation dialog.
        """
        t0 = time.monotonic()

        # Resolve autonomy level
        if autonomy is None:
            try:
                from brain.brain_state import brain_state
                autonomy = brain_state.autonomy_level
            except Exception:
                autonomy = 2

        self._log.info("[AUTONOMY] goal=%r steps=%d autonomy=%d", goal, len(steps), autonomy)

        if autonomy == 0:
            self._log.info("[AUTONOMY] level=reactive — returning plan only, no execution")
            return LoopResult(
                goal=goal, success=False,
                steps_total=len(steps), steps_done=0,
                spoken_parts=[f"Here's my plan for '{goal}': " +
                               "; ".join(s.description for s in steps) + ". Say yes to proceed."],
                latency_ms=0.0,
            )

        if autonomy == 1:
            self._log.info("[AUTONOMY] level=suggestive — proposing plan")
            return LoopResult(
                goal=goal, success=False,
                steps_total=len(steps), steps_done=0,
                spoken_parts=[f"I'd suggest: " +
                               ", ".join(s.description for s in steps) + ". Want me to proceed?"],
                latency_ms=0.0,
            )

        result = LoopResult(
            goal=goal,
            success=True,
            steps_total=len(steps),
            steps_done=0,
        )

        self._log.info("[AUTONOMY_PLAN] goal=%r steps=[%s]",
                       goal, ", ".join(s.description for s in steps))

        for step in steps:
            # Risk gate
            needs_confirm = self._needs_confirmation(step.risk, autonomy)

            if needs_confirm:
                prompt = f"Step {step.index + 1}: {step.description} — risk level {step.risk}. Confirm?"
                self._log.info("[AUTONOMY_STEP] index=%d desc=%r risk=%s → awaiting_confirmation",
                               step.index, step.description, step.risk)

                approved = False
                if on_confirm:
                    try:
                        approved = await on_confirm(step, prompt)
                    except Exception:
                        approved = False

                if not approved:
                    step.done = True
                    step.success = False
                    step.spoken = f"Skipped '{step.description}' — confirmation not received."
                    step.error = "confirmation_declined"
                    result.spoken_parts.append(step.spoken)
                    result.success = False
                    result.halted_at = step.index
                    self._log.info("[AUTONOMY_STEP] index=%d SKIPPED no_confirmation", step.index)
                    continue

            # Execute step
            step_spoken = await self._execute_step(step)
            step.done = True
            result.steps_done += 1

            if step_spoken:
                result.spoken_parts.append(step_spoken)
                if on_spoken:
                    try:
                        await asyncio.to_thread(on_spoken, step_spoken)
                    except Exception:
                        pass

            self._log.info("[AUTONOMY_STEP] index=%d desc=%r success=%s spoken=%r",
                           step.index, step.description, step.success, step_spoken[:40] if step_spoken else "")

            if not step.success and step.risk in ("medium", "high"):
                result.success = False
                result.halted_at = step.index
                result.errors.append(step.error or f"Step {step.index} failed")
                self._log.info("[AUTONOMY] halted at step=%d reason=%s", step.index, step.error)
                break

        result.latency_ms = (time.monotonic() - t0) * 1000
        if not result.spoken_parts:
            result.spoken_parts = [f"Done with {goal}." if result.success else f"Couldn't complete {goal}."]

        self._log.info("[AUTONOMY_DONE] goal=%r success=%s steps=%d/%d latency=%.1fms",
                       goal, result.success, result.steps_done, result.steps_total, result.latency_ms)
        return result

    async def _execute_step(self, step: LoopStep) -> str:
        """Dispatch a single step to the appropriate agent or tool."""
        try:
            if step.tool:
                # Direct tool dispatch via contract
                from shared.tool_contract import ToolRequest, ToolDispatcher
                from api.tools import registry as _tool_registry
                from brain.brain_state import brain_state

                req = ToolRequest(
                    tool_name=step.tool,
                    intent=step.description,
                    params=step.tool_args,
                    risk_level=step.risk,
                    source_agent=step.agent_id,
                )
                dispatcher = ToolDispatcher(_tool_registry, brain_state.autonomy_level)
                result = await asyncio.to_thread(dispatcher.dispatch, req)
                step.success = result.success
                step.error = None if result.success else result.message
                logger.info("[BRAIN_TOOL_RESULT] tool=%s success=%s latency=%.1fms",
                            step.tool, result.success, result.latency_ms)
                if result.success:
                    return result.message or f"Done: {step.description}."
                else:
                    return f"Couldn't complete '{step.description}': {result.message}."

            elif step.agent_id:
                # Dispatch to agent
                from agents.registry import brain_agent_registry
                from agents.base import AgentContext, AgentPlan, PlanStep as _PS
                agent = brain_agent_registry.get(step.agent_id)
                if agent is None:
                    step.success = False
                    step.error = f"Agent '{step.agent_id}' not found"
                    return step.error

                ctx = AgentContext(transcript=step.description, intent=step.description,
                                   entities=step.tool_args)
                plan = AgentPlan(goal=step.description,
                                 steps=[_PS(0, step.description, tool=step.tool,
                                            tool_args=step.tool_args, risk=step.risk)])
                agent_result = await asyncio.to_thread(agent.execute, plan, ctx)
                step.success = agent_result.success
                step.error = agent_result.error
                return agent_result.spoken or ""

            else:
                # Internal state step — no external call
                step.success = True
                return f"Done: {step.description}."

        except Exception as exc:
            step.success = False
            step.error = str(exc)
            logger.warning("[AUTONOMY_STEP] exception at step %d: %s", step.index, exc)
            return f"Error at '{step.description}': {exc}."

    @staticmethod
    def _needs_confirmation(risk: str, autonomy: int) -> bool:
        if risk == "high":
            return True          # always confirm destructive/external actions
        if risk == "medium" and autonomy < 3:
            return True
        return False

    def build_work_mode_steps(self) -> list[LoopStep]:
        return [
            LoopStep(0, "Open VS Code",   "automation_agent", tool="open_application",
                     tool_args={"name": "Code"},    risk="low"),
            LoopStep(1, "Open browser",   "automation_agent", tool="open_application",
                     tool_args={"name": "chrome"},  risk="low"),
            LoopStep(2, "Set work mode",  "automation_agent", risk="low"),
        ]

    def build_chill_mode_steps(self) -> list[LoopStep]:
        return [
            LoopStep(0, "Open Spotify",   "automation_agent", tool="open_application",
                     tool_args={"name": "Spotify"}, risk="low"),
            LoopStep(1, "Set chill mode", "automation_agent", risk="low"),
        ]


autonomy_loop = AutonomyLoop()
