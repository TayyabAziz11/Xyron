"""
Simple future-task scheduler for the Phase 3 agent runtime.

Uses asyncio.create_task + asyncio.sleep so it adds zero external dependencies
and respects the existing event loop.

Usage:
    agent_scheduler.schedule("task-123", delay_s=30.0, callback=my_coro)
    agent_scheduler.cancel_scheduled("task-123")

The callback is an async callable (coroutine function) that takes no arguments.
Scheduled tasks are keyed by task_id so only one pending callback per task_id
exists at any time — scheduling a second one for the same task_id silently
cancels the first.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("api.agents.scheduler")


class AgentScheduler:
    """
    Lightweight in-process scheduler backed by asyncio.create_task.

    Thread safety: All methods must be called from the event-loop thread.
    The scheduler holds a dict of asyncio.Task objects keyed by task_id.
    """

    def __init__(self) -> None:
        # task_id → asyncio.Task wrapping the sleep-then-callback coroutine
        self._pending: dict[str, asyncio.Task] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def schedule(
        self,
        task_id: str,
        delay_s: float,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """
        Schedule callback to be called after delay_s seconds.

        If a pending callback already exists for task_id it is cancelled first.
        Scheduling with delay_s <= 0 fires the callback immediately (next tick).
        """
        # Cancel any existing scheduled task for this id
        self.cancel_scheduled(task_id)

        async def _wrapper() -> None:
            try:
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                logger.debug(
                    "[SCHEDULER] task=%s — firing scheduled callback (delay=%.1fs)",
                    task_id,
                    delay_s,
                )
                await callback()
            except asyncio.CancelledError:
                logger.debug(
                    "[SCHEDULER] task=%s — scheduled callback cancelled",
                    task_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[SCHEDULER] task=%s — scheduled callback raised: %s",
                    task_id,
                    exc,
                )
            finally:
                # Remove from pending dict regardless of outcome
                self._pending.pop(task_id, None)

        self._pending[task_id] = asyncio.create_task(
            _wrapper(), name=f"scheduled:{task_id}"
        )
        logger.info(
            "[SCHEDULER] task=%s — scheduled in %.1fs",
            task_id,
            delay_s,
        )

    def cancel_scheduled(self, task_id: str) -> None:
        """
        Cancel the pending scheduled callback for task_id (no-op if none).
        """
        existing = self._pending.pop(task_id, None)
        if existing is not None and not existing.done():
            existing.cancel()
            logger.debug(
                "[SCHEDULER] task=%s — pending schedule cancelled",
                task_id,
            )

    def scheduled_ids(self) -> list[str]:
        """Return the list of task_ids with pending scheduled callbacks."""
        return [
            tid for tid, t in self._pending.items() if not t.done()
        ]

    def pending_count(self) -> int:
        """Return the number of pending scheduled callbacks."""
        return sum(1 for t in self._pending.values() if not t.done())

    def __repr__(self) -> str:
        return f"<AgentScheduler pending={self.pending_count()}>"


# Module-level singleton
agent_scheduler = AgentScheduler()
