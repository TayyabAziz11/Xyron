"""
Pronunciation engine — applied globally before every Kokoro synthesis call.

Replaces words from the lexicon with TTS-friendly phonetic forms.
Replacements are chunk-boundary-safe: they operate on complete words only
(\b boundaries) so "GitHub" in a sentence never gets split into "Git" + "Hub".

Usage:
    from voice.pronunciation_engine import pronunciation_engine
    cleaned = pronunciation_engine.apply("Xyron is ready.")
    # → "Zyron is ready."
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

from voice.pronunciation_lexicon import ALL as _LEXICON

logger = logging.getLogger(__name__)


class PronunciationEngine:
    """Applies whole-word phonetic substitutions before TTS synthesis."""

    # Pre-compiled (pattern, replacement) list — ordered longest-first so
    # "VS Code" matches before "VS" or "Code" individually.
    _RULES: ClassVar[list[tuple[re.Pattern[str], str]]] = []
    _built: ClassVar[bool] = False

    @classmethod
    def _build(cls) -> None:
        if cls._built:
            return
        items = sorted(_LEXICON.items(), key=lambda kv: len(kv[0]), reverse=True)
        for original, spoken in items:
            # Word-boundary aware — \b before and after the whole phrase
            escaped = re.escape(original)
            pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
            cls._RULES.append((pattern, spoken))
        cls._built = True

    def apply(self, text: str) -> str:
        """Return text with all lexicon entries replaced by their spoken forms."""
        if not text:
            return text
        self._build()
        result = text
        for pattern, spoken in self._RULES:
            new = pattern.sub(spoken, result)
            if new != result:
                # Log first match found for this replacement
                m = pattern.search(result)
                if m:
                    logger.debug(
                        "[PRONUNCIATION] original=%r spoken=%r",
                        m.group(0), spoken,
                    )
                result = new
        return result


pronunciation_engine = PronunciationEngine()
