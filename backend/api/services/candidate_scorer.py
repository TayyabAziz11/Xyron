"""
Confidence Voting Engine — Phase 2.7

Produces a final CandidateScore for each N-best transcript by combining
signals from multiple layers. The candidate with the highest total score wins.

Score signals (weights sum to 1.0):
  stt      0.35  — Whisper avg_logprob (normalised)
  entity   0.25  — entity corrector fuzzy match quality
  language 0.10  — language detector confidence
  tool     0.12  — tool prediction confidence (pattern hit vs. LLM needed)
  context  0.10  — ContextStack pronoun resolution hit
  screen   0.05  — screen context relevance
  memory   0.03  — recent entity match in memory

Log markers:
  [CANDIDATE_SCORE]   — per-candidate breakdown
  [CANDIDATE_WINNER]  — final winner + margin over runner-up
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Score breakdown dataclass ─────────────────────────────────────────────────

@dataclass
class CandidateScore:
    transcript:   str
    # Per-signal scores [0..1]
    stt:          float = 0.0
    language:     float = 0.0
    entity:       float = 0.0
    tool:         float = 0.0
    context:      float = 0.0
    screen:       float = 0.0
    memory:       float = 0.0
    # Weighted total
    total:        float = 0.0

    def breakdown(self) -> str:
        return (
            f"stt={self.stt:.2f} lang={self.language:.2f} ent={self.entity:.2f} "
            f"tool={self.tool:.2f} ctx={self.context:.2f} scr={self.screen:.2f} "
            f"mem={self.memory:.2f} → total={self.total:.3f}"
        )


_WEIGHTS = {
    "stt":      0.35,
    "language": 0.10,
    "entity":   0.25,
    "tool":     0.12,
    "context":  0.10,
    "screen":   0.05,
    "memory":   0.03,
}

# ── Tool-routing pattern (mirror of tool_aware_corrector, lightweight) ────────
_TOOL_PATS: list[tuple[re.Pattern, float]] = [
    # High-confidence patterns (exact match)
    (re.compile(r'\b(?:open|launch|start)\s+(?:chrome|firefox|edge|vs\s*code|notepad|calculator|discord|spotify)\b', re.I), 0.95),
    (re.compile(r'\b(?:install|download)\s+\S', re.I), 0.85),
    (re.compile(r'\b(?:volume\s+(?:up|down)|mute|unmute)\b', re.I), 0.92),
    (re.compile(r'\b(?:take|capture)\s+(?:a\s+)?screenshot\b', re.I), 0.95),
    (re.compile(r'\bplay\s+.+\s+on\s+(?:youtube|spotify)\b', re.I), 0.90),
    # Medium-confidence
    (re.compile(r'\b(?:open|close|create|delete|move|copy)\b', re.I), 0.72),
    (re.compile(r'\b(?:search|find|look\s+up)\b', re.I), 0.68),
]

_PRONOUN_RE = re.compile(
    r'\b(it|this|that|them|the\s+(?:app|folder|file|one))\b', re.I
)
_EN_CMD_WORDS = frozenset({
    "open", "close", "install", "download", "volume", "screenshot",
    "play", "search", "find", "create", "delete", "mute", "shutdown",
})


def _tool_confidence(text: str) -> float:
    for pat, conf in _TOOL_PATS:
        if pat.search(text):
            return conf
    # Weak signal: has ≥2 command words
    hits = sum(1 for w in text.lower().split() if w in _EN_CMD_WORDS)
    if hits >= 2:
        return 0.55
    if hits == 1:
        return 0.40
    return 0.20


def _context_score(text: str) -> float:
    """Score how well this candidate resolves with the current ContextStack."""
    try:
        from api.services.context_stack import context_stack
        if _PRONOUN_RE.search(text):
            resolved = context_stack.resolve(text)
            return 0.85 if resolved else 0.10
        return 0.50  # no pronoun — neutral
    except Exception:
        return 0.50


def _screen_score(text: str) -> float:
    """Score based on screen context relevance."""
    try:
        from api.services.window_context import _get_active_window  # type: ignore
        win = _get_active_window()
        if not win:
            return 0.50
        win_l = win.lower()
        for word in text.lower().split():
            if len(word) >= 4 and word in win_l:
                return 0.85
        return 0.40
    except Exception:
        return 0.50


def _flight_session_active() -> bool:
    try:
        from api.agents.browser_agent import flight_session_state as _fss
        return _fss.get_active() is not None
    except Exception:
        return False


def _travel_score(text: str) -> float:
    """Phase 4.10 — boost signal for active-flight-session candidates:
    does this text match a known follow-up pattern (stops/sort/baggage/
    etc.) or resolve confidently against the travel entity resolver
    (airline/city, e.g. "camera rates" -> Emirates)? Only ever consulted
    when a flight session is active — no effect on generic commands."""
    try:
        from api.agents.browser_agent.flight_conversation import FlightFollowUpResolver
        if FlightFollowUpResolver.detect(text) is not None:
            return 1.0
    except Exception:
        pass
    try:
        from api.agents.browser_agent.travel_entity_resolver import TravelEntityResolver
        air = TravelEntityResolver.resolve_airline(text)
        if air.canonical_name:
            return air.confidence
        loc = TravelEntityResolver.resolve_location(text)
        if loc.canonical_city:
            return loc.confidence
    except Exception:
        pass
    return 0.0


def _memory_score(text: str, session_state: Optional[dict]) -> float:
    """Score based on recent session entities matching the command text."""
    try:
        from api.services.context_stack import context_stack
        recent = context_stack.recent(5)
        text_l = text.lower()
        for ent in recent:
            if ent.display and ent.display.lower() in text_l:
                return 0.80
        return 0.30
    except Exception:
        return 0.30


# ── Main voting function ──────────────────────────────────────────────────────

def vote(
    candidates: list,            # list[TranscriptCandidate] after entity+tool corrector
    session_state: Optional[dict] = None,
    lang_confidence: float = 0.95,
) -> CandidateScore:
    """
    Score every candidate and return the winner CandidateScore.

    Args:
        candidates:       ranked list from tool_aware_corrector
        session_state:    current WS session state
        lang_confidence:  confidence from language detector for the primary candidate

    Returns:
        CandidateScore for the winning candidate
    """
    if not candidates:
        return CandidateScore(transcript="", total=0.0)

    scored: list[CandidateScore] = []
    _travel_active = _flight_session_active()

    for i, cand in enumerate(candidates):
        text = cand.text
        # Normalise: first candidate's lang_confidence, rest slightly lower
        lang_conf = lang_confidence if i == 0 else max(0.0, lang_confidence - 0.15 * i)

        # Confidence-corruption fix: entity_corrector.rescore() stamps the
        # real fuzzy-match score onto entity_match_score for every candidate
        # (including unmatched ones, where it's near 0). Previously this
        # branch fell back to reusing STT confidence for primary/fast_model
        # candidates, so a repeated-loop hallucination with high (corrupted)
        # STT confidence also scored high on the "entity" signal despite the
        # entity corrector having already flagged no real match (score=0.03).
        _entity_score = getattr(cand, "entity_match_score", None)
        if _entity_score is None:
            _entity_score = (
                min(1.0, cand.confidence * 1.05)
                if cand.source not in ("primary", "fast_model") else cand.confidence
            )

        s = CandidateScore(
            transcript = text,
            stt        = cand.confidence,
            language   = lang_conf,
            entity     = _entity_score,
            tool       = _tool_confidence(text),
            context    = _context_score(text),
            screen     = _screen_score(text),
            memory     = _memory_score(text, session_state),
        )

        s.total = (
            _WEIGHTS["stt"]      * s.stt +
            _WEIGHTS["language"] * s.language +
            _WEIGHTS["entity"]   * s.entity +
            _WEIGHTS["tool"]     * s.tool +
            _WEIGHTS["context"]  * s.context +
            _WEIGHTS["screen"]   * s.screen +
            _WEIGHTS["memory"]   * s.memory
        )

        logger.info(
            "[CANDIDATE_SCORE] rank=%d text=%r %s",
            i + 1, text[:60], s.breakdown(),
        )

        # Travel-aware boost — only ever applied while a flight session is
        # active, so generic (non-travel) commands are scored exactly as
        # before. Blended rather than replacing the total, so a candidate
        # with a great travel match but terrible STT confidence still
        # can't dominate on the travel signal alone.
        if _travel_active:
            travel = _travel_score(text)
            blended = s.total * 0.7 + travel * 0.3
            logger.info(
                "[TRAVEL_CANDIDATE_SCORE] rank=%d text=%r travel_score=%.3f blended_total=%.3f",
                i + 1, text[:60], travel, blended,
            )
            s.total = blended

        scored.append(s)

    winner = max(scored, key=lambda s: s.total)
    runner_up_total = sorted(s.total for s in scored)[-2] if len(scored) >= 2 else 0.0
    margin = winner.total - runner_up_total

    if _travel_active:
        logger.info("[TRAVEL_CANDIDATE_WINNER] text=%r total=%.3f margin=%.3f",
                    winner.transcript[:60], winner.total, margin)

    logger.info(
        "[CANDIDATE_WINNER] text=%r total=%.3f margin=%.3f",
        winner.transcript[:60], winner.total, margin,
    )
    return winner
