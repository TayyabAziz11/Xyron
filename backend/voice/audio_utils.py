"""Audio capture utilities for AI Operator voice pipeline."""
from __future__ import annotations
import io
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav_io
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1

def record_audio(duration_seconds: float = 5.0, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Synchronously record audio for a fixed duration. Returns float32 numpy array."""
    logger.info("Recording %.1fs of audio at %dHz...", duration_seconds, sample_rate)
    audio = sd.rec(
        int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=CHANNELS,
        dtype="float32",
    )
    sd.wait()
    return audio.flatten()

def record_until_silence(
    max_duration: float = 15.0,
    silence_threshold: float = 0.01,
    silence_duration: float = 1.0,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Record until silence is detected or max_duration reached.

    Uses a rolling buffer with VAD (Voice Activity Detection) based on RMS energy.
    """
    chunk_size = int(sample_rate * 0.1)  # 100ms chunks
    max_chunks = int(max_duration / 0.1)
    silence_chunks = int(silence_duration / 0.1)

    frames: list[np.ndarray] = []
    silent_count = 0
    speaking_started = False

    with sd.InputStream(samplerate=sample_rate, channels=CHANNELS, dtype="float32") as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_size)
            chunk_flat = chunk.flatten()
            frames.append(chunk_flat)
            rms = float(np.sqrt(np.mean(chunk_flat ** 2)))

            if rms > silence_threshold:
                speaking_started = True
                silent_count = 0
            elif speaking_started:
                silent_count += 1
                if silent_count >= silence_chunks:
                    break

    return np.concatenate(frames) if frames else np.zeros(100, dtype="float32")

def numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert numpy float32 array to WAV bytes (for Whisper)."""
    buf = io.BytesIO()
    audio_int16 = (audio * 32767).astype(np.int16)
    wav_io.write(buf, sample_rate, audio_int16)
    return buf.getvalue()

def save_wav(audio: np.ndarray, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
    """Save numpy audio array as WAV file."""
    audio_int16 = (audio * 32767).astype(np.int16)
    wav_io.write(str(path), sample_rate, audio_int16)
    logger.debug("Saved WAV to %s", path)

def list_input_devices() -> list[dict]:
    """List available input audio devices."""
    devices = sd.query_devices()
    return [
        {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]
