"""
Central request tracer for Xyron voice pipeline.

Generates unique VX-XXXXXX trace IDs per utterance.
Stores last 100 traces for replay and inspection via /api/v1/debug/*.
"""
from __future__ import annotations

import random
import string
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional


def new_trace_id() -> str:
    """Generate a unique trace ID: VX-XXXXXX (6 uppercase alphanumeric chars)."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"VX-{suffix}"


@dataclass
class TraceRecord:
    trace_id:         str
    transcript:       str                    = ""
    normalized:       str                    = ""
    route_tier:       int                    = -1
    route_rule:       str                    = ""
    route_tool:       str                    = ""
    route_confidence: float                  = 0.0
    action:           str                    = ""
    plan_steps:       list[str]              = field(default_factory=list)
    tools_executed:   list[dict[str, Any]]   = field(default_factory=list)
    status:           str                    = "pending"  # pending | success | error
    error_type:       Optional[str]          = None
    error_detail:     str                    = ""
    start_ts:         float                  = field(default_factory=time.time)
    end_ts:           Optional[float]        = None
    timings_ms:       dict[str, float]       = field(default_factory=dict)
    result:           str                    = ""

    @property
    def total_ms(self) -> float:
        if self.end_ts:
            return (self.end_ts - self.start_ts) * 1000
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id":          self.trace_id,
            "transcript":        self.transcript,
            "normalized":        self.normalized,
            "route": {
                "tier":          self.route_tier,
                "rule":          self.route_rule,
                "tool":          self.route_tool,
                "confidence":    round(self.route_confidence, 3),
            },
            "action":            self.action,
            "plan_steps":        self.plan_steps,
            "tools_executed":    self.tools_executed,
            "status":            self.status,
            "error_type":        self.error_type,
            "error_detail":      self.error_detail,
            "execution_time_ms": round(self.total_ms, 1),
            "timings_ms":        {k: round(v, 1) for k, v in self.timings_ms.items()},
            "result":            self.result,
        }


class TraceStore:
    """Thread-safe in-memory store for the last N traces."""

    def __init__(self, maxsize: int = 100) -> None:
        self._store:             OrderedDict[str, TraceRecord] = OrderedDict()
        self._lock               = threading.Lock()
        self._max                = maxsize
        self._current_trace_id:  Optional[str] = None

    def start(self, trace_id: str) -> TraceRecord:
        rec = TraceRecord(trace_id=trace_id, start_ts=time.time())
        with self._lock:
            self._store[trace_id] = rec
            self._current_trace_id = trace_id
            if len(self._store) > self._max:
                self._store.popitem(last=False)
        return rec

    def get(self, trace_id: str) -> Optional[TraceRecord]:
        with self._lock:
            return self._store.get(trace_id)

    def last(self) -> Optional[TraceRecord]:
        with self._lock:
            if not self._store:
                return None
            return next(reversed(self._store.values()))

    def current_trace_id(self) -> Optional[str]:
        with self._lock:
            return self._current_trace_id

    def finish(
        self,
        trace_id: str,
        status:   str = "success",
        result:   str = "",
    ) -> None:
        with self._lock:
            rec = self._store.get(trace_id)
            if rec:
                rec.end_ts = time.time()
                rec.status = status
                rec.result = result


trace_store = TraceStore()
