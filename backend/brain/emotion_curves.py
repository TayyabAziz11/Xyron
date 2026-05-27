"""
Emotion Curves — sentence-level emotional arcs for natural delivery.

Instead of one flat emotion for an entire response, generates a curve
that varies per sentence segment. Feeds into prosody_planner.py.

Curves are small, natural, and never melodramatic.
Human pacing only — no robotic emotional extremes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EmotionSegment:
    segment:  str    # role: "reaction" | "realization" | "future" | "body" | "closing"
    emotion:  str    # matches prosody_planner emotion keys
    speed:    float  # 0.85–1.15 speaking rate multiplier
    pause_ms: int    # extra pause before this segment (ms)


# ── Curve templates per intent ────────────────────────────────────────────────

_CURVES: dict[str, list[EmotionSegment]] = {

    "self_upgrade": [
        EmotionSegment("reaction",    "warm_surprise",  1.00,  0),
        EmotionSegment("realization", "excited",        1.06,  80),
        EmotionSegment("body",        "warm",           1.02,  40),
        EmotionSegment("future",      "ambitious",      1.04,  60),
        EmotionSegment("closing",     "confident",      1.00,  50),
    ],

    "frustration": [
        EmotionSegment("reaction",    "empathy",        0.96,  0),
        EmotionSegment("body",        "calm",           0.94,  60),
        EmotionSegment("solution",    "focused",        1.00,  40),
        EmotionSegment("closing",     "supportive",     0.97,  30),
    ],

    "ask_future_desire": [
        EmotionSegment("reaction",    "thoughtful",     0.96,  0),
        EmotionSegment("desire_1",    "warm",           0.98,  80),
        EmotionSegment("desire_2",    "ambitious",      1.02,  40),
        EmotionSegment("closing",     "determined",     1.00,  60),
    ],

    "intro_audience": [
        EmotionSegment("opening",     "confident",      1.00,  0),
        EmotionSegment("identity",    "proud",          1.02,  60),
        EmotionSegment("capabilities", "engaged",       1.03,  40),
        EmotionSegment("future",      "ambitious",      1.04,  80),
        EmotionSegment("closing",     "confident",      1.00,  60),
    ],

    "intro_short": [
        EmotionSegment("identity",    "confident",      1.00,  0),
        EmotionSegment("closing",     "warm",           0.98,  40),
    ],

    "intro_technical": [
        EmotionSegment("opening",     "neutral",        1.00,  0),
        EmotionSegment("stack",       "focused",        1.02,  40),
        EmotionSegment("closing",     "confident",      1.00,  50),
    ],

    "work_mode": [
        EmotionSegment("confirm",     "focused",        1.02,  0),
        EmotionSegment("action",      "determined",     1.03,  40),
    ],

    "chill_mode": [
        EmotionSegment("confirm",     "relaxed",        0.93,  0),
        EmotionSegment("action",      "warm",           0.92,  40),
    ],

    "explain_capability": [
        EmotionSegment("opening",     "engaged",        1.00,  0),
        EmotionSegment("list",        "confident",      1.01,  40),
        EmotionSegment("closing",     "proud",          1.00,  60),
    ],

    "dev_help": [
        EmotionSegment("confirm",     "focused",        1.01,  0),
        EmotionSegment("analysis",    "thoughtful",     0.97,  40),
        EmotionSegment("solution",    "confident",      1.02,  60),
    ],

    "memory_query": [
        EmotionSegment("confirm",     "thoughtful",     0.97,  0),
        EmotionSegment("recall",      "engaged",        1.00,  60),
    ],

    # Default flat curve
    "_default": [
        EmotionSegment("body",        "neutral",        1.00,  0),
    ],
}


def get_curve(intent: str) -> list[EmotionSegment]:
    """Return the emotion curve for the given intent."""
    return _CURVES.get(intent, _CURVES["_default"])


def apply_curve_to_sentences(
    sentences: list[str],
    intent:    str,
) -> list[dict]:
    """
    Pair each sentence with an emotion segment from the curve.

    Returns a list of dicts:
    [{"text": "...", "emotion": "...", "speed": 1.0, "pause_ms": 0}, ...]
    """
    curve  = get_curve(intent)
    result = []

    for i, sentence in enumerate(sentences):
        if not sentence.strip():
            continue
        seg = curve[min(i, len(curve) - 1)]
        result.append({
            "text":     sentence.strip(),
            "emotion":  seg.emotion,
            "speed":    seg.speed,
            "pause_ms": seg.pause_ms,
        })

    return result


def dominant_emotion(intent: str) -> str:
    """Return the primary emotion for a given intent."""
    curve = get_curve(intent)
    if len(curve) >= 2:
        return curve[1].emotion
    return curve[0].emotion if curve else "neutral"
