"""
Command Router — dispatches natural language commands to the appropriate agent.

Flow:
  1. API receives command text via POST /api/v1/commands
  2. Router iterates registered agents, calls agent.can_handle(command)
  3. First matching agent calls agent.run(command)
  4. Result is stored back on the Command record

New agents plug in by:
  - Subclassing BaseAgent
  - Implementing can_handle() and run()
  - Registering via router.register(MyAgent())
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)


class CommandRouter:
    """
    Routes incoming commands to registered agents.

    Agent priority is determined by registration order — first registered,
    first checked. Agents with more specific can_handle() logic should be
    registered before catch-all agents.
    """

    def __init__(self) -> None:
        self._agents: List[BaseAgent] = []

    def register(self, agent: BaseAgent) -> None:
        """Register an agent. Registration order determines priority."""
        self._agents.append(agent)
        logger.info("Registered agent: %s", agent.name)

    def route(self, command: str, **kwargs) -> AgentResult:
        """
        Dispatch command to the best matching agent.

        Returns a fallback result if no agent can handle it.
        """
        command = command.strip()

        for agent in self._agents:
            try:
                if agent.can_handle(command):
                    logger.info("Routing '%s...' → %s", command[:40], agent.name)
                    start = time.monotonic()
                    result = agent.run(command, **kwargs)
                    result.duration_ms = int((time.monotonic() - start) * 1000)
                    return result
            except Exception as exc:
                logger.exception("Agent %s raised during can_handle: %s", agent.name, exc)
                continue

        logger.warning("No agent matched command: '%s...'", command[:60])
        return AgentResult(
            success=False,
            output="No agent could handle this command. Please be more specific or try a different phrasing.",
            agent="router",
            command=command,
            error="no_agent_matched",
        )

    def list_agents(self) -> List[str]:
        """Return names of all registered agents."""
        return [a.name for a in self._agents]

    def route_direct(self, agent_name: str, command: str, **kwargs) -> Optional[AgentResult]:
        """
        Route directly to a named agent — bypasses the can_handle() loop.
        Returns None if no agent with that name is registered.
        Used by brain/planner.py to delegate goal decomposition to planner_agent.
        """
        for agent in self._agents:
            if agent.name == agent_name:
                logger.info("Direct route '%s...' → %s", command[:40], agent_name)
                start = time.monotonic()
                result = agent.run(command, **kwargs)
                result.duration_ms = int((time.monotonic() - start) * 1000)
                return result
        logger.warning("route_direct: agent %r not registered", agent_name)
        return None


# Module-level singleton — import and use this everywhere
router = CommandRouter()
