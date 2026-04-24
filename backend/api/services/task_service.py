"""
Task Service — multi-step task planner and executor.

Flow:
  1. create_and_run(goal) → Task (status=planning)
  2. Background thread: plan_task_sync() → list of TaskStep dicts via GPT-4o-mini
  3. Execute each step via the central tool registry
  4. Update step / task status in-place (frontend polls GET /tasks/{id})

All tool definitions and execution are in api/tools/ (central registry).
This service owns only planning and orchestration.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Schemas ───────────────────────────────────────────────────────────────────

TaskStepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
TaskStatus     = Literal["planning", "running", "completed", "failed", "cancelled"]


class TaskStep(BaseModel):
    id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    index:        int
    description:  str
    tool:         str
    params:       Dict[str, Any] = {}
    status:       TaskStepStatus = "pending"
    result:       Optional[str]  = None
    error:        Optional[str]  = None
    started_at:   Optional[str]  = None
    completed_at: Optional[str]  = None


class Task(BaseModel):
    id:                   str  = Field(default_factory=lambda: str(uuid.uuid4()))
    goal:                 str
    steps:                List[TaskStep] = []
    status:               TaskStatus = "planning"
    risk_level:           Literal["low", "medium", "high"] = "low"
    confirmation_required: bool = False
    created_at:           str  = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z")
    completed_at:         Optional[str] = None
    result:               Optional[str] = None
    error:                Optional[str] = None


# ── Planner ───────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are a task planning AI. Break the user's goal into concrete steps using the available tools.

Rules:
- Simple single actions → 1 step.
- Complex multi-step goals → 2–5 steps.
- Each step uses exactly one tool.
- Return ONLY a JSON array — no explanation, no markdown.

Format:
[
  {"tool": "<tool_name>", "params": {<key>: <value>}, "description": "<short human label>"},
  ...
]"""


def plan_task_sync(
    goal:       str,
    history:    list[dict] | None = None,
    openai_key: str | None = None,
) -> list[dict]:
    """Use GPT-4o-mini to break a goal into tool steps."""
    if not openai_key:
        return [{"tool": "general_query", "params": {"query": goal}, "description": goal[:80]}]

    try:
        from api.tools import registry  # lazy import avoids circular
        definitions = registry.get_definitions()
        tool_lines  = "\n".join(
            f"- {t['function']['name']}: {t['function']['description']}"
            for t in definitions
        )
        system = _PLANNER_SYSTEM + f"\n\nAvailable tools:\n{tool_lines}"

        msgs: list[dict] = [{"role": "system", "content": system}]
        if history:
            for h in history[-4:]:
                msgs.append({"role": h.get("role", "user"), "content": h.get("text", "")})
        msgs.append({"role": "user", "content": f"Goal: {goal}"})

        from openai import OpenAI
        resp = OpenAI(api_key=openai_key).chat.completions.create(
            model="gpt-4o-mini",
            messages=msgs,
            max_tokens=600,
            temperature=0.2,
        )
        raw = (resp.choices[0].message.content or "").strip()
        m   = re.search(r"\[[\s\S]*\]", raw)
        if m:
            steps = json.loads(m.group(0))
            if isinstance(steps, list) and steps:
                return steps
    except Exception as exc:
        logger.warning("Task planner failed: %s", exc)

    return [{"tool": "general_query", "params": {"query": goal}, "description": goal[:80]}]


# ── Task Service ──────────────────────────────────────────────────────────────

class TaskService:
    _MAX_TASKS = 100

    def __init__(self) -> None:
        self._lock:  threading.Lock         = threading.Lock()
        self._tasks: OrderedDict[str, Task] = OrderedDict()
        self._pool:  ThreadPoolExecutor     = ThreadPoolExecutor(max_workers=4)

    def create_and_run(
        self,
        goal:       str,
        history:    list[dict] | None = None,
        openai_key: str | None        = None,
    ) -> Task:
        task = Task(goal=goal)
        with self._lock:
            self._tasks[task.id] = task
            while len(self._tasks) > self._MAX_TASKS:
                self._tasks.popitem(last=False)
        self._pool.submit(self._plan_and_execute, task.id, goal, history, openai_key)
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_recent(self, limit: int = 10) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.reverse()
        return tasks[:limit]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status in ("planning", "running"):
                task.status = "cancelled"
                return True
        return False

    # ── Private ───────────────────────────────────────────────────────────────

    def _plan_and_execute(
        self,
        task_id:    str,
        goal:       str,
        history:    list[dict] | None,
        openai_key: str | None,
    ) -> None:
        try:
            from api.tools import registry
            from api.tools.safety import assess_risk

            self._set(task_id, status="planning")

            # ── Plan ──────────────────────────────────────────────────────────
            step_dicts = plan_task_sync(goal, history, openai_key)
            steps = [
                TaskStep(
                    index=i,
                    description=s.get("description", f"Step {i + 1}"),
                    tool=s.get("tool", "general_query"),
                    params=s.get("params", {}),
                )
                for i, s in enumerate(step_dicts)
            ]

            risks    = [assess_risk(s.tool, s.params) for s in steps]
            max_risk = "high" if "high" in risks else ("medium" if "medium" in risks else "low")

            self._set(task_id, steps=steps, status="running",
                      risk_level=max_risk, confirmation_required=(max_risk == "high"))

            # ── Execute ───────────────────────────────────────────────────────
            ctx = {"openai_key": openai_key or ""}

            for step in steps:
                task = self.get(task_id)
                if task and task.status == "cancelled":
                    break

                self._set_step(task_id, step.id, status="running",
                               started_at=datetime.now(timezone.utc).isoformat() + "Z")
                try:
                    result = registry.execute(step.tool, step.params, ctx)
                    text   = result.text or ("Done." if result.success else (result.error or "Failed."))
                    status = "completed" if result.success else "failed"
                    self._set_step(task_id, step.id, status=status, result=text,
                                   completed_at=datetime.now(timezone.utc).isoformat() + "Z")
                    if not result.success:
                        logger.warning("Step %s (%s) failed: %s", step.id, step.tool, result.error)
                except Exception as exc:
                    logger.warning("Task %s step %s crashed: %s", task_id, step.id, exc)
                    self._set_step(task_id, step.id, status="failed", error=str(exc),
                                   completed_at=datetime.now(timezone.utc).isoformat() + "Z")

            # ── Summarise ──────────────────────────────────────────────────────
            task = self.get(task_id)
            if task and task.status != "cancelled":
                done  = sum(1 for s in task.steps if s.status == "completed")
                total = len(task.steps)
                self._set(
                    task_id,
                    status="completed",
                    result=f"Completed {done}/{total} steps for: {goal}",
                    completed_at=datetime.now(timezone.utc).isoformat() + "Z",
                )

        except Exception as exc:
            logger.exception("Task %s crashed", task_id)
            self._set(task_id, status="failed", error=str(exc))

    def _set(self, task_id: str, **kwargs: Any) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                for k, v in kwargs.items():
                    setattr(task, k, v)

    def _set_step(self, task_id: str, step_id: str, **kwargs: Any) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            for step in task.steps:
                if step.id == step_id:
                    for k, v in kwargs.items():
                        setattr(step, k, v)
                    break


task_service = TaskService()
