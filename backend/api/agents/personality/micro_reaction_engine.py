from __future__ import annotations

"""
MicroReactionEngine — map events to optional audio/text reactions.

Audio assets are checked at runtime via Path.exists(). If an asset is
absent the engine silently falls back to text reactions. Neither missing
assets nor unexpected event names raise exceptions — the engine always
returns a safe empty string rather than blocking a response.

Log tags: [MICRO_REACTION_INSERTED]
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("api.agents.personality.micro_reaction_engine")


class MicroReactionEngine:
    """Insert subtle audio/text reactions keyed by (event, mode)."""

    ASSETS_DIR: Path = Path("backend/voice/reactions")

    ASSET_MAP: dict[str, str] = {
        "success": "chime.wav",
        "error":   "error_tone.wav",
        "thinking": "hmm.wav",
        "laugh":    "soft_laugh.wav",
    }

    # Text reactions: {event → {mode_value → text_prefix}}
    # Empty string = no text reaction for that combination.
    _TEXT_REACTIONS: dict[str, dict[str, str]] = {
        "success": {
            "default":      "",
            "friendly":     "✓ ",
            "funny":        "🎉 ",
            "jarvis":       "",
            "minimal":      "",
            "professional": "",
            "developer":    "[OK] ",
            "creative":     "",
            "research":     "",
        },
        "error": {
            "default":      "",
            "friendly":     "",
            "funny":        "😬 ",
            "jarvis":       "",
            "minimal":      "",
            "professional": "",
            "developer":    "[ERR] ",
            "creative":     "",
            "research":     "",
        },
        "thinking": {
            "default":      "",
            "friendly":     "",
            "funny":        "🤔 ",
            "jarvis":       "",
            "minimal":      "",
            "professional": "",
            "developer":    "",
            "creative":     "✨ ",
            "research":     "",
        },
        "laugh": {
            "default":      "",
            "friendly":     "",
            "funny":        "😄 ",
            "jarvis":       "",
            "minimal":      "",
            "professional": "",
            "developer":    "",
            "creative":     "",
            "research":     "",
        },
        "warning": {
            "default":      "",
            "friendly":     "⚠️ ",
            "funny":        "⚠️ ",
            "jarvis":       "",
            "minimal":      "",
            "professional": "",
            "developer":    "[WARN] ",
            "creative":     "",
            "research":     "",
        },
    }

    # ── Asset resolution ──────────────────────────────────────────────────────

    def get_asset_path(self, reaction_type: str) -> Optional[Path]:
        """Return the audio asset Path if it exists on disk, else None."""
        filename = self.ASSET_MAP.get(reaction_type)
        if not filename:
            return None
        path = self.ASSETS_DIR / filename
        return path if path.exists() else None

    # ── Decision ──────────────────────────────────────────────────────────────

    def should_insert(self, event: str, mode_value: str) -> bool:
        """
        Return True if a text reaction is appropriate for this event/mode.
        MINIMAL and PROFESSIONAL modes suppress most reactions.
        """
        if mode_value == "minimal":
            return False
        if mode_value == "professional" and event != "error":
            return False
        event_map = self._TEXT_REACTIONS.get(event, {})
        return bool(event_map.get(mode_value, ""))

    # ── Text reaction ─────────────────────────────────────────────────────────

    def get_text_reaction(self, event: str, mode_value: str) -> str:
        """
        Return text reaction prefix for the event/mode.
        Returns "" if none defined or mode is MINIMAL.
        Never raises.
        """
        if mode_value == "minimal":
            return ""
        event_map = self._TEXT_REACTIONS.get(event, {})
        reaction = event_map.get(mode_value, "")
        if reaction:
            logger.debug(
                "[MICRO_REACTION_INSERTED] type=%s mode=%s text=%r",
                event,
                mode_value,
                reaction,
            )
        return reaction
