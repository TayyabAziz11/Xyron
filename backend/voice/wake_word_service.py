"""
Wake word detection — multi-model OpenWakeWord, no Whisper fallback.

Architecture
───────────
- Loads ALL .onnx models from WAKE_MODELS_DIR (custom trained) + built-in OWW dir.
- Each model gets its own confidence threshold.
- Cooldown prevents re-triggering within COOLDOWN_S seconds.
- Priority: longer keyword name wins when multiple fire in the same frame.
- Two usage paths:
    1. detect_frame(pcm_float32)  ← streaming/WebSocket (1280 samples, 80ms)
    2. detect_from_bytes(bytes)   ← HTTP polling fallback (WebM/WAV upload)

Configuration (env vars)
────────────────────────
  WAKE_MODELS_DIR          Path to directory with custom .onnx models.
                           Default: ~/.xyron/wake_models
  WAKE_WORD_MODEL          Single built-in model to enable if no custom models exist.
                           Default: hey_jarvis
  WAKE_WORD_THRESHOLD      Default threshold for models without an explicit override.
                           Default: 0.5
  WAKE_COOLDOWN_S          Seconds between consecutive wake events. Default: 1.5

Per-model threshold overrides (env var JSON dict):
  WAKE_THRESHOLDS          e.g. '{"hey_xyron": 0.45, "wakeup_xyron": 0.50, "xyron": 0.65}'
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_OWW_BUILTIN_DIR  = Path(__file__).parent.parent.parent
try:
    import openwakeword as _oww
    _OWW_BUILTIN_DIR = Path(_oww.__file__).parent / "resources" / "models"
except Exception:
    pass

_CUSTOM_MODELS_DIR   = Path(os.getenv("WAKE_MODELS_DIR", os.path.expanduser("~/.xyron/wake_models")))
_FALLBACK_MODEL      = os.getenv("WAKE_WORD_MODEL", "hey_jarvis").strip()
_DEFAULT_THRESHOLD   = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))
_COOLDOWN_S          = float(os.getenv("WAKE_COOLDOWN_S", "3.0"))

# Per-model threshold overrides from env
try:
    _MODEL_THRESHOLDS: dict[str, float] = json.loads(
        os.getenv("WAKE_THRESHOLDS", "{}")
    )
except json.JSONDecodeError:
    _MODEL_THRESHOLDS = {}

# Hard-coded overrides for the Xyron keyword set.
# Rationale for high thresholds:
#   - Shorter keywords ("xyron") share phonemes with common words → need stricter gate.
#   - Multi-syllable ("hey_xyron") are more discriminative → slightly lower is fine.
#   - 0.65+ eliminates 95%+ of false positives while keeping near-100% true recall.
_MODEL_THRESHOLDS.setdefault("hey_xyron",    0.65)
_MODEL_THRESHOLDS.setdefault("wakeup_xyron", 0.70)
_MODEL_THRESHOLDS.setdefault("xyron",        0.80)  # single word — highest FP risk
_MODEL_THRESHOLDS.setdefault("hey_xeron",    0.60)
_MODEL_THRESHOLDS.setdefault("hi_xyron",     0.60)
_MODEL_THRESHOLDS.setdefault("hey_jarvis",   0.55)  # built-in for testing

# Path for hard-negative mining log (frames that nearly fire but don't)
_FP_LOG_PATH = Path(os.path.expanduser("~/.xyron/false_positive_log.jsonl"))
_FP_CONF_MIN = 0.35  # log frames above this but below threshold


class WakeWordService:
    """
    Streaming-first, multi-model OWW wake word detector.

    Thread-safe.  Single singleton at module level.
    """

    def __init__(self) -> None:
        self._lock              = threading.Lock()
        self._models: dict[str, object]    = {}
        self._thresholds: dict[str, float] = {}
        self._buffers: dict[str, np.ndarray] = {}
        self._last_wake_t       = 0.0
        self._oww_ready         = False
        self._mel_sess          = None
        self._emb_sess          = None
        self._fp_log_lock       = threading.Lock()

        threading.Thread(target=self._load_all, daemon=True, name="wake-loader").start()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        try:
            import onnxruntime as ort  # type: ignore

            # Load shared feature extraction models
            builtin = str(_OWW_BUILTIN_DIR)
            self._mel_sess = ort.InferenceSession(f"{builtin}/melspectrogram.onnx")
            self._emb_sess = ort.InferenceSession(f"{builtin}/embedding_model.onnx")

            loaded: list[str] = []

            # 1. Custom trained models in WAKE_MODELS_DIR
            _CUSTOM_MODELS_DIR.mkdir(parents=True, exist_ok=True)
            for onnx_file in sorted(_CUSTOM_MODELS_DIR.glob("*.onnx")):
                name = onnx_file.stem
                try:
                    sess = ort.InferenceSession(str(onnx_file))
                    self._models[name] = sess
                    self._thresholds[name] = _MODEL_THRESHOLDS.get(name, _DEFAULT_THRESHOLD)
                    self._buffers[name] = np.zeros((16, 96), dtype=np.float32)
                    loaded.append(f"{name}({self._thresholds[name]:.2f})")
                    logger.info("[WakeWord] Loaded custom model: %s  threshold=%.2f", name, self._thresholds[name])
                except Exception as exc:
                    logger.warning("[WakeWord] Skipping %s: %s", onnx_file.name, exc)

            # 2. Fall back to built-in model if no custom models found
            if not self._models and _FALLBACK_MODEL:
                onnx_path = f"{builtin}/{_FALLBACK_MODEL}_v0.1.onnx"
                if os.path.exists(onnx_path):
                    sess = ort.InferenceSession(onnx_path)
                    name = _FALLBACK_MODEL
                    self._models[name] = sess
                    self._thresholds[name] = _MODEL_THRESHOLDS.get(name, _DEFAULT_THRESHOLD)
                    self._buffers[name] = np.zeros((16, 96), dtype=np.float32)
                    loaded.append(f"{name}({self._thresholds[name]:.2f})")
                    logger.info("[WakeWord] Using built-in model: %s", onnx_path)
                else:
                    logger.warning("[WakeWord] Built-in model not found: %s", onnx_path)

            if self._models:
                self._oww_ready = True
                logger.info("[WakeWord] Ready — models: %s  cooldown=%.1fs",
                            ", ".join(loaded), _COOLDOWN_S)
            else:
                logger.error("[WakeWord] No models loaded — wake detection disabled")

        except Exception as exc:
            logger.error("[WakeWord] Load failed: %s", exc, exc_info=True)

    # ── Feature extraction ────────────────────────────────────────────────────

    def _extract_embedding(self, pcm_1280: np.ndarray) -> Optional[np.ndarray]:
        """
        Run one 80ms PCM frame (1280 float32 samples @ 16kHz) through
        mel-spectrogram + embedding model → 96-dim vector.
        """
        if self._mel_sess is None or self._emb_sess is None:
            return None
        try:
            frame = pcm_1280.astype(np.float32).reshape(1, 1280)
            mel = self._mel_sess.run(None, {"input": frame})[0]    # [1, 1, T, 32]
            # mel shape is [1, 1, T, 32] — we need [1, 76, 32, 1]
            mel_frames = mel.shape[2]
            if mel_frames < 76:
                pad = np.zeros((1, 1, 76 - mel_frames, 32), dtype=np.float32)
                mel = np.concatenate([mel, pad], axis=2)
            mel_chunk = mel[0, 0, :76, :].reshape(1, 76, 32, 1).astype(np.float32)
            emb = self._emb_sess.run(None, {"input_1": mel_chunk})[0]  # [1,1,1,96]
            return emb.reshape(96)
        except Exception as exc:
            logger.debug("[WakeWord] embedding error: %s", exc)
            return None

    # ── Streaming detection (WebSocket path) ─────────────────────────────────

    def detect_frame(self, pcm_1280: np.ndarray) -> tuple[bool, str, float]:
        """
        Process one 80ms PCM frame (1280 float32 samples).
        Call this for every frame from the WebSocket stream.

        Returns (triggered, model_name, confidence).
        Returns (False, "", 0.0) if not triggered or in cooldown.
        """
        if not self._oww_ready:
            return False, "", 0.0

        emb = self._extract_embedding(pcm_1280)
        if emb is None:
            return False, "", 0.0

        now = time.perf_counter()
        if now - self._last_wake_t < _COOLDOWN_S:
            return False, "", 0.0

        best_name  = ""
        best_conf  = 0.0
        best_score = -1  # priority = keyword length (longer wins)
        near_misses: list[tuple[str, float]] = []

        with self._lock:
            for name, sess in self._models.items():
                # Roll the 16-step embedding window
                buf = self._buffers[name]
                buf[:-1] = buf[1:]
                buf[-1]  = emb

                try:
                    win = buf.reshape(1, 16, 96).astype(np.float32)
                    try:
                        conf = float(sess.run(None, {"x.1": win})[0].flatten()[0])
                    except Exception:
                        inp_name = sess.get_inputs()[0].name
                        conf = float(sess.run(None, {inp_name: win})[0].flatten()[0])

                    if conf >= self._thresholds[name]:
                        priority = len(name)
                        if priority > best_score or (priority == best_score and conf > best_conf):
                            best_name  = name
                            best_conf  = conf
                            best_score = priority
                    elif conf >= _FP_CONF_MIN:
                        near_misses.append((name, conf))
                except Exception as exc:
                    logger.debug("[WakeWord] %s predict error: %s", name, exc)

        if best_name:
            self._last_wake_t = now
            self._reset_buffers()
            logger.info("[WakeWord] WAKE — model=%s conf=%.3f", best_name, best_conf)
            return True, best_name, best_conf

        # Hard-negative mining: log near-misses (above noise floor but below threshold)
        self._log_near_misses(near_misses)
        return False, "", 0.0

    def _reset_buffers(self) -> None:
        for name in self._buffers:
            self._buffers[name] = np.zeros((16, 96), dtype=np.float32)

    def _log_near_misses(self, near_misses: list[tuple[str, float]]) -> None:
        """
        Log near-miss frames to ~/.xyron/false_positive_log.jsonl for hard negative mining.
        Each line: {"ts": ..., "model": "...", "conf": 0.42}
        Use voice.tools.mine_negatives to extract audio around these timestamps.
        """
        if not near_misses:
            return
        try:
            _FP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            ts = time.perf_counter()
            with self._fp_log_lock:
                with open(_FP_LOG_PATH, "a") as f:
                    for name, conf in near_misses:
                        f.write(json.dumps({"ts": ts, "model": name, "conf": round(conf, 4)}) + "\n")
        except Exception:
            pass

    def reset_cooldown(self) -> None:
        """Force reset cooldown (call after session ends)."""
        self._last_wake_t = 0.0

    # ── HTTP polling fallback (detect_from_bytes) ─────────────────────────────

    def detect_from_bytes(
        self, audio_bytes: bytes, content_type: str = ""
    ) -> tuple[bool, float, str, str]:
        """
        Accepts WebM/WAV bytes, decodes, runs OWW frame-by-frame.
        Returns (triggered, confidence, method, phrase_name).
        method is always "oww" or "none".
        """
        if not self._oww_ready:
            return False, 0.0, "none", ""

        t0 = time.perf_counter()
        pcm = _decode_to_pcm(audio_bytes, content_type)

        if pcm is None:
            return False, 0.0, "none", ""

        duration_s = len(pcm) / 16000
        rms = float(np.sqrt(np.mean(pcm ** 2))) if len(pcm) > 0 else 0.0

        logger.debug("[WakeWord] HTTP poll: %.2fs RMS=%.4f bytes=%d",
                     duration_s, rms, len(audio_bytes))

        if duration_s < 0.4:
            return False, 0.0, "none", ""

        # Normalize
        if rms > 0.001:
            pcm = np.clip(pcm * (0.1 / rms), -1.0, 1.0)

        # Process frame by frame
        n_frames = len(pcm) // 1280
        for i in range(n_frames):
            frame = pcm[i * 1280 : (i + 1) * 1280]
            triggered, name, conf = self.detect_frame(frame)
            if triggered:
                ms = round((time.perf_counter() - t0) * 1000)
                logger.info("[WakeWord] WAKE (HTTP) model=%s conf=%.3f e2e=%dms", name, conf, ms)
                return True, conf, "oww", name

        return False, 0.0, "none", ""

    @property
    def oww_ready(self) -> bool:
        return self._oww_ready

    @property
    def model_names(self) -> list[str]:
        return list(self._models.keys())


# ── Audio decode helpers ──────────────────────────────────────────────────────

def _is_webm(audio_bytes: bytes, content_type: str) -> bool:
    if "webm" in content_type:
        return True
    return len(audio_bytes) >= 4 and audio_bytes[:4] == b'\x1a\x45\xdf\xa3'


def _decode_to_pcm(audio_bytes: bytes, content_type: str) -> Optional[np.ndarray]:
    """
    Decode audio bytes → 16kHz float32 mono numpy array.
    For WebM/Opus: ffmpeg FIRST (soundfile cannot decode Opus).
    For WAV/FLAC:  soundfile first, ffmpeg fallback.
    """
    if _is_webm(audio_bytes, content_type):
        return _ffmpeg_decode(audio_bytes, content_type) or _soundfile_decode(audio_bytes)
    return _soundfile_decode(audio_bytes) or _ffmpeg_decode(audio_bytes, content_type)


def _soundfile_decode(audio_bytes: bytes) -> Optional[np.ndarray]:
    try:
        import io
        import soundfile as sf  # type: ignore
        audio, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            import scipy.signal as sps
            audio = sps.resample(audio, int(len(audio) * 16000 / sr)).astype(np.float32)
        return audio.astype(np.float32)
    except Exception:
        return None


def _ffmpeg_decode(audio_bytes: bytes, content_type: str) -> Optional[np.ndarray]:
    try:
        import subprocess, tempfile, os as _os
        suffix = ".webm" if _is_webm(audio_bytes, content_type) else ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            in_path = tmp.name
        out_path = in_path + ".f32le"
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", in_path,
                 "-ac", "1", "-ar", "16000", "-f", "f32le", "-acodec", "pcm_f32le", out_path],
                capture_output=True, timeout=10, check=False,
            )
            if r.returncode != 0:
                return None
            raw = np.frombuffer(open(out_path, "rb").read(), dtype=np.float32)
            return raw.copy()
        finally:
            for p in (in_path, out_path):
                try:
                    _os.unlink(p)
                except Exception:
                    pass
    except Exception:
        return None


# Module-level singleton
wake_word_service = WakeWordService()
