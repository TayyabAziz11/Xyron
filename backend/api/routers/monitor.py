"""
Real-time system monitor router.

  GET  /api/v1/monitor/snapshot  — one-shot JSON snapshot (voice / REST)
  WS   /api/v1/monitor/ws        — live stream, 1s cadence
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.services.system_monitor_service import system_monitor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/snapshot")
async def snapshot():
    """One-shot system metrics for REST callers (voice queries, etc.)."""
    return {"success": True, "data": system_monitor.get_snapshot()}


@router.get("/history/{metric}")
async def history(metric: str, n: int = 60):
    """Return up to n seconds of sparkline history for a metric."""
    allowed = {"cpu", "ram", "gpu", "net_up", "net_down"}
    if metric not in allowed:
        return {"success": False, "error": f"Unknown metric '{metric}'. Choose from {sorted(allowed)}"}
    return {"success": True, "metric": metric, "data": system_monitor.get_history(metric, n)}


@router.websocket("/ws")
async def monitor_ws(websocket: WebSocket):
    """
    WebSocket stream — sends one snapshot immediately on connect, then live
    metric frames at ~1 s intervals as the background sampler fires them.
    """
    await websocket.accept()
    client = websocket.client
    logger.info("[SystemMonitor] WS connect from %s", client)

    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    system_monitor.subscribe(q)

    try:
        # Initial full snapshot with history so the frontend can draw sparklines immediately
        await websocket.send_json({"type": "snapshot", "data": system_monitor.get_full_snapshot()})

        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # Send a keepalive ping so the connection doesn't silently die
                await websocket.send_json({"type": "ping"})
                continue

            depth = q.qsize()
            if depth > 0:
                # Non-zero depth means the producer is outpacing this
                # consumer (slow frontend/network) — visible, not fatal;
                # _safe_put_metrics on the producer side already prevents
                # this from ever raising QueueFull.
                logger.debug("[QUEUE_DEPTH] queue=metrics depth=%d", depth)
                if depth >= 5:
                    logger.info("[EVENT_BACKPRESSURE] queue=metrics depth=%d client=%s", depth, client)

            await websocket.send_json(msg)

    except WebSocketDisconnect:
        logger.info("[SystemMonitor] WS disconnect from %s", client)
    except Exception as exc:
        logger.warning("[SystemMonitor] WS error from %s: %s", client, exc)
    finally:
        system_monitor.unsubscribe(q)
