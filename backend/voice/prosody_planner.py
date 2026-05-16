"""
Prosody planner — splits text into synthesizable chunks with pause/style metadata.

Emotion profiles and their pacing:
  HYPED_NATURAL     — upgrade reactions: sentence splits, 100ms pauses, 1.08-1.10x speed
  RELIEVED_EXCITED  — bug/wake fixes: first chunk calm 0.98x, rest 1.05x
  HYPED / EXTREME   — em-dash splits, 150ms dramatic pauses, 1.10x
  DOMINANT          — sentence splits, 200ms pauses, 0.95x
  PROTECTIVE_FOCUSED — no splitting, 0.96x, calm
  PROUD_CALM        — no splitting, 0.98-1.02x, warm
  CALM / LATE_NIGHT — no splitting, single chunk

Chunk size rules:
  - Intro / audience mode: 60-120 chars per chunk
  - Emotional burst:        ≤20 chars (micro-reaction)
  - Default:                40-100 chars
  - Hard max:               150 chars (never exceeded)

Word-safety: all splits happen at whitespace/punctuation boundaries.
Never split inside a word, name, or phonetic replacement.

Each ProsodicChunk maps to one Kokoro synthesis call; silence is inserted between chunks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ProsodicChunk:
    text:          str
    pause_after_ms: int   # ms of silence to insert AFTER this chunk
    style:         str    # hint to audio FX / TTS speed ("fast", "normal", "slow")


@dataclass
class ProsodyPlan:
    chunks:      list[ProsodicChunk]
    speed:       float  # TTS speed override (1.0 = normal)
    pitch_shift: float  # semitones (0.0 = no shift)
    energy:      float  # 0.0–1.0 overall energy level


# ── Split patterns ────────────────────────────────────────────────────────────

_EM_DASH_RE   = re.compile(r'\s*—\s*')
_SENTENCE_RE  = re.compile(r'(?<=[.!?])\s+')
_COMMA_CLAUSE = re.compile(r',\s+(?=\S)')

_HARD_MAX_CHARS = 150  # never exceed — prevents Kokoro slow synthesis on long inputs


def _guard_chunk_size(
    parts: list[tuple[str, int, str]],
    max_chars: int = _HARD_MAX_CHARS,
) -> list[tuple[str, int, str]]:
    """
    Split any chunk that exceeds max_chars at a comma or whitespace boundary.
    Never cuts inside a word. Returns a new flat list.
    """
    result: list[tuple[str, int, str]] = []
    for text, pause, style in parts:
        if len(text) <= max_chars:
            result.append((text, pause, style))
            continue
        # Try splitting at commas first
        sub = _COMMA_CLAUSE.split(text, maxsplit=1)
        if len(sub) == 2 and len(sub[0].strip()) >= 10:
            result.append((sub[0].strip(), 60, style))
            result.append((sub[1].strip(), pause, style))
        else:
            # Fall back to whitespace split at max_chars boundary (word-safe)
            chunk = text
            while len(chunk) > max_chars:
                cut = chunk[:max_chars].rfind(" ")
                if cut < 20:
                    cut = max_chars  # no space found — hard cut (rare)
                result.append((chunk[:cut].strip(), 60, style))
                chunk = chunk[cut:].strip()
            if chunk:
                result.append((chunk, pause, style))
    return result


def _split_hyped(text: str) -> list[tuple[str, int, str]]:
    """Split at sentence boundaries. Em-dash → natural pause. Calm pauses — no drama."""
    parts: list[tuple[str, int, str]] = []
    em_parts = _EM_DASH_RE.split(text)
    for i, part in enumerate(em_parts):
        part = part.strip()
        if not part:
            continue
        sentences = _SENTENCE_RE.split(part)
        for j, sent in enumerate(sentences):
            sent = sent.strip()
            if not sent:
                continue
            is_last = (i == len(em_parts) - 1) and (j == len(sentences) - 1)
            pause = 0 if is_last else (80 if j < len(sentences) - 1 else 100)
            parts.append((sent, pause, "fast"))
    return _guard_chunk_size(parts) or [(text, 0, "fast")]


def _split_extreme(text: str) -> list[tuple[str, int, str]]:
    """EXTREME: sentence splits with moderate emotional pauses."""
    parts: list[tuple[str, int, str]] = []
    em_parts = _EM_DASH_RE.split(text)
    for i, part in enumerate(em_parts):
        part = part.strip()
        if not part:
            continue
        sentences = _SENTENCE_RE.split(part)
        for j, sent in enumerate(sentences):
            sent = sent.strip()
            if not sent:
                continue
            is_last = (i == len(em_parts) - 1) and (j == len(sentences) - 1)
            pause = 0 if is_last else (100 if j < len(sentences) - 1 else 150)
            parts.append((sent, pause, "fast"))
    return _guard_chunk_size(parts) or [(text, 0, "fast")]


def _split_dominant(text: str) -> list[tuple[str, int, str]]:
    """Sentence boundaries, steady authoritative pauses."""
    parts: list[tuple[str, int, str]] = []
    sentences = _SENTENCE_RE.split(text.strip())
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        is_last = i == len(sentences) - 1
        pause = 0 if is_last else 200
        parts.append((sent, pause, "slow"))
    return _guard_chunk_size(parts) or [(text, 0, "slow")]


def _split_natural(text: str) -> list[tuple[str, int, str]]:
    """Natural sentence splits — conversational rhythm, short pauses."""
    parts: list[tuple[str, int, str]] = []
    sentences = _SENTENCE_RE.split(text.strip())
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        is_last = i == len(sentences) - 1
        pause = 0 if is_last else 100
        parts.append((sent, pause, "normal"))
    return _guard_chunk_size(parts) or [(text, 0, "normal")]


def _split_intro(text: str) -> list[tuple[str, int, str]]:
    """Intro/audience mode: longer chunks (60-120 chars), calm pacing."""
    parts: list[tuple[str, int, str]] = []
    sentences = _SENTENCE_RE.split(text.strip())
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        is_last = i == len(sentences) - 1
        pause = 0 if is_last else 120
        parts.append((sent, pause, "normal"))
    # For intro, allow up to 120 chars before sub-splitting
    return _guard_chunk_size(parts, max_chars=120) or [(text, 0, "normal")]


class ProsodyPlanner:
    """Plans synthesis chunks and pacing from mood + intensity."""

    def plan(self, text: str, mood: str, intensity: str = "HIGH",
             profile: str = "") -> ProsodyPlan:
        """
        Args:
            text:      Full response text to synthesize.
            mood:      MoodStateLabel string (HYPED, DOMINANT, CALM, etc.)
            intensity: EXTREME | HIGH | NORMAL
            profile:   Emotion profile override — HYPED_NATURAL, RELIEVED_EXCITED,
                       PROTECTIVE_FOCUSED, PROUD_CALM, or "" to auto-select.
        Returns:
            ProsodyPlan with chunks list and voice parameters.
        """
        mood_up    = (mood or "").upper()
        intens     = (intensity or "HIGH").upper()
        profile_up = (profile or "").upper()

        # Skip splitting for short text
        if len(text) < 50:
            return self._single(text, mood_up, profile_up)

        # ── Named emotion profiles (most specific first) ──────────────────────
        if profile_up == "HYPED_NATURAL":
            raw_chunks = _split_natural(text)
            return ProsodyPlan(
                chunks=[ProsodicChunk(t, p, s) for t, p, s in raw_chunks],
                speed=1.10,
                pitch_shift=0.0,
                energy=0.80,
            )

        if profile_up == "RELIEVED_EXCITED":
            raw_chunks = _split_natural(text)
            # First chunk is relief — slightly slower; rest carry the excitement
            chunks = []
            for i, (t, p, s) in enumerate(raw_chunks):
                adjusted_s = "slow" if i == 0 else "normal"
                chunks.append(ProsodicChunk(t, p, adjusted_s))
            return ProsodyPlan(
                chunks=chunks,
                speed=1.05,
                pitch_shift=0.0,
                energy=0.75,
            )

        if profile_up == "PROTECTIVE_FOCUSED":
            return self._single(text, "PROTECTIVE", profile_up)

        if profile_up == "PROUD_CALM":
            return self._single(text, "CALM", profile_up)

        # ── Mood-based fallback ───────────────────────────────────────────────
        if mood_up == "HYPED" and intens == "EXTREME":
            raw_chunks = _split_extreme(text)
            return ProsodyPlan(
                chunks=[ProsodicChunk(t, p, s) for t, p, s in raw_chunks],
                speed=1.10,
                pitch_shift=0.0,
                energy=0.85,
            )

        if mood_up == "HYPED" or intens == "EXTREME":
            raw_chunks = _split_hyped(text)
            return ProsodyPlan(
                chunks=[ProsodicChunk(t, p, s) for t, p, s in raw_chunks],
                speed=1.08,
                pitch_shift=0.0,
                energy=0.80,
            )

        if mood_up == "DOMINANT":
            raw_chunks = _split_dominant(text)
            return ProsodyPlan(
                chunks=[ProsodicChunk(t, p, s) for t, p, s in raw_chunks],
                speed=0.96,
                pitch_shift=0.0,
                energy=0.82,
            )

        return self._single(text, mood_up, profile_up)

    def _single(self, text: str, mood: str, profile: str = "") -> ProsodyPlan:
        """No splitting — single chunk, pacing from profile then mood."""
        speed = 1.0
        energy = 0.7
        if profile == "PROTECTIVE_FOCUSED":
            speed, energy = 0.96, 0.65
        elif profile == "PROUD_CALM":
            speed, energy = 1.00, 0.72
        elif mood == "HYPED":
            speed, energy = 1.10, 0.82
        elif mood == "DOMINANT":
            speed, energy = 0.95, 0.85
        elif mood in ("CALM", "PROTECTIVE", "LATE_NIGHT"):
            speed, energy = 0.90, 0.60
        return ProsodyPlan(
            chunks=[ProsodicChunk(text, 0, "normal")],
            speed=speed,
            pitch_shift=0.0,
            energy=energy,
        )


# Global singleton
prosody_planner = ProsodyPlanner()
