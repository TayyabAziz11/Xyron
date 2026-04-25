"""
Context resolver — replaces pronouns and vague references with concrete entities
extracted from episodic memory (recent session turns).

Wire in voice.py immediately after receiving body.text, before any routing.
"""
from __future__ import annotations

import re
import logging
from .episodic_memory import episodic_memory

logger = logging.getLogger(__name__)

# Vague references that need grounding
_VAGUE_RE = re.compile(
    r'\b(it|this|that|them|these|those|the\s+file|the\s+folder|the\s+video|'
    r'the\s+app|the\s+document|that\s+file|that\s+folder|that\s+video|'
    r'that\s+app|the\s+same(?:\s+one)?)\b',
    re.IGNORECASE,
)

# Patterns to extract last-mentioned entity from assistant turns
_ENTITY_PATTERNS = [
    # File with extension: "Opening video 1.mp4"
    (re.compile(
        r'(?:open(?:ing|ed)|found|playing|launch(?:ing|ed))\s+([\w\s\-\.]+\.(?:mp4|avi|mkv|mov|pdf|docx|xlsx|pptx|txt|py|js|ts|zip|exe))',
        re.IGNORECASE), 1),
    # Named folder: "Opening IT Course"
    (re.compile(
        r'(?:open(?:ing|ed))\s+([\w\s\-]+?)(?:\s*\.|\s*$)',
        re.IGNORECASE), 1),
    # App name after "Launching/Opening X"
    (re.compile(
        r'(?:launch(?:ing|ed)|start(?:ing|ed)|open(?:ing|ed))\s+([\w\s]+)',
        re.IGNORECASE), 1),
]

# Patterns to extract from user turns (e.g. "open Chrome" → Chrome is the entity)
_USER_ENTITY_PATTERNS = [
    (re.compile(r'open\s+([\w\s\-\.]+?)(?:\s+for me|\s+please|$)', re.IGNORECASE), 1),
    (re.compile(r'play\s+([\w\s\-\.]+)', re.IGNORECASE), 1),
    (re.compile(r'send\s+(?:the\s+)?(?:email|message)\s+to\s+([\w\s\@\.]+)', re.IGNORECASE), 1),
]


def _extract_entity(turns: list) -> str | None:
    """Scan recent turns newest-first for the last concrete named entity."""
    for turn in turns:
        patterns = _ENTITY_PATTERNS if turn.role == "assistant" else _USER_ENTITY_PATTERNS
        for pattern, group in patterns:
            m = pattern.search(turn.text)
            if m:
                entity = m.group(group).strip().rstrip(".")
                if len(entity) > 2:  # ignore single-char matches
                    return entity
    return None


def resolve(text: str, session_id: str) -> str:
    """
    Replace vague pronouns with the last concrete entity from session history.
    Returns original text unchanged if no pronouns found or no entity to substitute.
    """
    if not _VAGUE_RE.search(text):
        return text

    turns = episodic_memory.session_history(session_id, n=8)
    if not turns:
        return text

    entity = _extract_entity(turns)
    if not entity:
        return text

    resolved = _VAGUE_RE.sub(entity, text, count=1)
    if resolved != text:
        logger.info("[CTX] '%s' → '%s'", text, resolved)
    return resolved
