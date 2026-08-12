from __future__ import annotations

"""
ProjectMemory — persist and recall Xyron-built projects across sessions.

Storage: ~/.xyron/projects.json

Schema per record:
  {
    "name":          "clothing-website",
    "path":          "/mnt/c/Users/Dell/Desktop/Xyron Projects/clothing-website",
    "stack":         "vite-react-tailwind",
    "port":          5173,
    "url":           "http://localhost:5173",
    "app_type":      "clothing-ecommerce",
    "design_brief":  "Modern minimalist…",
    "status":        "verified",   # scaffold | built | verified | failed
    "created_at":    "2026-07-03T12:00:00",
    "updated_at":    "2026-07-03T12:05:00",
  }

Log tags: [PROJECT_MEMORY_WRITE] [PROJECT_MEMORY_READ] [PROJECT_MEMORY_HIT]
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_STORE_PATH = Path.home() / ".xyron" / "projects.json"
_MAX_RECORDS = 50   # rolling window


class ProjectMemory:
    """Load, save, and look up project records."""

    # ── Internal ───────────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        if not _STORE_PATH.exists():
            return []
        try:
            return json.loads(_STORE_PATH.read_text("utf-8"))
        except Exception as exc:
            logger.warning("[PROJECT_MEMORY] load error: %s", exc)
            return []

    def _save(self, records: list[dict]) -> None:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            _STORE_PATH.write_text(json.dumps(records, indent=2), "utf-8")
            logger.debug("[PROJECT_MEMORY_WRITE] %d records", len(records))
        except Exception as exc:
            logger.error("[PROJECT_MEMORY] save error: %s", exc)

    # ── Public API ─────────────────────────────────────────────────────────────

    def save_project(self, record: dict[str, Any]) -> None:
        """Upsert *record* by name and persist to disk.

        If a record with the same ``name`` already exists it is updated;
        otherwise a new one is prepended so ``get_last()`` returns it first.
        """
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        records = self._load()

        # Upsert
        existing = next((r for r in records if r.get("name") == record.get("name")), None)
        if existing:
            existing.update(record)
            existing["updated_at"] = now_iso
        else:
            record.setdefault("created_at", now_iso)
            record["updated_at"] = now_iso
            records.insert(0, record)

        # Rolling window
        records = records[:_MAX_RECORDS]
        self._save(records)
        logger.info("[PROJECT_MEMORY_WRITE] saved project=%r", record.get("name"))

    def get_last_project(self) -> Optional[dict]:
        """Return the most recently saved project, or None."""
        records = self._load()
        if records:
            hit = records[0]
            logger.info("[PROJECT_MEMORY_HIT] last project=%r", hit.get("name"))
            return hit
        logger.debug("[PROJECT_MEMORY_READ] no projects found")
        return None

    def get_project_by_name(self, name: str) -> Optional[dict]:
        """Return the record whose ``name`` matches *name* (case-insensitive)."""
        name_lower = name.lower()
        records = self._load()
        for r in records:
            if r.get("name", "").lower() == name_lower:
                logger.info("[PROJECT_MEMORY_HIT] project=%r", r.get("name"))
                return r
        return None

    def search_projects(self, keyword: str) -> list[dict]:
        """Return records whose name or app_type contains *keyword*."""
        kw = keyword.lower()
        records = self._load()
        results = [
            r for r in records
            if kw in r.get("name", "").lower()
            or kw in r.get("app_type", "").lower()
        ]
        logger.debug("[PROJECT_MEMORY_READ] search=%r hits=%d", keyword, len(results))
        return results

    def list_projects(self) -> list[dict]:
        """Return all stored project records."""
        records = self._load()
        logger.info("[PROJECT_MEMORY_READ] listing %d projects", len(records))
        return records

    def update_status(self, name: str, status: str) -> None:
        """Update the status field of an existing project record."""
        records = self._load()
        for r in records:
            if r.get("name") == name:
                r["status"] = status
                r["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                break
        self._save(records)


# Module-level singleton
project_memory = ProjectMemory()
