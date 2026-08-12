"""
ResourceLockManager — asyncio locks for shared resources.

Prevents conflicting parallel agent actions:
- Two agents writing to the same file
- Two browser sessions on the same page
- Concurrent terminal operations

Usage:
    async with resource_lock_manager.lock("filesystem"):
        # safe to write files here
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator

logger = logging.getLogger("api.agents.coordinator.locks")

_RESOURCE_NAMES = [
    "browser",
    "filesystem",
    "terminal",
    "vscode",
    "audio",
    "downloads",
    "store",
]


class ResourceLockManager:
    """Manager for named asyncio locks."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        # Pre-create locks for known resources
        for name in _RESOURCE_NAMES:
            self._locks[name] = asyncio.Lock()

    def _get_lock(self, resource: str) -> asyncio.Lock:
        if resource not in self._locks:
            self._locks[resource] = asyncio.Lock()
        return self._locks[resource]

    @contextlib.asynccontextmanager
    async def lock(self, resource: str) -> AsyncIterator[None]:
        """
        Async context manager for acquiring a resource lock.

        Logs [RESOURCE_LOCK_ACQUIRED] on entry, [RESOURCE_LOCK_RELEASED] on exit.
        [RESOURCE_LOCK_WAIT] if lock is contended.
        """
        lk = self._get_lock(resource)
        if lk.locked():
            logger.info("[RESOURCE_LOCK_WAIT] resource=%s — waiting for lock", resource)
        async with lk:
            logger.info("[RESOURCE_LOCK_ACQUIRED] resource=%s", resource)
            try:
                yield
            finally:
                logger.info("[RESOURCE_LOCK_RELEASED] resource=%s", resource)

    def is_locked(self, resource: str) -> bool:
        """Check if a resource is currently locked."""
        lk = self._locks.get(resource)
        return lk.locked() if lk else False

    def locked_resources(self) -> list[str]:
        """Return list of currently locked resource names."""
        return [name for name, lk in self._locks.items() if lk.locked()]


resource_lock_manager = ResourceLockManager()
