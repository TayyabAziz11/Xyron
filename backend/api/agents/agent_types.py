"""
Shared type definitions for the Phase 3 agent runtime.

No external dependencies — only stdlib + dataclasses.
All specialist agents and the runtime import from here; nothing here
imports from anywhere else in the package to keep the dependency graph acyclic.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


# ── Enums ─────────────────────────────────────────────────────────────────────


class AgentStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AgentType(str, Enum):
    BROWSER = "browser"
    CODING = "coding"
    AUTOMATION = "automation"
    PERSONALITY = "personality"
    COORDINATOR = "coordinator"  # Phase 4 — multi-agent orchestrator
    GENERIC = "generic"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Step-level types ───────────────────────────────────────────────────────────


@dataclass
class AgentStep:
    """One discrete action within an AgentPlan.

    Phase 3 (Planning Engine) additions — all optional/default-None so every
    existing plan (LLM-generated or the 1-step fallback) is unaffected:
      expected_output    — what a correct result should look like (free text,
                            surfaced to the planner/LLM; not itself checked)
      success_condition  — declarative check against World State, evaluated
                            by world_state_check.check_condition() in
                            agent_executor.py alongside the existing
                            tool-specific verifier (both must pass)
      rollback_tool/args — executed by agent_recovery.py as a last resort
                            when all retries are exhausted, to undo partial
                            effects before the step is marked FAILED
    """

    index: int
    description: str
    tool: Optional[str] = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retries: int = 0
    expected_output: Optional[str] = None
    success_condition: Optional[dict[str, Any]] = None
    rollback_tool: Optional[str] = None
    rollback_args: dict[str, Any] = field(default_factory=dict)

    def duration_s(self) -> Optional[float]:
        """Wall-clock seconds between start and completion, or None if not finished."""
        if self.started_at is not None and self.completed_at is not None:
            return self.completed_at - self.started_at
        return None

    def mark_started(self) -> None:
        self.started_at = time.time()
        self.status = StepStatus.RUNNING

    def mark_completed(self, result_text: str) -> None:
        self.completed_at = time.time()
        self.status = StepStatus.COMPLETED
        self.result = result_text

    def mark_failed(self, error_text: str) -> None:
        self.completed_at = time.time()
        self.status = StepStatus.FAILED
        self.error = error_text

    def mark_skipped(self, reason: str = "") -> None:
        self.completed_at = time.time()
        self.status = StepStatus.SKIPPED
        self.result = reason or "skipped"


# ── Plan-level types ───────────────────────────────────────────────────────────


@dataclass
class AgentPlan:
    """Multi-step plan produced by the planner."""

    goal: str
    steps: list[AgentStep] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False
    estimated_duration_s: int = 30
    metadata: dict[str, Any] = field(default_factory=dict)

    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)

    def total_steps(self) -> int:
        return len(self.steps)

    def progress_pct(self) -> int:
        total = self.total_steps()
        if total == 0:
            return 0
        return int(self.completed_count() / total * 100)


@dataclass
class StepResult:
    """Result produced by executing a single AgentStep."""

    success: bool
    output: str
    data: dict[str, Any] = field(default_factory=dict)
    needs_approval: bool = False
    approval_prompt: str = ""
    should_retry: bool = False


# ── Task-level types ───────────────────────────────────────────────────────────


@dataclass
class AgentTask:
    """
    Top-level unit of work managed by AgentRuntime.

    One task maps to one user goal and runs as a background asyncio.Task.
    The ws_send_fn field is intentionally excluded from repr to avoid
    leaking coroutine objects into log output.
    """

    task_id: str
    agent_type: AgentType
    goal: str
    status: AgentStatus = AgentStatus.PENDING
    plan: Optional[AgentPlan] = None
    current_step: int = 0
    progress_pct: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result_summary: Optional[str] = None
    error_message: Optional[str] = None
    # WebSocket send callback — async, takes a dict, returns bool (False = connection closed)
    ws_send_fn: Optional[Callable[[dict[str, Any]], Awaitable[bool]]] = field(
        default=None, repr=False
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def elapsed_s(self) -> float:
        """Seconds since task started (0 if not started yet)."""
        if self.started_at is not None:
            return time.time() - self.started_at
        return 0.0

    def is_terminal(self) -> bool:
        return self.status in (
            AgentStatus.COMPLETED,
            AgentStatus.CANCELLED,
            AgentStatus.FAILED,
        )

    def progress_text(self) -> str:
        """Human-readable progress string, suitable for voice output."""
        if self.plan is None:
            return self.status.value
        total = self.plan.total_steps()
        done = self.plan.completed_count()
        return f"Step {done}/{total}: {self.status.value}"
