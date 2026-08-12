"""
Goal state machine for a single operator task execution.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class GoalStatus(Enum):
    PENDING   = auto()
    RUNNING   = auto()
    SUCCESS   = auto()
    FAILED    = auto()
    RETRYING  = auto()


@dataclass
class GoalState:
    goal:        str
    tool_name:   str
    params:      dict[str, Any]
    trace_id:    str = field(default_factory=lambda: f"VX-{uuid.uuid4().hex[:6].upper()}")
    status:      GoalStatus = GoalStatus.PENDING
    started_at:  float = field(default_factory=time.time)
    finished_at: float = 0.0
    retries:     int = 0
    max_retries: int = 3
    result:      str = ""
    error:       str = ""
    steps_log:   list[str] = field(default_factory=list)

    def mark_running(self) -> None:
        self.status = GoalStatus.RUNNING

    def mark_success(self, result: str) -> None:
        self.status     = GoalStatus.SUCCESS
        self.result     = result
        self.finished_at = time.time()

    def mark_failed(self, error: str) -> None:
        self.status     = GoalStatus.FAILED
        self.error      = error
        self.finished_at = time.time()

    def can_retry(self) -> bool:
        return self.retries < self.max_retries

    def increment_retry(self) -> None:
        self.retries += 1
        self.status   = GoalStatus.RETRYING

    def elapsed_ms(self) -> float:
        end = self.finished_at or time.time()
        return (end - self.started_at) * 1000

    def log_step(self, msg: str) -> None:
        self.steps_log.append(msg)
