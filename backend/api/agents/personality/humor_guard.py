from __future__ import annotations

"""
HumorGuard — suppress humor in sensitive or high-stakes contexts.

check()    — returns True if humor is safe, False if context is sensitive.
sanitize() — strips emoji and humor phrases from text when context is blocked.

No I/O — must complete in < 1 ms.

Log tags: [HUMOR_GUARD_BLOCKED]
"""

import logging
import re

logger = logging.getLogger("api.agents.personality.humor_guard")

# ── Blocked context keywords ───────────────────────────────────────────────────

BLOCKED_CONTEXTS: list[str] = [
    "error",
    "security",
    "payment",
    "booking",
    "job application",
    "delete",
    "format",
    "shutdown",
    "personal data",
    "password",
    "bank",
    "credit card",
    "medical",
    "legal",
    "emergency",
    "virus",
    "malware",
    "hack",
    "reset",
    "wipe",
    "permanent",
    "data loss",
    "irreversible",
]

# Emoji broad-range pattern
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F1E0-\U0001F1FF"   # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)

# Common humor textual markers
_HUMOR_TEXT_RE = re.compile(
    r"\b(haha|lol|lmao|rofl|hehe|heehee|chuckle|lmaooo|xd|xD|teehee|kidding|jk|wink)\b",
    re.IGNORECASE,
)

_MULTI_SPACE = re.compile(r" {2,}")


class HumorGuard:
    """Prevent humor in sensitive / high-stakes contexts."""

    def check(self, text: str, context: str, mode_value: str) -> bool:
        """
        Returns True  — humor is safe.
        Returns False — context is sensitive; suppress humor.
        """
        combined = f"{text} {context}".lower()
        for keyword in BLOCKED_CONTEXTS:
            if keyword.lower() in combined:
                logger.info(
                    "[HUMOR_GUARD_BLOCKED] reason=sensitive_context keyword=%r mode=%s",
                    keyword,
                    mode_value,
                )
                return False
        return True

    def sanitize(self, text: str, context: str, mode_value: str) -> str:
        """
        If context is sensitive, strip emoji and humor phrases from *text*.
        If context is safe, return *text* unchanged.
        """
        if self.check(text, context, mode_value):
            return text  # no sanitization needed

        logger.info(
            "[HUMOR_GUARD_BLOCKED] reason=sanitizing_response mode=%s", mode_value
        )

        result = _EMOJI_RE.sub("", text)
        result = _HUMOR_TEXT_RE.sub("", result)
        result = _MULTI_SPACE.sub(" ", result).strip()

        # Re-ensure punctuation after stripping
        if result and result[-1] not in ".!?":
            result += "."

        return result
