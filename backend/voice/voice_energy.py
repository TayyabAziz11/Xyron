"""
Voice energy analysis — Phase 2 of emotional cognition.

Wraps AudioEmotionDetector to expose a clean VoiceEnergyResult with
normalized RMS, stress estimation, and a five-class voice_state label.

Uses real acoustic metrics from librosa (via the existing detector).
No fake randomness. Returns neutral on any failure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Normalization reference values — based on typical microphone speech
_RMS_REF       = 0.08   # RMS amplitude at which energy_norm = 1.0
_ZCR_STRESS    = 0.20   # zero-crossing rate fully contributing to stress
_RATE_STRESS   = 8.0    # syllable peaks/sec fully contributing to stress
_PITCH_CENTER  = 150.0  # Hz — neutral speaking pitch


@dataclass
class VoiceEnergyResult:
    voice_state: str   # excited | stressed | calm | tired | neutral
    energy:      float  # 0.0–1.0 normalized RMS loudness
    stress:      float  # 0.0–1.0 composite stress signal


class VoiceEnergyAnalyzer:
    """Analyzes voice energy from audio bytes using the existing acoustic pipeline."""

    def analyze(self, audio_bytes: bytes, sample_rate: int = 16000) -> VoiceEnergyResult:
        """
        Extract voice energy from audio bytes.

        Delegates to AudioEmotionDetector for feature extraction, then
        maps acoustic features to the VoiceEnergyResult format.
        Returns neutral on any failure to avoid blocking the pipeline.
        """
        try:
            from voice.emotion_detector import AudioEmotionDetector
            result = AudioEmotionDetector().detect(audio_bytes, sample_rate)
            return self._map(result)
        except Exception as exc:
            logger.debug("[VoiceEnergy] analysis failed: %s", exc)
            return VoiceEnergyResult("neutral", 0.5, 0.0)

    def _map(self, audio_result) -> VoiceEnergyResult:
        feats = audio_result.features or {}
        raw_energy  = feats.get("energy", 0.02)
        pitch       = feats.get("pitch", _PITCH_CENTER)
        speech_rate = feats.get("speech_rate", 4.0)
        zcr         = feats.get("zcr", 0.08)

        energy_norm = min(1.0, raw_energy / _RMS_REF)

        # Composite stress: fast rate + high ZCR + pitch deviation
        stress = min(1.0, (
            (zcr         / _ZCR_STRESS)              * 0.40 +
            (speech_rate / _RATE_STRESS)             * 0.40 +
            (abs(pitch - _PITCH_CENTER) / _PITCH_CENTER) * 0.20
        ))

        _label_map = {
            "excited":  "excited",
            "happy":    "excited",
            "stressed": "stressed",
            "tired":    "tired",
            "sad":      "calm",
            "neutral":  "neutral",
        }
        voice_state = _label_map.get(audio_result.emotion, "neutral")

        return VoiceEnergyResult(
            voice_state=voice_state,
            energy=round(energy_norm, 3),
            stress=round(stress, 3),
        )


voice_energy_analyzer = VoiceEnergyAnalyzer()
