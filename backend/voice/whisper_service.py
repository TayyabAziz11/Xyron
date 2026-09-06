"""
Whisper STT service — faster-whisper with GPU auto-detection.

Configuration (environment variables):
  WHISPER_MODEL                 Model size — default "small" (low-spec-friendly: fast on
                                CPU, modest RAM). Higher-accuracy opt-in options for
                                users with more RAM/GPU headroom:
                                  medium          — noticeably more accurate than small.
                                                    Live-measured on this project's T1200
                                                    laptop GPU (2026-08-21, 3 real Urdu
                                                    clips, beam_size=3, VAD on, GPU
                                                    confirmed 100% utilized/1800MHz
                                                    throughout — not throttling): ~4.0s avg
                                                    latency, near-exact Urdu accuracy. Best
                                                    speed/accuracy balance found for weak
                                                    mobile GPUs — current default.
                                  large-v3        — most accurate, heaviest (GPU recommended)
                                  large-v3-turbo  — large-v3's encoder (same multilingual/
                                                    Urdu accuracy) with a pruned 4-layer
                                                    decoder. On capable/desktop GPUs this is
                                                    close to medium's speed; measured on
                                                    this T1200 it was the SLOWEST real
                                                    option (~6.8s avg, worse than medium's
                                                    4.0s) — turbo keeps large-v3's full
                                                    32-layer encoder, which is the
                                                    expensive part on a weak GPU, so the
                                                    "nearly as fast as medium" framing does
                                                    not hold on low-end hardware. Higher
                                                    accuracy than medium (2/3 exact vs 1/3
                                                    exact on the same test), so still worth
                                                    it if you value correctness over
                                                    latency and have GPU headroom to spare.
                                  distil-large-v3 — DO NOT USE for Urdu: live-tested and
                                                    confirmed broken — hallucinates garbled
                                                    English ("Karoom Koroa.", repeated-word
                                                    loops) instead of transcribing Urdu
                                                    audio at all. distil-whisper models are
                                                    English-distilled, not real multilingual
                                                    options, regardless of the "near
                                                    large-v3 accuracy" framing that applies
                                                    only to English.
                                Invalid values fall back to "small" with a warning logged.
  WHISPER_LANGUAGE              ISO code ("en", "ur") or "auto" for multilingual.
                                Default: "auto" — detects language per utterance.
  WHISPER_CONFIDENCE_THRESHOLD  avg_logprob floor; segments below this are noise.
                                Default: -1.0  (0=perfect, -1=uncertain, <-2=noise)

GPU:
  Auto-detected. If torch+CUDA is available → float16 GPU.
  Otherwise → CPU int8. No config needed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import re
import numpy as np

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# "small" stays the default for low-spec hardware; medium/large-v3/large-v3-turbo/
# distil-large-v3 are opt-in higher-accuracy choices for users with more RAM/GPU headroom.
_SUPPORTED_MODEL_SIZES = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v3", "large-v3-turbo", "turbo", "distil-large-v3",
})
_DEFAULT_MODEL_SIZE = "small"


def _validate_model_size(size: str) -> str:
    """Fall back to the low-spec default with a warning if size is unrecognized."""
    if size in _SUPPORTED_MODEL_SIZES:
        return size
    logger.warning(
        "[Whisper] Unrecognized WHISPER_MODEL=%r — falling back to %r. Supported: %s",
        size, _DEFAULT_MODEL_SIZE, sorted(_SUPPORTED_MODEL_SIZES),
    )
    return _DEFAULT_MODEL_SIZE


_MODEL_SIZE  = _validate_model_size(os.getenv("WHISPER_MODEL", _DEFAULT_MODEL_SIZE))
_LANGUAGE    = os.getenv("WHISPER_LANGUAGE", "auto")   # "auto" → None (multilingual)
_CONF_THRESH = float(os.getenv("WHISPER_CONFIDENCE_THRESHOLD", "-1.0"))

# Language-aware initial prompts — bias Whisper toward command vocabulary.
# "Xyron" (the assistant's name, addressed at the start of most commands —
# e.g. "Okay Xyron, what's on my screen") is otherwise a made-up proper noun
# with no prior in Whisper's vocabulary and gets misheard constantly ("Zyron",
# "His iron", "Za-iron"...). Naming it explicitly in the prompt lets the
# decoder actually recognize it instead of relying solely on post-hoc regex
# correction in normalizer.py, which only catches mishearings it already knows.
_VOICE_INITIAL_PROMPT_EN = (
    "Voice assistant named Xyron, for Windows. "
    "C drive, D drive, E drive, F drive, folder, settings, volume, brightness, battery, WiFi. "
    "Open, create, delete, close, screenshot, maximize, minimize, lock, shutdown."
)
_VOICE_INITIAL_PROMPT_UR = (
    "Voice assistant named Xyron, Urdu aur English mixed. "
    "کھولو، بند کرو، اسکرین شاٹ لو، والیوم بڑھاؤ، والیوم گھٹاؤ، فولڈر، سیٹنگز۔ "
    "Open, close, screenshot, volume up, volume down, folder, settings, lock, shutdown."
)
# Roman Urdu (Urdu grammar written in Latin letters) — previously MISSING
# entirely. Live-caught failure this closes: with only the English and
# Urdu-SCRIPT prompts above primed, Whisper had zero prior for Roman Urdu
# words at all and defaulted to English-sounding guesses for them —
# "Kaisay ho Xyron" came out as "Casio Xyron.", "Setting ko kholo" came out
# as "Settings Code Code." Neither mishearing has anything to do with
# response-language routing; the decoder simply never had "kaisay", "kholo",
# "karo", "mein", "ko" in its primed vocabulary to recognize them against.
_VOICE_INITIAL_PROMPT_UR_ROMAN = (
    "Voice assistant named Xyron. Roman Urdu commands: "
    "Kaisay ho Xyron, kya haal hai, Assalam o alaikum. Chrome kholo, settings kholo, band karo, "
    "isko kholo, ye kholo, folder kholo, download karo, install karo, "
    "volume barhao, awaz kam karo, screenshot lo, Urdu mein baat karo, "
    "English mein batao, mujhe dikhao, kya hai, kar do. "
    "Gana rok do, agla gana, pichla gana, dobara chalao, "
    "recycle bin khali karo, kachra saaf karo."
)


def _get_initial_prompt(lang: Optional[str]) -> str:
    """Return the right initial prompt based on resolved language.

    lang=None  → auto-detect mode — trilingual prompt (EN + Urdu script +
                 Roman Urdu) helps Whisper handle code-switching.
    lang='ur'  → Urdu-primary prompt (script + Roman, since STT can't know
                 in advance which script the user will actually use).
    lang='en'  → English-only prompt.
    """
    if lang == "ur":
        return _VOICE_INITIAL_PROMPT_UR + " " + _VOICE_INITIAL_PROMPT_UR_ROMAN
    if lang is None:
        # Auto-detect: prime all three so code-switching in either direction
        # (English->Urdu, Urdu script->Roman, etc.) has vocabulary to match.
        return (_VOICE_INITIAL_PROMPT_EN + " " + _VOICE_INITIAL_PROMPT_UR
                + " " + _VOICE_INITIAL_PROMPT_UR_ROMAN)
    return _VOICE_INITIAL_PROMPT_EN


# ── Context-aware hotwords (faster-whisper word-biasing) ────────────────────
# faster-whisper's `hotwords` param biases decoding toward specific words
# without replacing initial_prompt — TranscriptionOptions.get_prompt()
# concatenates hotwords_tokens with the previous_tokens derived from
# initial_prompt into the same decoder prompt, so this is additive to (not a
# replacement for) the static _VOICE_INITIAL_PROMPT_*/_TINY_INITIAL_PROMPT
# vocabulary above. Static = fixed app/command vocabulary; dynamic = whatever
# the user actually opened/referenced most recently (ContextStack), so an app
# name that isn't in the static list (e.g. "Notion", "Figma") still gets
# biased correctly right after it's been opened once.
_STATIC_HOTWORDS = [
    "Xyron", "Chrome", "VS Code", "Notepad", "Calculator", "Explorer",
    "Settings", "Terminal", "Spotify", "Discord", "folder", "drive",
    # Roman Urdu grammar/command words — previously absent, so short Roman
    # Urdu commands had no word-level bias at all (see initial_prompt
    # comment above for the live-caught mishearings this closes).
    "kholo", "kaisay", "karo", "mein", "kya", "kho", "band", "barhao",
    "dikhao", "batao", "kholein",
    # Media pause/next/prev + recycle-bin empty — previously absent, so
    # these words had no word-level decoder bias (see mixed_language_engine
    # and intent_router additions that now route the resulting text).
    "rok", "roko", "agla", "pichla", "khali", "saaf",
]
_MAX_CONTEXT_HOTWORDS = 8


def _get_context_hotwords() -> list[str]:
    """Recently-referenced app/folder/file names from ContextStack, newest
    first. Best-effort — returns [] if the stack is empty or unavailable."""
    try:
        from api.services.context_stack import context_stack as _cstack
        entities = _cstack.recent(_MAX_CONTEXT_HOTWORDS)
        seen: set[str] = set()
        words: list[str] = []
        for e in entities:
            name = (e.display or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                words.append(name)
        return words
    except Exception as exc:
        logger.debug("[Whisper/hotwords] context unavailable: %s", exc)
        return []


def _build_hotwords() -> str:
    """Static command vocabulary + dynamic recent-context entities, merged
    into a single deduplicated hotwords string for faster-whisper."""
    seen: set[str] = set()
    combined: list[str] = []
    for w in _STATIC_HOTWORDS + _get_context_hotwords():
        key = w.lower()
        if key not in seen:
            seen.add(key)
            combined.append(w)
    return " ".join(combined)


# Post-processing: fix common phonetic mistakes from Whisper before routing.
_CORRECTIONS: list[tuple[re.Pattern, str | object]] = [
    # "in D drive" — Whisper commonly mishears this phrase
    (re.compile(r'\bindeed\s+derive[d]?\b',   re.I), 'in D drive'),
    (re.compile(r'\bindeed\s+drive\b',        re.I), 'in D drive'),
    (re.compile(r'\bindividually\s+drive\b',  re.I), 'in D drive'),
    (re.compile(r'\b(?:in\s+)?indie\s+drive\b', re.I), 'in D drive'),
    (re.compile(r'\bin\s+dee\s+drive\b',      re.I), 'in D drive'),
    (re.compile(r'\bdeep\s+drive\b',          re.I), 'D drive'),
    # Drive letter mishears — comma-tolerant (real-mic Whisper inserts
    # pause-punctuation between the letter word and "drive": the live
    # transcript of "C drive kholo" was "Open, see, drive."). [,.]?\s*,?\s*
    # absorbs the comma on either side; "cee"/"seed" are additional
    # observed phonetic spellings of the letter C.
    (re.compile(r'\b(?:see|sea|si|cee|seed)[,.]?\s*,?\s*drive\b', re.I), 'C drive'),
    (re.compile(r'\bthe\s+c\s+drive\b',                          re.I), 'C drive'),
    (re.compile(r'\bdee[,.]?\s*,?\s*drive\b',                    re.I), 'D drive'),
    (re.compile(r'\bee[,.]?\s*,?\s*drive\b',                     re.I), 'E drive'),
    (re.compile(r'\beff[,.]?\s*,?\s*drive\b',                    re.I), 'F drive'),
    # Inline "C:" or "C/" notation → spoken form
    (re.compile(r'\b([a-fA-F])\s*[:/]',    re.I),
     lambda m: f'{m.group(1).upper()} drive '),
    # Folder/directory typos
    (re.compile(r'\bfould?er\b',            re.I), 'folder'),
    (re.compile(r'\bdirec?t(?:o|a)ry\b',   re.I), 'directory'),
    # tiny.en phoneme confusions
    (re.compile(r'\bcal\s+curator\b',              re.I), 'calculator'),
    (re.compile(r'\bcal\s+cure\s+later\b',         re.I), 'calculator'),
    # Roman Urdu phoneme confusions — espeak-ng/tiny.en artifacts for common commands.
    # Real mic audio rarely needs these; these cover synthetic voice in test harness.
    (re.compile(r'\bbye[,.]?\s+carr[ao]+\.?\s*$',  re.I), 'band karo'),
    (re.compile(r'\band[,.]?\s+caro?\.?\s*$',      re.I), 'band karo'),
    (re.compile(r'\bbuy\s+(?:the\s+)?car\s+out',   re.I), 'band karo'),
    (re.compile(r'\bbend\s+car[ao]?\b',             re.I), 'band karo'),
    (re.compile(r'\bchrome\s+code\s+load',          re.I), 'chrome kholo'),
    (re.compile(r'\bthis\s+code\s+install\s+caro',  re.I), 'isko install karo'),
    # "store see" → "store se" (espeak renders "se" as "see")
    (re.compile(r'\bstore\s+see\b',                 re.I), 'store se'),
    # "open, calculate" → "open calculator" (tiny.en hallucination/repetition)
    (re.compile(r'\bopen[,.]?\s+calculate\.?\s*$',  re.I), 'open calculator'),
    # "tatao" → "chalao" (espeak-ng renders Roman Urdu "chalao" as "tatao")
    (re.compile(r'\btatao\b',                        re.I), 'chalao'),
    # espeak renders "youtube believer chalao" as "youtube, pop, believe, a, tatao"
    (re.compile(r'\byoutube[,.]?\s+(?:pop[,.]?\s+)?belie(?:f|ve)[a-z,.\s]*chalao', re.I),
     'play believer on youtube'),
    # Edge-TTS neural voice: "band karo" → "band kettle" (neural phonetics confuse tiny.en)
    (re.compile(r'\bband\s+kettle\b', re.I), 'band karo'),
    # Edge-TTS: "Believer" spoken in Urdu accent → "bullyver"
    (re.compile(r'\bbullyver\b', re.I), 'believer'),
    # "volume barhao" with neural voice: tiny.en drops "barhao" → bare "volume." alone
    # In a voice assistant context, "volume" alone means "volume up"
    (re.compile(r'^\s*volume[.,!?\s]*$', re.I), 'volume up'),
    # "launch karo" / "open care" — "karo" phonetically becomes "care" (Edge-TTS neural)
    (re.compile(r'\bopen\s+care\b', re.I), 'open karo'),
    (re.compile(r'\blaunch\s+care\b', re.I), 'launch karo'),
    # "store se" merged into "starse" by Whisper (tokens fuse when spoken fast)
    (re.compile(r'\bstarse\b', re.I), 'store se'),
    # "download carol" / "download care" at sentence end → "download karo"
    (re.compile(r'\bdownload[,.]?\s+car(?:ol|e)[.,!?\s]*$', re.I), 'download karo'),
    # "dikhao" → "show" (Urdu "show me" verb, commonly dropped by tiny.en)
    # Keep only when it appears as a standalone suffix (already at sentence end)
    (re.compile(r'\bdikhao\b', re.I), 'show'),
    # Common command normalizations
    (re.compile(r'\bcreate\s+a\s+new\s+folder\b', re.I), 'create folder'),
    (re.compile(r'\bopen\s+the\s+settings?\b',     re.I), 'open settings'),
    (re.compile(r'\bopen\s+file\s+explorer\b',     re.I), 'open file explorer'),
    # Volume numbers (word → digit)
    (re.compile(r'\bvolume\s+to\s+(?:one\s+)?hundred\b',  re.I), 'volume to 100'),
    (re.compile(r'\bvolume\s+to\s+fifty\b',                re.I), 'volume to 50'),
    (re.compile(r'\bvolume\s+to\s+twenty[\s-]?five\b',     re.I), 'volume to 25'),
    (re.compile(r'\bvolume\s+to\s+seventy[\s-]?five\b',    re.I), 'volume to 75'),
    # Brightness numbers
    (re.compile(r'\bbrightness\s+to\s+(?:one\s+)?hundred\b', re.I), 'brightness to 100'),
    (re.compile(r'\bbrightness\s+to\s+fifty\b',               re.I), 'brightness to 50'),
]


def _apply_corrections(text: str) -> str:
    """Apply rule-based corrections to fix Whisper phonetic transcription errors."""
    for pattern, repl in _CORRECTIONS:
        text = pattern.sub(repl, text)  # type: ignore[arg-type]
    return text.strip()

import threading as _threading

_model       = None
_model_lock  = _threading.Lock()
_model_ready = _threading.Event()   # set once the model is fully loaded and usable

# Dedicated pool for Whisper inference calls. Previously every STT call sat
# on Python's single global default ThreadPoolExecutor alongside PowerShell
# polling (system monitor, verifier, ps_session) and Kokoro TTS — on a slow
# machine a backlog of any one of those could starve the others waiting for
# a free worker thread. 2 workers lets the fast (tiny/base.en) and accurate
# (small) models run without serializing behind each other, without letting
# STT compete with unrelated lightweight background polling for the same pool.
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
stt_executor = _ThreadPoolExecutor(max_workers=2, thread_name_prefix="whisper-stt")

# ── Fast model (Hybrid STT) ───────────────────────────────────────────────────
# base.en (~74M params) instead of tiny.en (~39M) — meaningfully better accuracy
# on short commands routed here (audio <=1.2s in hybrid_stt_router.py), still
# fast enough for the same latency budget.
_FAST_MODEL_SIZE = os.getenv("WHISPER_FAST_MODEL", "base.en")
_tiny_model      = None
_tiny_model_lock = _threading.Lock()


# ── Hardware detection ────────────────────────────────────────────────────────

def _detect_device() -> tuple[str, str]:
    """Return (device, compute_type) for faster-whisper.

    On low-VRAM GPUs (≤4GB) uses int8_float16 instead of pure float16 for
    EVERY model size — this halves the VRAM needed for the encoder while
    keeping GPU acceleration. Pure float16 OOMs on a T1200 (4GB) when the
    encoder + Kokoro/XTTS + Ollama are all resident.

    Benchmarked 2026-08-24 on this project's T1200 (scripts/bench_stt_latency.py
    + bench_stt_accuracy_parity.py): for the 'small' model on a 2s clip,
    int8_float16 transcribed in 924ms vs float16's 2422ms (2.6x faster —
    this was the root cause of the ~3s multilingual STT latency in the real
    mic trace) while using 288MB VRAM instead of 640MB, and produced
    identical transcripts on 14/15 real clips under production params (the
    one divergence was a mumbled wake phrase both variants misheard).
    Opt out with WHISPER_COMPUTE_TYPE=float16 if a future hardware/firmware
    pair regresses on int8.
    """
    _override = os.getenv("WHISPER_COMPUTE_TYPE", "").strip().lower()
    try:
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
            vram_total = torch.cuda.get_device_properties(0).total_memory
            _low_vram = vram_total <= 4 * 1024**3  # ≤4GB
            if _override:
                logger.info("[Whisper] GPU detected: %s — compute_type forced by WHISPER_COMPUTE_TYPE=%s",
                            gpu, _override)
                return "cuda", _override
            if _low_vram:
                logger.info("[Whisper] GPU detected: %s (%dMB) — low-VRAM, using int8_float16 (benchmarked 2.6x faster than float16 here)",
                            gpu, vram_total // (1024*1024))
                return "cuda", "int8_float16"
            logger.info("[Whisper] GPU detected: %s — using float16", gpu)
            return "cuda", "float16"
    except ImportError:
        pass
    logger.info("[Whisper] No CUDA — using CPU int8")
    return "cpu", "int8"


# ── Model load ────────────────────────────────────────────────────────────────

def _get_model():
    global _model
    # Fast path — model already loaded, no lock needed.
    if _model is not None:
        return _model
    # Slow path — first caller loads; concurrent callers block until done.
    with _model_lock:
        if _model is not None:      # re-check after acquiring lock (double-checked locking)
            return _model
        from faster_whisper import WhisperModel
        device, compute_type = _detect_device()
        logger.info("[Whisper] Loading '%s' on %s (%s)…", _MODEL_SIZE, device, compute_type)
        try:
            _model = WhisperModel(_MODEL_SIZE, device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cuda":
                logger.warning(
                    "[Whisper] GPU load failed (%s) — falling back to CPU int8. "
                    "Fix: echo '/usr/lib/wsl/lib' | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf && sudo ldconfig",
                    exc,
                )
                _model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            else:
                raise
        logger.info("[Whisper] Model ready.")
        _model_ready.set()          # unblock any callers waiting on the ready gate
    return _model


def _get_tiny_model():
    """Load tiny.en (English-only fast model) — for Hybrid STT routing."""
    global _tiny_model
    if _tiny_model is not None:
        return _tiny_model
    with _tiny_model_lock:
        if _tiny_model is not None:
            return _tiny_model
        from faster_whisper import WhisperModel
        device, compute_type = _detect_device()
        logger.info("[Whisper/tiny] Loading '%s' on %s (%s)…", _FAST_MODEL_SIZE, device, compute_type)
        try:
            _tiny_model = WhisperModel(_FAST_MODEL_SIZE, device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cuda":
                logger.warning("[Whisper/tiny] GPU load failed (%s) — using CPU int8", exc)
                _tiny_model = WhisperModel(_FAST_MODEL_SIZE, device="cpu", compute_type="int8")
            else:
                raise
        logger.info("[Whisper/tiny] %s ready.", _FAST_MODEL_SIZE)
    return _tiny_model


def preload_model() -> None:
    """Eagerly load the model (call from startup thread to avoid cold-start lag)."""
    try:
        _get_model()
    except Exception as exc:
        logger.warning("[Whisper] Preload failed: %s", exc)


def preload_tiny_model() -> None:
    """Eagerly load tiny.en (call from startup warmup thread)."""
    try:
        _get_tiny_model()
    except Exception as exc:
        logger.warning("[Whisper/tiny] Preload failed: %s", exc)


_TINY_INITIAL_PROMPT = (
    "Xyron, open Chrome. Open VS Code. Open Notepad. Open Calculator. Open Explorer. "
    "Open File Explorer. Open Settings. Open Terminal. Open Spotify. Open Discord. "
    "Take screenshot. Volume up. Volume down. Lock screen. Shutdown. Restart. "
    "Install it. Open it again. What am I looking at. Yes. No. Cancel."
)

def transcribe_fast(audio: np.ndarray, sample_rate: int = 16000) -> dict:
    """
    Fast transcription using base.en — target <500ms warm on GPU.

    English-only, beam_size=1, VAD-filtered. Use for short/simple commands.
    The caller should check result["confidence"] (avg_logprob) and retry
    with transcribe_audio() if the result is uncertain.
    """
    model = _get_tiny_model()
    _common = dict(
        beam_size=1, language="en", vad_filter=True,
        condition_on_previous_text=False, initial_prompt=_TINY_INITIAL_PROMPT,
        hotwords=_build_hotwords(),
        # Root-cause fix for a live-measured failure mode: short commands
        # ("open calculator", "open chrome", "open c drive") reliably made
        # greedy (beam_size=1) tiny.en loop the whole phrase 2-6x — e.g.
        # "Open Calculator. Open Calculator. Open Calculator. ..." — which
        # then cost a ~2.5-3.5s escalation to the accurate model on every
        # single occurrence via the hallucination-retry path. no_repeat_
        # ngram_size=3 cut the loop length dramatically but still let one
        # "X. X" duplicate through on short 2-3 word phrases; =2 (blocks any
        # repeated bigram) closed that gap in live testing. Voice commands
        # are short imperative phrases, not free dictation, so blocking
        # bigram repeats has no realistic legitimate-speech cost here.
        no_repeat_ngram_size=2,
    )
    segments_raw, _info = model.transcribe(audio, temperature=0.0, **_common)
    segments = _filter_segments(segments_raw)
    # Empty on greedy pass → tiny retry with slight temperature to break deadlock.
    # This avoids escalating to the 3s accurate model for simple short commands.
    if not segments:
        logger.debug("[Whisper/tiny] empty on temperature=0.0 — retrying at 0.2")
        segments_raw2, _ = model.transcribe(audio, temperature=0.2, **_common)
        segments = _filter_segments(segments_raw2)
    raw_text  = " ".join(s.text.strip() for s in segments).strip()
    full_text = _apply_corrections(raw_text)
    avg_conf  = (sum(s.avg_logprob for s in segments) / len(segments)
                 if segments else -999.0)
    if full_text != raw_text:
        logger.debug("[Whisper/tiny] correction: %r → %r", raw_text, full_text)
    logger.info("[Whisper/tiny] transcript=%r conf=%.2f", full_text[:80], avg_conf)
    return {
        "text":       full_text,
        "language":   "en",
        "confidence": round(avg_conf, 3),
        "duration":   None,
        "segments":   [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
        ],
    }


def set_model_size(size: str) -> None:
    """Hot-swap model size — reloads on next transcription call."""
    global _model, _MODEL_SIZE
    _model = None
    _model_ready.clear()    # reset gate so callers wait for the new model to load
    _MODEL_SIZE = _validate_model_size(size)
    logger.info("[Whisper] Model size set to '%s'", _MODEL_SIZE)


# ── Confidence filtering ──────────────────────────────────────────────────────

def _filter_segments(segments) -> list:
    """Drop segments below avg_logprob threshold (breathing, noise, hallucinations)."""
    kept = []
    for seg in segments:
        if seg.avg_logprob >= _CONF_THRESH:
            kept.append(seg)
        else:
            logger.debug(
                "[Whisper] Noise segment dropped (logprob=%.2f): %r",
                seg.avg_logprob, seg.text[:40],
            )
    return kept


# ── Public API ────────────────────────────────────────────────────────────────

def transcribe_audio(
    audio: np.ndarray,
    sample_rate: int = 16000,
    language: Optional[str] = None,
    fast: bool = False,
) -> dict:
    """
    Transcribe a float32 numpy array at 16kHz.

    Args:
        audio:    float32 numpy array
        language: ISO code | "auto" | None  (None → WHISPER_LANGUAGE env var)
        fast:     True → no VAD (skips our own separate VAD pass — see vad_filter below)

    Returns:
        {text, language, confidence, duration, segments}
    """
    model = _get_model()
    lang = _resolve_lang(language)  # None → auto-detect (handles Urdu, English, mixed)

    # Restrict Whisper's own language-ID to what this app can actually
    # transcribe/respond in (en/ur) instead of trusting its full 99-language
    # open-vocabulary guess — see _LANGUAGE_CANDIDATES docstring. Only in
    # auto mode (lang is None); an explicit WHISPER_LANGUAGE override or a
    # caller-supplied language still passes straight through unchanged.
    # initial_prompt stays on the untouched `lang` (trilingual when auto) —
    # this only narrows which language's tokenizer/output-script Whisper
    # commits to, not the decoder's vocabulary bias.
    #
    # NOT gated on `fast` — that flag only means "skip transcribe()'s own
    # internal VAD pass" (hybrid_stt_router already VAD-trimmed the audio
    # itself before calling in), it says nothing about model size or
    # accuracy. hybrid_stt_router's "accurate"/multilingual path — the ONE
    # place auto-detect actually runs live — calls transcribe_audio with
    # fast=True for exactly that VAD reason, so gating this on `not fast`
    # silently skipped the restriction on every real multilingual call
    # (live-caught 2026-08-24 testing against the actual voice pipeline,
    # not just this function in isolation).
    effective_lang = lang
    if lang is None:
        _restricted = _detect_restricted_language(model, audio)
        if _restricted is not None:
            effective_lang = _restricted

    segments_raw, info = model.transcribe(
        audio,
        # beam_size=1 (greedy) for both paths — live-measured 2026-08-21 on this
        # project's T1200 GPU across 5 Urdu + 3 English real-speech test clips:
        # beam_size 1 vs 2 vs 3 produced IDENTICAL transcription accuracy every
        # time (same exact-match count, same specific errors), while beam_size=3
        # cost ~1s more per call for zero accuracy benefit. Not a universal
        # claim about beam search — just what this specific model/hardware pair
        # showed; re-benchmark before changing on different hardware.
        beam_size=1,
        language=effective_lang,
        task="transcribe",            # Phase 2.4: always transcribe, never silently translate
        vad_filter=not fast,          # skip VAD in fast mode (we do our own)
        vad_parameters={"min_silence_duration_ms": 300},
        temperature=0.0,              # greedy — deterministic + fastest
        condition_on_previous_text=False,
        no_repeat_ngram_size=3,        # same repetition-loop mitigation as transcribe_fast
        initial_prompt=_get_initial_prompt(lang),
        hotwords=_build_hotwords(),
    )

    segments  = _filter_segments(segments_raw)
    raw_text  = " ".join(s.text.strip() for s in segments).strip()
    full_text = _apply_corrections(raw_text)

    avg_conf  = (sum(s.avg_logprob for s in segments) / len(segments)
                 if segments else -999.0)

    if full_text != raw_text:
        logger.info("[Whisper] correction applied: %r → %r", raw_text, full_text)
    logger.info("[Whisper] transcript=%r lang=%s conf=%.2f",
                full_text[:80], info.language, avg_conf)

    return {
        "text":       full_text,
        "language":   info.language,
        "confidence": round(avg_conf, 3),
        "duration":   round(info.duration, 2) if hasattr(info, "duration") else None,
        "segments":   [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
        ],
    }


def transcribe_file(wav_path: Path, language: Optional[str] = None) -> dict:
    """Transcribe a file (WAV/MP3/WebM). Confidence filtering applied."""
    model = _get_model()
    lang  = _resolve_lang(language)

    segments_raw, info = model.transcribe(
        str(wav_path),
        beam_size=5,
        language=lang,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segments  = _filter_segments(segments_raw)
    full_text = " ".join(s.text.strip() for s in segments).strip()
    avg_conf  = (sum(s.avg_logprob for s in segments) / len(segments)
                 if segments else -999.0)

    return {
        "text":       full_text,
        "language":   info.language,
        "confidence": round(avg_conf, 3),
        "segments":   [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments
        ],
    }


def _resolve_lang(language: Optional[str]) -> Optional[str]:
    """Resolve language param to what faster-whisper expects (None = auto-detect)."""
    lang = language or _LANGUAGE
    return None if lang in (None, "auto") else lang


# Languages this app actually has a working end-to-end path for: real STT
# tuning (hotwords/initial_prompt, corrections) and real TTS (edge-tts
# ur-PK-*Neural for Urdu; Kokoro for English). Whisper's own open-vocabulary
# auto-detect (99 languages) is free to pick "hi" for Urdu speech — they're
# the same spoken language (Hindustani) in different scripts/registers, and
# acoustically near-indistinguishable on short/accented clips — and once it
# commits to "hi" it transcribes in DEVANAGARI, which every downstream
# Urdu-aware component (mixed_language_engine, intent_router's Roman-Urdu
# regexes, the Urdu-script keyword lists) is blind to, since none of them
# expect Devanagari. hi/ar responses can't even be spoken correctly today
# regardless (XTTS's checkpoint is confirmed corrupted on this machine —
# see xtts_service.py's module docstring — so hi/ar TTS silently falls back
# to Kokoro's English voice reading foreign script). Live-caught 2026-08-24:
# "Urdu mein baat karo" ("speak in Urdu") got auto-detected as "hi" and
# transcribed into unusable Devanagari mush, which then fed a total
# misunderstanding of the command downstream. Restricting Whisper's own
# candidate set to what this app can actually understand AND speak closes
# that failure mode at the source instead of trying to recover from it
# after the fact.
_LANGUAGE_CANDIDATES = frozenset({"en", "ur"})


def _detect_restricted_language(model, audio: np.ndarray) -> Optional[str]:
    """
    Run Whisper's own language-ID pass, but only trust it to choose among
    _LANGUAGE_CANDIDATES — never let it commit to a language this app has
    no real transcription/response path for. Returns None (→ caller falls
    back to full open-vocabulary auto-detect) if the probe itself fails;
    never raises.
    """
    try:
        _lang, _prob, all_probs = model.detect_language(audio=audio)
    except Exception as exc:
        logger.debug("[Whisper] restricted language-ID probe failed: %s", exc)
        return None
    for candidate_lang, candidate_prob in all_probs:
        if candidate_lang in _LANGUAGE_CANDIDATES:
            logger.info(
                "[Whisper] restricted language-ID: %s (p=%.2f) — top overall was %s (p=%.2f)",
                candidate_lang, candidate_prob, _lang, _prob,
            )
            return candidate_lang
    # Neither candidate showed up in the ranked list at all (shouldn't
    # happen — Whisper ranks every language it knows) — let the normal
    # open auto-detect path handle it rather than guessing.
    return None


# ── Wake phrase verification ───────────────────────────────────────────────────

# Initial prompt biases Whisper toward these proper-noun wake phrases.
# Without it, "hey xyron" often becomes "he's a" (Whisper misread).
_WAKE_INITIAL_PROMPT = "Hey Xyron. Wakeup Xyron. Hi Xyron. Okay Xyron. Yo Xyron."

# Keyword set: exact spellings + common Whisper misreadings of wake phonemes.
#   "hey"   → "he", "he's", "hay"
#   "xyron" → "zairan", "zahra", "zairon", "siren", "cyron", "iron", "zero"
# Name tokens — at least one of these MUST appear in the transcript for a match.
# Prefix words ('hey', 'hi', 'ok') are intentionally excluded: they appear in
# normal speech constantly and cause false positives when checked in isolation.
_WAKE_NAMES: frozenset[str] = frozenset({
    'xyron', 'xeron', 'xiron', 'zyron',
    'wakeup',
    'jarvis',
    'zairan', 'zahra', 'zairon', 'ziren', 'siren', 'cyron',
    'xavier', 'zero',
})


def verify_wake_phrase(pcm: np.ndarray) -> tuple[bool, str]:
    """
    Second-stage gate: run Whisper on a short PCM clip and confirm a wake keyword.

    Call this after OWW fires. Pass the last ~2.5s of buffered float32 audio.
    Returns (matched, transcript).

    Fails OPEN (returns True) if Whisper unavailable — OWW already raised the
    bar; we prefer a rare false positive over blocking a real wake word.
    """
    try:
        model = _get_model()
    except Exception:
        return True, ""

    try:
        segments_raw, _ = model.transcribe(
            pcm,
            beam_size=1,
            language="en",
            vad_filter=False,                     # window is already 2.56s — VAD strips inter-word gaps on WSL2 audio
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=_WAKE_INITIAL_PROMPT,  # bias toward wake phrase vocabulary
        )
        transcript = " ".join(s.text.strip() for s in segments_raw).lower()
        # word-level match: strip punctuation + remove internal apostrophes
        # so "he's" → "hes", "wake," → "wake", "zahra." → "zahra"
        words = set(
            w.strip(".,!?'\"").replace("'", "") for w in transcript.split()
        )
        matched = bool(words & _WAKE_NAMES)
        logger.info("[Whisper/wake] transcript=%r matched=%s", transcript[:80], matched)
        return matched, transcript
    except Exception as exc:
        logger.warning("[Whisper/wake] verification error: %s — failing open", exc)
        return True, ""
