"""
Shared contract between brain/agents (Tayyab) and tool executors (Qasim).

Brain decides WHAT.  Tools decide HOW.

No agent may import tool executors directly.
All cross-boundary calls go through ToolRequest → ToolResult.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


RiskLevel = str  # "low" | "medium" | "high"
AutonomyLevel = int  # 0–4


@dataclass
class ToolRequest:
    tool_name: str
    intent: str
    params: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = "low"
    requires_confirmation: bool = False
    source_agent: str = "orchestrator"
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.risk_level not in ("low", "medium", "high"):
            raise ValueError(f"risk_level must be low|medium|high, got {self.risk_level!r}")
        if self.risk_level == "high":
            self.requires_confirmation = True


@dataclass
class ToolResult:
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)
    requires_followup: bool = False

    @classmethod
    def failure(cls, reason: str, **kwargs: Any) -> "ToolResult":
        return cls(success=False, message=reason, **kwargs)

    @classmethod
    def ok(cls, message: str, data: dict[str, Any] | None = None, **kwargs: Any) -> "ToolResult":
        return cls(success=True, message=message, data=data or {}, **kwargs)


class ToolDispatcher:
    """
    Receives ToolRequests from agents, enforces autonomy gate, dispatches to registry.
    Owned by both developers — modify only via PR with both reviewers.
    """

    def __init__(self, registry: Any, autonomy_level: AutonomyLevel = 1) -> None:
        self._registry = registry
        self.autonomy_level = autonomy_level

    def dispatch(self, request: ToolRequest) -> ToolResult:
        t0 = time.monotonic()

        if request.requires_confirmation and not self._is_approved(request):
            return ToolResult.failure(
                "Pending user confirmation",
                warnings=[f"Tool '{request.tool_name}' queued for approval"],
            )

        executor = self._registry.get(request.tool_name)
        if executor is None:
            return ToolResult.failure(f"Unknown tool: {request.tool_name}")

        try:
            result: ToolResult = executor(request.params, request.context)
        except Exception as exc:
            result = ToolResult.failure(str(exc))

        result.latency_ms = (time.monotonic() - t0) * 1000
        return result

    def _is_approved(self, request: ToolRequest) -> bool:
        if request.risk_level == "high":
            return False
        if request.risk_level == "medium" and self.autonomy_level < 3:
            return False
        return True
