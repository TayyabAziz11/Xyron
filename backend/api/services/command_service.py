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
    # ── Confirmation / cancellation (must be first) ──────────────────────────
    (["post it", "publish it", "go ahead", "yes confirm", "confirm it", "do it", "send it"], "confirm", "confirm_draft"),
    (["cancel it", "reject it", "don't send", "no cancel", "discard it", "never mind"], "cancel", "reject_draft"),
    # ── Specific platforms ────────────────────────────────────────────────────
    (["linkedin", "li post", "li article"], "linkedin", "linkedin_draft"),
    (["whatsapp", "wa message", "wa chat"], "whatsapp", "whatsapp_send"),
    (["instagram", "ig post", "ig story"], "instagram", "instagram_draft"),
    (["odoo", "invoice", "accounting", "erp"], "odoo", "odoo_query"),
    # Email — generic action words after specific platforms
    (["email", "mail", "inbox", "gmail", "reply email"], "email", "email_draft"),
    # Workflow actions
    (["approval", "approve", "pending", "review"], "approval", "list_approvals"),
    (["summary", "summarize", "report", "briefing"], "reporting", "daily_summary"),
    (["workflow", "task", "process"], "workflow", "list_workflows"),
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


def _run_email_skill(text: str, skill: str, command_id: str) -> dict:
    """Generate email draft and store it."""
    import re

    recipient = ""
    subject = f"Follow-up: {text[:40]}"

    # Try to extract "to [Name]" from command
    m = re.search(r'\bto\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', text)
    if m:
        recipient = m.group(1)
        subject = f"Message for {recipient}"

    content: Optional[str] = None
    try:
        _ensure_src_path()
        from ai_operator.core.content_generator import ContentGenerator
        gen = ContentGenerator()
        content = gen.generate_linkedin_post(
            topic=f"Professional email: {text}",
            tone="professional",
        )
        if not isinstance(content, str):
            content = str(content)
    except Exception:
        content = (
            f"Subject: {subject}\n\n"
            f"Dear {recipient or 'Team'},\n\n"
            f"I wanted to reach out regarding: {text}\n\n"
            f"[Add OPENAI_API_KEY to generate AI-written emails]\n\n"
            f"Best regards"
        )

    from .draft_service import draft_service
    draft = draft_service.create(
        command_id=command_id,
        draft_type="email",
        title=subject,
        content=content,
        metadata={"to": recipient, "subject": subject},
    )

    return {
        "result": f"Email draft created:\n\n{content}",
        "draft_id": draft.id,
        "draft_type": "email",
        "action_hint": "send it",
    }


def _run_linkedin_skill(text: str, skill: str, command_id: str) -> dict:
    """Generate LinkedIn post draft and store it."""
    content: Optional[str] = None
    try:
        _ensure_src_path()
        from ai_operator.core.content_generator import ContentGenerator
        gen = ContentGenerator()
        content = gen.generate_linkedin_post(topic=text, tone="professional")
        if not isinstance(content, str):
            content = str(content)
    except Exception:
        content = (
            f"🚀 Exciting update about: {text}\n\n"
            f"[AI-generated content — add OPENAI_API_KEY to backend/.env for real drafts]\n\n"
            f"#AI #Innovation #AIOperator"
        )

    from .draft_service import draft_service
    draft = draft_service.create(
        command_id=command_id,
        draft_type="linkedin_post",
        title=f"LinkedIn Post: {text[:50]}",
        content=content,
    )

    return {
        "result": f"LinkedIn draft created:\n\n{content}",
        "draft_id": draft.id,
        "draft_type": "linkedin_post",
        "action_hint": "post it",
    }


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


def _run_confirm_draft(command_id: str) -> dict:
    """Confirm and execute the most recent pending draft."""
    from .draft_service import draft_service
    from .draft_executor import execute_draft

    pending = draft_service.list_pending()
    if not pending:
        return {"result": "No pending drafts to confirm.", "draft_id": None}

    # Execute the most recent one (list_pending returns all; pick first created_at desc)
    latest = sorted(pending, key=lambda d: d.created_at, reverse=True)[0]
    result = execute_draft(latest.id)

    return {
        "result": result.get("message", "Action executed."),
        "draft_id": latest.id,
        "execution_result": result,
        "spoken": result.get("spoken", "Done!"),
    }


def _run_reject_draft(command_id: str) -> dict:
    """Cancel the most recent pending draft."""
    from .draft_service import draft_service

    pending = draft_service.list_pending()
    if not pending:
        return {"result": "No pending drafts to cancel."}
    latest = sorted(pending, key=lambda d: d.created_at, reverse=True)[0]
    draft_service.reject(latest.id)
    return {"result": "Draft cancelled.", "draft_id": latest.id}


def _dispatch_to_skill(text: str, intent: CommandIntent, command_id: str) -> dict | str:
    """Route to real skill implementation or return structured placeholder."""
    agent = intent.agent
    skill = intent.skill

    if agent == "confirm":
        return _run_confirm_draft(command_id)
    elif agent == "cancel":
        return _run_reject_draft(command_id)
    elif agent == "email":
        return _run_email_skill(text, skill, command_id)
    elif agent == "linkedin":
        return _run_linkedin_skill(text, skill, command_id)
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
            result_data = _dispatch_to_skill(text, intent, command_id)

            # Handle both string and dict returns
            if isinstance(result_data, dict):
                result_text = result_data.get("result", "")
                draft_id    = result_data.get("draft_id")
                action_hint = result_data.get("action_hint", "")
                # Use execution spoken override if present (for confirm flows)
                spoken_override = result_data.get("spoken")
            else:
                result_text     = result_data
                draft_id        = None
                action_hint     = ""
                spoken_override = None

            # Generate spoken assistant response
            assistant_resp: Optional[str] = None
            try:
                _voice_root = Path(__file__).parent.parent.parent
                if str(_voice_root) not in sys.path:
                    sys.path.insert(0, str(_voice_root))
                from voice.response_generator import generate_assistant_response
                assistant_resp = generate_assistant_response(
                    command_text=text,
                    result=result_text,
                    agent=intent.agent,
                    skill=intent.skill,
                    draft_id=draft_id,
                    action_hint=action_hint,
                )
            except Exception as rg_exc:
                logger.debug("Response generator failed (non-fatal): %s", rg_exc)

            # Allow executor to override the spoken response (e.g. on confirm)
            if spoken_override:
                assistant_resp = spoken_override

            self.update_status(
                command_id,
                CommandStatus.completed,
                result=result_text,
                assistant_response=assistant_resp,
                draft_id=draft_id,
            )
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
        assistant_response: Optional[str] = None,
        draft_id: Optional[str] = None,
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
            if assistant_response is not None:
                cmd.assistant_response = assistant_response
            if draft_id is not None:
                cmd.draft_id = draft_id
        return cmd


# Module-level singleton
command_service = CommandService()
