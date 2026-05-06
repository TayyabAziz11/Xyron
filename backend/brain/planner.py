"""
Multi-step task planner.

Handles compound voice commands like:
  "Create a 'Projects' folder and then open it"
  "Delete the temp files and also empty the trash"

Splits the transcript into atomic steps; each step is re-routed through
the orchestrator by the caller (voice_ws.process_utterance).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

# Split on common compound-command connectors
_SPLIT_RE = re.compile(
    r'\s+(?:and\s+then|then|after\s+that|and\s+also|also)\s+',
    re.IGNORECASE,
)


@dataclass
class PlanStep:
    index:  int
    text:   str
    done:   bool = False
    result: Optional[str] = None


class Plan:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps   = steps
        self._cursor = 0

    @property
    def current(self) -> Optional[PlanStep]:
        return self.steps[self._cursor] if self._cursor < len(self.steps) else None

    @property
    def is_complete(self) -> bool:
        return self._cursor >= len(self.steps)

    def advance(self, result: Optional[str] = None) -> None:
        if self.current:
            self.current.done   = True
            self.current.result = result
            self._cursor += 1

    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.done)
        return f"{done}/{len(self.steps)} steps done"

    def combined_response(self) -> str:
        parts = [s.result for s in self.steps if s.result]
        return " ".join(parts)


ExecuteFn = Callable[[str, list[dict]], Coroutine[Any, Any, str]]


class Planner:
    """
    Parses a compound transcript into a Plan.
    Execution is delegated back to the caller via `execute_fn` to avoid
    circular imports with the orchestrator/voice_ws.
    """

    def build(self, transcript: str) -> Optional[Plan]:
        """Return a Plan if the transcript contains multi-step markers, else None."""
        parts = [p.strip() for p in _SPLIT_RE.split(transcript) if p.strip()]
        if len(parts) < 2:
            return None
        steps = [PlanStep(index=i, text=p) for i, p in enumerate(parts, 1)]
        logger.info("[PLANNER] %d-step plan: %s", len(steps), [s.text for s in steps])
        return Plan(steps)

    async def execute(
        self,
        plan:       Plan,
        execute_fn: ExecuteFn,
        history:    list[dict],
    ) -> str:
        """
        Run each step sequentially via `execute_fn(step_text, history) -> response_text`.
        Passes prior step results into history so later steps have context.
        Returns a combined response string.
        """
        while not plan.is_complete:
            step = plan.current
            if not step:
                break
            logger.info("[PLANNER] executing step %d/%d: %r",
                        step.index, len(plan.steps), step.text)
            try:
                result = await execute_fn(step.text, history)
            except Exception as exc:
                logger.warning("[PLANNER] step %d error: %s", step.index, exc)
                result = "That step failed."
            plan.advance(result=result)
            history.append({"role": "assistant", "text": result})

        return plan.combined_response() or "Done."


# Module-level singleton
planner = Planner()
