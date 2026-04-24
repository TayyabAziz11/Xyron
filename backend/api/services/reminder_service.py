"""
Reminder Service — voice-set proactive reminders.

The user says "remind me to check emails in 30 minutes" → a background thread
fires a notification after the delay → frontend polls /api/v1/reminders/fired
→ the assistant speaks the reminder aloud.

All timers are in-memory (survive as long as the process runs).
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class Reminder:
    __slots__ = ("id", "message", "fire_at", "fired", "created_at")

    def __init__(self, message: str, fire_at: datetime) -> None:
        self.id         = str(uuid.uuid4())
        self.message    = message
        self.fire_at    = fire_at
        self.fired      = False
        self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "message":    self.message,
            "fire_at":    self.fire_at.isoformat(),
            "fired":      self.fired,
            "created_at": self.created_at.isoformat(),
        }


# ── Natural language duration parser ─────────────────────────────────────────

_DURATION_PATTERNS = [
    (re.compile(r'(\d+)\s*(?:hour|hr)s?', re.IGNORECASE),    3600),
    (re.compile(r'(\d+)\s*(?:minute|min)s?', re.IGNORECASE), 60),
    (re.compile(r'(\d+)\s*(?:second|sec)s?', re.IGNORECASE), 1),
]

def parse_duration_seconds(text: str) -> Optional[int]:
    """Return total seconds from a natural language duration string, or None."""
    total = 0
    matched = False
    for pattern, multiplier in _DURATION_PATTERNS:
        m = pattern.search(text)
        if m:
            total += int(m.group(1)) * multiplier
            matched = True
    return total if matched else None


def parse_reminder_text(text: str) -> tuple[str, Optional[int]]:
    """
    Parse "remind me to X in Y minutes" → (message, seconds).
    Returns (message, None) if duration cannot be parsed.
    """
    text = text.strip()

    # Extract duration
    seconds = parse_duration_seconds(text)

    # Extract message (what to remind about)
    # "remind me to check emails in 30 minutes" → "check emails"
    # "remind me about the meeting in 1 hour" → "about the meeting"
    msg_match = re.search(
        r'\bremind\s+me\s+(?:to\s+|about\s+|that\s+)?(.+?)(?:\s+in\s+\d+|\s+after\s+\d+|$)',
        text, re.IGNORECASE,
    )
    if msg_match:
        message = msg_match.group(1).strip().rstrip(".,!?")
    else:
        message = text

    # Fallback: if message is empty, use full text
    if not message or len(message) < 2:
        message = text

    return message, seconds


# ── Reminder Service ──────────────────────────────────────────────────────────

class ReminderService:
    _CHECK_INTERVAL = 1.0   # check every second

    def __init__(self) -> None:
        self._lock     = threading.Lock()
        self._active:  dict[str, Reminder] = {}   # id → Reminder (not yet fired)
        self._fired:   list[Reminder]      = []   # fired, waiting to be picked up
        self._thread   = threading.Thread(target=self._loop, daemon=True, name="ReminderLoop")
        self._thread.start()

    def add(self, message: str, seconds: int) -> Reminder:
        fire_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        r = Reminder(message=message, fire_at=fire_at)
        with self._lock:
            self._active[r.id] = r
        logger.info("Reminder set: '%s' in %ds (at %s)", message, seconds, fire_at.isoformat())
        return r

    def cancel(self, reminder_id: str) -> bool:
        with self._lock:
            if reminder_id in self._active:
                del self._active[reminder_id]
                return True
        return False

    def list_active(self) -> list[dict]:
        with self._lock:
            return [r.to_dict() for r in self._active.values()]

    def pop_fired(self) -> list[dict]:
        """Return and clear all reminders that have fired since last poll."""
        with self._lock:
            result = [r.to_dict() for r in self._fired]
            self._fired.clear()
        return result

    def _loop(self) -> None:
        import time
        while True:
            try:
                now = datetime.now(timezone.utc)
                with self._lock:
                    due = [r for r in self._active.values() if r.fire_at <= now]
                for r in due:
                    r.fired = True
                    with self._lock:
                        self._active.pop(r.id, None)
                        self._fired.append(r)
                    logger.info("Reminder fired: '%s'", r.message)
            except Exception as exc:
                logger.warning("Reminder loop error: %s", exc)
            time.sleep(self._CHECK_INTERVAL)


reminder_service = ReminderService()
