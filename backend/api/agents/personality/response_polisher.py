from __future__ import annotations

"""
ResponsePolisher — apply final text cleanup to a raw response.

Pipeline (in order, all in-memory — no I/O):
  1. Apply ToneManager phrase transforms
  2. Ensure text ends with punctuation
  3. Remove duplicate whitespace
  4. Capitalise first character
  5. Truncate to MAX_VOICE_LENGTH at the nearest sentence boundary

Total time budget: < 2 ms.
"""

import logging
import re

from api.agents.personality.tone_manager import ToneManager

logger = logging.getLogger("api.agents.personality.response_polisher")

_MULTI_SPACE = re.compile(r" {2,}")
_SENTENCE_END = re.compile(r"[.!?…]")


class ResponsePolisher:
    """Instant response polish — no I/O, no LLM calls."""

    MAX_VOICE_LENGTH: int = 150

    def __init__(self) -> None:
        self._tone = ToneManager()

    def polish(self, text: str, mode_value: str) -> str:
        """
        Apply all polish steps and return the cleaned string.

        *mode_value* is a PersonalityMode.value string (e.g. "jarvis").
        """
        if not text:
            return text

        # 1. Tone transforms
        result = self._tone.apply(text, mode_value)

        # 2. Ensure ends with punctuation
        result = self._ensure_punctuation(result)

        # 3. Collapse duplicate spaces
        result = _MULTI_SPACE.sub(" ", result).strip()

        # 4. Capitalise first character
        if result:
            result = result[0].upper() + result[1:]

        # 5. Truncate for voice
        result = self.truncate_for_voice(result)

        return result

    def _ensure_punctuation(self, text: str) -> str:
        stripped = text.rstrip()
        if stripped and stripped[-1] not in ".!?…":
            return stripped + "."
        return text

    def truncate_for_voice(self, text: str) -> str:
        """
        Truncate *text* to MAX_VOICE_LENGTH characters at a sentence boundary.

        Strategy:
          1. If text fits, return as-is.
          2. Find the last sentence-end punctuation within the limit.
          3. If none found, truncate at the last word boundary and append "…".
        """
        if len(text) <= self.MAX_VOICE_LENGTH:
            return text

        candidate = text[: self.MAX_VOICE_LENGTH]

        # Last sentence boundary within candidate
        matches = list(_SENTENCE_END.finditer(candidate))
        if matches:
            cut = matches[-1].start() + 1
            return candidate[:cut].rstrip()

        # Fall back to last word boundary
        last_space = candidate.rfind(" ")
        if last_space > 0:
            return candidate[:last_space].rstrip() + "…"

        # Hard cut
        return candidate.rstrip() + "…"
