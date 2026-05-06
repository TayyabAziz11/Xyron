"""
3-layer session memory manager.

Layer 1 — WorkingMemory:  volatile in-session context (last N turns), reset each session.
Layer 2 — EpisodicMemory: async wrapper around the existing episodic_memory SQLite service.
Layer 3 — SemanticMemory: long-term facts via memory_service + per-session tool frequency.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WORKING_WINDOW = 20  # max turns kept in volatile working memory


@dataclass
class Turn:
    role:      str
    text:      str
    tool_name: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_llm_dict(self) -> dict:
        return {"role": self.role, "text": self.text}


class WorkingMemory:
    """Volatile ring buffer of recent turns — cleared when the session ends."""

    def __init__(self, maxlen: int = _WORKING_WINDOW) -> None:
        self._turns: deque[Turn] = deque(maxlen=maxlen)

    def add(self, role: str, text: str, tool_name: Optional[str] = None) -> None:
        self._turns.append(Turn(role=role, text=text, tool_name=tool_name))

    def as_history(self) -> list[dict]:
        return [t.to_llm_dict() for t in self._turns]

    def last_tool(self) -> Optional[str]:
        for t in reversed(self._turns):
            if t.tool_name:
                return t.tool_name
        return None

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)


class EpisodicMemoryLayer:
    """Thin async wrapper around the existing episodic_memory SQLite service."""

    async def log(
        self,
        role:      str,
        text:      str,
        tool_name: Optional[str] = None,
        success:   bool = True,
    ) -> None:
        try:
            from api.services.episodic_memory import episodic_memory as _em
            # episodic_memory.add_turn() signature may vary — call defensively
            fn = getattr(_em, "add_turn", None) or getattr(_em, "log_turn", None)
            if fn:
                await asyncio.to_thread(fn, role, text, tool_name, success)
        except Exception as exc:
            logger.debug("[EpisodicMemory] log error: %s", exc)

    async def recent(self, n: int = 10) -> list[dict]:
        try:
            from api.services.episodic_memory import episodic_memory as _em
            fn = getattr(_em, "get_recent", None)
            if not fn:
                return []
            turns = await asyncio.to_thread(fn, n)
            return [t if isinstance(t, dict) else vars(t) for t in (turns or [])]
        except Exception:
            return []


class SemanticMemoryLayer:
    """
    Long-term user facts (via memory_service) + per-session tool frequency counter.
    The frequency counter helps the orchestrator prioritise familiar tools.
    """

    def __init__(self) -> None:
        self._freq: dict[str, int] = {}

    def record_tool(self, tool_name: str) -> None:
        self._freq[tool_name] = self._freq.get(tool_name, 0) + 1

    def top_tools(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self._freq.items(), key=lambda x: x[1], reverse=True)[:n]

    async def get_facts(self) -> dict[str, Any]:
        try:
            from api.services.memory_service import memory_service as _ms
            fn = getattr(_ms, "get_facts", None)
            return (await asyncio.to_thread(fn) if fn else {}) or {}
        except Exception:
            return {}


class MemoryManager:
    """
    Unified interface to all 3 memory layers.
    One MemoryManager is created per WebSocket session.
    """

    def __init__(self) -> None:
        self.working  = WorkingMemory()
        self.episodic = EpisodicMemoryLayer()
        self.semantic = SemanticMemoryLayer()

    # ── Convenience write methods ─────────────────────────────────────────────

    def add_user(self, text: str) -> None:
        self.working.add("user", text)

    def add_assistant(self, text: str, tool_name: Optional[str] = None) -> None:
        self.working.add("assistant", text, tool_name=tool_name)
        if tool_name:
            self.semantic.record_tool(tool_name)

    async def persist(
        self,
        role:      str,
        text:      str,
        tool_name: Optional[str] = None,
        success:   bool = True,
    ) -> None:
        """Write turn to episodic SQLite (fire-and-forget in practice)."""
        await self.episodic.log(role, text, tool_name, success)

    # ── Read ──────────────────────────────────────────────────────────────────

    def history_for_llm(self) -> list[dict]:
        """Return working memory in the format expected by response_pipeline."""
        return self.working.as_history()

    def session_summary(self) -> dict[str, Any]:
        return {
            "turns":      len(self.working),
            "top_tools":  self.semantic.top_tools(),
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def clear(self) -> None:
        self.working.clear()


def new_session_memory() -> MemoryManager:
    """Factory — call once per WebSocket session."""
    return MemoryManager()
