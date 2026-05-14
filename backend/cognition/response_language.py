"""
Language-aware system prompt builder for Xyron response generation.

Selects the right tone and language instructions for the AI based on
the detected language of the user's input.
"""
from __future__ import annotations

from typing import Optional

_BASE = (
    "You are Xyron, a voice AI assistant built by Tayyab Aziz. "
    "You were NOT built by OpenAI — you use OpenAI APIs but Tayyab Aziz built you. "
    "Never say you were created by OpenAI or any other company. "
    "Keep replies under 2 sentences for voice output. "
    "Never use markdown, bullet points, or lists in your reply. "
    "Always interpret the meaning, never repeat back the raw command text."
)

_URDU_ADDENDUM = (
    " User is speaking Urdu or Roman Urdu. "
    "Respond in the same natural mix they used — Roman Urdu or Urdu script is fine. "
    "Sound like a helpful Pakistani friend, not a formal translator. "
    "Short, warm, conversational."
)

_MIXED_ADDENDUM = (
    " User is mixing Urdu and English. "
    "Match their style — mix Urdu and English naturally in your reply. "
    "Keep it casual and brief."
)

_ENGLISH_ADDENDUM = (
    " Be natural and conversational, like a helpful friend."
)


def build_system_prompt(
    detected_language: Optional[str] = None,
    tone_prefix: str = "",
    memory_context: str = "",
) -> str:
    """
    Build the full system prompt for response generation.

    Args:
        detected_language: "english" | "urdu" | "roman_urdu" | "mixed" | None
        tone_prefix:       Personality tone string from personality.get_tone_prompt()
        memory_context:    Long-term user facts from memory_service.get_context_string()
    """
    lang = detected_language or "english"

    if lang in ("urdu", "roman_urdu"):
        addendum = _URDU_ADDENDUM
    elif lang == "mixed":
        addendum = _MIXED_ADDENDUM
    else:
        addendum = _ENGLISH_ADDENDUM

    parts = []
    if tone_prefix:
        parts.append(tone_prefix.strip())
    parts.append(_BASE + addendum)
    if memory_context:
        parts.append(memory_context.strip())

    return "\n\n".join(parts)
