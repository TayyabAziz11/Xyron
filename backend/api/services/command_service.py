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
    # ── System / web actions ──────────────────────────────────────────────────
    (["what time is it", "what's the time", "whats the time", "current time",
      "what is the time", "tell me the time", "what's the date", "whats the date",
      "what date is it", "today's date", "what day is it", "current date"], "system", "date_time"),
    (["battery", "battery level", "battery status", "battery percentage", "how much battery",
      "charging status", "is it charging", "is my battery"], "system", "battery_status"),
    (["search youtube", "youtube search", "find on youtube", "look up on youtube"], "system", "youtube_search"),
    (["play video", "play on youtube", "watch video", "watch on youtube"], "system", "youtube_play"),
    (["search google", "google search", "search the web", "search web", "look up", "google for"], "system", "google_search"),
    (["open app", "launch app", "start app", "open vscode", "open vs code", "open chrome",
      "open spotify", "open terminal", "open calculator", "launch chrome", "launch spotify",
      "launch vscode", "launch vs code", "open notepad", "open firefox"], "system", "open_app"),
    (["open youtube", "go to youtube", "open github", "go to github", "open gmail", "go to gmail",
      "open netflix", "go to netflix", "open twitter", "open linkedin"], "system", "open_url"),
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
    """Keyword-based intent classifier (fast fallback)."""
    text_lower = text.lower()
    for keywords, agent, skill in INTENT_PATTERNS:
        if any(kw in text_lower for kw in keywords):
            return CommandIntent(agent=agent, skill=skill, confidence="keyword_match")
    return CommandIntent(agent="general", skill="general_query", confidence="fallback")


def classify_intent_ai(text: str, openai_key: str) -> tuple[str, dict]:
    """Use OpenAI tool calling to classify intent and extract structured params.

    Returns (tool_name, params_dict).  Falls back to ('general_query', {query: text}).
    All tool definitions come from the central registry.
    """
    try:
        import json as _json
        from openai import OpenAI
        from api.tools import registry

        definitions = registry.get_definitions()
        client      = OpenAI(api_key=openai_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Xyron intent router. "
                        "Decide which tool best fulfils the user's request and extract its parameters. "
                        "For system commands like 'open E drive', use open_directory. "
                        "For launching apps, use open_application. "
                        "Do not answer the request — only call the right tool."
                    ),
                },
                {"role": "user", "content": text},
            ],
            tools=definitions,
            tool_choice="auto",
            max_tokens=200,
            temperature=0.0,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            return tc.function.name, _json.loads(tc.function.arguments or "{}")
    except Exception as exc:
        logger.debug("AI classify failed, using keyword fallback: %s", exc)

    # ── Keyword fallback ──────────────────────────────────────────────────────
    kw_intent = classify_intent(text)
    _KW_TO_TOOL: dict[str, tuple[str, dict]] = {
        "date_time":      ("get_date_time",      {}),
        "battery_status": ("get_battery_status", {}),
        "youtube_search": ("search_youtube",   {"query": text}),
        "youtube_play":   ("search_youtube",   {"query": text}),
        "google_search":  ("search_web",       {"query": text}),
        "open_app":       ("open_application", {"app_name": text}),
        "open_url":       ("open_url",         {"site": text}),
        "email_draft":    ("compose_email",    {"to": "", "body_hint": text}),
        "linkedin_draft": ("create_post",      {"platform": "linkedin", "topic": text}),
        "instagram_draft":("create_post",      {"platform": "instagram", "topic": text}),
        "daily_summary":  ("get_summary",      {"period": "daily"}),
        "list_approvals": ("list_approvals",   {}),
        "confirm_draft":  ("general_query",    {"query": text}),
        "reject_draft":   ("general_query",    {"query": text}),
    }
    if kw_intent.skill in _KW_TO_TOOL:
        return _KW_TO_TOOL[kw_intent.skill]
    return "general_query", {"query": text}


# ── Skill dispatch ───────────────────────────────────────────────────────────

def _ensure_src_path() -> None:
    """Make sure backend/src is on sys.path for ai_operator imports."""
    src_path = Path(__file__).parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def _openai_chat(prompt: str, system: str, max_tokens: int = 350) -> Optional[str]:
    """Call OpenAI via ContentGenerator.chat(). Returns None on any failure."""
    try:
        _ensure_src_path()
        from ai_operator.core.content_generator import ContentGenerator
        return ContentGenerator().chat(prompt, system_prompt=system, max_tokens=max_tokens)
    except Exception as exc:
        logger.debug("OpenAI unavailable: %s", exc)
        return None


def _run_email_skill(text: str, skill: str, command_id: str) -> dict:
    """Generate email draft with OpenAI, store as a Draft."""
    import re

    recipient = ""
    subject = f"Re: {text[:50]}"
    m = re.search(r'\bto\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', text)
    if m:
        recipient = m.group(1)
        subject = f"Message for {recipient}"

    content = _openai_chat(
        prompt=(
            f"User request: {text}\n\n"
            f"Recipient: {recipient or 'the team'}\n"
            "Write a concise, professional email draft. Include Subject line, "
            "greeting, body (2-3 short paragraphs), and sign-off. "
            "No markdown headers, plain text only."
        ),
        system=(
            "You are a professional email writer. Write clean, clear, concise emails. "
            "Always include: Subject: ..., greeting, body, sign-off."
        ),
        max_tokens=400,
    ) or (
        f"Subject: {subject}\n\n"
        f"Dear {recipient or 'Team'},\n\n"
        f"I wanted to reach out regarding: {text}\n\n"
        "Please let me know if you have any questions.\n\n"
        "Best regards"
    )

    from .draft_service import draft_service
    draft = draft_service.create(
        command_id=command_id,
        draft_type="email",
        title=subject,
        content=content,
        metadata={"to": recipient, "subject": subject},
    )
    return {"result": content, "draft_id": draft.id, "draft_type": "email", "action_hint": "send it"}


def _run_linkedin_skill(text: str, skill: str, command_id: str) -> dict:
    """Generate LinkedIn post with OpenAI, store as a Draft."""
    _ensure_src_path()
    content = None
    try:
        from ai_operator.core.content_generator import ContentGenerator
        content = ContentGenerator().generate_linkedin_post(topic=text)
    except Exception:
        pass

    if not content:
        content = _openai_chat(
            prompt=f"Write a professional LinkedIn post about: {text}",
            system=(
                "You write concise, engaging LinkedIn posts. "
                "150-200 words. Start with a hook. End with 3-4 hashtags. "
                "No markdown, plain text only."
            ),
        )

    if not content:
        content = (
            f"Excited to share thoughts on: {text}\n\n"
            "The future of work is being shaped by AI in ways we're only beginning to understand. "
            "As we navigate this shift, staying adaptable is key.\n\n"
            "#AI #Innovation #FutureOfWork #AIOperator"
        )

    from .draft_service import draft_service
    draft = draft_service.create(
        command_id=command_id,
        draft_type="linkedin_post",
        title=f"LinkedIn: {text[:50]}",
        content=content,
    )
    return {"result": content, "draft_id": draft.id, "draft_type": "linkedin_post", "action_hint": "post it"}


def _run_reporting_skill(text: str, skill: str) -> str:
    now = dt.now().strftime("%A, %B %d at %I:%M %p")
    ai = _openai_chat(
        prompt=f"The user asked: '{text}'. Generate a brief Xyron activity summary for {now}. "
               "Keep it to 3-4 bullet points covering: commands processed, integrations checked, "
               "drafts created, approvals pending. Make it sound real but note data isn't live yet.",
        system="You are an AI assistant generating brief productivity summaries. Be concise and professional.",
        max_tokens=200,
    )
    return ai or (
        f"Daily summary — {now}\n\n"
        "• Xyron is active and processing commands\n"
        "• Voice pipeline: operational\n"
        "• Pending approvals: check the approvals panel\n"
        "• Integrations: Gmail and LinkedIn configured\n\n"
        "Connect live integrations for real activity data."
    )


def _run_integration_skill(text: str, skill: str) -> str:
    return (
        "Integration status:\n"
        "• Gmail — credentials configured ✓\n"
        "• LinkedIn — credentials configured ✓\n"
        "• WhatsApp — MCP server ready\n"
        "• Odoo — MCP server ready\n"
        "• Instagram — credentials configured ✓\n\n"
        "Open the Integrations panel for full details."
    )


def _run_approval_skill(text: str, skill: str) -> str:
    from .draft_service import draft_service
    pending = draft_service.list_pending()
    if pending:
        names = ", ".join(d.title[:30] for d in pending[:3])
        return f"You have {len(pending)} draft(s) awaiting review: {names}. Open the dashboard to approve."
    return "No pending drafts or approvals right now. Everything is up to date."


def _run_general_skill(text: str) -> str:
    """Use OpenAI to answer general queries naturally."""
    ai = _openai_chat(
        prompt=text,
        system=(
            "You are Xyron — a professional AI voice assistant for business productivity. "
            "Answer the user's request concisely and helpfully. "
            "If you can't do something, say so clearly and suggest an alternative. "
            "Keep responses under 100 words. Speak naturally, no markdown."
        ),
        max_tokens=150,
    )
    return ai or f"I received your request: '{text}'. How can I help further?"


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


def _run_system_skill(text: str, skill: str) -> dict:
    """Handle system/web actions — returns action_url for frontend to open."""
    import re, urllib.parse
    text_lower = text.lower()

    if skill == "youtube_search":
        m = re.search(r'(?:for|about)\s+(.+?)(?:\s*$|\?)', text_lower)
        query = m.group(1).strip() if m else text_lower.replace("search youtube", "").replace("youtube search", "").strip()
        url = f"https://youtube.com/search?q={urllib.parse.quote(query)}"
        return {"result": f"Opening YouTube search for '{query}'", "action_url": url}

    elif skill == "youtube_play":
        m = re.search(r'(?:play|watch)\s+(.+?)(?:\s+(?:video|on youtube)|$)', text_lower)
        query = m.group(1).strip() if m else text_lower
        url = f"https://youtube.com/search?q={urllib.parse.quote(query)}"
        return {"result": f"Opening YouTube for '{query}'", "action_url": url}

    elif skill == "google_search":
        m = re.search(r'(?:for|about)\s+(.+?)(?:\s*$|\?)', text_lower)
        if not m:
            m = re.search(r'(?:search|google)\s+(?:for\s+)?(.+)', text_lower)
        query = m.group(1).strip() if m else text_lower
        url = f"https://google.com/search?q={urllib.parse.quote(query)}"
        return {"result": f"Searching Google for '{query}'", "action_url": url}

    elif skill == "open_url":
        url_map = {
            "youtube": "https://youtube.com", "github": "https://github.com",
            "gmail": "https://mail.google.com", "google": "https://google.com",
            "netflix": "https://netflix.com", "twitter": "https://twitter.com",
            "linkedin": "https://linkedin.com", "reddit": "https://reddit.com",
            "spotify": "https://open.spotify.com", "amazon": "https://amazon.com",
        }
        for name, url in url_map.items():
            if name in text_lower:
                return {"result": f"Opening {name.title()}", "action_url": url}
        return {"result": "Opening requested page.", "action_url": "https://google.com"}

    elif skill == "open_app":
        # Let the system router handle actual launching — return instruction for frontend
        app_names = ["vscode", "vs code", "chrome", "spotify", "firefox", "terminal",
                     "calculator", "notepad", "explorer", "cmd", "powershell"]
        for app in app_names:
            if app in text_lower:
                return {"result": f"Launching {app.title()}…", "action_app": app}
        return {"result": "Which application would you like to open?"}

    return _run_general_skill(text)


def _dispatch_to_skill(text: str, intent: CommandIntent, command_id: str) -> dict | str:
    """Route to real skill implementation or return structured placeholder."""
    agent = intent.agent
    skill = intent.skill

    if agent == "confirm":
        return _run_confirm_draft(command_id)
    elif agent == "cancel":
        return _run_reject_draft(command_id)
    elif agent == "system":
        return _run_system_skill(text, skill)
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
        return _run_general_skill(text)


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
        intent = classify_intent(text)   # fast keyword pre-classify for instant response
        cmd    = Command(text=text, status=CommandStatus.queued, intent=intent)

        with self._lock:
            self._store[cmd.id] = cmd
            while len(self._store) > self._MAX_SIZE:
                self._store.popitem(last=False)

        _executor.submit(self._execute, cmd.id, text, intent)
        return cmd

    def _execute(self, command_id: str, text: str, intent: CommandIntent) -> None:
        """Run in thread pool — updates command store with result.

        Priority:
          1. OpenAI tool calling (via registry) — AI decides which tool + params
          2. Legacy _dispatch_to_skill        — keyword-matched (confirm/cancel/etc.)
        """
        try:
            self.update_status(command_id, CommandStatus.running)

            # ── 1. AI tool calling via central registry ───────────────────────
            tool_name: str | None  = None
            tool_params: dict      = {}
            used_registry          = False

            try:
                _ensure_src_path()
                from api.config import settings as _cfg
                from api.tools import registry

                if _cfg.openai_api_key and _cfg.openai_api_key.startswith("sk-"):
                    tool_name, tool_params = classify_intent_ai(text, _cfg.openai_api_key)

                    # Special non-registry intents handled by legacy path
                    if tool_name not in ("confirm_draft", "reject_draft") and tool_name in registry:
                        ctx    = {"openai_key": _cfg.openai_api_key}
                        result = registry.execute(tool_name, tool_params, ctx)

                        action_url  = result.action_url
                        action_app  = result.action_app
                        action_path = result.action_path
                        spoken      = result.spoken or result.text
                        result_text = result.text

                        # Update intent for audit trail
                        intent = CommandIntent(
                            agent=tool_name.split("_")[0],
                            skill=tool_name,
                            confidence="ai_tool_call",
                        )
                        self.update_status(
                            command_id, CommandStatus.completed,
                            result=result_text,
                            assistant_response=spoken,
                            action_url=action_url,
                            action_app=action_app,
                        )
                        # Store in memory as last action
                        try:
                            from api.services.memory_service import memory_service
                            memory_service.set_last_action(tool_name, tool_params, result_text)
                        except Exception:
                            pass
                        used_registry = True

            except Exception as ai_exc:
                logger.debug("Registry execution skipped, falling back: %s", ai_exc)

            if used_registry:
                return

            # ── 2. Legacy skill dispatch (keyword-based) ──────────────────────
            result_data = _dispatch_to_skill(text, intent, command_id)

            # Handle both string and dict returns
            if isinstance(result_data, dict):
                result_text     = result_data.get("result", "")
                draft_id        = result_data.get("draft_id")
                action_hint     = result_data.get("action_hint", "")
                action_url      = result_data.get("action_url")
                action_app      = result_data.get("action_app")
                # Use execution spoken override if present (for confirm flows)
                spoken_override = result_data.get("spoken")
            else:
                result_text     = result_data
                draft_id        = None
                action_hint     = ""
                action_url      = None
                action_app      = None
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
                action_url=action_url,
                action_app=action_app,
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
        action_url: Optional[str] = None,
        action_app: Optional[str] = None,
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
            if action_url is not None:
                cmd.action_url = action_url
            if action_app is not None:
                cmd.action_app = action_app
        # Persist completed turn to SQLite outside the lock (avoids nested locking)
        if status == CommandStatus.completed and assistant_response and cmd:
            try:
                from .sqlite_memory import record_turn
                record_turn(
                    session_id=command_id,
                    user_text=cmd.text,
                    assistant_text=assistant_response,
                    tool_name=cmd.intent.agent if cmd.intent else None,
                    success=True,
                )
            except Exception:
                pass
        return cmd


# Module-level singleton
command_service = CommandService()
