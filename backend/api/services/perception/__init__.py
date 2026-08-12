"""
Perception Engine — Phase 2.

Converts current desktop/browser/UI state into structured World State
updates. Perception observes; it never reasons and never calls into
reasoning/decision code directly — see perception_engine.py's module
docstring for the full architecture rationale.
"""
from __future__ import annotations

from .perception_engine import perception_engine

__all__ = ["perception_engine"]
