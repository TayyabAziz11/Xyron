"""
Hybrid STT Router — selects tiny.en (fast) or small (accurate) per utterance.

Fast path:  tiny.en — target <500ms warm on GPU, beam_size=1, English-only
Accurate:   small   — production model (~3s on T1200), multilingual

Decision logic:
  Pre-transcription: audio_dur_ms + session_state → mode
    fast           → tiny.en only (very short audio, confirmed simple context)
    fast_with_retry → tiny.en, then retry accurate if confidence is low
    accurate       → skip tiny.en, go straight to small

  Post-fast: confidence (avg_logprob) + word_count → retry decision

Required log markers (parsed by test harness):
  [STT_ROUTER_DECISION] mode= reason=
  [STT_FAST_RESULT]     transcript= confidence= ms=
  [STT_ACCURATE_RESULT] transcript= confidence= ms=
  [STT_RETRY_TRIGGERED] reason=
  [STT_FINAL_SELECTED]  model= transcript= total_ms=
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Known simple commands — accept tiny.en even at low confidence ─────────────
# Distinct enough that tiny.en almost never mis-transcribes them.
_SIMPLE_VOCAB: frozenset[str] = frozenset({
    # App opens
    "open chrome", "open firefox", "open edge", "open microsoft edge",
    "open vs code", "open vscode", "open visual studio code",
    "open notepad", "open calculator", "open explorer", "open file explorer",
    "open settings", "open terminal", "open command prompt",
    "open task manager", "open spotify", "open discord", "open steam",
    "open slack", "open zoom", "open teams", "open microsoft teams",
    "open word", "open excel", "open powershell",
    # Pronoun follow-ups
    "open it", "open it again", "open this", "open that",
    "close it", "close this", "close that",
    "install it", "download it", "install this", "download this",
    "uninstall it", "delete it",
    # Confirmations
    "yes", "yeah", "yep", "yup", "sure", "no", "nope",
    "cancel", "stop", "ok", "okay", "go ahead", "do it", "confirm",
    # Media
    "pause", "play", "resume", "mute", "unmute", "skip", "next", "back",
    # Volume/brightness
    "volume up", "volume down",
    # Screen queries
    "what am i looking at", "what app is open", "what's open",
    "what is open", "what app is this", "what am i working on",
    # Split variants tiny.en may produce
    "open note pad", "open note-pad", "open visual studio code",
    "open task manager", "open command prompt",
    # Misc
    "screenshot", "take screenshot", "take a screenshot",
    "lock", "lock screen", "lock it", "lock the screen",
    "shutdown", "shut down", "restart", "sleep",
})


def _decide_mode(audio_dur_ms: float, session_state: dict) -> tuple[str, str]:
    """
    Pre-transcription routing decision based on audio features and session state.

    Returns:
        (mode, reason) where mode is 'fast', 'fast_with_retry', or 'accurate'
    """
    # Explicit forced retry (Phase 4.10 short-command reliability): the fast
    # model returned an empty transcript for a short in-session flight
    # command — skip straight to the accurate model instead of re-deciding
    # "fast" again for the same short audio.
    if session_state.get("force_accurate"):
        return "accurate", "forced_accurate_retry"

    # Pending confirmation → user saying yes/no/cancel → tiny.en always sufficient
    if session_state.get("pending_confirmation"):
        return "fast", "pending_confirmation"

    # Multilingual session — previous turn was non-English → skip tiny.en entirely.
    # tiny.en is English-only and produces garbage on Urdu/Hindi/Arabic audio.
    _ml_lang = session_state.get("ml_detected_lang", "en")
    if _ml_lang and _ml_lang not in ("en",):
        logger.info("[STT_LANG_ROUTE] mode=multilingual reason=multilingual_session lang=%s", _ml_lang)
        return "accurate", f"multilingual_session_lang={_ml_lang}"

    # Very short audio (≤ 1.2s ≈ 1–2 words at normal pace) → simple, no retry needed
    if audio_dur_ms <= 1200:
        return "fast", f"short_audio_{audio_dur_ms:.0f}ms"

    # Very long audio (> 6s) → definitely a complex command → skip tiny.en
    if audio_dur_ms > 6000:
        return "accurate", f"long_audio_{audio_dur_ms:.0f}ms"

    # Medium range (1.8s–6s) → try tiny.en, retry accurate if result is uncertain
    return "fast_with_retry", f"medium_audio_{audio_dur_ms:.0f}ms"


def collapse_duplicate_sentences(text: str) -> str:
    """
    Collapse a transcript that's the same sentence repeated once, e.g.
    "Open Settings. Open settings." -> "Open Settings." or the near-miss
    "Open VS Code. Open VSCode." -> "Open VS Code." (compares normalized —
    alphanumeric only, case-insensitive — so a stray space/case difference
    from a second STT decode pass still collapses).

    Live-measured root cause of two real bugs this closes:
      1. tiny.en producing "Open Settings. Open settings." (4 words, low
         confidence) tripped _needs_retry's uncertain-entity-command check
         purely because of the word count from the duplicate, forcing an
         unnecessary ~2.5-3s escalation to the accurate model for what was
         already an unambiguous 2-word command.
      2. "Open VS Code. Open VSCode." (differs only by a space) was NOT
         byte-identical, so a naive exact-match dedup missed it — it then
         got corrected against a context_stack entity that a PRIOR turn's
         same bug had already pushed as "vs code. open vscode", compounding
         instead of fixing, and intent_router extracted the whole garbled
         phrase as app_name — which is why "open vs code" silently failed
         to open anything.

    Only collapses when EVERY sentence normalizes identically — never
    touches genuinely different multi-sentence commands.
    """
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text) if p.strip()]
    if len(parts) < 2:
        return text

    def _norm(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', s.lower())

    first_norm = _norm(parts[0])
    if first_norm and all(_norm(p) == first_norm for p in parts):
        return parts[0]
    return text


# Command verbs that legitimately open a turn AND that Whisper is prone to
# echo back as their own trailing sentence fragment on short imperative
# commands (observed live: "Open YouTube. Open." for a 1s "open youtube"
# utterance — the trailing "Open." is not a repeat of the whole sentence,
# so collapse_duplicate_sentences above doesn't catch it; it's an echo of
# just the leading verb). Deliberately the same small, closed set
# hybrid_stt_router already reasons about elsewhere in this file, not an
# attempt to cover every possible verb.
_ECHOABLE_VERBS = frozenset({
    "open", "close", "play", "pause", "stop", "download", "install",
    "click", "search", "find", "get",
})


def strip_trailing_verb_echo(text: str) -> str:
    """
    Strip a trailing sentence that's JUST the leading command verb echoed
    back on its own, e.g. "Open YouTube. Open." -> "Open YouTube." — closes
    a real bug this collapse_duplicate_sentences (above) cannot: the
    trailing fragment isn't a repeat of the FULL first sentence, only of
    its leading verb, so a whole-sentence-equality check never fires.

    Live-measured consequence of leaving this unstripped: intent_router's
    greedy `open\s+(.+)` pattern captured the entire garbled transcript as
    app_name ("youtube. open"), _launch_app's unknown-app fallback then
    ran `cmd.exe /c start "" "youtube. open"` — which Windows resolves as a
    web search for that literal text — silently "succeeding" while never
    opening YouTube at all.

    Only strips trailing fragments that are ENTIRELY the echoed verb (after
    stripping punctuation) — never touches a trailing sentence that adds
    any other content, so "open youtube and play music" is untouched.
    """
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text) if p.strip()]
    if len(parts) < 2:
        return text

    def _norm(s: str) -> str:
        return re.sub(r'[^a-z]', '', s.lower())

    first_words = parts[0].split()
    if not first_words:
        return text
    leading_verb = _norm(first_words[0])
    if leading_verb not in _ECHOABLE_VERBS:
        return text

    kept = [parts[0]]
    trimmed_any = False
    for p in parts[1:]:
        if _norm(p) == leading_verb:
            trimmed_any = True
            continue
        kept.append(p)

    if not trimmed_any:
        return text
    return " ".join(kept)


def detect_hallucination(text: str, audio_dur_ms: float) -> tuple[bool, str, dict]:
    """
    Detect Whisper decoder hallucination loops (e.g. "open open open ...",
    "open VS Code" repeated dozens of times) that a length/duration check
    catches far more reliably than raw avg_logprob — a looping decoder is
    often *confident* about its own repeated tokens.

    Runs independently of STT mode/retry routing so it also covers the
    short-audio (<=1200ms) fast path, which previously never ran any
    hallucination check at all.

    Signals (any one is sufficient):
      - unique_token_ratio: low word diversity over enough words to matter
      - repetition_count: same word/bigram/trigram repeated back-to-back
      - words_per_second / chars_per_second: physically impossible for the
        audio duration (a 1s clip cannot contain 100 words)
    """
    words = text.lower().split()
    n = len(words)
    audio_s = max(audio_dur_ms / 1000.0, 0.05)

    if n == 0:
        return False, "", {"word_count": 0, "unique_ratio": 1.0, "repeated_phrase": None}

    unique_ratio = len(set(words)) / n
    wps = n / audio_s
    cps = len(text) / audio_s

    repetition_count = 1
    repeated_phrase: Optional[str] = None
    for size in (1, 2, 3):
        if n < size * 3:
            continue
        i = 0
        while i < n - size:
            phrase = tuple(words[i:i + size])
            count = 1
            j = i + size
            while j + size <= n and tuple(words[j:j + size]) == phrase:
                count += 1
                j += size
            if count > repetition_count:
                repetition_count = count
                repeated_phrase = " ".join(phrase)
            i += 1

    reasons = []
    if n >= 8 and unique_ratio < 0.30:
        reasons.append(f"low_unique_ratio_{unique_ratio:.2f}")
    if repetition_count >= 4:
        reasons.append(f"repeated_phrase_x{repetition_count}")
    if n >= 6 and wps > 6.0:
        reasons.append(f"impossible_words_per_second_{wps:.1f}")
    if n >= 6 and cps > 25.0:
        reasons.append(f"impossible_chars_per_second_{cps:.1f}")

    info = {
        "word_count": n, "unique_ratio": unique_ratio, "words_per_second": wps,
        "chars_per_second": cps, "repetition_count": repetition_count,
        "repeated_phrase": repeated_phrase,
    }
    return bool(reasons), ";".join(reasons), info


def _needs_retry(result: dict, audio_dur_ms: float) -> tuple[bool, str]:
    """
    Post-fast-transcription retry decision.

    Retry when:
      1. Empty transcript — nothing was heard
      2. Very low confidence (< -0.7) — tiny.en not confident at all
      3. Medium-low confidence (< -0.45) with 3+ words — probable entity name

    Never retry when:
      - Transcript matches known simple vocab (pattern overrides confidence)

    Hallucination-loop detection is handled separately by
    detect_hallucination(), called unconditionally in route() regardless of
    mode — it used to live only here, which meant the short-audio "fast"
    path (<=1200ms, no retry check at all) never ran it.
    """
    text  = (result.get("text") or "").strip()
    conf  = result.get("confidence", -999.0)
    words = text.split()
    n     = len(words)
    norm  = text.lower().rstrip('.!?,')

    if n == 0:
        return True, "empty_transcript"

    # Known simple command → accept regardless of confidence
    if norm in _SIMPLE_VOCAB:
        return False, "simple_vocab_match"

    # Commands with folder/file keywords → entity name expected → always use accurate
    _ENTITY_HINTS = frozenset({"folder", "file", "directory", "document"})
    if _ENTITY_HINTS & set(norm.split()):
        return True, "entity_hint_keyword"

    # Very uncertain → retry no matter the length
    if conf < -0.7:
        return True, f"very_low_conf_{conf:.2f}"

    # Medium-low confidence on a 4+ word command → probable entity name
    # (3-word commands stay on fast path unless conf < -0.7)
    if conf < -0.45 and n >= 4:
        return True, f"uncertain_entity_cmd_conf_{conf:.2f}_words_{n}"

    return False, f"acceptable_conf_{conf:.2f}"


def route(
    audio: np.ndarray,
    audio_dur_ms: float,
    session_state: Optional[dict] = None,
) -> tuple[dict, str, Optional[dict]]:
    """
    Route STT to the optimal model for this utterance.

    Args:
        audio:        float32 numpy array at 16kHz
        audio_dur_ms: duration of audio in milliseconds
        session_state: current WebSocket session state dict

    Returns:
        (result_dict, model_name_str, secondary_result_or_None)
        secondary_result is the fast-model result when both models ran (fast_with_retry).
        Phase 2.1 uses secondary as an N-best candidate.
    """
    from voice.whisper_service import transcribe_audio, transcribe_fast

    state = session_state or {}
    mode, reason = _decide_mode(audio_dur_ms, state)
    logger.info("[STT_ROUTER_DECISION] mode=%s reason=%s audio_ms=%.0f", mode, reason, audio_dur_ms)

    # ── Accurate path — skip tiny.en entirely ────────────────────────────────
    if mode == "accurate":
        t0 = time.monotonic()
        result = transcribe_audio(audio, 16000, None, True)   # None → auto-detect (Urdu/English)
        ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[STT_ACCURATE_RESULT] transcript=%r confidence=%.2f ms=%.0f",
            (result.get("text") or "")[:80], result.get("confidence", -999.0), ms,
        )
        logger.info(
            "[STT_FINAL_SELECTED] model=small transcript=%r total_ms=%.0f",
            (result.get("text") or "")[:80], ms,
        )
        return result, "small", None  # no secondary for accurate-only path

    # ── Fast path — run tiny.en ───────────────────────────────────────────────
    t0_fast = time.monotonic()
    try:
        fast_result = transcribe_fast(audio)
    except Exception as exc:
        logger.warning("[STT_FAST_FAILED] tiny.en error=%s — falling back to accurate", exc)
        t0 = time.monotonic()
        result = transcribe_audio(audio, 16000, None, True)   # None → auto-detect
        ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[STT_ACCURATE_RESULT] transcript=%r confidence=%.2f ms=%.0f",
            (result.get("text") or "")[:80], result.get("confidence", -999.0), ms,
        )
        logger.info(
            "[STT_FINAL_SELECTED] model=small(tiny_failed) transcript=%r total_ms=%.0f",
            (result.get("text") or "")[:80], ms,
        )
        return result, "small(tiny_failed)", None

    ms_fast = (time.monotonic() - t0_fast) * 1000
    logger.info(
        "[STT_FAST_RESULT] transcript=%r confidence=%.2f ms=%.0f",
        (fast_result.get("text") or "")[:80], fast_result.get("confidence", -999.0), ms_fast,
    )

    # ── Duplicate-sentence collapse — before the retry/hallucination
    # decisions, not after. A cleaned, confident short command should never
    # pay for an escalation that its own duplicate artifact caused (see
    # collapse_duplicate_sentences docstring for the two live bugs this
    # closes: an unnecessary retry and a silently-failing "open vs code").
    _collapsed = collapse_duplicate_sentences(fast_result.get("text") or "")
    if _collapsed != (fast_result.get("text") or ""):
        logger.info("[STT_DUPLICATE_COLLAPSED] %r → %r", fast_result.get("text"), _collapsed)
        fast_result = {**fast_result, "text": _collapsed}

    _de_echoed = strip_trailing_verb_echo(fast_result.get("text") or "")
    if _de_echoed != (fast_result.get("text") or ""):
        logger.info("[STT_TRAILING_ECHO_STRIPPED] %r → %r", fast_result.get("text"), _de_echoed)
        fast_result = {**fast_result, "text": _de_echoed}

    # ── Hallucination check — runs regardless of mode, including the
    # short-audio "fast" path (<=1200ms) which previously skipped
    # _needs_retry (and therefore its hallucination check) entirely. A
    # 1-second clip producing a 100+ word repeated-loop transcript is
    # exactly the failure this closes. ──────────────────────────────────────
    _halluc, _halluc_reason, _halluc_info = detect_hallucination(
        fast_result.get("text") or "", audio_dur_ms,
    )
    if _halluc:
        logger.warning(
            "[STT_HALLUCINATION_DETECTED] reason=%s audio_ms=%.0f word_count=%d "
            "unique_ratio=%.2f repeated_phrase=%r",
            _halluc_reason, audio_dur_ms, _halluc_info["word_count"],
            _halluc_info["unique_ratio"], _halluc_info["repeated_phrase"],
        )
        logger.info("[STT_HALLUCINATION_RETRY]")
        t0_h = time.monotonic()
        try:
            acc_result = transcribe_audio(audio, 16000, None, True)
        except Exception as exc:
            logger.warning("[STT_ACCURATE_FAILED] small model error=%s during hallucination retry", exc)
            logger.warning("[STT_HALLUCINATION_REJECTED] reason=accurate_retry_failed")
            return (
                {"text": "", "confidence": -999.0, "language": fast_result.get("language", "en"), "hallucinated": True},
                "rejected", None,
            )
        ms_h = (time.monotonic() - t0_h) * 1000
        _halluc2, _halluc2_reason, _halluc2_info = detect_hallucination(
            acc_result.get("text") or "", audio_dur_ms,
        )
        if _halluc2:
            logger.warning(
                "[STT_HALLUCINATION_REJECTED] reason=%s word_count=%d unique_ratio=%.2f",
                _halluc2_reason, _halluc2_info["word_count"], _halluc2_info["unique_ratio"],
            )
            return (
                {"text": "", "confidence": -999.0, "language": acc_result.get("language", "en"), "hallucinated": True},
                "rejected", None,
            )
        logger.info(
            "[STT_FINAL_SELECTED] model=small(hallucination_retry) transcript=%r total_ms=%.0f",
            (acc_result.get("text") or "")[:80], ms_fast + ms_h,
        )
        return acc_result, "small(hallucination_retry)", None

    # ── No-retry fast path ────────────────────────────────────────────────────
    if mode == "fast":
        logger.info(
            "[STT_FINAL_SELECTED] model=tiny.en transcript=%r total_ms=%.0f",
            (fast_result.get("text") or "")[:80], ms_fast,
        )
        return fast_result, "tiny.en", None

    # ── fast_with_retry — check if accurate retry is warranted ────────────────
    needs_retry, retry_reason = _needs_retry(fast_result, audio_dur_ms)

    if not needs_retry:
        logger.info(
            "[STT_FINAL_SELECTED] model=tiny.en transcript=%r total_ms=%.0f",
            (fast_result.get("text") or "")[:80], ms_fast,
        )
        return fast_result, "tiny.en", None

    logger.info("[STT_RETRY_TRIGGERED] reason=%s", retry_reason)
    t0_acc = time.monotonic()
    try:
        acc_result = transcribe_audio(audio, 16000, None, True)  # None → auto-detect
    except Exception as exc:
        logger.warning("[STT_ACCURATE_FAILED] small model error=%s — using fast result", exc)
        logger.info(
            "[STT_FINAL_SELECTED] model=tiny.en(acc_failed) transcript=%r total_ms=%.0f",
            (fast_result.get("text") or "")[:80], ms_fast,
        )
        return fast_result, "tiny.en(acc_failed)", None

    ms_acc = (time.monotonic() - t0_acc) * 1000
    total_ms = ms_fast + ms_acc
    logger.info(
        "[STT_ACCURATE_RESULT] transcript=%r confidence=%.2f ms=%.0f",
        (acc_result.get("text") or "")[:80], acc_result.get("confidence", -999.0), ms_acc,
    )
    logger.info(
        "[STT_FINAL_SELECTED] model=small(retry) transcript=%r total_ms=%.0f",
        (acc_result.get("text") or "")[:80], total_ms,
    )
    # Phase 2.1: expose fast_result as secondary candidate (both models ran)
    return acc_result, "small(retry)", fast_result
