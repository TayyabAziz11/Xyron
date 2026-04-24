"""Gmail tools — read inbox and send emails via the Gmail service."""
from __future__ import annotations

import logging
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)


def _exec_read_inbox(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    max_results = int(params.get("max_results", 5))
    try:
        from api.services.gmail_service import read_inbox, build_inbox_spoken, is_configured
        if not is_configured():
            return ToolResult(
                success=False,
                text="Gmail not configured.",
                spoken="Gmail isn't set up yet. Please add your OAuth credentials to get started.",
            )
        emails = read_inbox(max_results=max_results)
        spoken = build_inbox_spoken(emails)
        return ToolResult(
            success=True,
            text=f"Fetched {len(emails)} emails",
            spoken=spoken,
            data={"emails": emails, "count": len(emails)},
        )
    except Exception as exc:
        logger.error("read_inbox failed: %s", exc)
        return ToolResult(
            success=False,
            text=str(exc),
            spoken="I had trouble reading your inbox right now. Make sure Gmail is authorized.",
        )


def _exec_send_email(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    to      = (params.get("to") or "").strip()
    subject = (params.get("subject") or "").strip()
    body    = (params.get("body") or "").strip()
    if not to or not body:
        return ToolResult(success=False, text="Missing recipient or body.", spoken="I need a recipient and a message body to send an email.")
    try:
        from api.services.gmail_service import send_email, is_configured
        if not is_configured():
            return ToolResult(success=False, text="Gmail not configured.", spoken="Gmail isn't set up yet.")
        send_email(to=to, subject=subject or "(no subject)", body=body)
        return ToolResult(
            success=True,
            text=f"Email sent to {to}",
            spoken=f"Done — email sent to {to}.",
            data={"to": to, "subject": subject},
        )
    except Exception as exc:
        logger.error("send_email failed: %s", exc)
        return ToolResult(
            success=False,
            text=str(exc),
            spoken=f"I couldn't send the email to {to}. {exc}",
        )


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    name="read_inbox",
    definition={
        "type": "function",
        "function": {
            "name": "read_inbox",
            "description": "Read recent unread emails from Gmail inbox. Use for: 'read my emails', 'check my inbox', 'any new emails', 'what emails do I have'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Number of emails to fetch (default 5)", "default": 5},
                },
                "required": [],
            },
        },
    },
    executor=_exec_read_inbox,
    risk="low",
    category="communication",
)

registry.register(
    name="send_email",
    definition={
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email via Gmail. Use for: 'send email to X saying Y', 'email X about Y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body":    {"type": "string", "description": "Email body text"},
                },
                "required": ["to", "body"],
            },
        },
    },
    executor=_exec_send_email,
    risk="medium",
    category="communication",
)
