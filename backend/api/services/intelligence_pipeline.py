"""
Intelligence Pipeline — Phase 2 Orchestrator

Wires together all Phase 2 intelligence layers into a single async call
that enhances the primary Whisper transcript before it enters the existing
intent router.

Pipeline:
  1. N-best decoding     (Phase 2.1) — build candidate list from dual-model results
  2. Entity correction   (Phase 2.2) — fuzzy-match against entity database
  3. Tool-aware scoring  (Phase 2.3) — boost candidates matching predicted tool context
  4. Mixed-language pass (Phase 2.5) — map code-switched commands to canonical English
  5. Language memory     (Phase 2.6) — update language decay model in session_state
  6. Confidence voting   (Phase 2.7) — pick winner via multi-signal scoring
  7. Contextual repair   (Phase 2.8) — use screen/explorer/window context for repairs

Design constraints:
  • <50ms added latency on warm GPU (target; all layers are O(N) text ops)
  • Fully fail-safe — any exception returns the original transcript unchanged
  • Never touches: wake word, VAD, session lifecycle, FollowUpResolverV2,
    ContextStack writes, ScreenContextAgent, VerifierV2, Kokoro, XTTS

Log markers:
  [INTEL_START]         — entry with primary transcript
  [INTEL_NBEST]         — candidate count after N-best
  [INTEL_ENTITY]        — entity correction applied
  [INTEL_MIXED]         — mixed-language canonical applied
  [INTEL_WINNER]        — final chosen transcript
  [INTEL_SKIP]          — skipped (english simple command, no gain expected)
  [FINAL_CANONICAL_COMMAND] — the canonical command entering intent routing
  [INTEL_MS]            — total pipeline latency
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Result object ─────────────────────────────────────────────────────────────

@dataclass
class IntelligenceResult:
    winner_text:    str
    original_text:  str
    corrected:      bool             # True if winner_text != original_text
    candidates:     list = field(default_factory=list)
    winner_score:   Optional[object] = None   # CandidateScore
    latency_ms:     float = 0.0


# ── Language memory (Phase 2.6) ───────────────────────────────────────────────
# Tracks per-session language preference with exponential decay.

_LANG_DECAY = 0.7           # each new turn reduces prior weight by 30%
_LANG_THRESHOLD = 0.45      # if language probability > 45%, use it as session default

def _update_language_memory(detected_lang: str, session_state: dict) -> None:
    """
    Update the language probability model in session_state.
    Uses exponential decay so recent turns dominate.

    session_state["lang_memory"] = {"ur": 0.82, "ar": 0.12, "en": 0.06}
    """
    memory: dict[str, float] = session_state.setdefault("lang_memory", {})
    # Decay all existing probabilities
    for k in list(memory):
        memory[k] *= _LANG_DECAY
    # Boost detected language
    memory[detected_lang] = memory.get(detected_lang, 0.0) + (1.0 - _LANG_DECAY)
    # Normalise
    total = sum(memory.values()) or 1.0
    for k in memory:
        memory[k] /= total
    # Update session dominant language
    dominant = max(memory, key=memory.get)  # type: ignore[arg-type]
    if memory[dominant] >= _LANG_THRESHOLD and dominant != "en":
        if session_state.get("ml_detected_lang") != dominant:
            logger.info(
                "[LANGUAGE_MEMORY] lang=%s prob=%.2f → setting session dominant",
                dominant, memory[dominant],
            )
            session_state["ml_detected_lang"] = dominant
            # Memory-dominant is deliberate multilingual evidence — give it a
            # high conf so hybrid_stt_router's confidence gate honors it.
            session_state["ml_detected_lang_conf"] = 0.95


# ── Skip heuristic ────────────────────────────────────────────────────────────
# Skip the full intelligence pipeline for trivially simple English commands
# where we know Whisper tiny.en is reliable — saves ~5ms.

import re as _re

_SIMPLE_COMMANDS = _re.compile(
    r'^(?:yes|no|yeah|nope|ok|okay|cancel|stop|go ahead|confirm|'
    r'volume\s+(?:up|down)|mute|unmute|pause|play|resume|next|back|skip|'
    r'take\s+(?:a\s+)?screenshot|lock(?:\s+screen)?|shutdown|restart|'
    r'open\s+chrome|open\s+firefox|open\s+edge|open\s+notepad|'
    r'open\s+calculator|open\s+settings|open\s+terminal|'
    r'open\s+task\s*manager|open\s+(?:file\s+)?explorer|'
    r'open\s+(?:the\s+)?[a-z]\s+drive|'
    r'open\s+(?:display|sound|network|bluetooth|wi-?fi|update|privacy|'
    r'system|apps|personalization|accounts?)\s+settings|'
    r'close\s+it|open\s+it|install\s+it)\s*[.!?]?$',
    _re.I,
)


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def process(
    stt_result: dict,
    session_state: dict,
    secondary_result: Optional[dict] = None,
    audio_dur_ms: float = 0.0,
    trace_id: str = "",
) -> IntelligenceResult:
    """
    Run the Phase 2 intelligence pipeline on a Whisper STT result.

    Args:
        stt_result:       primary result dict from hybrid_stt_router
        session_state:    current WebSocket session state dict (mutated in-place for lang_memory)
        secondary_result: optional fast-model result (tiny.en) if available
        audio_dur_ms:     audio duration for logging
        trace_id:         per-turn trace ID (api.services.tracer) — passed through
                          to mixed_language_engine.analyze() so its
                          [MIXED_LANGUAGE] log can be correlated with the
                          rest of this turn's logs.

    Returns:
        IntelligenceResult with winner_text (possibly improved transcript)
    """
    t0 = time.monotonic()

    original_text = (stt_result.get("text") or "").strip()
    raw_lang      = (stt_result.get("language") or "en").lower()

    logger.info(
        "[INTEL_START] text=%r lang=%s audio_ms=%.0f secondary=%s",
        original_text[:70], raw_lang, audio_dur_ms, "yes" if secondary_result else "no",
    )

    # ── Deterministic fast lane — bypass the full pipeline for clear,
    # unambiguous English commands. No confidence gate: _SIMPLE_COMMANDS is
    # an anchored exact-phrase match (not fuzzy), so once the text matches
    # one of these specific short commands there's nothing left for the
    # entity/candidate pipeline to usefully correct — and raw avg_logprob
    # doesn't reliably separate good from bad here anyway (live-measured:
    # a legit "Open display settings." scored -0.30, a hallucinated repeat
    # loop scored -0.10 — *less* negative than the good transcript). An
    # earlier version of this gate (stt_confidence > -0.5) was measured
    # live to reject legitimate "Open calculator." transcripts whose
    # accurate-model confidence came back below threshold, defeating the
    # whole point of the fast lane. Hallucinated audio is already screened
    # out upstream in hybrid_stt_router.detect_hallucination() before this
    # function ever sees the transcript, so no further gating is needed.
    if raw_lang == "en" and _SIMPLE_COMMANDS.match(original_text):
        stt_confidence = stt_result.get("confidence", -999.0)
        logger.info("[INTEL_SKIP] simple_english cmd=%r", original_text[:50])
        logger.info(
            "[INTELLIGENCE_PIPELINE_BYPASSED] reason=deterministic_command confidence=%.2f text=%r",
            stt_confidence, original_text[:60],
        )
        return IntelligenceResult(
            winner_text=original_text, original_text=original_text,
            corrected=False, latency_ms=0.0,
        )

    # ── Phase 2.6: Language memory update ────────────────────────────────────
    try:
        from api.services.language_detector import detect as _lang_detect
        _lang_info = _lang_detect(original_text, raw_lang)
        detected_lang = _lang_info.get("lang", raw_lang)
        lang_confidence = _lang_info.get("confidence", 0.90)
        _update_language_memory(detected_lang, session_state)
    except Exception as _le:
        detected_lang   = raw_lang
        lang_confidence = 0.80
        logger.debug("[INTEL] lang_detect failed: %s", _le)

    # ── Phase 2.5: Mixed-language pre-pass ────────────────────────────────────
    mixed_canonical: Optional[str] = None
    _is_compound = False
    if detected_lang not in ("en",):
        try:
            from api.services.mixed_language_engine import (
                analyze as _mixed_analyze,
                split_compound as _mixed_split_compound,
            )
            # Compound check FIRST. analyze() takes the FIRST _VERB_MAP
            # pattern that matches ANYWHERE in the text and canonicalizes
            # only that one action — on a compound utterance ("YouTube کو
            # کھولو اور کوئی گانا چلا دو") it silently collapses to a
            # single garbled canonical and DISCARDS the rest. If this were
            # allowed to override winner_text below (the unconditional-for-
            # non-English override a few lines down), orchestrator.decide()
            # downstream would receive an already-mangled, no-longer-
            # splittable string, and its OWN split_compound() call (see
            # brain/orchestrator.py) would never get a fair shot at the
            # real original wording — the exact live-caught bug
            # (2026-09-04) this whole change closes. So: when the
            # utterance IS a confident compound, skip the single-shot
            # analyze()/override entirely and let winner_text stay as the
            # untouched original_text (see the override block below),
            # so orchestrator sees clean, splittable input.
            _compound_steps = _mixed_split_compound(original_text, detected_lang, trace_id=trace_id)
            if _compound_steps:
                _is_compound = True
                logger.info(
                    "[INTEL_MIXED_COMPOUND] original=%r steps=%s — skipping single-shot override",
                    original_text[:60], _compound_steps,
                )
            else:
                mixed_canonical = _mixed_analyze(original_text, detected_lang, trace_id=trace_id)
            if mixed_canonical:
                logger.info("[INTEL_MIXED] %r → %r", original_text[:60], mixed_canonical[:60])
                logger.info(
                    "[ML_CANONICALIZATION] original=%r lang=%s method=deterministic "
                    "canonical=%r confidence=%.2f context_refs=none latency_ms=%.0f",
                    original_text[:80], detected_lang, mixed_canonical[:80],
                    lang_confidence, (time.monotonic() - t0) * 1000,
                )
        except Exception as _me:
            logger.debug("[INTEL] mixed_language failed: %s", _me)

    # ── Phase 2.1: N-best decoding ────────────────────────────────────────────
    candidates = []
    try:
        from voice.nbest_decoder import decode_nbest
        candidates = decode_nbest(
            primary=stt_result,
            secondary=secondary_result,
            n=5,
            audio_dur_ms=audio_dur_ms,
        )
        logger.info("[INTEL_NBEST] candidates=%d", len(candidates))
    except Exception as _ne:
        logger.debug("[INTEL] nbest_decode failed: %s", _ne)

    # If N-best failed, wrap original as single candidate
    if not candidates:
        from voice.nbest_decoder import TranscriptCandidate
        candidates = [TranscriptCandidate(
            text=original_text, confidence=0.60, avg_logprob=-1.5,
            compression_ratio=0.0, language=raw_lang, beam_rank=1, source="primary",
        )]

    # ── Phase 2.2: Entity correction ─────────────────────────────────────────
    try:
        from api.services.entity_corrector import rescore as _entity_rescore
        candidates = await asyncio.to_thread(_entity_rescore, candidates, session_state)
        if candidates and candidates[0].text != original_text:
            logger.info("[INTEL_ENTITY] %r → %r", original_text[:60], candidates[0].text[:60])
    except Exception as _ee:
        logger.debug("[INTEL] entity_correct failed: %s", _ee)

    # ── Phase 2.3: Tool-aware correction ─────────────────────────────────────
    try:
        from api.services.tool_aware_corrector import rescore as _tool_rescore
        candidates = await asyncio.to_thread(_tool_rescore, candidates, session_state)
    except Exception as _te:
        logger.debug("[INTEL] tool_correct failed: %s", _te)

    # ── Phase 2.7: Confidence voting ─────────────────────────────────────────
    winner_score = None
    try:
        from api.services.candidate_scorer import vote as _vote
        winner_score = await asyncio.to_thread(_vote, candidates, session_state, lang_confidence)
        winner_text = winner_score.transcript
    except Exception as _ve:
        logger.debug("[INTEL] vote failed: %s", _ve)
        winner_text = candidates[0].text if candidates else original_text

    # ── Phase 2.8: Contextual repair ─────────────────────────────────────────
    try:
        from api.services.context_resolver import resolve as _ctx_resolve
        _session_id = session_state.get("session_id", "")
        # Was the one synchronous call left in this otherwise fully-threaded
        # pipeline — can trigger a blocking PowerShell round-trip via
        # window_context (for "close/minimize/switch to..." style phrasing)
        # directly on the event loop thread. Thread it like every other step.
        repaired = await asyncio.to_thread(_ctx_resolve, winner_text, _session_id)
        if repaired != winner_text:
            logger.info("[INTEL_CTX_REPAIR] %r → %r", winner_text[:60], repaired[:60])
            winner_text = repaired
    except Exception as _cre:
        logger.debug("[INTEL] ctx_repair failed: %s", _cre)

    # ── Mixed-language override ────────────────────────────────────────────────
    # If mixed engine produced a canonical and it looks more actionable than the
    # voted winner, prefer it.
    if _is_compound:
        # See the Phase 2.5 comment above: a confident compound split means
        # the single-shot mixed_canonical was never computed. Reset
        # winner_text to the clean original transcript rather than
        # whatever N-best voting/context-repair produced — orchestrator's
        # own split_compound() call downstream needs the real original
        # wording (connectors intact) to split correctly, not a
        # candidate-voted/context-repaired variant that was never
        # validated against compound input.
        winner_text = original_text
    elif mixed_canonical:
        from api.services.candidate_scorer import _tool_confidence
        mc_conf = _tool_confidence(mixed_canonical)
        wt_conf = _tool_confidence(winner_text)
        if mc_conf > wt_conf or detected_lang not in ("en",):
            winner_text = mixed_canonical

    # ── Final log ─────────────────────────────────────────────────────────────
    latency_ms = (time.monotonic() - t0) * 1000
    corrected = winner_text != original_text

    logger.info(
        "[INTEL_WINNER] corrected=%s original=%r winner=%r ms=%.1f",
        corrected, original_text[:60], winner_text[:60], latency_ms,
    )
    logger.info("[FINAL_CANONICAL_COMMAND] %r", winner_text[:80])
    logger.info("[INTEL_MS] ms=%.1f", latency_ms)

    return IntelligenceResult(
        winner_text    = winner_text,
        original_text  = original_text,
        corrected      = corrected,
        candidates     = candidates,
        winner_score   = winner_score,
        latency_ms     = latency_ms,
    )
