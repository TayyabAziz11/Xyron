"""
Proactive Assistant Service — Feature #7
Background thread that monitors time + context and queues notifications.
Consumers (SSE endpoint or WebSocket) drain the queue to speak to the user.

Monitors:
  - Upcoming calendar events (warns 10 min before)
  - Break reminders (no activity > 90 min)
  - Unsaved file detection (placeholder — needs active window tracking)
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 60      # seconds between proactive checks
_BREAK_MINUTES  = 90      # minutes before break reminder
_PRE_MEETING_MINUTES = 10 # minutes before meeting to warn


class ProactiveNotification:
    def __init__(self, message: str, category: str = "info", priority: int = 5):
        self.message  = message
        self.category = category   # "meeting", "break", "reminder", "tip"
        self.priority = priority   # 1=urgent, 10=low
        self.ts       = time.time()


class ProactiveService:

    def __init__(self) -> None:
        self._queue:       queue.Queue[ProactiveNotification] = queue.Queue()
        self._running      = False
        self._thread:      threading.Thread | None = None
        self._openai_key   = ""
        self._last_active  = time.time()   # updated on every user command
        self._warned_meetings: set[str] = set()   # event ids already warned about
        self._last_break_warn = 0.0
        self._listeners:   list[Callable[[ProactiveNotification], None]] = []

    def start(self, openai_key: str = "") -> None:
        if self._running:
            return
        self._openai_key = openai_key
        self._running    = True
        self._thread     = threading.Thread(
            target=self._loop, daemon=True, name="proactive"
        )
        self._thread.start()
        logger.info("ProactiveService started")

    def stop(self) -> None:
        self._running = False

    def mark_active(self) -> None:
        """Call on every user interaction to reset the break timer."""
        self._last_active = time.time()

    def subscribe(self, callback: Callable[[ProactiveNotification], None]) -> None:
        self._listeners.append(callback)

    def poll(self) -> Optional[ProactiveNotification]:
        """Non-blocking: return next notification or None."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _emit(self, msg: str, category: str = "info", priority: int = 5) -> None:
        n = ProactiveNotification(msg, category, priority)
        self._queue.put(n)
        for cb in self._listeners:
            try:
                cb(n)
            except Exception:
                pass

    def _loop(self) -> None:
        while self._running:
            try:
                from api.services.background_scheduler import scheduler as _sched
                if not _sched.should_run("proactive"):
                    time.sleep(_CHECK_INTERVAL)
                    continue
            except Exception:
                pass
            try:
                self._check_all()
            except Exception as exc:
                logger.debug("Proactive check error: %s", exc)
            time.sleep(_CHECK_INTERVAL)

    def _check_all(self) -> None:
        self._check_break()
        self._check_meetings()
        self._check_habit_suggestions()

    def _check_habit_suggestions(self) -> None:
        """Suggest tools the user typically uses at this hour, based on episodic memory."""
        try:
            from .episodic_memory import episodic_memory
            import time as _time

            hour = int(_time.strftime("%H"))
            patterns = episodic_memory.tools_at_hour(hour)
            if not patterns:
                return

            # Only suggest if the user has been active recently (last 5 min)
            idle_minutes = (_time.time() - self._last_active) / 60
            if idle_minutes > 5:
                return

            # Only suggest once per hour
            last_hour_key = f"habit_{hour}"
            now_ts = _time.time()
            if getattr(self, "_last_habit_ts", {}).get(last_hour_key, 0) > now_ts - 3600:
                return

            # Find the top tool not in "boring" category
            _boring = {"system_info", "system_health", "get_volume", "get_battery_status"}
            top_tool = next((t for t in patterns if t not in _boring and patterns[t] >= 3), None)
            if not top_tool:
                return

            _suggestions = {
                "read_inbox":    "You usually check your inbox around this time — want me to open it?",
                "get_summary":   "Want your daily summary? You usually check it around now.",
                "take_screenshot": "Heads up — you often take screenshots at this hour. Need one?",
                "search_web":    "You usually search the web around now. What are you looking for?",
                "open_application": "Ready to start working? You usually launch apps around this time.",
                "smart_open":    "You often open files around now. Want me to find something?",
            }
            msg = _suggestions.get(top_tool)
            if msg:
                if not hasattr(self, "_last_habit_ts"):
                    self._last_habit_ts = {}
                self._last_habit_ts[last_hour_key] = now_ts
                self._emit(msg, category="habit", priority=8)
        except Exception as exc:
            import logging as _l
            _l.getLogger(__name__).debug("Habit suggestion error: %s", exc)

    def _check_break(self) -> None:
        idle_minutes = (time.time() - self._last_active) / 60
        since_warn   = time.time() - self._last_break_warn
        if idle_minutes >= _BREAK_MINUTES and since_warn > _BREAK_MINUTES * 60:
            self._emit(
                f"Hey, you've been at it for over {int(idle_minutes)} minutes without a break. "
                "Maybe step away for a few minutes?",
                category="break",
                priority=7,
            )
            self._last_break_warn = time.time()

    def _check_meetings(self) -> None:
        """Warn 10 minutes before upcoming calendar events."""
        try:
            from .calendar_service import calendar_service
            now       = datetime.now(timezone.utc)
            events    = calendar_service.list_events(days_ahead=1, max_results=10)
            for ev in events:
                start_iso = ev.get("start", {}).get("dateTime")
                if not start_iso:
                    continue
                from datetime import datetime as dt
                start = dt.fromisoformat(start_iso.replace("Z", "+00:00"))
                minutes_until = (start - now).total_seconds() / 60
                ev_id = ev.get("id", start_iso)
                if 0 < minutes_until <= _PRE_MEETING_MINUTES and ev_id not in self._warned_meetings:
                    title = ev.get("summary", "a meeting")
                    self._emit(
                        f"Just a heads up — you have '{title}' in about {int(minutes_until)} minutes.",
                        category="meeting",
                        priority=2,
                    )
                    self._warned_meetings.add(ev_id)
        except Exception as exc:
            logger.debug("Meeting check skipped: %s", exc)


proactive_service = ProactiveService()
