"""Episodic memory history and stats endpoints — Feature #6."""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


@router.get("/history")
async def memory_history(hours: float = Query(24.0), limit: int = Query(100)):
    """Return recent episodic turns (user + assistant) with tool calls."""
    try:
        from ..services.episodic_memory import episodic_memory as _em
        import sqlite3, json, time
        cutoff = time.time() - hours * 3600
        con = sqlite3.connect(_em._path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM turns WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        con.close()
        entries = []
        for r in rows:
            entries.append({
                "id":         r["id"],
                "session_id": r["session_id"],
                "role":       r["role"],
                "content":    r["text"],
                "tool":       r["tool_name"],
                "ts":         r["timestamp"],
            })
        return {"entries": entries, "hours": hours}
    except Exception as exc:
        return {"entries": [], "error": str(exc)}


@router.get("/stats")
async def memory_stats():
    """Return tool usage frequency, hourly patterns, and activity summary."""
    try:
        from ..services.episodic_memory import episodic_memory as _em
        freq   = _em.tool_frequency()
        top3   = _em.top_tools_now(5)
        summary = _em.summary_since(24)
        # Hourly patterns: count tool usage by hour
        import sqlite3, time
        cutoff = time.time() - 7 * 24 * 3600   # last 7 days
        con = sqlite3.connect(_em._path)
        rows = con.execute(
            "SELECT tool_name, strftime('%H', timestamp, 'unixepoch') AS hour, COUNT(*) AS cnt "
            "FROM turns WHERE timestamp >= ? AND tool_name IS NOT NULL "
            "GROUP BY tool_name, hour ORDER BY cnt DESC",
            (cutoff,),
        ).fetchall()
        con.close()
        hourly: dict[str, dict[str, int]] = {}
        for tool_name, hour, cnt in rows:
            hourly.setdefault(tool_name, {})[hour] = cnt
        return {
            "tool_frequency": freq,
            "top_tools_now":  top3,
            "activity_24h":   summary,
            "hourly_patterns": hourly,
        }
    except Exception as exc:
        return {"tool_frequency": {}, "error": str(exc)}
