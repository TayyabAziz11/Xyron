"""
Realtime audio FX for Xyron voice identity.
All processing is done on numpy float32 arrays — no subprocesses, no cloud.

Presets:
  subtle    — DEFAULT mode: light reverb, gentle warmth, minimal stereo
  crisp     — WORK mode:    compression, air, minimal reverb
  cinematic — TAKEOVER:    reverb, stereo spread, bass boost, subtle saturation
  warm      — CHILL mode:  warm EQ, reverb, stereo
  ambient   — HOME mode:   soft reverb, gentle warmth, stereo spread
  bypass    — no processing

Performance target: <100ms added latency on CPU.
"""
from __future__ import annotations

import io
import logging
import wave

import numpy as np
from scipy import signal as _sig

logger = logging.getLogger(__name__)

# ── DSP primitives ────────────────────────────────────────────────────────────

def _normalize(samples: np.ndarray, target: float = 0.92) -> np.ndarray:
    peak = float(np.max(np.abs(samples)))
    if peak > 0.005:
        samples = samples * (target / peak)
    return samples


def _compress(samples: np.ndarray, threshold: float = 0.55, ratio: float = 3.5, makeup: float = 1.05) -> np.ndarray:
    """Feedforward soft-knee compressor."""
    abs_s = np.abs(samples)
    gain = np.where(
        abs_s > threshold,
        threshold + (abs_s - threshold) / ratio,
        abs_s,
    ) / (abs_s + 1e-9)
    return np.clip(samples * gain * makeup, -1.0, 1.0)


def _lowpass_warmth(samples: np.ndarray, sr: int, cutoff: int = 7200) -> np.ndarray:
    """2nd-order Butterworth low-pass for warmth."""
    nyq = sr / 2.0
    if cutoff >= nyq:
        return samples
    b, a = _sig.butter(2, cutoff / nyq, btype="low")
    return _sig.lfilter(b, a, samples).astype(np.float32)


def _bass_boost(samples: np.ndarray, sr: int, freq: int = 180, boost_db: float = 2.5) -> np.ndarray:
    """Low-shelf boost for cinematic depth."""
    nyq = sr / 2.0
    b, a = _sig.butter(1, freq / nyq, btype="low")
    low = _sig.lfilter(b, a, samples)
    gain = 10 ** (boost_db / 20.0)
    return np.clip(samples + low * (gain - 1.0), -1.0, 1.0).astype(np.float32)


def _soft_saturation(samples: np.ndarray, drive: float = 0.04) -> np.ndarray:
    """Subtle tanh soft-clip for synthetic character."""
    return (np.tanh(samples * (1.0 + drive * 8.0)) / (1.0 + drive)).astype(np.float32)


def _multitap_reverb(samples: np.ndarray, sr: int, room_ms: float = 28.0, wet: float = 0.10) -> np.ndarray:
    """
    Short multi-tap reverb using fixed delay times with exponential decay.
    Sounds clean and avoids random noise artifacts.
    """
    # Delay times chosen to be mutually prime (avoids flutter echo)
    delays_ms = [room_ms, room_ms * 1.31, room_ms * 1.73, room_ms * 2.29]
    gains = [0.50, 0.32, 0.20, 0.12]

    out = samples.copy()
    for d_ms, g in zip(delays_ms, gains):
        d = int(sr * d_ms / 1000.0)
        if d < 1 or d >= len(samples):
            continue
        delayed = np.zeros_like(samples)
        delayed[d:] = samples[: len(samples) - d]
        out += delayed * g * wet

    return _normalize(out.astype(np.float32))


def _stereo_haas(samples: np.ndarray, sr: int, delay_ms: float = 7.0, width: float = 0.25) -> np.ndarray:
    """
    Convert mono to stereo with a Haas-effect spread.
    Left  = original
    Right = slightly delayed + attenuated version
    Returns shape (N, 2) float32.
    """
    d = int(sr * delay_ms / 1000.0)
    right = np.zeros_like(samples)
    if d > 0:
        right[d:] = samples[: len(samples) - d] * (1.0 - width * 0.4)
        right[:d] = samples[:d]
    else:
        right = samples.copy()

    stereo = np.stack([samples, right], axis=1)
    return stereo.astype(np.float32)


# ── Preset chains ─────────────────────────────────────────────────────────────

def _apply_subtle(samples: np.ndarray, sr: int) -> np.ndarray:
    samples = _compress(samples)
    samples = _multitap_reverb(samples, sr, room_ms=22, wet=0.08)
    samples = _lowpass_warmth(samples, sr, cutoff=7500)
    return samples


def _apply_crisp(samples: np.ndarray, sr: int) -> np.ndarray:
    samples = _compress(samples, threshold=0.60, ratio=2.8, makeup=1.08)
    samples = _multitap_reverb(samples, sr, room_ms=14, wet=0.04)
    return samples


def _apply_cinematic(samples: np.ndarray, sr: int) -> np.ndarray:
    samples = _compress(samples, threshold=0.50, ratio=4.0, makeup=1.05)
    samples = _bass_boost(samples, sr, freq=160, boost_db=3.0)
    samples = _multitap_reverb(samples, sr, room_ms=40, wet=0.16)
    samples = _soft_saturation(samples, drive=0.03)
    samples = _normalize(samples)
    return samples


def _apply_warm(samples: np.ndarray, sr: int) -> np.ndarray:
    samples = _compress(samples, threshold=0.58, ratio=3.0, makeup=1.05)
    samples = _lowpass_warmth(samples, sr, cutoff=6800)
    samples = _multitap_reverb(samples, sr, room_ms=30, wet=0.12)
    return samples


def _apply_ambient(samples: np.ndarray, sr: int) -> np.ndarray:
    samples = _compress(samples, threshold=0.58, ratio=3.0, makeup=1.04)
    samples = _lowpass_warmth(samples, sr, cutoff=7000)
    samples = _multitap_reverb(samples, sr, room_ms=34, wet=0.14)
    return samples


_PRESET_FNS = {
    "subtle":    _apply_subtle,
    "crisp":     _apply_crisp,
    "cinematic": _apply_cinematic,
    "warm":      _apply_warm,
    "ambient":   _apply_ambient,
}

# ── Global FX enable/disable ───────────────────────────────────────────────────

_fx_enabled: bool = True


def set_fx_enabled(enabled: bool) -> None:
    global _fx_enabled
    _fx_enabled = enabled


def is_fx_enabled() -> bool:
    return _fx_enabled


# ── Public API ────────────────────────────────────────────────────────────────

def apply_fx(
    samples: np.ndarray,
    sr: int,
    preset: str = "subtle",
    *,
    stereo: bool = True,
) -> np.ndarray:
    """
    Apply an FX preset chain to float32 mono samples.

    Returns float32 array — mono (N,) or stereo (N, 2) depending on `stereo`.
    Never raises; returns the original samples on any error.
    """
    if not _fx_enabled or preset == "bypass":
        return _stereo_haas(samples, sr, width=0.15) if stereo else samples

    try:
        fn = _PRESET_FNS.get(preset, _apply_subtle)
        processed = fn(samples.astype(np.float32), sr)
        if stereo:
            # Cinematic preset gets wider stereo
            width = 0.35 if preset == "cinematic" else 0.20 if preset in ("ambient", "warm") else 0.15
            processed = _stereo_haas(processed, sr, width=width)
        return processed
    except Exception as exc:
        logger.warning("[AudioFX] %s preset failed: %s", preset, exc)
        return samples


def apply_fx_to_wav(wav_bytes: bytes, preset: str = "subtle") -> bytes:
    """
    Decode WAV → apply FX → re-encode WAV (mono 16-bit PCM).
    Input and output are raw WAV bytes.
    """
    if not _fx_enabled or preset == "bypass":
        return wav_bytes

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth  = wf.getsampwidth()
            sr         = wf.getframerate()
            raw        = wf.readframes(wf.getnframes())

        if sampwidth == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
        elif sampwidth == 4:
            samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483647.0
        else:
            return wav_bytes

        if n_channels == 2:
            samples = samples.reshape(-1, 2).mean(axis=1)

        processed = apply_fx(samples, sr, preset, stereo=False)

        pcm = (np.clip(processed, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    except Exception as exc:
        logger.warning("[AudioFX] WAV processing failed: %s", exc)
        return wav_bytes
