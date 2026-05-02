"""
Context resolver — replaces pronouns and vague references with concrete entities.

Resolution priority:
  1. Active window (for window-management commands like "close it", "minimize this")
  2. Typed memory slot matching the command type (last_app, last_file, last_folder, last_url)
  3. Entity extracted from recent episodic session history (original behaviour)

Wire in voice.py immediately after receiving body.text, before any routing.
"""
from __future__ import annotations

import re
import logging
from .episodic_memory import episodic_memory

logger = logging.getLogger(__name__)

# Commands that reference the active foreground window
_WINDOW_CMD_RE = re.compile(
    r'\b(close|minimize|maximise|maximize|hide|restore|switch\s+to|focus|'
    r'bring\s+(?:up|to\s+front))\b',
    re.IGNORECASE,
)

# Commands that reference the last app (not necessarily the foreground window)
_APP_CMD_RE = re.compile(
    r'\b(close|kill|stop|quit|reopen|restart|switch\s+to)\b',
    re.IGNORECASE,
)
_FILE_CMD_RE = re.compile(
    r'\b(open|delete|rename|move|copy|read|edit|preview)\b.*\b(file|document|pdf|video|photo|image)\b',
    re.IGNORECASE,
)
_FOLDER_CMD_RE = re.compile(
    r'\b(open|navigate|go\s+to|show)\b.*\b(folder|directory|location)\b',
    re.IGNORECASE,
)
_URL_CMD_RE = re.compile(
    r'\b(open|visit|go\s+to|navigate\s+to|share|bookmark)\b.*\b(site|page|link|url|website)\b',
    re.IGNORECASE,
)

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


def _typed_slot_entity(text: str) -> str | None:
    """
    Pick the best typed memory slot for the given command text.
    Returns the entity string (e.g. "chrome", "report.pdf") or None.
    """
    try:
        from .memory_service import memory_service
        ctx = memory_service.get_context()

        # Window-management commands → active foreground window first
        if _WINDOW_CMD_RE.search(text):
            try:
                from .window_context import window_context
                win = window_context.get_active_window()
                if win and win.get("proc_name"):
                    return win["proc_name"]
            except Exception:
                pass
            slot = ctx.get("last_app")
            if slot and slot.get("name"):
                return slot["name"]

        # App-targeted commands
        if _APP_CMD_RE.search(text) and not _FILE_CMD_RE.search(text):
            slot = ctx.get("last_app")
            if slot and slot.get("name"):
                return slot["name"]

        # File-targeted commands
        if _FILE_CMD_RE.search(text):
            slot = ctx.get("last_file")
            if slot and slot.get("path"):
                return slot["path"]

        # Folder-targeted commands
        if _FOLDER_CMD_RE.search(text):
            slot = ctx.get("last_folder")
            if slot and slot.get("path"):
                return slot["path"]

        # URL-targeted commands
        if _URL_CMD_RE.search(text):
            slot = ctx.get("last_url")
            if slot and slot.get("url"):
                return slot["url"]

    except Exception:
        pass
    return None


def resolve(text: str, session_id: str) -> str:
    """
    Replace vague pronouns with the best available concrete entity.

    Priority:
      1. Active window / typed memory slot (command-type aware)
      2. Entity extracted from recent episodic session history
    Returns original text unchanged if no pronouns found or no entity to substitute.
    """
    if not _VAGUE_RE.search(text):
        return text

    # Priority 1: typed slot / active window
    entity = _typed_slot_entity(text)

    # Priority 2: session history scan (original behaviour)
    if not entity:
        turns = episodic_memory.session_history(session_id, n=8)
        entity = _extract_entity(turns) if turns else None

    if not entity:
        return text

    resolved = _VAGUE_RE.sub(entity, text, count=1)
    if resolved != text:
        logger.info("[CTX] '%s' → '%s'", text, resolved)
    return resolved
