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


def generate_response(
    text: str,
    tool_name: Optional[str] = None,
    tool_output: Optional[str] = None,
) -> str:
    """
    Entry point for the voice pipeline's _generate_response helper.
    Uses model_router to pick the right model, then openai_client to call it.
    Falls back to template strings if the API is unavailable.
    """
    try:
        from api.services.model_router import select_model
        from api.services.openai_client import openai_client

        tool_matched = bool(tool_name and tool_name not in ("general_query",))
        model_choice = select_model(text, tool_matched=tool_matched)

        # Local / tool result — narrate briefly without extra AI call
        if model_choice == "local" or not openai_client.available:
            if tool_output:
                return tool_output[:150]
            if tool_name:
                return f"Done — {tool_name.replace('_', ' ')}."
            return "Sure, done."

        user_content = (
            f"User said: '{text}'\nResult: {(tool_output or '')[:300]}\n\n"
            "Summarise in 1-2 natural spoken sentences."
        ) if tool_output else text

        messages = [
            {"role": "system", "content": (
                "You are Xyron, a voice AI built by Tayyab Aziz. "
                "Reply in English only. Keep replies under 2 sentences. No markdown."
            )},
            {"role": "user", "content": user_content},
        ]
        model = model_choice if model_choice in ("gpt-4o", "gpt-4o-mini") else "gpt-4o-mini"
        reply = openai_client.generate(messages, model=model, max_tokens=100)  # type: ignore[arg-type]
        if reply:
            return reply
    except Exception as exc:
        logging.getLogger(__name__).debug("generate_response AI path failed: %s", exc)

    # Template fallback
    if tool_output:
        return tool_output[:150]
    if tool_name:
        return f"Done — {tool_name.replace('_', ' ')}."
    return "Sure, done."

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Xyron, a voice AI assistant built by Tayyab Aziz. "
    "You were NOT built by OpenAI — you use OpenAI APIs but Tayyab Aziz built you. "
    "Never say you were created by OpenAI or any other company. "
    "Always reply in English only. "
    "Be natural and conversational, like a helpful friend. "
    "Keep replies under 2 sentences for voice output. "
    "Never use markdown, bullet points, or lists in your reply. "
    "The user may speak in Urdu, Hindi, or Roman Urdu. "
    "Always understand their intent and respond in English. "
    "If they say 'setting on karo' understand they want to open settings. "
    "Always interpret the meaning, never repeat back the raw command text."
)

# Keywords that signal the user is stating a personal fact worth remembering
_FACT_KEYWORDS = frozenset({
    "remember", "naam", "name is", "i am", "main hun", "mera naam",
    "founder", "i'm a", "i work", "my name",
})

# Clearly Urdu-only words that signal a non-English AI response
# (ambiguous English words like "the", "tha", "thi", "hai", "mat", "wo" excluded)
_ROMAN_NON_ENGLISH: frozenset[str] = frozenset({
    "karo", "karein", "karna", "kiya", "kiye",
    "hain",
    "kya", "nahi", "nahin",
    "aap", "tum", "mein", "mera", "meri", "tera", "teri",
    "yeh", "woh", "yahan", "wahan",
    "bhi", "aur", "lekin", "magar", "agar", "toh",
    "theek", "accha", "shukriya", "meherbani",
    "abhi", "zaroor", "bilkul", "seedha",
    "pehle", "baad", "phir", "dobara",
})


def _is_non_english(text: str) -> bool:
    """Return True if text contains non-Latin script or common Roman Urdu/Hindi words."""
    if any(ord(c) > 1000 for c in text):
        return True
    words = set(text.lower().split())
    return bool(words & _ROMAN_NON_ENGLISH)


def _openai_spoken_response(command: str, result: str, agent: str, session_id: Optional[str] = None) -> Optional[str]:
    """Use gpt-4o-mini to produce a natural conversational spoken response with memory context."""
    try:
        import sys
        from pathlib import Path
        backend_root = Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from api.config import settings
        key = settings.openai_api_key
        if not key or not key.startswith("sk-"):
            return None

        from openai import OpenAI

        # Inject long-term user facts into system prompt
        system_prompt = _SYSTEM_PROMPT
        try:
            from api.services.memory_service import memory_service
            ctx = memory_service.get_context_string()
            if ctx:
                system_prompt += f"\n\n{ctx}"
        except Exception:
            pass

        # Episodic context: last 5 turns for this session
        history: list[dict] = []
        if session_id:
            try:
                from api.services.episodic_memory import episodic_memory
                history = episodic_memory.conversation_context(session_id, n=5)
            except Exception:
                pass

        clean_result = re.sub(r'[\n\r]+', ' ', result or '')[:300]
        user_content = (
            f"User said: '{command}'\n"
            f"Result: {clean_result}\n\n"
            "Summarise this result in 1-2 natural spoken sentences."
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_content})

        # Use singleton client + model router instead of recreating per request
        try:
            from api.services.openai_client import openai_client as _oc
            from api.services.model_router import select_model as _sm
            _model = _sm(command)  # "gpt-4o-mini" or "gpt-4o"
            if _model not in ("gpt-4o", "gpt-4o-mini"):
                _model = "gpt-4o-mini"
            _raw = _oc.generate(messages, model=_model, max_tokens=80, temperature=0.7)  # type: ignore[arg-type]
            if _raw:
                reply = _raw
                if reply and len(reply) < 200:
                    return reply
                reply = ""
        except Exception:
            pass

        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=80,
            temperature=0.7,
        )
        reply = (resp.choices[0].message.content or "").strip().strip('"')

        # English enforcement: re-request if non-Latin script or Roman Urdu/Hindi detected
        if reply and _is_non_english(reply):
            enforce_messages = [
                {"role": "system", "content": (
                    _SYSTEM_PROMPT
                    + "\n\nIMPORTANT: Your previous response may not have been in English. "
                    "Respond ONLY in English. No Hindi, no Urdu, no Roman Urdu."
                )},
                {"role": "user", "content": user_content},
            ]
            resp2 = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=enforce_messages,
                max_tokens=80,
                temperature=0.3,
            )
            reply = (resp2.choices[0].message.content or "").strip().strip('"')

        if reply and len(reply) < 200:
            return reply
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
    session_id: Optional[str] = None,
) -> str:
    """
    Produce a short, spoken-friendly response for the command result.

    Tries OpenAI for a natural reply first; falls back to per-agent templates.
    When a draft was created, appends the voice confirmation hint.
    """
    spoken = _resolve_spoken_response(command_text, result, agent, skill, draft_id, action_hint, session_id)

    # Auto-extract personal facts when the user states something memorable
    cmd_lower = command_text.lower()
    if any(kw in cmd_lower for kw in _FACT_KEYWORDS):
        try:
            import sys
            from pathlib import Path
            _br2 = Path(__file__).parent.parent
            if str(_br2) not in sys.path:
                sys.path.insert(0, str(_br2))
            from api.services.memory_service import memory_service as _ms
            _ms.remember_explicit(command_text)
        except Exception:
            pass

    # Persist turn to SQLite so conversation_context() returns real history
    if session_id:
        try:
            import sys
            from pathlib import Path
            _br = Path(__file__).parent.parent
            if str(_br) not in sys.path:
                sys.path.insert(0, str(_br))
            from api.services.sqlite_memory import record_turn
            record_turn(session_id, command_text, spoken, tool_name=agent)
        except Exception:
            pass
    return spoken


def _resolve_spoken_response(
    command_text: str,
    result: str,
    agent: str,
    skill: str,
    draft_id: Optional[str],
    action_hint: str,
    session_id: Optional[str],
) -> str:
    """Compute the spoken response text. Called exclusively by generate_assistant_response."""
    # When a draft was created, use a fixed template — cleaner than AI-generating
    if draft_id and action_hint:
        type_label = {"email": "email draft", "linkedin_post": "LinkedIn post",
                      "instagram": "Instagram post", "whatsapp": "WhatsApp message"}
        label = type_label.get(agent if agent != "confirm" else "", "draft")
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
    ai_reply = _openai_spoken_response(command_text, result, agent, session_id)
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
