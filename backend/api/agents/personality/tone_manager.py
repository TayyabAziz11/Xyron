from __future__ import annotations

"""
ToneManager — phrase-level transforms from raw text to mode-appropriate text.

All transforms are simple str.replace() calls applied in descending length order
(longest match first) to prevent shorter patterns shadowing longer ones.
No regex, no I/O — must complete in < 1 ms.
"""

import logging

logger = logging.getLogger("api.agents.personality.tone_manager")

# Circular-import guard: PersonalityMode is imported as a string key here.
# The engine passes mode.value (a plain string) rather than the enum instance.

# Keys are PersonalityMode.value strings.
_TRANSFORMS: dict[str, dict[str, str]] = {

    "default": {
        # Make responses warmer / more personal
        "Opening ":    "I'll open ",
        "Launching ":  "I'll launch ",
        "Closing ":    "I'll close ",
        "Searching ":  "I'm searching for ",
        "Done.":       "Done. Everything is ready.",
        "Error.":      "I hit a problem there. Let me try another way.",
    },

    "professional": {
        # Strip filler, keep it crisp
        "I'll open ":   "Opening ",
        "I'll launch ": "Launching ",
        "I'll close ":  "Closing ",
        "Let me ":      "",
        "Sure! ":       "",
        "Sure, ":       "",
        "Of course! ":  "",
        "I'm searching for ": "Searching for ",
        "Done. Everything is ready.":
            "Task completed.",
        "I hit a problem there. Let me try another way.":
            "An error occurred. Attempting alternative approach.",
    },

    "friendly": {
        "Opening ":     "Sure! Opening ",
        "Launching ":   "Sure! Launching ",
        "Done.":        "Done! All set!",
        "Error.":       "Oops! I hit a snag there, but I'll sort it out!",
        "Searching ":   "Let me search for ",
    },

    "jarvis": {
        "I'll open ":   "Opening ",
        "I'll launch ": "Launching ",
        "I'll close ":  "Closing ",
        "Let me ":      "",
        "Sure! ":       "",
        "Sure, ":       "",
        "Of course! ":  "",
        "Done.":        "Task complete, Sir.",
        "Done! All set!": "Task complete, Sir.",
        "Error.":
            "I've encountered an obstacle, Sir. Attempting alternative approach.",
        "I hit a problem there. Let me try another way.":
            "I've encountered an obstacle, Sir. Attempting alternative approach.",
    },

    "minimal": {
        # Strip every filler phrase — leave only the core verb-object
        "I'll open ":   "",
        "I'll launch ": "",
        "I'll close ":  "",
        "I'm ":         "",
        "Let me ":      "",
        " for you":     "",
        " right now":   "",
        " right away":  "",
        "Sure! ":       "",
        "Sure, ":       "",
        "Of course! ":  "",
        "Of course, ":  "",
        "Done. Everything is ready.": "Done.",
        "Done! All set!": "Done.",
        "I hit a problem there. Let me try another way.": "Error. Retrying.",
    },

    "funny": {
        "Opening ":   "Opening (beep boop) ",
        "Launching ": "Launching (3… 2… 1…) ",
        "Done.":      "Done! Nailed it! 🎉",
        "Error.":     "Whoops! Something went sideways. 😅 Let me try again.",
    },

    "developer": {
        "I'll open ":   "Launching process: ",
        "Opening ":     "Initiating: ",
        "Launching ":   "Spawning: ",
        "Done.":        "Process exited 0. All good.",
        "Error.":       "Error detected. Stack trace captured. Attempting fallback.",
        "I hit a problem there. Let me try another way.":
            "Exception encountered. Switching to fallback path.",
    },

    "creative": {
        "Opening ":   "Let me open ",
        "Launching ": "Bringing to life: ",
        "Done.":      "And… done! The stage is set.",
        "Error.":     "Hmm, we hit a creative block. Let me find another way.",
    },

    "research": {
        "I'll open ":   "Opening ",
        "Launching ":   "Initiating ",
        "Done.":        "Complete. Data retrieved successfully.",
        "Error.":       "Process failed. Attempting alternative methodology.",
        "I hit a problem there. Let me try another way.":
            "Initial approach failed. Switching to secondary method.",
    },
}


class ToneManager:
    """Apply mode-specific phrase transforms to raw response text."""

    def apply(self, text: str, mode_value: str) -> str:
        """
        Apply transforms for *mode_value* to *text*.

        Transforms are applied longest-key-first to avoid partial shadowing.
        Returns the transformed string (unchanged if no transforms match).
        """
        transforms = _TRANSFORMS.get(mode_value, {})
        if not transforms:
            return text

        result = text
        # Sort descending by key length to match longer patterns first
        for old, new in sorted(transforms.items(), key=lambda kv: len(kv[0]), reverse=True):
            if old in result:
                result = result.replace(old, new)

        return result
