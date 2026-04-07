"""
BaseAgent — abstract interface that every agent must implement.

Design principles:
- Agents receive a plain-text command (or structured payload) and return a result.
- Agents coordinate skills; they do not implement business logic directly.
- Agents are stateless by default — state lives in services or the filesystem.
- Every agent must implement: `can_handle`, `run`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class AgentResult:
    """Structured result returned by any agent execution."""
    success: bool
    output: str
    agent: str
    command: str
    duration_ms: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z"
    )


class BaseAgent(ABC):
    """Abstract base class for all AI Operator agents."""

    #: Human-readable name, used in logs and API responses
    name: str = "base_agent"

    #: Trigger keywords — used by the router to match commands
    keywords: List[str] = []

    @abstractmethod
    def can_handle(self, command: str) -> bool:
        """
        Return True if this agent can handle the given command text.

        The router calls this on all registered agents and picks the first match.
        """

    @abstractmethod
    def run(self, command: str, **kwargs: Any) -> AgentResult:
        """
        Execute the command and return a structured result.

        Args:
            command: Natural language command string
            **kwargs: Optional context (user_id, session_id, etc.)

        Returns:
            AgentResult with success flag, output text, and metadata
        """

    def _result(
        self,
        success: bool,
        output: str,
        command: str,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        """Convenience factory for building AgentResult."""
        return AgentResult(
            success=success,
            output=output,
            agent=self.name,
            command=command,
            error=error,
            metadata=metadata or {},
        )
