"""Cognition package — cognitive state engine for Xyron."""
from .cognitive_state import (
    AttentionLevel,
    CognitiveState,
    MoodBias,
    cognitive_state,
)
from .state_transitions import (
    transition_to_idle,
    transition_to_listening,
    transition_to_processing,
    transition_to_speaking,
)

__all__ = [
    "AttentionLevel",
    "CognitiveState",
    "MoodBias",
    "cognitive_state",
    "transition_to_idle",
    "transition_to_listening",
    "transition_to_processing",
    "transition_to_speaking",
]
from .emotion_engine import EmotionEngine, EmotionResult, emotion_engine
from .mood_state_machine import MoodState, MoodStateMachine, MoodContext, mood_machine
from .expression_engine import ExpressionEngine, expression_engine
