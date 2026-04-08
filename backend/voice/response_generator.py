"""
Generates natural-language assistant responses for spoken TTS output.

Each skill result is mapped to a short, speakable response that sounds
natural when read aloud — no markdown, no long technical text.
"""
from __future__ import annotations

import re
from typing import Optional


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

    Args:
        command_text: Original user command
        result: Raw skill result text
        agent: Agent name (email, linkedin, approval, etc.)
        skill: Skill name
        draft_id: If a draft was created, its ID (triggers action hint)
        action_hint: Phrase the user should say to confirm (e.g. "post it")

    Returns:
        A 1-2 sentence response suitable for TTS playback.
    """
    # Strip markdown, code blocks, and long technical output
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
