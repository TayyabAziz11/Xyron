"""
Cognitive state engine — shared singleton representing Xyron's mental state.

Every router and service reads from or writes to `cognitive_state`.
Thread-safe: all mutations go through update() which holds the RLock.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AttentionLevel(str, Enum):
    IDLE       = "IDLE"
    LISTENING  = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING   = "SPEAKING"
    FOCUSED    = "FOCUSED"


class MoodBias(str, Enum):
    NEUTRAL  = "neutral"
    ALERT    = "alert"
    CALM     = "calm"
    STRESSED = "stressed"


@dataclass
class CognitiveState:
    attention:        AttentionLevel = AttentionLevel.IDLE
    mood_bias:        MoodBias       = MoodBias.NEUTRAL
    last_user_emotion: str           = "neutral"
    emotion_intensity: float         = 0.5
    active_goal:      Optional[str]  = None
    current_task:     Optional[str]  = None
    context_summary:  str            = ""
    active_ui_mode:   str            = "default"
    turn_count:       int            = 0
    last_updated:     float          = field(default_factory=time.time)
    _lock:            threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False,
    )

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if not hasattr(self, key):
                    raise AttributeError(f"CognitiveState has no field {key!r}")
                setattr(self, key, value)
            self.last_updated = time.time()

    def snapshot(self) -> dict:
        """Return a copy of all public fields — safe to serialise."""
        with self._lock:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


# Global singleton — import this everywhere.
cognitive_state = CognitiveState()
