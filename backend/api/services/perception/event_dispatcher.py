"""
event_dispatcher.py — Perception Engine: the unified observation loop.

Honest scope note on "event-driven, no polling": WSL2 Linux Python has no
accessible hook into native Windows events (SetWinEventHook, clipboard
listener, raw input idle detection all require a native Windows message
loop, which would mean standing up a persistent Windows-side listener
process talking back over a pipe — a much bigger, separate undertaking).
What's implemented instead is *change-detection on a short interval*: every
tick is cheap (window_context's own 2s cache + a handful of ~30ms warm
PowerShell/CDP reads), and every write goes through world_state.publish(),
which is diff-only — so nothing downstream is ever notified unless
something actually changed. That satisfies "publish only changed state"
and "avoid unnecessary CPU/GPU work" even though the underlying detection
mechanism is short-interval polling rather than true OS interrupts. Vision
(the genuinely expensive tier) is excluded from this loop entirely — it is
only ever invoked on explicit request (see perception_engine.request_vision).

This loop runs as an asyncio task on the app's main event loop (not a
background thread) because browser_perception awaits Playwright Page
methods, and Playwright's async objects are bound to the loop they were
created on — mixing a separate thread's event loop with the one
browser_workspace's Page lives on would break CDP calls.

Cadence: fast tier (window/browser/desktop/selection) every ~2.5s; monitor
topology every ~60s (displays essentially never change).

Logs: [PERCEPTION_TICK_MS] [PERCEPTION_TICK_FAIL]
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_CYCLE_SECONDS = 2.5
_MONITOR_INTERVAL_SECONDS = 60.0


class PerceptionEventDispatcher:

    def __init__(self, cycle_seconds: float = _CYCLE_SECONDS,
                 monitor_interval_seconds: float = _MONITOR_INTERVAL_SECONDS) -> None:
        self._cycle_seconds = cycle_seconds
        self._monitor_interval = monitor_interval_seconds
        self._stop_event = asyncio.Event()
        self._last_monitor_refresh = 0.0
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        logger.info("[PERCEPTION] event dispatcher loop started (cycle=%.1fs)", self._cycle_seconds)
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            try:
                await self.tick()
            except Exception:
                logger.exception("[PERCEPTION_TICK_FAIL]")
            logger.debug("[PERCEPTION_TICK_MS] %.1f", (time.monotonic() - t0) * 1000)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._cycle_seconds)
            except asyncio.TimeoutError:
                pass
        self._running = False

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._running

    async def tick(self) -> dict:
        """One observation cycle. Returns the combined snapshot (useful for tests/on-demand refresh)."""
        from api.services.world_state import world_state
        from . import browser_perception, desktop_perception, selection_tracker, multi_monitor_manager

        await asyncio.to_thread(world_state.refresh_sensors)
        window = world_state.get("current_foreground_window")

        browser_snapshot = await browser_perception.refresh()
        self._publish_browser(world_state, browser_snapshot)

        desktop_snapshot = await asyncio.to_thread(desktop_perception.refresh, window)
        self._publish_desktop(world_state, desktop_snapshot)

        selection = await asyncio.to_thread(
            selection_tracker.refresh, browser_snapshot, desktop_snapshot, window
        )
        world_state.publish("current_selection", selection, source="selection_tracker")

        now = time.monotonic()
        if now - self._last_monitor_refresh > self._monitor_interval:
            self._last_monitor_refresh = now
            monitors = await asyncio.to_thread(multi_monitor_manager.get_monitors)
            world_state.publish("monitors", [m.to_dict() for m in monitors], source="multi_monitor_manager")

        return {"browser": browser_snapshot, "desktop": desktop_snapshot, "selection": selection}

    def _publish_browser(self, world_state, snapshot: Optional[dict]) -> None:
        if not snapshot:
            world_state.publish("current_browser", None, source="browser_perception")
            world_state.publish("current_url", None, source="browser_perception")
            world_state.publish("current_tab", None, source="browser_perception")
            world_state.publish("current_repository", None, source="browser_perception")
            return

        world_state.publish("current_browser", {
            "url": snapshot["url"], "title": snapshot["title"],
            "tab_count": snapshot["tab_count"], "page_type": snapshot["page_type"],
        }, source="browser_perception")
        world_state.publish("current_url", snapshot["url"], source="browser_perception")
        world_state.publish("current_tab", snapshot["title"], source="browser_perception")

        if snapshot.get("product"):
            world_state.publish("current_product", snapshot["product"], source="browser_perception")
        if snapshot.get("visible_error"):
            world_state.publish("current_visible_error", snapshot["visible_error"], source="browser_perception")
        # Explicitly clear (not just "don't publish") when the current page
        # isn't a GitHub repository — otherwise a repo viewed earlier would
        # keep answering "what's on my screen?" after the user navigated
        # away to an unrelated site.
        world_state.publish("current_repository", snapshot.get("repository"), source="browser_perception")

    def _publish_desktop(self, world_state, snapshot: dict) -> None:
        # Only publish when desktop perception actually found something —
        # it doesn't cover every app (see _DOCUMENT_TITLE_APPS), and a blank
        # result here must not clobber a more specific publisher (e.g.
        # smart_open's current_document/current_file after opening a file).
        if snapshot.get("document"):
            world_state.publish("current_document", snapshot["document"]["name"], source="desktop_perception")
        if snapshot.get("task"):
            world_state.publish("current_task", snapshot["task"], source="desktop_perception")
