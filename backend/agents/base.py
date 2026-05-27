"""
Agent base classes — no LangChain, no AutoGen, no LlamaIndex.

Every agent implements:
  can_handle(frame, brain_state) -> float    0.0-1.0 routing score
  plan(frame, brain_state)       -> AgentPlan
  execute(plan, context)         -> AgentResult
  summarize_result(result)       -> str      short spoken sentence
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """Runtime context passed to agents during execution."""
    transcript:         str
    intent:             str
    entities:           dict[str, Any]       = field(default_factory=dict)
    mood_state:         str                  = "neutral"
    autonomy_level:     int                  = 2
    operator_name:      str                  = "Tayyab"
    session_history:    list[dict[str, Any]] = field(default_factory=list)
    extra:              dict[str, Any]       = field(default_factory=dict)


@dataclass
class PlanStep:
    """A single step within an AgentPlan."""
    index:       int
    description: str
    tool:        Optional[str]          = None
    tool_args:   dict[str, Any]         = field(default_factory=dict)
    agent:       Optional[str]          = None
    risk:        str                    = "low"   # "low" | "medium" | "high"
    done:        bool                   = False
    result:      Optional[str]          = None


@dataclass
class AgentPlan:
    """Multi-step plan produced by agent.plan()."""
    goal:                 str
    steps:                list[PlanStep]      = field(default_factory=list)
    risk_level:           str                 = "low"     # "low" | "medium" | "high"
    requires_confirmation: bool               = False
    rollback:             list[dict[str, Any]] = field(default_factory=list)
    estimated_duration_ms: int               = 0


@dataclass
class AgentResult:
    """Output of agent.execute()."""
    success:       bool
    output:        Any                   = None
    spoken:        str                   = ""          # short spoken summary
    error:         Optional[str]         = None
    tool_used:     Optional[str]         = None
    confidence:    float                 = 1.0
    metadata:      dict[str, Any]        = field(default_factory=dict)
    # Frontend action event — if set, voice.py emits type:action SSE instead of type:chunk
    action:        Optional[str]         = None
    action_params: dict[str, Any]        = field(default_factory=dict)


# ── BaseAgent ─────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    All Xyron brain agents extend this class.

    Subclasses must set:
      id, name, description, capabilities
    And implement:
      can_handle, plan, execute
    """

    id:           str = ""
    name:         str = ""
    description:  str = ""
    capabilities: list[str] = []

    def __init__(self) -> None:
        self._log = logging.getLogger(f"agent.{self.id}")

    @abstractmethod
    def can_handle(self, frame: Any, brain_state: Any) -> float:
        """
        Return a routing score 0.0–1.0.
        0.0 = cannot handle, 1.0 = perfect match.
        """
        ...

    @abstractmethod
    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        """Build an execution plan for the given semantic frame."""
        ...

    @abstractmethod
    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        """Execute the plan and return a result."""
        ...

    def summarize_result(self, result: AgentResult) -> str:
        """Return a short spoken sentence summarising the result."""
        if result.spoken:
            return result.spoken
        if result.success:
            return "Done."
        return f"I ran into an issue: {result.error or 'unknown error'}."

    def __repr__(self) -> str:
        return f"<Agent id={self.id!r} name={self.name!r}>"
