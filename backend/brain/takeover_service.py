"""
takeover_service.py — singleton state for Xyron takeover mode.

Tracks whether takeover is active, when it started, who triggered it, and
the last action taken. The service is the single source of truth — the
frontend syncs via GET /api/v1/takeover/status.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TakeoverState:
    active:          bool            = False
    started_at:      Optional[float] = None   # epoch seconds
    source:          str             = ""     # "voice" | "ui" | "api"
    autonomy_level:  int             = 0      # 0=off 1-100=active
    allowed_actions: list[str]       = field(default_factory=list)
    last_action:     Optional[str]   = None


class TakeoverService:
    def __init__(self) -> None:
        self._state = TakeoverState()

    # ── public API ────────────────────────────────────────────────────────────

    def activate_takeover(self, source: str = "voice") -> TakeoverState:
        self._state = TakeoverState(
            active=True,
            started_at=time.time(),
            source=source,
            autonomy_level=100,
            allowed_actions=["open_app", "browse", "write_file", "run_script"],
            last_action=None,
        )
        logger.info("[TAKEOVER_MODE] enabled=true source=%s autonomy_level=100", source)
        logger.info("[SYSTEM_CONTROL] takeover_active=true source=%s", source)
        return self._state

    def deactivate_takeover(self) -> TakeoverState:
        prev_source = self._state.source
        self._state = TakeoverState(active=False)
        logger.info("[TAKEOVER_MODE] enabled=false previous_source=%s", prev_source)
        logger.info("[SYSTEM_CONTROL] takeover_active=false")
        return self._state

    def get_takeover_state(self) -> TakeoverState:
        return self._state

    def record_action(self, action: str) -> None:
        self._state.last_action = action


# module-level singleton
takeover_service = TakeoverService()
