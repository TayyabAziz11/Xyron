"""
Emotion-driven audio FX mapper — Phase 2 of performance upgrade.

Maps MoodState values to voice.audio_fx presets for Kokoro TTS output.
Called inside _kokoro_to_wav to override the voice_personality preset
when mood state requires stronger emotional audio shaping.

Preset effects (defined in voice.audio_fx):
  cinematic — reverb, bass boost, saturation, high normalization (HYPED)
  crisp     — tight compression, air, minimal reverb (DOMINANT/INTENSE/FOCUSED)
  warm      — warm EQ, softer compression, reverb (PROTECTIVE/CALM/LATE_NIGHT)
  subtle    — light compression + reverb (default)
"""
from __future__ import annotations

# States that produce strong emotional audio — override voice_personality preset
_MOOD_OVERRIDE_STATES = frozenset({
    "HYPED", "DOMINANT", "INTENSE", "PROTECTIVE", "EXCITED",
})

_MOOD_PRESET: dict[str, str] = {
    "HYPED":      "hyped",
    "EXCITED":    "subtle",
    "DOMINANT":   "crisp",
    "INTENSE":    "crisp",
    "PROTECTIVE": "warm",
    "LATE_NIGHT": "warm",
    "CALM":       "warm",
    "PLAYFUL":    "subtle",
    "FOCUSED":    "crisp",
    "ANALYTICAL": "crisp",
    "LOCKED_IN":  "crisp",
}


def get_emotion_preset(mood_state: str) -> str:
    """Return the FX preset for the given mood state. Falls through to 'subtle'."""
    return _MOOD_PRESET.get(mood_state.upper(), "subtle")


def should_override(mood_state: str) -> bool:
    """
    True if the mood state should override the voice_personality preset.
    Only strong emotional states override — neutral states use voice_personality.
    """
    return mood_state.upper() in _MOOD_OVERRIDE_STATES
