"""
MemoryBridge — unified write/read API across episodic (SQLite) and semantic (ChromaDB).

  bridge.add(text, role, session_id)  →  writes to both layers
  bridge.recall(query, n)             →  semantic similarity search
  bridge.recent(n)                    →  latest episodic turns
"""
from __future__ import annotations

import logging
from typing import Any

from .semantic_store import semantic_store

logger = logging.getLogger(__name__)


class MemoryBridge:

    def add(
        self,
        text:       str,
        role:       str = "user",
        session_id: str = "default",
        tool_name:  str | None = None,
        metadata:   dict[str, Any] | None = None,
    ) -> None:
        """Write one turn to both episodic SQLite and semantic ChromaDB."""
        # Episodic write
        try:
            from api.services.episodic_memory import episodic_memory as _em
            _em.save(session_id, role, text, tool_name)
        except Exception as exc:
            logger.debug("[MemoryBridge] episodic write error: %s", exc)

        # Semantic write
        meta = {"role": role, "session_id": session_id}
        if tool_name:
            meta["tool_name"] = tool_name
        if metadata:
            meta.update({k: str(v) for k, v in metadata.items()})
        semantic_store.remember(text, meta)

    def recall(self, query: str, n: int = 5) -> list[dict]:
        """Return top-n semantically similar memories."""
        return semantic_store.recall(query, n)

    def recent(self, n: int = 10) -> list[dict]:
        """Return n most recent episodic turns as plain dicts."""
        try:
            from api.services.episodic_memory import episodic_memory as _em
            turns = _em.recent(n)
            result = []
            for t in (turns or []):
                result.append(t if isinstance(t, dict) else {
                    "role":      getattr(t, "role",      ""),
                    "text":      getattr(t, "text",      ""),
                    "tool_name": getattr(t, "tool_name", None),
                    "timestamp": getattr(t, "timestamp", 0.0),
                })
            return result
        except Exception as exc:
            logger.debug("[MemoryBridge] recent error: %s", exc)
            return []


memory_bridge = MemoryBridge()
