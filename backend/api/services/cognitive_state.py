"""Shared cognitive state singleton — live session data for the voice AI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CognitiveState:
    last_user_emotion: Optional[str] = None
    active_ui_mode: str = "default"

    def update(self, **kwargs: Any) -> None:
        """Update one or more state fields by name."""
        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise AttributeError(f"CognitiveState has no field {key!r}")
            setattr(self, key, value)


# Module-level singleton shared across all routers.
cognitive_state = CognitiveState()
