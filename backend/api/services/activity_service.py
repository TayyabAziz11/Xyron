"""
Activity Service — reads JSON audit log files from Logs/.

Log files are newline-delimited JSON, one file per day (YYYY-MM-DD.json).
Each line is a JSON object conforming to the AuditLogger schema.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from ..config import settings
from ..schemas.activity import ActivityEvent


def _log_entry_to_event(entry: dict, source_file: str) -> ActivityEvent:
    """Convert a raw audit log entry dict to an ActivityEvent."""
    # Derive a stable ID from timestamp + action_type
    raw_id = f"{entry.get('timestamp', '')}-{entry.get('action_type', '')}-{source_file}"
    event_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]

    # Map result field to status
    result = entry.get("result", "success")
    status_map = {"success": "success", "failure": "error", "partial": "warning"}
    status = status_map.get(result, "info")

    action_type = entry.get("action_type", "unknown")
    actor = entry.get("actor", "system")
    target = entry.get("target")

    description = action_type.replace("_", " ").title()
    if target:
        description += f" → {target}"

    return ActivityEvent(
        id=event_id,
        event_type=action_type,
        description=description,
        status=status,
        timestamp=entry.get("timestamp", ""),
        source=source_file,
        actor=actor,
        target=target,
        metadata={
            k: v for k, v in entry.items()
            if k not in ("timestamp", "action_type", "actor", "target", "result")
        },
    )


class ActivityService:
    """Reads activity events from Logs/YYYY-MM-DD.json files."""

    def list_events(self, limit: int = 50, days: int = 7) -> List[ActivityEvent]:
        """Return activity events from the last N days, newest first."""
        if not settings.logs_dir.exists():
            return []

        events: List[ActivityEvent] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        for log_file in sorted(settings.logs_dir.glob("*.json"), reverse=True):
            # Parse date from filename
            try:
                file_date = datetime.strptime(log_file.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if file_date < cutoff:
                break  # Files are sorted, so we can stop

            entries = self._read_log_file(log_file)
            for entry in entries:
                events.append(_log_entry_to_event(entry, log_file.name))

            if len(events) >= limit:
                break

        return events[:limit]

    def get_event(self, event_id: str) -> Optional[ActivityEvent]:
        """Find a single event by its derived ID."""
        if not settings.logs_dir.exists():
            return None

        for log_file in sorted(settings.logs_dir.glob("*.json"), reverse=True):
            entries = self._read_log_file(log_file)
            for entry in entries:
                event = _log_entry_to_event(entry, log_file.name)
                if event.id == event_id:
                    return event
        return None

    def _read_log_file(self, path: Path) -> List[dict]:
        """Read newline-delimited JSON log file. Returns empty list on error."""
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return entries


# Module-level singleton
activity_service = ActivityService()
