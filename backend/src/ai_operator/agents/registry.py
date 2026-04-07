"""
Agent Registry — registers all agents with the command router.

Import this module once at application startup to make all agents available.
The router singleton is shared across the process.

To add a new agent:
  1. Create the agent class (subclass BaseAgent)
  2. Import and register it here
  3. The router will automatically include it in command routing
"""
from __future__ import annotations

import logging

from .router import router
from .email_agent import EmailAgent
from .linkedin_agent import LinkedInAgent
from .reporting_agent import ReportingAgent
from .integration_agent import IntegrationAgent
from .approval_agent import ApprovalAgent

logger = logging.getLogger(__name__)


def register_all() -> None:
    """
    Register all agents in priority order.

    More specific agents (single-domain) are registered first.
    The fallback/catch-all agents go last.
    """
    router.register(EmailAgent())
    router.register(LinkedInAgent())
    router.register(ApprovalAgent())
    router.register(IntegrationAgent())
    router.register(ReportingAgent())  # ReportingAgent is last — its keywords are broad

    logger.info(
        "Agent registry initialized. %d agents active: %s",
        len(router.list_agents()),
        ", ".join(router.list_agents()),
    )


# Auto-register on import
register_all()
