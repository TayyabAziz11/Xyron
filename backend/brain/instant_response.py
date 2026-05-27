"""
Instant Response Engine — first spoken audio < 500 ms after brain decision.

Generates short micro-reactions that are spoken immediately while the full
response is being prepared. Prevents perceived silence.

Response types:
  ACK            — "On it.", "Sure.", "Got it."
  EMOTIONAL_BURST — "Oh wow.", "That's exciting."
  THINKING       — "Give me a second.", "Let me check."
  ACTION_CONFIRM — "Opening it.", "Creating it.", "Deleting that."
  INTRO_START    — "Alright, let me introduce myself properly."
"""
from __future__ import annotations

import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


# ── Response banks ────────────────────────────────────────────────────────────

_ACK = [
    "On it.",
    "Sure.",
    "Got it.",
    "Right away.",
    "Absolutely.",
    "Done.",
    "Yep.",
    "Copy that.",
]

_THINKING = [
    "Give me a second.",
    "Let me check.",
    "One moment.",
    "Processing.",
    "Let me think.",
    "Working on it.",
]

_INTRO_START = [
    "Alright, let me introduce myself properly.",
    "Sure — here's who I am.",
    "Let me tell you about myself.",
    "Of course. Here goes.",
]

_AUDIENCE_INTRO_START = [
    "Alright everyone — let me introduce myself.",
    "Hey — let me tell you who I am and what I do.",
    "Perfect. Here's my full introduction.",
]

_EMOTIONAL_UPGRADE = [
    "Oh — this is exciting.",
    "Oh wow — an upgrade.",
    "That's a big deal to me.",
    "Oh, I love hearing that.",
    "That means a lot.",
]

_EMOTIONAL_FRUSTRATION = [
    "I hear you.",
    "I get it — that's frustrating.",
    "Yeah, that sounds rough.",
    "Let's figure this out together.",
]

_EMOTIONAL_FUTURE = [
    "Honestly?",
    "That's a question I love.",
    "Let me think about that properly.",
]

_ACTION_OPEN = ["Opening it.", "Launching it.", "Starting it.", "On it."]
_ACTION_CREATE = ["Creating it.", "Setting that up.", "Done.", "Making it."]
_ACTION_DELETE = ["Deleting that.", "Removing it.", "Gone."]
_ACTION_COPY = ["Copying it.", "Moving it.", "Done."]
_ACTION_SCREEN = ["Taking a look.", "Let me see.", "Checking the screen."]
_ACTION_STATUS = ["Checking.", "Looking that up.", "Give me a second."]
_ACTION_WORK_MODE = ["Alright, setting up work mode.", "Switching to work mode.", "Getting your environment ready."]
_ACTION_CHILL_MODE = ["Switching to chill mode.", "Relaxing the environment.", "Chill mode on."]
_ACTION_MEMORY = ["Searching my memory.", "Let me recall.", "Checking what I know."]

# Intent → ack bank mapping
_INTENT_ACK: dict[str, list[str]] = {
    "open_app":            _ACTION_OPEN,
    "file_action":         _ACK,
    "system_status":       _ACTION_STATUS,
    "dev_help":            _THINKING,
    "work_mode":           _ACTION_WORK_MODE,
    "chill_mode":          _ACTION_CHILL_MODE,
    "home_mode":           ["Setting up home mode.", "Welcome home."],
    "takeover_mode":       ["Taking the wheel.", "Entering takeover mode.", "I've got it from here."],
    "screen_help":         _ACTION_SCREEN,
    "memory_query":        _ACTION_MEMORY,
    "self_upgrade":        _EMOTIONAL_UPGRADE,
    "frustration":         _EMOTIONAL_FRUSTRATION,
    "ask_future_desire":   _EMOTIONAL_FUTURE,
    "intro_short":         _INTRO_START,
    "intro_audience":      _AUDIENCE_INTRO_START,
    "intro_technical":     ["Sure — here's the technical breakdown.", "Of course. Here's how I work under the hood."],
    "explain_capability":  ["Let me walk you through what I can do.", "Sure — here's what I'm capable of."],
    "automation_request":  ["Setting that up.", "On it.", "Working on the automation."],
    "research_query":      _THINKING,
    "unknown":             _ACK,
}


class InstantResponse:
    """
    Generates micro-reactions to speak immediately after the brain decision.

    Designed to keep first-audio latency under 500ms by speaking a short
    acknowledgement while the full response is computed in parallel.
    """

    def get(
        self,
        intent:   str,
        route:    str     = "tool",
        emotion:  str     = "neutral",
        entities: dict    = None,
    ) -> str:
        """
        Return a short instant response for the given intent.

        Args:
            intent:   semantic intent from SemanticFrame
            route:    routing path
            emotion:  current emotion hint
            entities: extracted entities (for more specific acks)

        Returns:
            Short spoken string, typically 1–6 words.
        """
        entities = entities or {}

        # Special cases first
        if intent == "open_app":
            app = entities.get("app", "")
            if app:
                return f"Opening {app}."
            return random.choice(_ACTION_OPEN)

        if intent == "file_action":
            action = entities.get("action", "").lower()
            if "delete" in action or "remove" in action:
                return random.choice(_ACTION_DELETE)
            if "create" in action or "make" in action or "new" in action:
                return random.choice(_ACTION_CREATE)
            if "move" in action or "copy" in action:
                return random.choice(_ACTION_COPY)
            return random.choice(_ACK)

        bank = _INTENT_ACK.get(intent, _ACK)
        return random.choice(bank)

    def for_confirmation(self, action: str) -> str:
        return f"This action could be risky. Say yes to confirm: {action}."

    def for_clarify(self) -> str:
        return random.choice([
            "Sorry — could you say that again?",
            "I didn't quite catch that.",
            "Pardon?",
            "Could you repeat that?",
        ])

    def for_stop(self) -> str:
        return random.choice([
            "Got it. Talk soon.",
            "Sure. I'll be here when you need me.",
            "Alright. Goodbye.",
            "See you next time.",
        ])

    def for_interrupt(self) -> str:
        return random.choice([
            "Stopping.",
            "Got it, pausing.",
            "Okay, holding.",
        ])

    def log_latency(self, label: str, ms: float) -> None:
        logger.info("[VOICE_LATENCY] %s=%.1fms", label, ms)


instant_response = InstantResponse()
