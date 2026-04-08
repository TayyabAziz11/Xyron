"""
Generates natural-language assistant responses for spoken TTS output.

Priority:
1. OpenAI generates a concise spoken response (when key is available)
2. Template fallback per-agent (always works)

Each response is ≤ 2 sentences, no markdown, natural spoken English.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _openai_spoken_response(command: str, result: str, agent: str) -> Optional[str]:
    """Use OpenAI to produce a natural 1-sentence spoken response."""
    try:
        import sys
        from pathlib import Path
        src = Path(__file__).parent.parent / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from ai_operator.core.content_generator import ContentGenerator
        gen = ContentGenerator()
        # Strip markdown/noise from result before sending
        clean_result = re.sub(r'[\n\r]+', ' ', result or '')[:300]
        prompt = (
            f"User said: '{command}'\n"
            f"Result: {clean_result}\n\n"
            "Write a single natural spoken sentence (max 20 words) that an AI assistant "
            "would say to summarise this result. No markdown. Sound friendly and concise."
        )
        reply = gen.chat(prompt, system_prompt="You write ultra-concise spoken AI assistant replies.", max_tokens=60)
        if reply and len(reply) < 200:
            return reply.strip().strip('"')
    except Exception as exc:
        logger.debug("OpenAI spoken response failed: %s", exc)
    return None


def generate_assistant_response(
    command_text: str,
    result: str,
    agent: str,
    skill: str,
    draft_id: Optional[str] = None,
    action_hint: str = "",
) -> str:
    """
    Produce a short, spoken-friendly response for the command result.

    Tries OpenAI for a natural reply first; falls back to per-agent templates.
    When a draft was created, appends the voice confirmation hint.
    """
    # When a draft was created, use a fixed template — cleaner than AI-generating
    if draft_id and action_hint:
        type_label = {"email": "email draft", "linkedin_post": "LinkedIn post",
                      "instagram": "Instagram post", "whatsapp": "WhatsApp message"}
        label = type_label.get(agent if agent != "confirm" else "", "draft")
        # Determine label from action_hint context
        if action_hint == "send it":
            label = "email draft"
        elif action_hint == "post it":
            label = "LinkedIn post"
        return f"Your {label} is ready. Say '{action_hint}' to confirm, or open the dashboard to review."

    # For confirm/cancel agents, use the result directly
    if agent in ("confirm", "cancel"):
        clean = _clean_for_speech(result)
        return clean[:120] if clean else "Done."

    # Try OpenAI for a natural spoken reply
    ai_reply = _openai_spoken_response(command_text, result, agent)
    if ai_reply:
        return ai_reply

    # Fallback: template-based response
    clean = _clean_for_speech(result)

    # Route to per-agent response template
    if agent == "email":
        base = _email_response(skill, clean, command_text)
    elif agent == "linkedin":
        base = _linkedin_response(skill, clean, command_text)
    elif agent == "approval":
        base = _approval_response(skill, clean)
    elif agent == "reporting":
        base = _reporting_response(skill, clean)
    elif agent == "workflow":
        base = _workflow_response(skill, clean)
    elif agent == "integration":
        base = _integration_response(clean)
    elif agent == "activity":
        base = _activity_response(clean)
    elif agent == "whatsapp":
        base = _whatsapp_response(skill, clean)
    elif agent == "instagram":
        base = _instagram_response(skill, clean)
    elif agent == "odoo":
        base = _odoo_response(skill, clean)
    elif agent in ("confirm", "cancel"):
        return _general_response(clean, command_text)
    else:
        base = _general_response(clean, command_text)

    # When a draft was created, append the voice confirmation hint
    if draft_id and action_hint:
        return f"{base} Say '{action_hint}' to confirm, or open the dashboard to review."
    return base


def _clean_for_speech(text: str) -> str:
    """Remove markdown and technical noise, keep the essential content."""
    if not text:
        return ""
    # Remove markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove code blocks
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Remove inline code
    text = re.sub(r'`[^`]+`', '', text)
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove bullet points
    text = re.sub(r'^\s*[-*\u2022]\s+', '', text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Truncate to ~150 chars for speech
    if len(text) > 150:
        text = text[:147] + '...'
    return text


def _email_response(skill: str, clean: str, command: str) -> str:
    cmd_lower = command.lower()
    if 'summarize' in cmd_lower or 'summary' in skill:
        return "Here's your email summary. Check your inbox for full details."
    elif 'reply' in cmd_lower:
        return "I've drafted a reply."
    else:
        return "Your email draft is ready."


def _linkedin_response(skill: str, clean: str, command: str) -> str:
    if 'publish' in skill:
        return "Your LinkedIn post has been published successfully."
    return "Your LinkedIn draft is ready."


def _approval_response(skill: str, clean: str) -> str:
    if clean and 'pending' in clean.lower():
        return "You have pending approvals waiting. Check the approvals panel."
    if 'approved' in (clean or '').lower():
        return "The action has been approved and will execute shortly."
    return "Approval status updated. Open the approvals panel for details."


def _reporting_response(skill: str, clean: str) -> str:
    if 'weekly' in skill:
        return "Your weekly briefing is ready. Check the dashboard for details."
    return "Your summary report is ready. Open the dashboard to view it."


def _workflow_response(skill: str, clean: str) -> str:
    return "Workflow status retrieved. Check active workflows in the dashboard."


def _integration_response(clean: str) -> str:
    return "Integration status checked. View details in the integrations panel."


def _activity_response(clean: str) -> str:
    return "Here's your recent activity. Check the activity log for full details."


def _whatsapp_response(skill: str, clean: str) -> str:
    if 'send' in skill:
        return "Your WhatsApp message is ready and needs approval before sending."
    return "WhatsApp status retrieved. Check the dashboard for details."


def _instagram_response(skill: str, clean: str) -> str:
    return "Your Instagram content is ready. Open the dashboard to review it."


def _odoo_response(skill: str, clean: str) -> str:
    return "Odoo data retrieved. Check the accounting dashboard for details."


def _general_response(clean: str, command: str) -> str:
    cmd_short = command[:50].rstrip()
    return f"Done. I've processed your request about {cmd_short}. Check the dashboard for results."
