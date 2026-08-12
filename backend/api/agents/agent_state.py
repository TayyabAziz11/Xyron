"""
State machine for AgentTask lifecycle transitions.

Valid transitions:
  PENDING           → PLANNING, CANCELLED
  PLANNING          → RUNNING, FAILED, CANCELLED
  RUNNING           → PAUSED, WAITING_APPROVAL, RECOVERING, COMPLETED, FAILED, CANCELLED
  PAUSED            → RUNNING, CANCELLED
  WAITING_APPROVAL  → RUNNING, CANCELLED
  RECOVERING        → RUNNING, FAILED, CANCELLED
  COMPLETED         → (terminal)
  FAILED            → (terminal)
  CANCELLED         → (terminal)
"""
from __future__ import annotations

import logging
import time

from api.agents.agent_types import AgentStatus, AgentTask

logger = logging.getLogger("api.agents.state")

# Transition table: current status → set of valid next statuses
_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.PENDING: frozenset({
        AgentStatus.PLANNING,
        AgentStatus.CANCELLED,
    }),
    AgentStatus.PLANNING: frozenset({
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    }),
    AgentStatus.RUNNING: frozenset({
        AgentStatus.PAUSED,
        AgentStatus.WAITING_APPROVAL,
        AgentStatus.RECOVERING,
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    }),
    AgentStatus.PAUSED: frozenset({
        AgentStatus.RUNNING,
        AgentStatus.CANCELLED,
    }),
    AgentStatus.WAITING_APPROVAL: frozenset({
        AgentStatus.RUNNING,
        AgentStatus.CANCELLED,
    }),
    AgentStatus.RECOVERING: frozenset({
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    }),
    # Terminal states — no outgoing transitions
    AgentStatus.COMPLETED: frozenset(),
    AgentStatus.FAILED: frozenset(),
    AgentStatus.CANCELLED: frozenset(),
}

_TERMINAL: frozenset[AgentStatus] = frozenset({
    AgentStatus.COMPLETED,
    AgentStatus.FAILED,
    AgentStatus.CANCELLED,
})

_INTERRUPTIBLE: frozenset[AgentStatus] = frozenset({
    AgentStatus.RUNNING,
    AgentStatus.PAUSED,
    AgentStatus.WAITING_APPROVAL,
})


class AgentStateMachine:
    """
    Validates and applies status transitions for an AgentTask.

    All state mutations go through transition() so the constraint table
    is the single source of truth — no scattered status assignments.
    """

    def transition(self, task: AgentTask, new_status: AgentStatus) -> None:
        """
        Validate and apply a status transition in-place.

        Raises ValueError if the transition is not permitted.
        Updates task.status, and sets task.started_at / task.completed_at
        at the appropriate lifecycle points.
        """
        current = task.status
        allowed = _TRANSITIONS.get(current, frozenset())

        if new_status not in allowed:
            raise ValueError(
                f"Invalid agent state transition: {current.value!r} → {new_status.value!r} "
                f"(task={task.task_id}). Allowed: {[s.value for s in allowed]}"
            )

        logger.debug(
            "[STATE] task=%s %s → %s",
            task.task_id,
            current.value,
            new_status.value,
        )

        now = time.time()

        # Lifecycle timestamps
        if new_status == AgentStatus.RUNNING and task.started_at is None:
            task.started_at = now
        if new_status in _TERMINAL and task.completed_at is None:
            task.completed_at = now

        task.status = new_status

    def is_terminal(self, status: AgentStatus) -> bool:
        """Return True if the status is a terminal (no further transitions possible)."""
        return status in _TERMINAL

    def can_interrupt(self, status: AgentStatus) -> bool:
        """Return True if the task can be paused or cancelled from this status."""
        return status in _INTERRUPTIBLE

    def valid_transitions(self, status: AgentStatus) -> list[AgentStatus]:
        """Return the list of valid next statuses from the given status."""
        return list(_TRANSITIONS.get(status, frozenset()))


# Module-level singleton for convenience
agent_state_machine = AgentStateMachine()
