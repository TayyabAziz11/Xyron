"""
Per-task working memory for the Phase 3 agent runtime.

Provides a simple, fast, in-process key-value store keyed by task_id plus a
conversation-history list so agents can maintain context across steps without
touching the long-term episodic or semantic memory layers.

All data is lost when the process restarts — this is intentional; long-term
persistence is delegated to episodic_memory.py and memory_service.py.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("api.agents.memory")


class AgentMemory:
    """
    Thread-safe in-memory store for per-task agent state.

    Storage layout (per task_id):
      _store[task_id]["kv"]      → dict[str, Any]   — arbitrary key-value pairs
      _store[task_id]["history"] → list[dict]        — conversation turns

    All public methods are O(1) or O(n) where n is bounded by the number of
    active tasks or history entries, neither of which grows unboundedly in
    normal usage.
    """

    _MAX_HISTORY: int = 50  # maximum conversation turns kept per task

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _ensure_task(self, task_id: str) -> None:
        """Create the per-task slot if it does not exist yet (must hold lock)."""
        if task_id not in self._store:
            self._store[task_id] = {"kv": {}, "history": []}

    # ── Key-value API ──────────────────────────────────────────────────────────

    def set(self, task_id: str, key: str, value: Any) -> None:
        """Store an arbitrary value under key for the given task."""
        with self._lock:
            self._ensure_task(task_id)
            self._store[task_id]["kv"][key] = value
            logger.debug("agent_memory.set task=%s key=%r", task_id, key)

    def get(self, task_id: str, key: str, default: Any = None) -> Any:
        """Retrieve a value by key; returns default if not found."""
        with self._lock:
            slot = self._store.get(task_id)
            if slot is None:
                return default
            return slot["kv"].get(key, default)

    def get_all(self, task_id: str) -> dict[str, Any]:
        """Return a shallow copy of all key-value pairs for the task."""
        with self._lock:
            slot = self._store.get(task_id)
            if slot is None:
                return {}
            return dict(slot["kv"])

    def delete(self, task_id: str, key: str) -> None:
        """Remove a single key from the task's store (no-op if absent)."""
        with self._lock:
            slot = self._store.get(task_id)
            if slot is not None:
                slot["kv"].pop(key, None)

    # ── Conversation history API ───────────────────────────────────────────────

    def append_history(self, task_id: str, role: str, content: str) -> None:
        """
        Append a conversation turn to the task's history.

        role: "user" | "assistant" | "system" | "tool"
        Older turns are dropped when _MAX_HISTORY is exceeded.
        """
        with self._lock:
            self._ensure_task(task_id)
            history: list[dict] = self._store[task_id]["history"]
            history.append({"role": role, "content": content})
            # Trim from the front, keeping the most recent turns
            if len(history) > self._MAX_HISTORY:
                del history[: len(history) - self._MAX_HISTORY]

    def get_history(self, task_id: str) -> list[dict[str, str]]:
        """Return a copy of the conversation history for the task."""
        with self._lock:
            slot = self._store.get(task_id)
            if slot is None:
                return []
            return list(slot["history"])

    def clear_history(self, task_id: str) -> None:
        """Clear only the conversation history for the task."""
        with self._lock:
            slot = self._store.get(task_id)
            if slot is not None:
                slot["history"].clear()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def clear(self, task_id: str) -> None:
        """Remove all data for the given task (call when task is terminal)."""
        with self._lock:
            self._store.pop(task_id, None)
            logger.debug("agent_memory.clear task=%s", task_id)

    def active_task_ids(self) -> list[str]:
        """Return the list of task IDs currently in memory."""
        with self._lock:
            return list(self._store.keys())

    def __repr__(self) -> str:
        with self._lock:
            tasks = list(self._store.keys())
        return f"<AgentMemory tasks={tasks}>"


# Module-level singleton shared across the runtime
agent_memory = AgentMemory()
