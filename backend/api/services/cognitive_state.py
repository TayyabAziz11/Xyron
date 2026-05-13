"""
Shim — re-exports the canonical cognitive state from the cognition package.

All routers that do `from ..services.cognitive_state import cognitive_state`
continue to work without changes. New code should import directly from
`cognition.cognitive_state` instead.
"""
from cognition.cognitive_state import (  # noqa: F401
    AttentionLevel,
    CognitiveState,
    MoodBias,
    cognitive_state,
)

# Kept here for cognition router backward-compatibility.
VALID_EMOTIONS: frozenset[str] = frozenset({
    "neutral", "happy", "laughing", "joyful", "excited",
    "stressed", "tired", "sad", "curious", "focused",
    "surprised", "bored",
})

VALID_ATTENTION: frozenset[str] = frozenset({
    "IDLE", "LISTENING", "PROCESSING", "SPEAKING",
})
