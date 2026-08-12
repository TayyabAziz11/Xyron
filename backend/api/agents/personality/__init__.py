from __future__ import annotations

"""
Phase 3.4 — Personality Engine package.

Public surface:
    from api.agents.personality import personality_engine, PersonalityEngine, PersonalityMode
    from api.agents.personality import run
"""

from api.agents.personality.personality_engine import (
    PersonalityEngine,
    PersonalityMode,
    personality_engine,
    run,
)

__all__ = [
    "PersonalityEngine",
    "PersonalityMode",
    "personality_engine",
    "run",
]
