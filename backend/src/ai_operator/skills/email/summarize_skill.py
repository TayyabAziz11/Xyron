"""
Email Summarize Skill — summarize unread Gmail messages.

Public API:
    summarize_inbox(max_results=10) -> str
        Returns a formatted markdown summary of the most recent unread emails.
"""
from __future__ import annotations

from typing import Optional


def summarize_inbox(max_results: int = 10) -> str:
    """
    Fetch and summarize unread emails from Gmail.

    Returns a formatted string describing recent unread messages.
    Raises on credential failure so the caller (EmailAgent) can handle it gracefully.
    """
    from ...core.gmail_api_helper import GmailAPIHelper

    helper = GmailAPIHelper()
    service = helper.get_service()

    # Fetch unread messages
    result = service.users().messages().list(
        userId="me",
        q="is:unread",
        maxResults=max_results,
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return "No unread emails found."

    lines = [f"**{len(messages)} unread email(s):**\n"]
    for msg_ref in messages[:max_results]:
        msg = service.users().messages().get(
            userId="me",
            id=msg_ref["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender = headers.get("From", "unknown")
        date = headers.get("Date", "")

        lines.append(f"- **{subject}** from {sender} ({date})")

    return "\n".join(lines)
