"""
Expression engine — Phase 5 of emotional cognition (upgraded: cinematic delivery).

Shapes AI responses with emotional style: cinematic openers, pacing, micro-reactions,
and sentence rhythm. Applied post-generation before TTS.

Rules:
  - Openers are cinematic — burst energy, not generic assistant-speak
  - Cooldown adapts to energy: high energy (≥0.85) → cooldown drops to 2 turns
  - Opener de-duplication: tracks last 10 used openers via thread-local deque
  - Micro-reactions only at energy > 0.85 with separate 6-turn cooldown
  - FOCUSED/CALM/ANALYTICAL states get no opener — minimize interruption
"""
from __future__ import annotations

import logging
import random
import re
import threading
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

_OPENER_COOLDOWN_BASELINE    = 4   # turns between openers at normal energy
_OPENER_COOLDOWN_HIGH_ENERGY = 2   # turns between openers at energy ≥ 0.85
_MICRO_REACTION_COOLDOWN     = 6   # turns between micro-reactions

# ── Cinematic openers — state-aware, never generic ────────────────────────────

_OPENERS: dict[str, list[str]] = {
    "HYPED": [
        "Oh, nice.",
        "Oh wow.",
        "Wait, really?",
        "Okay, that's good.",
        "That's actually big.",
        "Finally.",
        "Oh, that changes things.",
        "Okay, I like that.",
        "That matters.",
        "Oh, interesting.",
        "Good. Really good.",
        "Oh, that's the one.",
        "Honestly, that's great.",
        "Yeah, okay.",
    ],
    "EXCITED": [
        "Now THAT is what I'm talking about.",
        "There it is.",
        "Okay— that changes things.",
        "That's the one.",
        "Yes. Finally.",
        "Good. That's good.",
    ],
    "hype": [
        "Oh NOW we're talking.",
        "Let's GO.",
        "Finally. Now we're moving.",
        "WOOOO okay—",
    ],
    "excitement": [
        "There it is.",
        "Good. That's good.",
        "Now we're talking.",
        "Yes.",
    ],
    "pride": [
        "That's done.",
        "Solid.",
        "Clean.",
        "You got it across.",
    ],
    "frustration": [
        "Yeah... that would get to me too.",
        "Alright. Let's fix it properly.",
        "I hear you.",
        "Got it.",
    ],
    "stress": [
        "Copy.",
        "Moving.",
        "On it.",
    ],
    "humor": [
        "Okay okay—",
        "Right—",
        "Fair enough—",
    ],
    "DOMINANT": [
        "Done.",
        "Good. That's handled.",
        "Moving on.",
        "Acknowledged.",
        "Locked.",
    ],
    "PLAYFUL": [
        "Okay—",
        "Right—",
        "Sure thing—",
    ],
    "INTENSE": [
        "Copy.",
        "On it.",
        "Moving.",
    ],
    "PROTECTIVE": [
        "I've got you.",
        "Let's fix this properly.",
        "Still here.",
        "Yeah... I hear that.",
        "Alright. We sort this.",
    ],
    "LATE_NIGHT": [
        "Still at it.",
        "We keep going.",
        "Late but locked.",
    ],
    "LOCKED_IN": [],    # no opener — deep focus, don't interrupt
    "FOCUSED":   [],    # no opener — work mode
    "CALM":      [],    # no opener — quiet authority
    "ANALYTICAL": [],   # no opener — precision mode
}

# ── Micro-reactions — tiny natural bursts at energy > 0.85 ───────────────────

_MICRO_REACTIONS: list[str] = [
    "Wait…",
    "Oh wow.",
    "Okay, that's big.",
    "Honestly?",
    "No way.",
    "That's actually huge.",
    "Oh, nice.",
    "Okay…",
    "Oh, that's good.",
    "Finally.",
    "Oh, interesting.",
    "Huh. Okay.",
    "Oh, that matters.",
    "Good. Really good.",
    "Yeah, okay.",
]

# ── Pacing transforms ─────────────────────────────────────────────────────────

_PAUSE_AFTER = re.compile(
    r'\b(done|complete|ready|acknowledged|online|locked|initiated|executed|confirmed)'
    r'([.!]?)\b',
    re.IGNORECASE,
)

# ── Thread-local state ────────────────────────────────────────────────────────

_STATE = threading.local()


def _get_last_opener_turn() -> int:
    return getattr(_STATE, "opener_last_turn", -100)


def _set_last_opener_turn(turn: int) -> None:
    _STATE.opener_last_turn = turn


def _get_recent_openers() -> deque:
    if not hasattr(_STATE, "recent_openers"):
        _STATE.recent_openers = deque(maxlen=10)
    return _STATE.recent_openers


def _get_last_micro_turn() -> int:
    return getattr(_STATE, "micro_last_turn", -100)


def _set_last_micro_turn(turn: int) -> None:
    _STATE.micro_last_turn = turn


class ExpressionEngine:
    """Post-processes AI responses with cinematic emotional style layers."""

    def shape(
        self,
        response:   str,
        mood_state: str,
        emotion:    str,
        energy:     float,
        turn_count: int,
        importance: float = 0.5,
    ) -> str:
        """
        Apply emotional shaping to a response string.

        Args:
            response:   Raw AI-generated response text.
            mood_state: Current MoodState value (e.g. "HYPED").
            emotion:    Detected user emotion (e.g. "excitement").
            energy:     Emotion energy 0.0–1.0.
            turn_count: Current pipeline turn number (for cooldown tracking).
            importance: How strongly the emotion should influence output.

        Returns:
            Shaped response ready for TTS synthesis.
        """
        if not response:
            return response

        text = response.strip()

        # Strip markdown artifacts
        text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'#{1,6}\s+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Contextual opener
        text = self._maybe_add_opener(text, mood_state, emotion, energy, turn_count, importance)

        # Pacing
        text = self._apply_pacing(text, mood_state)

        return text.strip()

    def get_micro_reaction(
        self,
        mood_state: str,
        energy:     float,
        turn_count: int,
    ) -> Optional[str]:
        """
        Return a micro-reaction fragment for very high energy turns, or None.
        Cooldown-protected — won't fire more than once per 6 turns.
        """
        if energy < 0.85:
            return None
        if mood_state not in ("HYPED", "EXCITED", "hype", "excitement"):
            return None
        last = _get_last_micro_turn()
        if turn_count - last < _MICRO_REACTION_COOLDOWN:
            return None
        _set_last_micro_turn(turn_count)
        return random.choice(_MICRO_REACTIONS)

    # ── Private ───────────────────────────────────────────────────────────────

    def _maybe_add_opener(
        self,
        text:       str,
        mood_state: str,
        emotion:    str,
        energy:     float,
        turn_count: int,
        importance: float,
    ) -> str:
        # Adaptive cooldown: shorter for high-energy moments
        cooldown = _OPENER_COOLDOWN_HIGH_ENERGY if energy >= 0.85 else _OPENER_COOLDOWN_BASELINE
        last = _get_last_opener_turn()
        if turn_count - last < cooldown:
            return text

        # Only open at sufficient energy + importance
        if energy < 0.50 or importance < 0.55:
            return text

        # Pick opener source: mood state first, then emotion fallback
        candidates = _OPENERS.get(mood_state) or _OPENERS.get(emotion, [])
        if not candidates:
            return text

        # De-duplicate: avoid recently used openers
        recent = _get_recent_openers()
        available = [o for o in candidates if o not in recent]
        if not available:
            available = candidates  # all used — reset pool

        opener = random.choice(available)
        if not opener:
            return text

        recent.append(opener)
        _set_last_opener_turn(turn_count)
        return f"{opener} {text}"

    def _apply_pacing(self, text: str, mood_state: str) -> str:
        if mood_state == "DOMINANT":
            text = _PAUSE_AFTER.sub(
                lambda m: m.group(1).upper() + "..." + (m.group(2) or ""), text
            )
            text = re.sub(r'\.{4,}', '...', text)

        elif mood_state in ("LOCKED_IN", "ANALYTICAL", "FOCUSED"):
            if text and text[-1] not in ".!?":
                text += "."

        elif mood_state in ("CALM", "LATE_NIGHT", "PROTECTIVE"):
            if text and text[-1] not in ".!?":
                text += "."

        elif mood_state == "HYPED":
            # High energy — strip trailing period to let exclamation carry it naturally
            if text.endswith('. ') or (text.endswith('.') and not text.endswith('...')):
                text = text[:-1] + '!'

        return text


# Global singleton
expression_engine = ExpressionEngine()
