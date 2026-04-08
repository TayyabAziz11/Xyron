"""Push-to-talk recorder for AI Operator voice pipeline.

Usage:
    recorder = PushToTalkRecorder()
    audio = recorder.record()          # blocks until silence or max_duration
    result = recorder.record_and_transcribe()   # records + returns text
"""
from __future__ import annotations

import logging
from typing import Optional
import numpy as np

from .audio_utils import record_until_silence, record_audio, SAMPLE_RATE
from .whisper_service import transcribe_audio

logger = logging.getLogger(__name__)


class PushToTalkRecorder:
    """Manages push-to-talk recording sessions."""

    def __init__(
        self,
        max_duration: float = 15.0,
        silence_threshold: float = 0.01,
        silence_duration: float = 1.2,
        sample_rate: int = SAMPLE_RATE,
    ):
        self.max_duration = max_duration
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate
        self._is_recording = False
        self._audio_buffer: Optional[np.ndarray] = None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def record(self) -> np.ndarray:
        """Record until silence detected or max_duration. Returns numpy float32 array."""
        self._is_recording = True
        logger.info("Push-to-talk: recording started (max %.1fs)", self.max_duration)
        try:
            audio = record_until_silence(
                max_duration=self.max_duration,
                silence_threshold=self.silence_threshold,
                silence_duration=self.silence_duration,
                sample_rate=self.sample_rate,
            )
            self._audio_buffer = audio
            return audio
        finally:
            self._is_recording = False
            logger.info("Push-to-talk: recording stopped")

    def record_and_transcribe(self, language: str = "en") -> dict:
        """Record audio then transcribe. Returns transcription result dict."""
        audio = self.record()
        logger.info("Transcribing %.1f seconds of audio...", len(audio) / self.sample_rate)
        result = transcribe_audio(audio, self.sample_rate, language)
        logger.info("Transcription: %r", result.get("text", ""))
        return result

    def get_last_audio(self) -> Optional[np.ndarray]:
        return self._audio_buffer
