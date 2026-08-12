"""
main_loop.py — captures a reference to the FastAPI app's main asyncio event
loop so worker-thread code (anything called via `asyncio.to_thread`, e.g.
tool executors dispatched through registry.execute()) can safely call back
into async objects that are bound to that specific loop.

Why this exists: browser_workspace.py's Playwright objects (Browser/Page)
are async-API objects created on the main event loop — Playwright's async
objects are bound to the loop they were created on, and calling their
methods from a different thread's loop breaks the underlying CDP transport
(see event_dispatcher.py's docstring for the same constraint on the
perception side). Tool executors registered in api/tools/registry.py are
synchronous functions run via asyncio.to_thread(), i.e. in a worker thread
with no event loop of its own — they cannot simply `await` a
browser_workspace coroutine. run_coro_from_thread() is the standard,
correct bridge for this: submit the coroutine to the main loop via
asyncio.run_coroutine_threadsafe() and block the worker thread on the
result.

Not a new architecture — a small, focused piece of plumbing required to
let browser_tools.py's tool executors reuse the SAME browser_workspace
every other browser-aware module in this codebase already uses, instead of
maintaining their own separate browser instance.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop
    logger.info("[MAIN_LOOP_CAPTURED]")


def get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    return _main_loop


def run_coro_from_thread(coro: "Coroutine[Any, Any, T]", timeout: float = 15.0) -> T:
    """
    Run *coro* on the captured main event loop from a worker thread and
    block until it completes. Raises RuntimeError if the main loop was
    never captured (app not fully started), or the original exception if
    the coroutine itself raised.
    """
    loop = _main_loop
    if loop is None:
        coro.close()
        raise RuntimeError("main event loop not captured yet — call set_main_loop() at startup")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)
