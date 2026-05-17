"""
RVC Voice Engine — three-tier emotional voice conversion.

Tier 1 — Full RVC  (when rvc_python + model files are present)
           Runs a trained voice conversion model per emotional preset.
           Highest quality; requires: pip install rvc-python, plus .pth model files.

Tier 2 — Lightweight  (available today with librosa + scipy)
           Per-preset pitch shifting and spectral EQ adjustments.
           Subtle but real improvement over flat Kokoro output.
           ~10–30ms overhead on CPU.

Tier 3 — Passthrough  (always available)
           Returns original Kokoro audio unchanged.

Selection: auto (Full RVC if available, else Lightweight, else Passthrough).

Environment:
    ENABLE_RVC=true             -- must be true to activate any conversion
    RVC_MODEL_DIR               -- parent dir for preset subdirs (default: ~/.xyron/models/rvc)
    RVC_DEFAULT_PRESET=neutral
    RVC_DEVICE=auto             -- "cuda", "cpu", or "auto"
    RVC_MAX_LATENCY_MS=250      -- skip RVC if previous call exceeded this
    RVC_LIGHTWEIGHT=true        -- force Lightweight tier, skip Full RVC check

Preset directory layout:
    <RVC_MODEL_DIR>/
        hyped/
            model.pth
            index.index       (optional)
            config.json       (optional)
        calm/
            model.pth
        neutral/
            model.pth
        ...

If a preset is missing its directory, the engine falls back to neutral.
If neutral is also missing, Lightweight or Passthrough is used.
"""
from __future__ import annotations

import io
import logging
import os
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Load .env so env vars work in standalone scripts too ─────────────────────
def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        _env_file = (Path(__file__).parent.parent / ".env").resolve()
        if _env_file.is_file():
            load_dotenv(_env_file, override=False)
    except ImportError:
        pass

_load_env()

# ── Env config ────────────────────────────────────────────────────────────────

_ENABLED        = os.getenv("ENABLE_RVC", "false").lower() == "true"
_MODEL_DIR      = Path(os.getenv("RVC_MODEL_DIR", str(Path.home() / ".xyron" / "models" / "rvc")))
_DEFAULT_PRESET = os.getenv("RVC_DEFAULT_PRESET", "neutral")
_DEVICE_PREF    = os.getenv("RVC_DEVICE", "auto")
_MAX_LATENCY_MS = int(os.getenv("RVC_MAX_LATENCY_MS", "250"))
_FORCE_LIGHT    = os.getenv("RVC_LIGHTWEIGHT", "false").lower() == "true"

# ── Emotion → preset mapping ──────────────────────────────────────────────────

MOOD_TO_PRESET: dict[str, str] = {
    "HYPED":            "hyped",
    "HYPED_NATURAL":    "hyped",
    "EXCITED":          "hyped",
    "RELIEVED_EXCITED": "relieved",
    "DOMINANT":         "dominant",
    "LOCKED_IN":        "dominant",
    "PROTECTIVE":       "protective",
    "PROTECTIVE_FOCUSED": "protective",
    "FOCUSED":          "protective",
    "CALM":             "calm",
    "LATE_NIGHT":       "late_night",
    "ANALYTICAL":       "calm",
    "AUDIENCE_MODE":    "audience_mode",
    "NEUTRAL":          "neutral",
}

# ── Lightweight transform params — pitch (semitones) + spectral EQ ────────────

_LIGHT_PRESETS: dict[str, dict] = {
    "neutral":       {"pitch": 0.0,   "brightness": 0.00, "warmth": 0.00},
    "hyped":         {"pitch": 1.2,   "brightness": 0.12, "warmth": 0.00},
    "relieved":      {"pitch": 0.5,   "brightness": 0.05, "warmth": 0.08},
    "dominant":      {"pitch": -1.5,  "brightness": -0.08, "warmth": 0.18},
    "protective":    {"pitch": -0.5,  "brightness": -0.05, "warmth": 0.12},
    "calm":          {"pitch": -0.4,  "brightness": -0.10, "warmth": 0.10},
    "late_night":    {"pitch": -1.0,  "brightness": -0.15, "warmth": 0.20},
    "audience_mode": {"pitch": -1.0,  "brightness": 0.08,  "warmth": 0.08},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wav_to_float(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        sr       = wf.getframerate()
        n_frames = wf.getnframes()
        raw      = wf.readframes(n_frames)
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    return pcm, sr


def _float_to_wav(pcm: np.ndarray, sr: int) -> bytes:
    clipped = np.clip(pcm, -1.0, 1.0)
    int16   = (clipped * 32767).astype(np.int16)
    buf     = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16.tobytes())
    return buf.getvalue()


def _pitch_shift_librosa(pcm: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Shift pitch by `semitones` using librosa. Returns float32 array."""
    if abs(semitones) < 0.05:
        return pcm
    import librosa  # type: ignore[import]
    return librosa.effects.pitch_shift(pcm, sr=sr, n_steps=semitones).astype(np.float32)


def _spectral_eq(pcm: np.ndarray, sr: int, brightness: float, warmth: float) -> np.ndarray:
    """
    Simple two-band shelf EQ.
    brightness: high-shelf gain at ~4kHz (positive = brighter, negative = darker)
    warmth:     low-shelf gain at ~300Hz  (positive = warmer, negative = thinner)
    Both in rough dB-like units mapped to linear gain (±0.2 → ±2dB ish).
    """
    if abs(brightness) < 0.01 and abs(warmth) < 0.01:
        return pcm
    from scipy.signal import butter, lfilter  # type: ignore[import]
    nyq = sr / 2.0
    out = pcm.copy()

    if abs(brightness) >= 0.01:
        freq = min(4000, nyq * 0.90)
        b, a = butter(1, freq / nyq, btype="high")
        hi   = lfilter(b, a, out)
        out  = np.clip(out + hi * brightness, -1.0, 1.0).astype(np.float32)

    if abs(warmth) >= 0.01:
        freq = min(350, nyq * 0.90)
        b, a = butter(1, freq / nyq, btype="low")
        lo   = lfilter(b, a, out)
        out  = np.clip(out + lo * warmth, -1.0, 1.0).astype(np.float32)

    return out


# ── RVC Engine ────────────────────────────────────────────────────────────────

class RVCVoiceEngine:
    """
    Emotional voice conversion with automatic tier selection and per-call latency guard.
    """

    def __init__(self) -> None:
        self._last_latency_ms: float = 0.0
        self._tier: str              = "passthrough"
        self._rvc_models: dict[str, object] = {}   # preset → loaded rvc model (lazy)
        self._librosa_ok: bool       = False

        if not _ENABLED:
            logger.info("[RVC] enabled=false tier=passthrough")
            return

        # Try Lightweight (always attempted if enabled; acts as floor)
        try:
            import librosa  # noqa: F401  # type: ignore[import]
            from scipy.signal import butter  # noqa: F401  # type: ignore[import]
            self._librosa_ok = True
            self._tier       = "lightweight"
            logger.info("[RVC] available=true tier=lightweight preset_params=%d", len(_LIGHT_PRESETS))
        except ImportError as e:
            logger.warning("[RVC] librosa/scipy not available (%s) — tier=passthrough", e)

        if _FORCE_LIGHT:
            logger.info("[RVC] RVC_LIGHTWEIGHT=true — skipping full RVC check")
            return

        # Try Full RVC
        if self._check_rvc_package():
            # Check if at least one preset model exists
            available_presets = self._scan_presets()
            if available_presets:
                self._tier = "full_rvc"
                logger.info("[RVC] available=true tier=full_rvc presets=%s", available_presets)
            else:
                logger.info(
                    "[RVC] rvc_python installed but no model files found at %s — "
                    "tier remains %s", _MODEL_DIR, self._tier
                )

    # ── Public API ────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return _ENABLED and self._tier in ("lightweight", "full_rvc")

    def get_tier(self) -> str:
        return self._tier

    def list_presets(self) -> list[str]:
        if self._tier == "full_rvc":
            return self._scan_presets()
        return list(_LIGHT_PRESETS.keys())

    def get_status(self) -> dict:
        return {
            "enabled":         _ENABLED,
            "tier":            self._tier,
            "model_dir":       str(_MODEL_DIR),
            "available_presets": self.list_presets(),
            "last_latency_ms": round(self._last_latency_ms, 1),
            "max_latency_ms":  _MAX_LATENCY_MS,
            "device_pref":     _DEVICE_PREF,
            "librosa_ok":      self._librosa_ok,
            "rvc_package_ok":  self._tier == "full_rvc",
        }

    def mood_to_preset(self, mood: str) -> str:
        """Map a MoodStateLabel to an RVC preset name."""
        return MOOD_TO_PRESET.get(mood.upper(), _DEFAULT_PRESET)

    def convert(
        self,
        audio_bytes:   bytes,
        preset:        str  = "neutral",
        emotion_state: str  = "NEUTRAL",
        intensity:     str  = "NORMAL",
    ) -> bytes:
        """
        Convert voice audio. Returns audio_bytes unchanged on any failure.

        Args:
            audio_bytes:   WAV bytes from Kokoro.
            preset:        RVC preset name (hyped/calm/dominant/…).
            emotion_state: MoodStateLabel — used as fallback for preset lookup.
            intensity:     EXTREME | HIGH | NORMAL — scales transform strength.
        """
        if not _ENABLED or self._tier == "passthrough":
            return audio_bytes

        # Latency guard — skip if previous call was too slow
        if self._last_latency_ms > _MAX_LATENCY_MS:
            logger.info("[RVC] skipped_due_to_latency=true last_ms=%.0f threshold=%d",
                        self._last_latency_ms, _MAX_LATENCY_MS)
            self._last_latency_ms = 0.0  # reset so next turn gets a chance
            return audio_bytes

        # Resolve preset
        resolved = preset or self.mood_to_preset(emotion_state)
        if resolved not in _LIGHT_PRESETS:
            resolved = _DEFAULT_PRESET

        t0 = time.monotonic()
        try:
            if self._tier == "full_rvc":
                result = self._convert_full_rvc(audio_bytes, resolved, intensity)
            else:
                result = self._convert_lightweight(audio_bytes, resolved, intensity)
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            logger.info("[RVC] conversion_success=true preset=%s tier=%s latency_ms=%.0f",
                        resolved, self._tier, self._last_latency_ms)
            return result
        except Exception as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            logger.warning("[RVC] fallback=true reason=%s preset=%s", exc, resolved)
            return audio_bytes

    # ── Tier 2: Lightweight ───────────────────────────────────────────────────

    def _convert_lightweight(self, audio_bytes: bytes, preset: str, intensity: str) -> bytes:
        params = _LIGHT_PRESETS.get(preset, _LIGHT_PRESETS["neutral"])

        # Scale transform strength by intensity
        scale = 1.0 if intensity == "EXTREME" else 0.75 if intensity == "HIGH" else 0.5

        pitch      = params["pitch"]      * scale
        brightness = params["brightness"] * scale
        warmth     = params["warmth"]     * scale

        pcm, sr = _wav_to_float(audio_bytes)

        pcm = _pitch_shift_librosa(pcm, sr, pitch)
        pcm = _spectral_eq(pcm, sr, brightness, warmth)

        return _float_to_wav(pcm, sr)

    # ── Tier 1: Full RVC ──────────────────────────────────────────────────────

    def _convert_full_rvc(self, audio_bytes: bytes, preset: str, intensity: str) -> bytes:
        """Run rvc_python inference. Lazy-loads model on first use per preset."""
        model = self._get_or_load_rvc_model(preset)
        if model is None:
            logger.info("[RVC] fallback=true reason=model_missing preset=%s", preset)
            return self._convert_lightweight(audio_bytes, preset, intensity)

        pcm, sr = _wav_to_float(audio_bytes)

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
            tmp_in.write(audio_bytes)
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path.replace(".wav", "_out.wav")
        try:
            model.infer_file(
                input_path  = tmp_in_path,
                output_path = tmp_out_path,
                f0method    = "rmvpe",  # best quality; falls back to harvest
                f0_up_key   = 0,        # no pitch shift (preset handles that)
            )
            with open(tmp_out_path, "rb") as f:
                return f.read()
        finally:
            for p in (tmp_in_path, tmp_out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def _get_or_load_rvc_model(self, preset: str) -> Optional[object]:
        """Lazy-load RVC model for preset. Returns None if not available."""
        if preset in self._rvc_models:
            return self._rvc_models[preset]

        preset_dir = _MODEL_DIR / preset
        if not preset_dir.is_dir():
            # Fall back to neutral
            preset_dir = _MODEL_DIR / "neutral"
        if not preset_dir.is_dir():
            return None

        model_path = preset_dir / "model.pth"
        index_path = preset_dir / "index.index"
        if not model_path.is_file():
            return None

        try:
            from rvc_python.infer import RVCInference  # type: ignore[import]
            device = self._resolve_device()
            rvc = RVCInference(device=device)
            rvc.load_model(
                str(model_path),
                str(index_path) if index_path.is_file() else None,
            )
            self._rvc_models[preset] = rvc
            logger.info("[RVC] model_loaded preset=%s device=%s", preset, device)
            return rvc
        except Exception as exc:
            logger.warning("[RVC] model_load_failed preset=%s reason=%s", preset, exc)
            return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_rvc_package(self) -> bool:
        try:
            import importlib
            importlib.import_module("rvc_python.infer")
            return True
        except ImportError:
            return False

    def _scan_presets(self) -> list[str]:
        """Return preset names that have a model.pth file."""
        if not _MODEL_DIR.is_dir():
            return []
        found = []
        for sub in sorted(_MODEL_DIR.iterdir()):
            if sub.is_dir() and (sub / "model.pth").is_file():
                found.append(sub.name)
        return found

    def _resolve_device(self) -> str:
        if _DEVICE_PREF == "auto":
            try:
                import torch
                return "cuda:0" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return _DEVICE_PREF


# Global singleton — constructed once at import
rvc_engine = RVCVoiceEngine()
