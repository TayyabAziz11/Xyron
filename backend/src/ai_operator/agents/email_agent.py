"""
Email Agent — handles email-related commands.

Capabilities:
  - Summarize unread emails
  - Draft reply or new email
  - Send email (requires approval for external sends)
  - Query inbox

Delegates to: EmailSkill, ApprovalSkill
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_EMAIL_KEYWORDS = [
    "email", "gmail", "inbox", "unread", "draft", "send email",
    "reply", "mail", "message to", "write to",
]


class EmailAgent(BaseAgent):
    """Agent that handles all email-related commands."""

    name = "email_agent"
    keywords = _EMAIL_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        lower = command.lower()

        # Route to appropriate skill
        if any(w in lower for w in ("unread", "inbox", "summarize", "summary", "check")):
            return self._summarize_inbox(command)
        elif any(w in lower for w in ("draft", "write", "compose")):
            return self._draft_email(command)
        elif "send" in lower:
            return self._request_send_approval(command)
        else:
            return self._summarize_inbox(command)

    def _summarize_inbox(self, command: str) -> AgentResult:
        """Summarize unread emails using GmailAPIHelper."""
        try:
            from ..skills.email.summarize_skill import summarize_inbox
            output = summarize_inbox()
            return self._result(True, output, command)
        except Exception as exc:
            logger.exception("EmailAgent summarize_inbox failed")
            return self._result(
                False,
                "Unable to access inbox. Check Gmail credentials in .secrets/.",
                command,
                error=str(exc),
            )

    def _draft_email(self, command: str) -> AgentResult:
        """Draft an email based on the command."""
        try:
            from ..skills.email.draft_skill import draft_email
            output = draft_email(command)
            return self._result(True, output, command, metadata={"action": "draft"})
        except Exception as exc:
            logger.exception("EmailAgent draft_email failed")
            return self._result(False, f"Draft failed: {exc}", command, error=str(exc))

    def _request_send_approval(self, command: str) -> AgentResult:
        """Queue an email send for human approval."""
        return self._result(
            True,
            "Email send queued for approval. Check the Approvals panel to review and confirm.",
            command,
            metadata={"action": "approval_requested", "type": "email_send"},
        )
