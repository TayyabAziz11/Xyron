"""
Command Service — in-memory command queue.

NOTE: Commands are stored in a Python dict (in-memory only).
This is intentional for v1 — persistence will be added in a future iteration.
Restart clears all command history.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..schemas.command import Command, CommandIntent, CommandStatus


# ── Intent classification ────────────────────────────────────────────────────
INTENT_PATTERNS: list[tuple[list[str], str, str]] = [
    (["email", "mail", "inbox", "message", "send", "draft", "reply"], "email", "email_draft"),
    (["linkedin", "post", "publish", "content", "article"], "linkedin", "linkedin_draft"),
    (["approval", "approve", "pending", "review"], "approval", "list_approvals"),
    (["summary", "summarize", "report", "briefing"], "reporting", "daily_summary"),
    (["workflow", "task", "process", "run"], "workflow", "list_workflows"),
    (["integration", "status", "health", "check"], "integration", "integration_status"),
    (["activity", "log", "history", "audit"], "activity", "activity_summary"),
]


def classify_intent(text: str) -> CommandIntent:
    """Simple keyword-based intent classifier."""
    text_lower = text.lower()
    for keywords, agent, skill in INTENT_PATTERNS:
        if any(kw in text_lower for kw in keywords):
            return CommandIntent(agent=agent, skill=skill, confidence="keyword_match")
    return CommandIntent(agent="general", skill="general_query", confidence="fallback")


# ── Service ──────────────────────────────────────────────────────────────────
class CommandService:
    """In-memory command store with FIFO eviction (max 200 commands)."""

    _MAX_SIZE = 200

    def __init__(self) -> None:
        self._store: OrderedDict[str, Command] = OrderedDict()

    def submit(self, text: str) -> Command:
        """Create, classify intent, and queue a new command."""
        intent = classify_intent(text)
        cmd = Command(text=text, status=CommandStatus.queued, intent=intent)
        self._store[cmd.id] = cmd

        # Evict oldest if over capacity
        while len(self._store) > self._MAX_SIZE:
            self._store.popitem(last=False)

        return cmd

    def get(self, command_id: str) -> Optional[Command]:
        return self._store.get(command_id)

    def list_recent(self, limit: int = 20) -> List[Command]:
        """Return most recent commands, newest first."""
        all_commands = list(self._store.values())
        all_commands.reverse()
        return all_commands[:limit]

    def update_status(
        self,
        command_id: str,
        status: CommandStatus,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[Command]:
        """Update command status — called by agent execution layer."""
        cmd = self._store.get(command_id)
        if not cmd:
            return None
        cmd.status = status
        cmd.updated_at = datetime.now(timezone.utc).isoformat() + "Z"
        if result is not None:
            cmd.result = result
        if error is not None:
            cmd.error = error
        return cmd


# Module-level singleton
command_service = CommandService()
