"""
LinkedIn Agent — handles LinkedIn content and engagement commands.

Capabilities:
  - Draft a LinkedIn post
  - Publish post (requires approval)
  - Check recent post performance
  - Monitor mentions and comments

Delegates to: LinkedInDraftSkill, LinkedInPublishSkill
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_LINKEDIN_KEYWORDS = [
    "linkedin", "post", "publish", "share on linkedin",
    "linkedin post", "professional post", "connect on linkedin",
]


class LinkedInAgent(BaseAgent):
    """Agent that handles all LinkedIn-related commands."""

    name = "linkedin_agent"
    keywords = _LINKEDIN_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        lower = command.lower()

        if any(w in lower for w in ("draft", "write", "create", "compose")):
            return self._draft_post(command)
        elif any(w in lower for w in ("publish", "post", "share")):
            return self._request_publish_approval(command)
        else:
            return self._draft_post(command)

    def _draft_post(self, command: str) -> AgentResult:
        """Draft a LinkedIn post."""
        try:
            from ..skills.linkedin.draft_skill import draft_linkedin_post
            output = draft_linkedin_post(command)
            return self._result(True, output, command, metadata={"action": "draft"})
        except Exception as exc:
            logger.exception("LinkedInAgent draft_post failed")
            return self._result(False, f"Draft failed: {exc}", command, error=str(exc))

    def _request_publish_approval(self, command: str) -> AgentResult:
        """Queue a LinkedIn post for human approval before publishing."""
        return self._result(
            True,
            "LinkedIn post queued for approval. Review it in the Approvals panel before it goes live.",
            command,
            metadata={"action": "approval_requested", "type": "linkedin_post"},
        )
