"""
Phase 3 Agent Runtime — public surface.

Import from here to avoid coupling to internal module paths:

    from api.agents import agent_runtime, AgentTask, AgentStatus, AgentType, StepResult
"""
from __future__ import annotations

from api.agents.agent_types import (
    AgentPlan,
    AgentStatus,
    AgentStep,
    AgentTask,
    AgentType,
    RiskLevel,
    StepResult,
    StepStatus,
)
from api.agents.agent_runtime import agent_runtime

__all__ = [
    # Runtime singleton
    "agent_runtime",
    # Primary data types
    "AgentTask",
    "AgentPlan",
    "AgentStep",
    "AgentStatus",
    "AgentType",
    "StepResult",
    "StepStatus",
    "RiskLevel",
]
