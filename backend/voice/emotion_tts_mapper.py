"""
Maps mood state to TTS prosody hints (speed, pitch).
Used by voice.py emotional guard to adjust pacing for emotionally-charged responses.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class TTSTransformResult:
    speed_hint: float  # 0.8–1.4; 1.0 = normal
    pitch_hint: float  # semitone shift; 0 = unchanged


_MOOD_PRESETS: dict[str, TTSTransformResult] = {
    "CALM":       TTSTransformResult(speed_hint=0.95, pitch_hint=-0.5),
    "FOCUSED":    TTSTransformResult(speed_hint=1.0,  pitch_hint=0.0),
    "EXCITED":    TTSTransformResult(speed_hint=1.1,  pitch_hint=0.5),
    "HYPED":      TTSTransformResult(speed_hint=1.2,  pitch_hint=1.0),
    "PLAYFUL":    TTSTransformResult(speed_hint=1.05, pitch_hint=0.5),
    "DOMINANT":   TTSTransformResult(speed_hint=0.9,  pitch_hint=-1.0),
    "ANALYTICAL": TTSTransformResult(speed_hint=0.95, pitch_hint=0.0),
    "LOCKED_IN":  TTSTransformResult(speed_hint=1.0,  pitch_hint=0.0),
    "INTENSE":    TTSTransformResult(speed_hint=1.1,  pitch_hint=0.5),
    "LATE_NIGHT": TTSTransformResult(speed_hint=0.85, pitch_hint=-0.5),
    "PROTECTIVE": TTSTransformResult(speed_hint=0.9,  pitch_hint=-0.5),
}
_DEFAULT = TTSTransformResult(speed_hint=1.0, pitch_hint=0.0)


class EmotionTTSMapper:
    def transform(self, text: str, mood: str) -> TTSTransformResult:
        return _MOOD_PRESETS.get(mood.upper(), _DEFAULT)


emotion_tts_mapper = EmotionTTSMapper()
