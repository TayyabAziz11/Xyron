"""
perception_engine.py — Phase 2: top-level Perception Engine orchestrator.

Architecture — read this before extending anything under perception/:

    Perception Engine
    ├── Browser Perception    (browser_perception.py)  — CDP/Playwright
    ├── Desktop Perception    (desktop_perception.py)  — UI Automation via PS bridge
    ├── Vision Perception     (vision_perception.py)   — LAST resort, on-demand only
    ├── Selection Tracker     (selection_tracker.py)   — aggregates the above + Explorer/clipboard
    ├── Multi Monitor Manager (multi_monitor_manager.py)
    ├── Event Dispatcher      (event_dispatcher.py)    — the observation loop
    └── World State Publisher (every module above publishes directly into
                                world_state.py — there is no separate
                                "publisher" class; publishing is each
                                module's own responsibility, kept thin)

Hard rule, repeated from the Phase 2 brief because it's the whole point of
this module: Perception observes, World State stores, Reasoning consumes.
Nothing under api/services/perception/ calls into agents/, command routing,
or the LLM — it only reads OS/browser/UI state and calls world_state.publish().
If you're tempted to add a decision ("this looks like an error, so tell the
user...") here, that belongs in a reasoning layer that *consumes*
world_state.get_context()["current_visible_error"], not in perception code.

This is why the design supports future features (shopping assistant,
coding companion, screen tutoring, proactive suggestions, meeting
assistant, browser/desktop automation) without architectural change: they
all consume the same world_state.get_context() snapshot; none of them need
their own perception plumbing, and none of this module's code needs to
know they exist.

Logs: [PERCEPTION_ENGINE_START] [PERCEPTION_ENGINE_STOP] [PERCEPTION_VISION_REQUEST]
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .event_dispatcher import PerceptionEventDispatcher

logger = logging.getLogger(__name__)


class PerceptionEngine:

    def __init__(self) -> None:
        self._dispatcher = PerceptionEventDispatcher()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Schedule the observation loop on the current asyncio event loop.
        Must be called from within a running event loop (e.g. FastAPI's
        startup handler) — not a plain background thread, see
        event_dispatcher.py's docstring for why."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._dispatcher.run_forever())
        logger.info("[PERCEPTION_ENGINE_START]")

    def stop(self) -> None:
        self._dispatcher.stop()
        logger.info("[PERCEPTION_ENGINE_STOP]")

    @property
    def is_running(self) -> bool:
        return self._dispatcher.is_running

    async def refresh_now(self) -> dict:
        """
        On-demand full refresh — for "what am I looking at?" style requests
        where the caller needs current data right now rather than whatever
        the last background tick happened to publish.
        """
        return await self._dispatcher.tick()

    async def request_vision(self, reason: str, openai_key: str) -> Optional[dict]:
        """
        Explicit, on-demand vision fallback. Never called automatically by
        the background loop — only when a caller has already checked
        world_state.get_context() and found Browser/Desktop Perception
        didn't answer the question (e.g. no browser connected AND no
        recognized document/app). Throttled internally by vision_perception.
        """
        from . import vision_perception, multi_monitor_manager
        from api.services.world_state import world_state

        monitors = world_state.get("monitors") or []
        monitor_index = next((m["index"] for m in monitors if m.get("has_foreground_window")), None)

        result = await asyncio.to_thread(vision_perception.maybe_capture, reason, openai_key, monitor_index)
        if result:
            world_state.set_focused_object(
                {"type": "vision", "value": result["description"]}, source="vision_perception"
            )
            logger.info("[PERCEPTION_VISION_REQUEST] reason=%s", reason)
        return result


perception_engine = PerceptionEngine()
