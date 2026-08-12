"""
Dev Observer — background loop that watches the active dev session and emits passive insights.

[DEV_OBSERVER] / [PASSIVE_INSIGHT] log prefixes.
Runs only when cognitive_state.code_mode == True.
Loop interval: 5 seconds.
Insight throttle: max 1 insight per 60 seconds (non-spammy).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_LOOP_INTERVAL    = 5
_INSIGHT_INTERVAL = 60

_state: dict = {
    "running":         False,
    "last_insight_at": 0.0,
    "last_insight":    None,
    "last_file":       None,
    "file_counts":     defaultdict(int),   # file_path → visit count this session
    "clients":         set(),              # asyncio.Queue per SSE client
}


def _basename(path: str | None) -> str:
    return Path(path).name if path else "active file"


async def _maybe_insight() -> dict | None:
    """Evaluate current context and produce one insight if the throttle allows."""
    try:
        from api.services.cognitive_state import cognitive_state as cs
        if not cs.code_mode:
            return None

        now = time.time()
        if now - _state["last_insight_at"] < _INSIGHT_INTERVAL:
            return None

        file_path = cs.active_file
        project   = cs.active_project
        candidates: list[dict] = []

        # ── 1. Repeated file visits ───────────────────────────────────────────
        if file_path:
            _state["file_counts"][file_path] += 1
            count = _state["file_counts"][file_path]
            if count >= 4:
                candidates.append({
                    "type": "insight",
                    "message": f"You have returned to {_basename(file_path)} {count}× this session.",
                    "severity": "info",
                    "source": "file",
                })

        # ── 2. Active file issues ─────────────────────────────────────────────
        if file_path and file_path != _state.get("last_file"):
            from .file_intelligence import analyze_file
            ctx = await asyncio.to_thread(analyze_file, file_path, project)
            if ctx.get("issues"):
                candidates.append({
                    "type": "insight",
                    "message": f"Issue in {_basename(file_path)}: {ctx['issues'][0]}",
                    "severity": "warning",
                    "source": "file",
                })
            if ctx.get("todos"):
                candidates.append({
                    "type": "insight",
                    "message": f"{len(ctx['todos'])} TODO/FIXME comment(s) in {_basename(file_path)}.",
                    "severity": "info",
                    "source": "file",
                })

        # ── 3. Project memory — recurring errors & stack ──────────────────────
        if project:
            from .project_memory import get_memory
            mem = await asyncio.to_thread(get_memory, project)

            rec = mem.get("recurring_errors", [])
            if rec and rec[0].get("count", 0) >= 3:
                top = rec[0]
                candidates.append({
                    "type": "insight",
                    "message": (f"Recurring error: {top.get('type','unknown')} "
                                f"seen {top['count']}× in {mem['project_name']}."),
                    "severity": "warning",
                    "source": "memory",
                })

            stack = mem.get("detected_stack", [])
            if stack and project != _state.get("last_project_stack_emitted"):
                candidates.append({
                    "type": "insight",
                    "message": f"Project uses {', '.join(stack[:3])}.",
                    "severity": "info",
                    "source": "project",
                })
                _state["last_project_stack_emitted"] = project

        if not candidates:
            return None

        chosen = candidates[0]
        _state["last_insight_at"] = now
        _state["last_file"]       = file_path
        _state["last_insight"]    = chosen

        logger.info("[PASSIVE_INSIGHT] message=%r severity=%s source=%s "
                    "latency_ms=0 action=emit file=%s project=%s",
                    chosen["message"], chosen["severity"], chosen["source"],
                    file_path, project)
        return chosen

    except Exception as exc:
        logger.debug("[DEV_OBSERVER] insight evaluation error: %s", exc)
    return None


async def observer_loop() -> None:
    """Background asyncio task — runs for the server lifetime."""
    if _state["running"]:
        return
    _state["running"] = True
    logger.info("[DEV_OBSERVER] started loop_interval=%ds insight_interval=%ds",
                _LOOP_INTERVAL, _INSIGHT_INTERVAL)
    while True:
        try:
            from api.services.background_scheduler import scheduler as _sched
            if not _sched.should_run("dev_observer"):
                await asyncio.sleep(_LOOP_INTERVAL)
                continue
            from api.services.cognitive_state import cognitive_state as cs
            if cs.code_mode:
                insight = await _maybe_insight()
                if insight and _state["clients"]:
                    dead: set = set()
                    for q in list(_state["clients"]):
                        try:
                            q.put_nowait(insight)
                        except asyncio.QueueFull:
                            pass
                        except Exception:
                            dead.add(q)
                    _state["clients"] -= dead
        except Exception as exc:
            logger.debug("[DEV_OBSERVER] loop tick error: %s", exc)
        await asyncio.sleep(_LOOP_INTERVAL)


async def observer_sse_stream() -> AsyncGenerator[str, None]:
    """SSE generator consumed by GET /api/v1/dev/observer-stream."""
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _state["clients"].add(q)
    logger.debug("[DEV_OBSERVER] client connected, total=%d", len(_state["clients"]))
    try:
        yield f"data: {json.dumps({'type': 'connected', 'message': 'Dev Observer active'})}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _state["clients"].discard(q)
        logger.debug("[DEV_OBSERVER] client disconnected, remaining=%d", len(_state["clients"]))
