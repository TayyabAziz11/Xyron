"""
Voice conversion facade — thin adapter so existing _vc.convert() call sites
work unchanged while delegating to the full RVC engine.

Existing callers pass (audio_bytes, emotion_state, intensity="NORMAL").
The RVC engine resolves the preset from emotion_state automatically.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class VoiceConversionEngine:
    """Thin facade over rvc_engine. Keeps backward-compatible call signature."""

    def __init__(self) -> None:
        from voice.rvc_engine import rvc_engine as _re
        self._engine = _re
        status = _re.get_status()
        logger.info(
            "[VOICE_CONVERSION] tier=%s enabled=%s presets=%d last_latency_ms=%.0f",
            status["tier"], status["enabled"],
            len(status["available_presets"]), status["last_latency_ms"],
        )

    def is_available(self) -> bool:
        return self._engine.is_available()

    def convert(
        self,
        audio_bytes:   bytes,
        emotion_state: str = "NEUTRAL",
        intensity:     str = "NORMAL",
    ) -> bytes:
        """
        Convert voice audio. Returns audio_bytes unchanged if RVC is disabled or fails.

        Args:
            audio_bytes:   WAV bytes from Kokoro.
            emotion_state: MoodStateLabel (HYPED, CALM, DOMINANT, …).
            intensity:     EXTREME | HIGH | NORMAL — scales transform strength.
        """
        preset = self._engine.mood_to_preset(emotion_state)
        return self._engine.convert(
            audio_bytes   = audio_bytes,
            preset        = preset,
            emotion_state = emotion_state,
            intensity     = intensity,
        )

    def list_presets(self) -> list[str]:
        return self._engine.list_presets()

    def get_status(self) -> dict:
        return self._engine.get_status()


# Singleton — imported as `from voice.voice_conversion import voice_conversion`
voice_conversion = VoiceConversionEngine()
