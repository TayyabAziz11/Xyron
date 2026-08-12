"""
N-Best Whisper Decoder — Phase 2.1

Produces a ranked list of TranscriptCandidates from the dual-model STT results
already available in the HybridSTTRouter (tiny.en + small), plus synthetic
phonetic variants generated in O(1) time.

No extra Whisper calls — zero added latency from model inference.

Log markers:
  [NBEST_START]      — entry with audio duration + model used
  [NBEST_CANDIDATE]  — each candidate text + score
  [NBEST_SELECTED]   — final top-ranked candidate before further corrections
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TranscriptCandidate:
    text:              str
    confidence:        float   # normalised [0, 1]; higher = better
    avg_logprob:       float   # raw Whisper avg_logprob (negative float)
    compression_ratio: float   # segment compression ratio from Whisper
    language:          str     # ISO code reported by Whisper
    beam_rank:         int     # 1 = best hypothesis, N = weakest
    source:            str     # "primary" | "fast_model" | "synthetic"
    # Set by entity_corrector.rescore() with the real fuzzy-match score
    # against the entity database — kept separate from `confidence` (STT
    # signal) so candidate_scorer can score the entity signal independently
    # instead of reusing STT confidence as a stand-in for entity quality.
    entity_match_score: float = 0.0

    @property
    def words(self) -> list[str]:
        return self.text.lower().split()


# ── Normalise avg_logprob → [0, 1] score ─────────────────────────────────────
# Whisper logprobs are typically in [-3.0, 0.0].
# -0.0 = perfect, -3.0+ = noise / hallucination.
_LP_FLOOR = -3.0
_LP_CEIL  = 0.0


def _lp_to_score(avg_logprob: float) -> float:
    clamped = max(_LP_FLOOR, min(_LP_CEIL, avg_logprob))
    return (clamped - _LP_FLOOR) / (_LP_CEIL - _LP_FLOOR)


def _repetition_penalty(text: str, audio_dur_ms: float) -> float:
    """
    A decoder stuck in a repetition loop reports a *high* avg_logprob for its
    own repeated tokens — _lp_to_score alone cannot distinguish "confident and
    correct" from "confident and looping". This penalty multiplies the
    logprob-derived score down when the text is either low-diversity or
    physically impossible for the audio duration (e.g. 100+ words from a
    1-second clip), so a single hallucinated candidate can no longer reach
    near-1.0 confidence just because it was the only one available.
    """
    words = text.lower().split()
    n = len(words)
    if n == 0:
        return 1.0

    penalty = 1.0
    unique_ratio = len(set(words)) / n
    if n >= 8 and unique_ratio < 0.40:
        penalty *= max(0.05, unique_ratio)

    if audio_dur_ms > 0:
        wps = n / max(audio_dur_ms / 1000.0, 0.05)
        if n >= 6 and wps > 6.0:
            penalty *= max(0.05, 6.0 / wps)

    return penalty


# ── Phonetic variant table ────────────────────────────────────────────────────
# Maps common Whisper misrecognitions to their corrections.
# Applied to the PRIMARY transcript to synthesise alternative candidates.
# Keep additions here minimal — the entity corrector handles the heavy lifting.
_PHONETIC_VARIANTS: list[tuple[re.Pattern, str]] = [
    # Drive letters
    (re.compile(r'\bindeed\s+derive[d]?\b',   re.I), 'in D drive'),
    (re.compile(r'\bsee\s+drive\b',           re.I), 'C drive'),
    (re.compile(r'\bdee\s+drive\b',           re.I), 'D drive'),
    # App names
    (re.compile(r'\bn\s*video\b',             re.I), 'NVIDIA App'),
    (re.compile(r'\bn\s*vidia\b',             re.I), 'NVIDIA App'),
    (re.compile(r'\bv\s*s\s*code\b',          re.I), 'VS Code'),
    (re.compile(r'\bvisual\s+studio\s+code\b',re.I), 'VS Code'),
    # Live-measured: tiny.en/small both mis-heard "VS Code" as "Vias Code"
    # under retry — entity_corrector's fuzzy score for "Vias code" -> "VS
    # Code" was 0.82, just under the 0.88 auto-correct threshold, so it
    # silently routed open_application with app_name="vias code" (no such
    # app) instead of opening VS Code. Same phonetic-variant fix pattern as
    # the entries above, not a threshold change (which would risk over-
    # correcting other candidates).
    (re.compile(r'\bvias\s+code\b',           re.I), 'VS Code'),
    (re.compile(r'\bpower\s+b\s*i\b',         re.I), 'Power BI'),
    (re.compile(r'\bdocker\s+desk\b',         re.I), 'Docker Desktop'),
    (re.compile(r'\bhack\s+a\s+ton\b',        re.I), 'Hackathon'),
    (re.compile(r'\bhack\s*athon\b',          re.I), 'Hackathon'),
    # Common misheards
    (re.compile(r'\bcalculator\b',            re.I), 'calculator'),
    (re.compile(r'\binstagram\b',             re.I), 'instagram'),
    (re.compile(r'\bwhatsapp\b',              re.I), 'whatsapp'),
]


def _apply_variant(text: str) -> str:
    for pat, repl in _PHONETIC_VARIANTS:
        text = pat.sub(repl, text)
    return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def decode_nbest(
    primary: dict,
    secondary: Optional[dict] = None,
    n: int = 5,
    audio_dur_ms: float = 0.0,
) -> list[TranscriptCandidate]:
    """
    Build an N-best candidate list from Whisper STT results.

    Args:
        primary:       main STT result dict (from small/accurate model)
        secondary:     optional fast-model result dict (from tiny.en)
        n:             max candidates; default 5
        audio_dur_ms:  for logging only

    Returns:
        Ranked list of TranscriptCandidate, index 0 = best.
    """
    logger.info(
        "[NBEST_START] audio_ms=%.0f primary_text=%r primary_conf=%.2f secondary=%s",
        audio_dur_ms,
        (primary.get("text") or "")[:60],
        primary.get("confidence", -999.0),
        "yes" if secondary else "no",
    )

    seen:    set[str]              = set()
    candidates: list[TranscriptCandidate] = []
    rank = 1

    def _add(text: str, conf: float, lp: float, lang: str, src: str) -> None:
        nonlocal rank
        norm = text.strip()
        if not norm or norm.lower() in seen:
            return
        seen.add(norm.lower())
        c = TranscriptCandidate(
            text=norm,
            confidence=conf,
            avg_logprob=lp,
            compression_ratio=0.0,
            language=lang,
            beam_rank=rank,
            source=src,
        )
        candidates.append(c)
        logger.info(
            "[NBEST_CANDIDATE] rank=%d src=%s lang=%s conf=%.2f text=%r",
            rank, src, lang, conf, norm[:60],
        )
        rank += 1

    # ── Candidate 1: primary model ────────────────────────────────────────────
    p_text = (primary.get("text") or "").strip()
    p_lp   = primary.get("confidence", -1.5)
    p_lang = primary.get("language") or "en"
    _add(p_text, _lp_to_score(p_lp) * _repetition_penalty(p_text, audio_dur_ms), p_lp, p_lang, "primary")

    # ── Candidate 2: secondary model (tiny.en, if different) ─────────────────
    if secondary:
        s_text = (secondary.get("text") or "").strip()
        s_lp   = secondary.get("confidence", -1.5)
        s_lang = secondary.get("language") or "en"
        _add(s_text, _lp_to_score(s_lp) * 0.92 * _repetition_penalty(s_text, audio_dur_ms), s_lp, s_lang, "fast_model")

    # ── Candidates 3-N: phonetic/variant synthetic alternatives ───────────────
    for src_text, src_conf in [(p_text, p_lp), (secondary.get("text", "") if secondary else "", -1.5)]:
        if not src_text or len(candidates) >= n:
            break
        variant = _apply_variant(src_text)
        if variant != src_text:
            _add(variant, _lp_to_score(src_conf) * 0.85 * _repetition_penalty(variant, audio_dur_ms), src_conf, "en", "synthetic")

    # Fill remaining slots with partial-correction variants (strip trailing punctuation, etc.)
    if p_text and len(candidates) < n:
        stripped = p_text.rstrip(".!?,;")
        _add(stripped, _lp_to_score(p_lp) * 0.80 * _repetition_penalty(stripped, audio_dur_ms), p_lp, p_lang, "synthetic")

    result = candidates[:n]
    if result:
        logger.info("[NBEST_SELECTED] pre_correction top=%r conf=%.2f", result[0].text[:60], result[0].confidence)
    return result
