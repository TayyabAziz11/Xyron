from __future__ import annotations

"""
EmotionPlanner — decide which emotional reaction prefix to insert.

Returns short prefix strings keyed by (event_type, mode).
MINIMAL mode always returns "".
Uses random.choice for variation within a mode's option list.
No I/O — must complete in < 1 ms.
"""

import logging
import random

logger = logging.getLogger("api.agents.personality.emotion_planner")

# {event_type: {mode_value: [options...]}}
# Empty string in options list means "no prefix" (valid choice for variation).
_REACTION_MAP: dict[str, dict[str, list[str]]] = {

    "success": {
        "default":      ["", "Great! "],
        "professional": [""],
        "friendly":     ["Awesome! ", "Great! ", ""],
        "jarvis":       [""],
        "minimal":      [""],
        "funny":        ["Haha! ", "Nailed it! ", ""],
        "developer":    [""],
        "creative":     ["Wonderful! ", ""],
        "research":     [""],
    },

    "thinking": {
        "default":      ["Let me think… ", "Hmm, ", ""],
        "professional": [""],
        "friendly":     ["Let me see… ", "Hmm, ", ""],
        "jarvis":       ["One moment, Sir. ", ""],
        "minimal":      [""],
        "funny":        ["Hmm, let me consult the oracle… ", ""],
        "developer":    ["Processing… ", ""],
        "creative":     ["Let me imagine… ", ""],
        "research":     ["Analyzing… ", ""],
    },

    "error": {
        "default":      ["I hit a snag. ", "Hmm, ", ""],
        "professional": [""],
        "friendly":     ["Oops! ", ""],
        "jarvis":       [""],
        "minimal":      [""],
        "funny":        ["Uh oh! ", "Whoops! ", ""],
        "developer":    ["Error detected. ", ""],
        "creative":     ["Hmm, ", ""],
        "research":     [""],
    },

    "humor": {
        "default":      ["", ""],
        "professional": [""],
        "friendly":     ["Haha, ", ""],
        "jarvis":       [""],
        "minimal":      [""],
        "funny":        ["Haha, ", "Ha! ", ""],
        "developer":    [""],
        "creative":     [""],
        "research":     [""],
    },

    "warning": {
        "default":      ["Heads up — ", ""],
        "professional": ["Note: ", ""],
        "friendly":     ["Just a heads-up! ", ""],
        "jarvis":       ["A word of caution, Sir. ", ""],
        "minimal":      [""],
        "funny":        ["Watch out! ⚠️ ", ""],
        "developer":    ["[WARN] ", ""],
        "creative":     ["A little warning… ", ""],
        "research":     ["Note: ", ""],
    },
}


class EmotionPlanner:
    """Return a mode-appropriate reaction prefix for a given event."""

    REACTIONS = {
        "success": [".", "Done.", ""],
        "thinking": ["Let me think...", "Hmm,", ""],
        "error": ["I hit a snag.", "Hmm,", ""],
        "humor": ["", "Haha,", ""],
    }

    def get_reaction(self, event_type: str, mode_value: str) -> str:
        """
        Returns a reaction prefix (possibly empty) for the event and mode.
        MINIMAL mode always returns "".
        Unknown events/modes return "".
        """
        if mode_value == "minimal":
            return ""

        event_map = _REACTION_MAP.get(event_type, {})
        options = event_map.get(mode_value, [""])
        return random.choice(options)
