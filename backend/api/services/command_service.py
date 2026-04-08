"""
Command Service — in-memory command queue with background execution.

NOTE: Commands are stored in a Python dict (in-memory only).
This is intentional for v1 — persistence will be added in a future iteration.
Restart clears all command history.
"""
from __future__ import annotations

import datetime
import logging
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime as dt, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..schemas.command import Command, CommandIntent, CommandStatus

logger = logging.getLogger(__name__)

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


# ── Skill dispatch ───────────────────────────────────────────────────────────

def _ensure_src_path() -> None:
    """Make sure backend/src is on sys.path for ai_operator imports."""
    src_path = Path(__file__).parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def _run_email_skill(text: str, skill: str) -> str:
    try:
        _ensure_src_path()
        from ai_operator.core.content_generator import ContentGenerator
        gen = ContentGenerator()
        if "draft" in skill or "draft" in text.lower():
            prompt = f"Draft a professional email based on this request: {text}"
            return gen.generate_text(prompt)
        elif "summarize" in skill or "summary" in text.lower():
            return f"Email summary requested: {text}\n[Connect Gmail to fetch real emails]"
        else:
            return f"Email skill: {skill}\nRequest: {text}\n[Gmail integration required for execution]"
    except Exception as exc:
        return (
            f"Email agent received: {text}\n"
            f"[Skill: {skill} — OpenAI key required for AI drafting]\n"
            f"Detail: {exc}"
        )


def _run_linkedin_skill(text: str, skill: str) -> str:
    try:
        _ensure_src_path()
        from ai_operator.core.content_generator import ContentGenerator
        gen = ContentGenerator()
        prompt = f"Write a professional LinkedIn post based on: {text}"
        return gen.generate_text(prompt)
    except Exception as exc:
        return (
            f"LinkedIn post draft requested: {text}\n"
            f"[Skill: {skill} — OpenAI key required]\n"
            f"Detail: {exc}"
        )


def _run_reporting_skill(text: str, skill: str) -> str:
    now = dt.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"Reporting skill: {skill}\n"
        f"Request: {text}\n\n"
        f"Summary generated at {now}\n"
        "[Connect activity logs and integrations for real data]"
    )


def _run_integration_skill(text: str, skill: str) -> str:
    return (
        f"Integration status check requested.\n"
        f"Request: {text}\n"
        "[See /app/integrations for current status]"
    )


def _run_approval_skill(text: str, skill: str) -> str:
    return (
        f"Approval workflow triggered.\n"
        f"Request: {text}\n"
        "[Check /app/approvals for pending items]"
    )


def _dispatch_to_skill(text: str, intent: CommandIntent) -> str:
    """Route to real skill implementation or return structured placeholder."""
    agent = intent.agent
    skill = intent.skill

    if agent == "email":
        return _run_email_skill(text, skill)
    elif agent == "linkedin":
        return _run_linkedin_skill(text, skill)
    elif agent == "reporting":
        return _run_reporting_skill(text, skill)
    elif agent == "integration":
        return _run_integration_skill(text, skill)
    elif agent == "approval":
        return _run_approval_skill(text, skill)
    else:
        return (
            f"Command received: {text!r}\n"
            f"Agent: {agent} | Skill: {skill}\n\n"
            "[General query — connect OpenAI key to enable AI responses]"
        )


# ── Service ──────────────────────────────────────────────────────────────────

_executor = ThreadPoolExecutor(max_workers=4)


class CommandService:
    """In-memory command store with FIFO eviction (max 200 commands)
    and background execution via thread pool.
    """

    _MAX_SIZE = 200

    def __init__(self) -> None:
        self._store: OrderedDict[str, Command] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, text: str) -> Command:
        """Create, classify intent, queue and kick off background execution."""
        intent = classify_intent(text)
        cmd = Command(text=text, status=CommandStatus.queued, intent=intent)

        with self._lock:
            self._store[cmd.id] = cmd
            # Evict oldest if over capacity
            while len(self._store) > self._MAX_SIZE:
                self._store.popitem(last=False)

        # Execute in background thread
        _executor.submit(self._execute, cmd.id, text, intent)

        return cmd

    def _execute(self, command_id: str, text: str, intent: CommandIntent) -> None:
        """Run in thread pool — updates command store with result."""
        try:
            self.update_status(command_id, CommandStatus.running)
            result = _dispatch_to_skill(text, intent)
            self.update_status(command_id, CommandStatus.completed, result=result)
        except Exception as exc:
            logger.exception("Command %s execution failed", command_id)
            self.update_status(command_id, CommandStatus.failed, error=str(exc))

    def get(self, command_id: str) -> Optional[Command]:
        return self._store.get(command_id)

    def list_recent(self, limit: int = 20) -> List[Command]:
        """Return most recent commands, newest first."""
        with self._lock:
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
        with self._lock:
            cmd = self._store.get(command_id)
            if not cmd:
                return None
            cmd.status = status
            cmd.updated_at = dt.now(timezone.utc).isoformat() + "Z"
            if result is not None:
                cmd.result = result
            if error is not None:
                cmd.error = error
        return cmd


# Module-level singleton
command_service = CommandService()
