"""
Personality profile — controls Xyron's tone, verbosity, and speaking style.

Dynamic: adapts based on time of day and turn_count from cognitive_state.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PersonalityProfile:
    name:             str   = "Xyron"
    tone:             str   = "direct"
    verbosity:        str   = "concise"
    humor_level:      float = 0.3
    confidence_level: float = 0.8

    @staticmethod
    def detect_language(text: str) -> str:
        """Return 'ur', 'en', or 'mixed' based on script character analysis."""
        if not text:
            return "en"
        urdu_chars  = sum(1 for c in text if ord(c) > 1000)
        latin_chars = sum(1 for c in text if c.isalpha() and ord(c) <= 127)
        total_alpha = urdu_chars + latin_chars
        if total_alpha == 0:
            return "en"
        urdu_ratio = urdu_chars / total_alpha
        if urdu_ratio > 0.3:
            return "mixed" if latin_chars > 0 and urdu_ratio < 0.9 else "ur"
        return "en"

    def get_tone_prompt(self, text: str = "") -> str:
        """Return a system prompt fragment encoding current personality + time context."""
        hour        = time.localtime().tm_hour
        turn_count  = self._get_turn_count()

        # Time-of-day adaptation
        if 22 <= hour or hour < 5:
            time_note = "It is late — keep replies especially brief and calm."
        elif 5 <= hour < 9:
            time_note = "It is morning — be energetic and concise."
        else:
            time_note = ""

        # Turn-count adaptation: get more concise in long sessions
        brevity_note = ""
        if turn_count > 20:
            brevity_note = "This is a long session — be extra concise, one sentence where possible."

        humor_note = (
            "Occasional dry wit is fine — one brief remark per several turns, never forced."
            if self.humor_level >= 0.3 else ""
        )

        lang_note = ""
        if text:
            lang = self.detect_language(text)
            if lang in ("ur", "mixed"):
                lang_note = "User speaks Urdu or mixes Urdu/English. Respond naturally in the same mix they used."

        parts = [
            f"Personality: {self.name} — {self.tone}, {self.verbosity}.",
            "Never use filler phrases like 'Certainly!', 'Of course!', 'Sure thing!', 'Absolutely!'.",
            "Never start a reply with sycophantic openers.",
            "Speak directly. State the answer first, then context if needed.",
            f"Confidence level is high ({self.confidence_level:.0%}) — no hedging, no second-guessing.",
        ]
        if humor_note:
            parts.append(humor_note)
        if time_note:
            parts.append(time_note)
        if brevity_note:
            parts.append(brevity_note)
        if lang_note:
            parts.append(lang_note)

        return " ".join(parts)

    @staticmethod
    def _get_turn_count() -> int:
        try:
            from cognition.cognitive_state import cognitive_state
            return cognitive_state.turn_count
        except Exception:
            return 0


personality = PersonalityProfile()
