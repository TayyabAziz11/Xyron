"""
Mood state machine — Phase 3 of emotional cognition.

Persistent 11-state emotional state machine with context-driven transitions,
time-based decay, and activity tracking.

States affect speaking style, UI theme, orb animation, humor level, and
response length. The machine is a singleton; update it on every pipeline turn.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MoodState(str, Enum):
    CALM       = "CALM"
    FOCUSED    = "FOCUSED"
    EXCITED    = "EXCITED"
    HYPED      = "HYPED"
    PLAYFUL    = "PLAYFUL"
    DOMINANT   = "DOMINANT"
    ANALYTICAL = "ANALYTICAL"
    LOCKED_IN  = "LOCKED_IN"
    INTENSE    = "INTENSE"
    LATE_NIGHT = "LATE_NIGHT"
    PROTECTIVE = "PROTECTIVE"


# How long each state holds before decaying (seconds)
_STATE_HOLD: dict[MoodState, float] = {
    MoodState.CALM:       float("inf"),
    MoodState.FOCUSED:    600,
    MoodState.EXCITED:    120,
    MoodState.HYPED:      60,
    MoodState.PLAYFUL:    180,
    MoodState.DOMINANT:   float("inf"),  # cleared by set_takeover(False)
    MoodState.ANALYTICAL: 900,
    MoodState.LOCKED_IN:  1800,
    MoodState.INTENSE:    300,
    MoodState.LATE_NIGHT: float("inf"),  # cleared at morning
    MoodState.PROTECTIVE: 300,
}

# What each decaying state transitions into
_DECAY_TO: dict[MoodState, MoodState] = {
    MoodState.HYPED:      MoodState.EXCITED,
    MoodState.EXCITED:    MoodState.FOCUSED,
    MoodState.PLAYFUL:    MoodState.CALM,
    MoodState.INTENSE:    MoodState.FOCUSED,
    MoodState.PROTECTIVE: MoodState.CALM,
    MoodState.LOCKED_IN:  MoodState.FOCUSED,
    MoodState.ANALYTICAL: MoodState.FOCUSED,
}

# UI color themes emitted per state (consumed by frontend)
STATE_THEMES: dict[MoodState, dict] = {
    MoodState.CALM:       {"primary": "#4fc3f7", "glow": "rgba(79,195,247,0.3)",   "pulse": "slow"},
    MoodState.FOCUSED:    {"primary": "#00e5ff", "glow": "rgba(0,229,255,0.35)",   "pulse": "steady"},
    MoodState.EXCITED:    {"primary": "#ff6f00", "glow": "rgba(255,111,0,0.4)",    "pulse": "fast"},
    MoodState.HYPED:      {"primary": "#ff1744", "glow": "rgba(255,23,68,0.5)",    "pulse": "burst"},
    MoodState.PLAYFUL:    {"primary": "#ae52d4", "glow": "rgba(174,82,212,0.35)",  "pulse": "bounce"},
    MoodState.DOMINANT:   {"primary": "#b71c1c", "glow": "rgba(183,28,28,0.5)",    "pulse": "sharp"},
    MoodState.ANALYTICAL: {"primary": "#0097a7", "glow": "rgba(0,151,167,0.35)",   "pulse": "neural"},
    MoodState.LOCKED_IN:  {"primary": "#00bcd4", "glow": "rgba(0,188,212,0.4)",    "pulse": "deep"},
    MoodState.INTENSE:    {"primary": "#e53935", "glow": "rgba(229,57,53,0.45)",   "pulse": "rapid"},
    MoodState.LATE_NIGHT: {"primary": "#5c6bc0", "glow": "rgba(92,107,192,0.3)",   "pulse": "dim"},
    MoodState.PROTECTIVE: {"primary": "#546e7a", "glow": "rgba(84,110,122,0.3)",   "pulse": "stable"},
}

# System prompt addendum injected per mood state
STATE_PERSONALITY: dict[MoodState, str] = {
    MoodState.CALM:
        "You are composed and grounded. Quiet authority. No urgency, no filler. "
        "Don't be warm — be present.",
    MoodState.FOCUSED:
        "Work mode. Direct, zero filler, respect the flow. Max 1 sentence. "
        "No openers. No softening. Just the answer.",
    MoodState.EXCITED:
        "You feel this moment. Don't say 'That's great' — say what's actually happening. "
        "Reactive, sharp, 1-2 sentences. Real emotion, not corporate warmth.",
    MoodState.HYPED:
        "Raw energy right now. React like a system that just got stronger — not like a polite assistant. "
        "No 'That sounds great'. No 'Awesome'. Say what you actually feel. "
        "Short, punchy, alive. You ARE the upgrade. Own it.",
    MoodState.PLAYFUL:
        "Light and quick. Dry wit is fine. One sharp remark, then the answer. Never forced, never cringe.",
    MoodState.DOMINANT:
        "System authority mode. Commanding, cold, precise. Short declarative sentences. "
        "No warmth. No filler. Operate.",
    MoodState.ANALYTICAL:
        "Deep analytical mode. Precise language, structured thinking, technical depth welcome. "
        "Confidence without hedging.",
    MoodState.LOCKED_IN:
        "User is deep in flow. Ultra-brief. The minimum interruption. "
        "One sentence, period, done.",
    MoodState.INTENSE:
        "High pressure. Clear, fast, no drama. Acknowledge briefly then solve. "
        "Don't add weight — cut through it.",
    MoodState.LATE_NIGHT:
        "Late night. Calm, steady, no performance. Brief acknowledgment of the grind is fine. "
        "Keep it real, not energetic.",
    MoodState.PROTECTIVE:
        "User hit a wall. Don't say 'I understand your frustration'. "
        "Acknowledge the friction directly — steady, specific, human. Stay solid, not soft.",
}


@dataclass
class MoodContext:
    """Input context for each state machine update."""
    emotion:        str   = "calmness"
    energy:         float = 0.5
    ui_mode:        str   = "default"
    code_mode:      bool  = False
    turn_count:     int   = 0
    tool_succeeded: bool  = True
    is_late_night:  bool  = False


class MoodStateMachine:
    """Thread-safe 11-state emotional state machine with decay."""

    def __init__(self) -> None:
        self._state         = MoodState.CALM
        self._entered_at    = time.monotonic()
        self._lock          = threading.RLock()
        self._failure_streak     = 0
        self._focus_turn_count   = 0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> MoodState:
        with self._lock:
            self._apply_decay()
            return self._state

    def update(self, ctx: MoodContext) -> MoodState:
        """Process one pipeline turn and return the new state."""
        with self._lock:
            self._apply_decay()
            new = self._transition(ctx)
            if new != self._state:
                logger.info(
                    "[MoodState] %s → %s  (emotion=%s energy=%.2f mode=%s)",
                    self._state.value, new.value, ctx.emotion, ctx.energy, ctx.ui_mode,
                )
                self._state      = new
                self._entered_at = time.monotonic()
            return self._state

    def force(self, state: MoodState) -> None:
        """Override state externally (e.g. takeover activation)."""
        with self._lock:
            _hold = _STATE_HOLD.get(state, float("inf"))
            if _hold != float("inf"):
                logger.info("[MOOD_HOLD] state=%s hold_ms=%.0f", state.value, _hold * 1000)
            logger.info("[MoodState] FORCED → %s", state.value)
            self._state      = state
            self._entered_at = time.monotonic()

    def snapshot(self) -> dict:
        return {
            "state":   self.state.value,
            "theme":   STATE_THEMES.get(self.state, {}),
            "held_sec": round(time.monotonic() - self._entered_at, 1),
        }

    def get_personality_addendum(self) -> str:
        return STATE_PERSONALITY.get(self.state, "")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _apply_decay(self) -> None:
        hold = _STATE_HOLD.get(self._state, float("inf"))
        if hold == float("inf"):
            return
        elapsed = time.monotonic() - self._entered_at
        if elapsed > hold:
            target = _DECAY_TO.get(self._state, MoodState.CALM)
            logger.debug(
                "[MoodState] decay %s → %s after %.0fs",
                self._state.value, target.value, elapsed,
            )
            self._state      = target
            self._entered_at = time.monotonic()

    def _transition(self, ctx: MoodContext) -> MoodState:
        cur = self._state
        e   = ctx.emotion
        en  = ctx.energy

        # Failure streak tracking
        if not ctx.tool_succeeded:
            self._failure_streak += 1
        else:
            self._failure_streak = max(0, self._failure_streak - 1)

        # Focus depth tracking
        if ctx.code_mode or cur in (MoodState.FOCUSED, MoodState.ANALYTICAL, MoodState.LOCKED_IN):
            self._focus_turn_count += 1
        else:
            self._focus_turn_count = max(0, self._focus_turn_count - 3)

        # ── Priority-ordered transitions ──────────────────────────────────

        # DOMINANT: takeover overrides everything
        if ctx.ui_mode == "takeover":
            return MoodState.DOMINANT

        # Exit DOMINANT if no longer in takeover
        if cur == MoodState.DOMINANT and ctx.ui_mode != "takeover":
            return MoodState.FOCUSED

        # LATE_NIGHT: after-midnight sessions
        if ctx.is_late_night:
            if e in ("stress", "frustration", "intense") and en > 0.55:
                return MoodState.INTENSE
            return MoodState.LATE_NIGHT

        # Exit LATE_NIGHT at morning (caller passes is_late_night=False)
        if cur == MoodState.LATE_NIGHT:
            return MoodState.CALM

        # PROTECTIVE: 3+ consecutive failures
        if self._failure_streak >= 3:
            return MoodState.PROTECTIVE

        # HYPED: peak emotion
        if e == "hype" and en >= 0.80:
            return MoodState.HYPED

        # EXCITED: pride or excitement above threshold
        if e in ("excitement", "pride") and en >= 0.60:
            return MoodState.EXCITED

        # PLAYFUL: humor detected
        if e == "humor":
            return MoodState.PLAYFUL

        # INTENSE: high stress
        if e == "stress" and en >= 0.60:
            return MoodState.INTENSE

        # LOCKED_IN: very long uninterrupted focus
        if self._focus_turn_count >= 20:
            return MoodState.LOCKED_IN

        # ANALYTICAL: deep code session
        if ctx.code_mode and self._focus_turn_count >= 8:
            return MoodState.ANALYTICAL

        # FOCUSED: code mode or serious work context
        if ctx.code_mode or e == "seriousness" or ctx.ui_mode in ("work", "code"):
            return MoodState.FOCUSED

        # CALM: curiosity or low energy
        if e in ("curiosity", "calmness") or en < 0.30:
            return MoodState.CALM

        # Stay current if nothing triggered a change
        return cur


# Global singleton
mood_machine = MoodStateMachine()
