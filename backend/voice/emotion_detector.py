"""
Audio-based emotion detection using rule-based acoustic signal analysis.

No ML model required. Features: pitch (pyin), RMS energy, zero-crossing rate,
and speech rate (syllable-like peak counting). Rule-based heuristics map the
feature vector to one of six emotion labels.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MAX_DURATION_SEC = 10  # only analyze first N seconds to bound librosa overhead


@dataclass
class EmotionResult:
    emotion: str       # neutral | happy | stressed | tired | excited | sad
    confidence: float
    features: dict = field(default_factory=dict)  # pitch, energy, zcr, speech_rate


class AudioEmotionDetector:
    """Rule-based audio emotion detector using acoustic features."""

    def detect(self, audio_bytes: bytes, sample_rate: int = 16000) -> EmotionResult:
        """Detect emotion from audio bytes (container format or raw int16 PCM).

        Decodes to 16kHz mono float32, extracts acoustic features, and applies
        rule-based heuristics to classify emotion. Returns neutral on any error.
        """
        try:
            y = self._decode_to_float32(audio_bytes, sample_rate)
            if y is None or len(y) == 0:
                return EmotionResult(emotion="neutral", confidence=0.0, features={})

            # Trim to first MAX_DURATION_SEC to keep librosa fast
            max_samples = _MAX_DURATION_SEC * sample_rate
            if len(y) > max_samples:
                y = y[:max_samples]

            features = self._extract_features(y, sample_rate)
            emotion, confidence = self._classify(features)

            log_feats = {k: round(float(v), 3) for k, v in features.items()}
            logger.info(
                "[EmotionDetector] emotion=%s conf=%.2f features=%s",
                emotion, confidence, log_feats,
            )
            return EmotionResult(emotion=emotion, confidence=confidence, features=features)

        except Exception as exc:
            logger.warning("[EmotionDetector] detection failed: %s", exc)
            return EmotionResult(emotion="neutral", confidence=0.0, features={})

    # ── Decoding ──────────────────────────────────────────────────────────────

    def _decode_to_float32(
        self, audio_bytes: bytes, sample_rate: int
    ) -> Optional[np.ndarray]:
        """Decode container audio (WebM/WAV/etc.) to float32 PCM at target sample rate."""
        # Attempt 1: soundfile (handles WAV/FLAC/OGG natively, fast, no subprocess)
        try:
            import io
            import soundfile as sf
            y, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
            if sr != sample_rate:
                import librosa
                y = librosa.resample(y, orig_sr=sr, target_sr=sample_rate)
            return y
        except Exception:
            pass

        # Attempt 2: ffmpeg subprocess (handles WebM/MP4/Opus/etc.)
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", "pipe:0",
                    "-f", "s16le",
                    "-ar", str(sample_rate),
                    "-ac", "1",
                    "pipe:1",
                ],
                input=audio_bytes,
                capture_output=True,
                timeout=10,
            )
            if proc.returncode == 0 and len(proc.stdout) >= 100:
                pcm = np.frombuffer(proc.stdout, dtype=np.int16)
                return pcm.astype(np.float32) / 32768.0
        except FileNotFoundError:
            pass  # ffmpeg not installed
        except Exception:
            pass

        # Attempt 3: treat bytes as raw int16 PCM as last resort
        try:
            pcm = np.frombuffer(audio_bytes, dtype=np.int16)
            return pcm.astype(np.float32) / 32768.0
        except Exception:
            return None

    # ── Feature extraction ────────────────────────────────────────────────────

    def _extract_features(self, y: np.ndarray, sample_rate: int) -> dict:
        """Extract pitch, RMS energy, ZCR, and speech rate from float32 audio."""
        import librosa
        from scipy.signal import find_peaks

        # Pitch via pyin — designed for monophonic voiced speech
        f0, _voiced_flag, _voiced_prob = librosa.pyin(
            y, fmin=80.0, fmax=400.0, sr=sample_rate
        )
        pitch = float(np.nanmean(f0)) if (f0 is not None and not np.all(np.isnan(f0))) else 0.0

        # RMS energy
        energy = float(np.sqrt(np.mean(y ** 2)))

        # Zero-crossing rate
        zcr = float(librosa.feature.zero_crossing_rate(y).mean())

        # Speech rate: count syllable-like energy peaks per second
        hop = 512
        rms_envelope = librosa.feature.rms(y=y, hop_length=hop)[0]
        peaks, _ = find_peaks(rms_envelope, height=rms_envelope.mean(), distance=4)
        duration_sec = len(y) / sample_rate
        speech_rate = len(peaks) / duration_sec if duration_sec > 0 else 0.0

        return {
            "pitch": pitch,
            "energy": energy,
            "zcr": zcr,
            "speech_rate": speech_rate,
        }

    # ── Classification ────────────────────────────────────────────────────────

    # Empirical thresholds — tune after observing real-world distributions.
    _HIGH_PITCH     = 180.0   # Hz
    _LOW_PITCH      = 120.0   # Hz
    _VERY_LOW_PITCH = 90.0    # Hz — distinguishes sad from tired
    _HIGH_ENERGY    = 0.04    # RMS amplitude
    _LOW_ENERGY     = 0.015
    _HIGH_ZCR       = 0.12    # zero-crossing rate
    _HIGH_RATE      = 5.0     # syllable peaks per second
    _LOW_RATE       = 3.0

    def _classify(self, features: dict) -> tuple[str, float]:
        """Map acoustic features to an emotion label and confidence score."""
        pitch       = features["pitch"]
        energy      = features["energy"]
        zcr         = features["zcr"]
        speech_rate = features["speech_rate"]

        hp = pitch  >= self._HIGH_PITCH
        lp = pitch  <= self._LOW_PITCH
        he = energy >= self._HIGH_ENERGY
        le = energy <= self._LOW_ENERGY
        hz = zcr    >= self._HIGH_ZCR
        hr = speech_rate >= self._HIGH_RATE
        lr = speech_rate <= self._LOW_RATE

        # Rule 1: high pitch + high energy + high rate → excited or stressed
        if hp and he and hr:
            if hz:
                return "stressed", self._confidence(
                    [pitch - self._HIGH_PITCH, energy - self._HIGH_ENERGY,
                     zcr - self._HIGH_ZCR, speech_rate - self._HIGH_RATE],
                    [self._HIGH_PITCH, self._HIGH_ENERGY, self._HIGH_ZCR, self._HIGH_RATE],
                )
            return "excited", self._confidence(
                [pitch - self._HIGH_PITCH, energy - self._HIGH_ENERGY,
                 speech_rate - self._HIGH_RATE],
                [self._HIGH_PITCH, self._HIGH_ENERGY, self._HIGH_RATE],
            )

        # Rule 2: low pitch + low energy + low rate → tired or sad
        if lp and le and lr:
            label = "sad" if pitch < self._VERY_LOW_PITCH else "tired"
            return label, self._confidence(
                [self._LOW_PITCH - pitch, self._LOW_ENERGY - energy,
                 self._LOW_RATE - speech_rate],
                [self._LOW_PITCH, self._LOW_ENERGY, self._LOW_RATE],
            )

        # Rule 3: high pitch + low energy → happy
        if hp and le:
            return "happy", self._confidence(
                [pitch - self._HIGH_PITCH, self._LOW_ENERGY - energy],
                [self._HIGH_PITCH, self._LOW_ENERGY],
            )

        # Default: neutral
        return "neutral", 0.5

    @staticmethod
    def _confidence(deltas: list[float], refs: list[float]) -> float:
        """Return 0.0–1.0 confidence proportional to distance from thresholds."""
        if not deltas:
            return 0.5
        norm = [min(abs(d) / max(abs(r), 1e-6), 1.0) for d, r in zip(deltas, refs)]
        return min(0.95, max(0.30, sum(norm) / len(norm)))
