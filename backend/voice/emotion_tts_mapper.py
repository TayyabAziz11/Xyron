"""
Emotion TTS mapper — Phase 6 of emotional cognition.

Transforms response text BEFORE Kokoro synthesis to shape:
  - Pacing (punctuation-driven breathing)
  - Pause density (ellipsis, em-dash)
  - Emphasis (capitalization of key words)
  - Sentence burst behavior (INTENSE/HYPED)

Modifies text only — does NOT change Kokoro speed parameter (that's in
voice_personality.py). The goal is punctuation-level control of rhythm.

Must NOT break TTS pipeline or add latency. All transforms are pure string ops.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSTransformResult:
    text:         str    # transformed text ready for synthesis
    speed_hint:   float  # suggested Kokoro speed multiplier (applied by caller)
    pause_factor: float  # relative pause density hint


# ── Speed hints per mood state ────────────────────────────────────────────────

_SPEED_MAP: dict[str, float] = {
    "CALM":       0.88,
    "FOCUSED":    1.02,
    "EXCITED":    1.10,
    "HYPED":      1.25,
    "PLAYFUL":    0.94,
    "DOMINANT":   0.80,
    "ANALYTICAL": 0.96,
    "LOCKED_IN":  1.00,
    "INTENSE":    1.12,
    "LATE_NIGHT": 0.85,
    "PROTECTIVE": 0.90,
}

_PAUSE_MAP: dict[str, float] = {
    "CALM":       1.3,
    "FOCUSED":    0.6,
    "EXCITED":    0.8,
    "HYPED":      0.5,
    "PLAYFUL":    1.0,
    "DOMINANT":   2.0,
    "ANALYTICAL": 0.7,
    "LOCKED_IN":  0.5,
    "INTENSE":    0.5,
    "LATE_NIGHT": 1.4,
    "PROTECTIVE": 1.2,
}

# Words to capitalize for emphasis per state (Kokoro reads caps with more energy)
_EMPHASIS_TARGETS: dict[str, list[str]] = {
    "HYPED":   ["now", "finally", "done", "go", "yes", "that"],
    "EXCITED": ["finally", "done", "yes", "perfect", "good"],
    "INTENSE": ["now", "done", "go", "fast", "clear"],
    "DOMINANT": ["acknowledged", "confirmed", "online", "locked"],
}

# Sentence-final status words that get a dramatic pause in DOMINANT mode
_DOMINANT_PAUSE = re.compile(
    r'\b(acknowledged|confirmed|complete|online|locked|initiated|ready|done|nominal)\b([.!]?)',
    re.IGNORECASE,
)

# Long sentence splitter for INTENSE/HYPED — split at natural commas
_COMMA_SPLIT = re.compile(r',\s+')

# Filler stripping for FOCUSED/LOCKED_IN
_FILLER = re.compile(
    r'^(Of course|Certainly|Sure thing|Sure|Absolutely|Definitely|No problem|Got it)[,!.]?\s*',
    re.IGNORECASE,
)


class EmotionTTSMapper:
    """Maps emotional state to TTS-ready text transformations."""

    def transform(
        self,
        text:       str,
        mood_state: str,
        emotion:    Optional[str] = None,
        energy:     float = 0.5,
    ) -> TTSTransformResult:
        """
        Apply emotion-driven text transformations before Kokoro synthesis.

        Args:
            text:       The response text to transform.
            mood_state: Current MoodState value string.
            emotion:    Detected user emotion (optional).
            energy:     Emotion energy 0.0–1.0.

        Returns:
            TTSTransformResult with shaped text and speed/pause hints.
        """
        if not text:
            return TTSTransformResult(text=text, speed_hint=1.0, pause_factor=1.0)

        t = text.strip()
        state = mood_state.upper()

        speed  = _SPEED_MAP.get(state, 0.92)
        pauses = _PAUSE_MAP.get(state, 1.0)

        # ── Universal: strip filler openers in work-focused states ────────
        if state in ("FOCUSED", "ANALYTICAL", "LOCKED_IN", "DOMINANT", "INTENSE"):
            t = _FILLER.sub("", t).strip()

        # ── State-specific transforms ─────────────────────────────────────

        if state == "DOMINANT":
            t = self._dominant_transform(t)

        elif state == "HYPED":
            t = self._hyped_transform(t)

        elif state == "EXCITED":
            t = self._excited_transform(t)

        elif state == "INTENSE":
            t = self._intense_transform(t)

        elif state in ("CALM", "LATE_NIGHT", "PROTECTIVE"):
            t = self._calm_transform(t)

        elif state in ("LOCKED_IN", "ANALYTICAL"):
            t = self._locked_transform(t)

        elif state == "PLAYFUL":
            t = self._playful_transform(t)

        # Ensure clean ending
        t = re.sub(r'\s+', ' ', t).strip()

        return TTSTransformResult(text=t, speed_hint=speed, pause_factor=pauses)

    # ── State-specific text transforms ────────────────────────────────────────

    def _dominant_transform(self, t: str) -> str:
        """Deliberate pauses at status words. Cold, precise."""
        t = _DOMINANT_PAUSE.sub(lambda m: m.group(1).upper() + "..." + (m.group(2) or ""), t)
        t = re.sub(r'\.{4,}', '...', t)
        return t

    def _hyped_transform(self, t: str) -> str:
        """Punchy. Emphasize key words. Short burst sentences."""
        # Capitalize emphasis targets
        for word in _EMPHASIS_TARGETS.get("HYPED", []):
            t = re.sub(rf'\b{re.escape(word)}\b', word.upper(), t, flags=re.IGNORECASE)
        # Add energy dash if sentence ends flatly
        if t and t[-1] == '.':
            t = t[:-1] + '.'
        return t

    def _excited_transform(self, t: str) -> str:
        """Brief energy. Key words get light emphasis."""
        for word in _EMPHASIS_TARGETS.get("EXCITED", []):
            t = re.sub(rf'\b{re.escape(word)}\b', word.upper(), t, count=1, flags=re.IGNORECASE)
        return t

    def _intense_transform(self, t: str) -> str:
        """Fast and direct. Split at commas for staccato rhythm."""
        # Shorten by removing hedging
        t = re.sub(r'\b(I think|I believe|it seems|probably|maybe)\b\s*', '', t, flags=re.IGNORECASE)
        # Replace commas with periods for staccato rhythm
        if t.count(',') >= 2:
            t = _COMMA_SPLIT.sub('. ', t, count=2)
        return t

    def _calm_transform(self, t: str) -> str:
        """Smooth, no abrupt endings. Natural sentence close."""
        if t and t[-1] not in '.!?':
            t += '.'
        # Soften any remaining exclamations
        t = re.sub(r'!+', '.', t)
        return t

    def _locked_transform(self, t: str) -> str:
        """Ultra-minimal. Hard stop. No softening."""
        t = re.sub(r'[.!]+$', '.', t)
        return t

    def _playful_transform(self, t: str) -> str:
        """Light rhythm. Keep natural flow."""
        if t and t[-1] not in '.!?':
            t += '.'
        return t


# Global singleton
emotion_tts_mapper = EmotionTTSMapper()
