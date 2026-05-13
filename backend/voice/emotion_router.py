"""
Emotion router for Xyron voice identity.
Maps detected emotional state (from CognitiveState) to voice delivery parameters.
"""
from __future__ import annotations

from typing import Optional


# ── Emotion → voice param mappings ───────────────────────────────────────────

_EMOTION_PARAMS: dict[str, dict] = {
    "neutral":   {"speed_delta": 0.0,   "fx_wet_scale": 1.0,  "suggested_mode": "DEFAULT"},
    "happy":     {"speed_delta": +0.05, "fx_wet_scale": 0.9,  "suggested_mode": "DEFAULT"},
    "laughing":  {"speed_delta": +0.08, "fx_wet_scale": 0.8,  "suggested_mode": "CHILL"},
    "joyful":    {"speed_delta": +0.06, "fx_wet_scale": 0.85, "suggested_mode": "DEFAULT"},
    "excited":   {"speed_delta": +0.10, "fx_wet_scale": 0.8,  "suggested_mode": "WORK"},
    "stressed":  {"speed_delta": +0.05, "fx_wet_scale": 1.1,  "suggested_mode": "WORK"},
    "tired":     {"speed_delta": -0.10, "fx_wet_scale": 1.2,  "suggested_mode": "CHILL"},
    "sad":       {"speed_delta": -0.08, "fx_wet_scale": 1.3,  "suggested_mode": "HOME"},
    "curious":   {"speed_delta": +0.02, "fx_wet_scale": 1.0,  "suggested_mode": "DEFAULT"},
    "focused":   {"speed_delta": 0.0,   "fx_wet_scale": 0.9,  "suggested_mode": "WORK"},
    "surprised": {"speed_delta": +0.05, "fx_wet_scale": 0.85, "suggested_mode": "DEFAULT"},
    "bored":     {"speed_delta": -0.05, "fx_wet_scale": 1.1,  "suggested_mode": "CHILL"},
}

_DEFAULT_PARAMS = {"speed_delta": 0.0, "fx_wet_scale": 1.0, "suggested_mode": "DEFAULT"}


def get_voice_params_for_emotion(emotion: str, intensity: float = 0.5) -> dict:
    """
    Given an emotion label and 0–1 intensity, return adjusted voice delivery params.

    Returns:
        speed_delta   — add to base speed (clamped to keep 0.70–1.30 total)
        fx_wet_scale  — multiply FX wet amounts by this factor
        suggested_mode — voice mode that best fits this emotion
    """
    base = _EMOTION_PARAMS.get(emotion.lower(), _DEFAULT_PARAMS)
    scale = max(0.0, min(1.0, intensity))
    return {
        "speed_delta":    base["speed_delta"]   * scale,
        "fx_wet_scale":   1.0 + (base["fx_wet_scale"] - 1.0) * scale,
        "suggested_mode": base["suggested_mode"],
    }


def adjust_speed(base_speed: float, emotion: str, intensity: float = 0.5) -> float:
    """Return Kokoro speed value adjusted for the current emotional state."""
    delta = get_voice_params_for_emotion(emotion, intensity)["speed_delta"]
    return float(max(0.70, min(1.30, base_speed + delta)))


def suggest_mode_for_emotion(emotion: str, intensity: float = 0.5) -> Optional[str]:
    """
    If emotion intensity is strong enough, suggest an auto-mode override.
    Returns None if intensity is too low to warrant a mode switch.
    """
    if intensity < 0.65:
        return None
    return get_voice_params_for_emotion(emotion, intensity)["suggested_mode"]
