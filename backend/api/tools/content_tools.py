"""Content tools — email, social posts, summaries, approvals, general AI."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from .registry import ToolResult, registry
from ..constants.prompts import CORE_IDENTITY

logger = logging.getLogger(__name__)


def _exec_compose_email(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    to       = params.get("to",       "").strip()
    hint     = params.get("body_hint", "").strip()
    subject  = params.get("subject", f"Message to {to}").strip()
    openai_k = ctx.get("openai_key", "")

    # Resolve pronoun "him"/"her"/"them" via last_contact in memory
    if to.lower() in ("him", "her", "them", "they"):
        try:
            from ..services.memory_service import memory_service
            last = memory_service.get_facts().get("last_contact", "")
            if last:
                to = last
        except Exception:
            pass

    # Build a synthetic text for the email skill
    text = f"write email to {to} about {hint}"
    tmp_id = str(uuid.uuid4())

    try:
        from ..services.command_service import _run_email_skill
        result = _run_email_skill(text, "email_draft", tmp_id)
        content = result.get("result", "Email draft created.")
        draft_id = result.get("draft_id")
        return ToolResult(
            success=True,
            text=content,
            spoken=f"Email to {to} drafted. Say 'send it' to confirm.",
            data={"to": to, "subject": subject, "draft_id": draft_id},
        )
    except Exception as exc:
        logger.warning("compose_email failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="Couldn't draft the email.", error=str(exc))


def _exec_create_post(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    platform = params.get("platform", "linkedin").strip().lower()
    topic    = params.get("topic", "").strip()
    tmp_id   = str(uuid.uuid4())

    try:
        if platform == "linkedin":
            from ..services.command_service import _run_linkedin_skill
            result = _run_linkedin_skill(topic, "linkedin_draft", tmp_id)
        else:
            from ..services.command_service import _openai_chat
            content = _openai_chat(
                prompt=f"Write a {platform} post about: {topic}",
                system=f"You write engaging {platform} posts. 150 words max. No markdown.",
            ) or f"Post about {topic}"
            result = {"result": content, "draft_id": None}

        return ToolResult(
            success=True,
            text=result.get("result", "Post drafted."),
            spoken=f"{platform.title()} post drafted. Say 'post it' to confirm.",
            data={"platform": platform, "topic": topic, "draft_id": result.get("draft_id")},
        )
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't draft the post.", error=str(exc))


def _exec_get_summary(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    period = params.get("period", "daily")
    try:
        from ..services.command_service import _run_reporting_skill
        text = _run_reporting_skill(f"{period} summary", "daily_summary")
        return ToolResult(success=True, text=text, spoken=text[:200])
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't generate summary.", error=str(exc))


def _exec_list_approvals(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    try:
        from ..services.command_service import _run_approval_skill
        text = _run_approval_skill("list approvals", "list_approvals")
        return ToolResult(success=True, text=text, spoken=text[:200])
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't fetch approvals.", error=str(exc))


def _exec_general_query(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    query    = params.get("query", "").strip()
    openai_k = ctx.get("openai_key", "")

    if not openai_k:
        return ToolResult(success=True, text=f"Processed: {query}", spoken=f"I heard: {query}")

    try:
        from openai import OpenAI
        # Inject memory context
        mem_ctx = ""
        try:
            from ..services.memory_service import memory_service
            mem_ctx = memory_service.get_context_string()
        except Exception:
            pass

        system = CORE_IDENTITY + "\nAnswer in 1-3 concise sentences. No markdown."
        if mem_ctx:
            system += f"\n\n{mem_ctx}"

        resp = OpenAI(api_key=openai_k).chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": query}],
            max_tokens=150,
        )
        answer = resp.choices[0].message.content or query
        return ToolResult(success=True, text=answer, spoken=answer)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't answer that.", error=str(exc))


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    name="compose_email",
    definition={
        "type": "function",
        "function": {
            "name": "compose_email",
            "description": "Draft a professional email to a recipient. Use for: 'email John about...', 'send email to...', 'write email'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":        {"type": "string", "description": "Recipient name, email, or pronoun (him/her)"},
                    "body_hint": {"type": "string", "description": "What the email should say or be about"},
                    "subject":   {"type": "string", "description": "Email subject line (optional)"},
                },
                "required": ["to", "body_hint"],
            },
        },
    },
    executor=_exec_compose_email,
    risk="medium",
    category="content",
)

registry.register(
    name="create_post",
    definition={
        "type": "function",
        "function": {
            "name": "create_post",
            "description": "Create a social media post for LinkedIn or Instagram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": ["linkedin", "instagram"], "description": "Social media platform"},
                    "topic":    {"type": "string", "description": "What to post about"},
                },
                "required": ["platform", "topic"],
            },
        },
    },
    executor=_exec_create_post,
    risk="medium",
    category="content",
)

registry.register(
    name="get_summary",
    definition={
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": "Generate a daily or weekly activity summary report.",
            "parameters": {
                "type": "object",
                "properties": {"period": {"type": "string", "enum": ["daily", "weekly"]}},
                "required": [],
            },
        },
    },
    executor=_exec_get_summary,
    risk="low",
    category="content",
)

registry.register(
    name="list_approvals",
    definition={
        "type": "function",
        "function": {
            "name": "list_approvals",
            "description": "List pending drafts and approvals that need review.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_list_approvals,
    risk="low",
    category="content",
)

registry.register(
    name="general_query",
    definition={
        "type": "function",
        "function": {
            "name": "general_query",
            "description": "Answer a general question, handle conversational requests, or process anything that doesn't fit other tools.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The user's question or request"}},
                "required": ["query"],
            },
        },
    },
    executor=_exec_general_query,
    risk="low",
    category="content",
)
