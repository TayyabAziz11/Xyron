"""
Sentinel Service — background health monitor for the Xyron voice pipeline.

Runs every 5 minutes in a daemon thread. Tracks:
  - voice session health (last activity age)
  - TTS synthesis failures
  - STT empty-transcript rate
  - OpenAI circuit state (open = quota exceeded)
  - CPU / memory pressure
  - Repeated failed commands

Writes a rolling report to ~/.ai-operator/sentinel_report.md.
Does NOT auto-modify code. Detect → log → suggest → report only.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPORT_PATH  = Path.home() / ".ai-operator" / "sentinel_report.md"
_INTERVAL_SEC = 300  # 5 minutes
_MAX_FAIL_CMD = 5    # alert if same command fails this many times


@dataclass
class _SentinelCounters:
    """In-memory counters reset each check cycle."""
    tts_failures:      int = 0
    stt_empty:         int = 0
    stt_total:         int = 0
    tool_failures:     dict = field(default_factory=dict)   # tool_name → fail_count
    ws_disconnects:    int = 0
    openai_quota_hits: int = 0

    def reset(self) -> None:
        self.tts_failures     = 0
        self.stt_empty        = 0
        self.stt_total        = 0
        self.tool_failures    = {}
        self.ws_disconnects   = 0
        self.openai_quota_hits = 0


class SentinelService:

    def __init__(self) -> None:
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._counters = _SentinelCounters()
        self._lock     = threading.Lock()
        self._last_activity_ref: Optional[list] = None  # [float] mutable ref from ws_session

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self, activity_ref: Optional[list] = None) -> None:
        """Start background monitoring. activity_ref is a mutable [timestamp] from ws_session."""
        if self._running:
            return
        self._running = True
        self._last_activity_ref = activity_ref
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="XyronSentinel"
        )
        self._thread.start()
        logger.info("[SENTINEL_CHECK] started interval=%ds", _INTERVAL_SEC)

    def stop(self) -> None:
        self._running = False

    # ── Counter mutation — called from voice pipeline ─────────────────────────

    def record_tts_failure(self) -> None:
        with self._lock:
            self._counters.tts_failures += 1

    def record_stt(self, empty: bool = False) -> None:
        with self._lock:
            self._counters.stt_total += 1
            if empty:
                self._counters.stt_empty += 1

    def record_tool_failure(self, tool_name: str) -> None:
        with self._lock:
            self._counters.tool_failures[tool_name] = (
                self._counters.tool_failures.get(tool_name, 0) + 1
            )

    def record_ws_disconnect(self) -> None:
        with self._lock:
            self._counters.ws_disconnects += 1

    def record_openai_quota(self) -> None:
        with self._lock:
            self._counters.openai_quota_hits += 1

    # ── Background loop ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            time.sleep(_INTERVAL_SEC)
            if not self._running:
                break
            try:
                from api.services.background_scheduler import scheduler as _sched
                if not _sched.should_run("sentinel"):
                    continue
            except Exception:
                pass
            try:
                self._check()
            except Exception as exc:
                logger.warning("[SENTINEL_CHECK] check error: %s", exc)

    def _check(self) -> None:
        logger.info("[SENTINEL_CHECK] running health check")
        issues: list[str] = []
        suggestions: list[str] = []

        with self._lock:
            c = self._counters
            snap = _SentinelCounters(
                tts_failures=c.tts_failures,
                stt_empty=c.stt_empty,
                stt_total=c.stt_total,
                tool_failures=dict(c.tool_failures),
                ws_disconnects=c.ws_disconnects,
                openai_quota_hits=c.openai_quota_hits,
            )
            self._counters.reset()

        # TTS failures
        if snap.tts_failures > 0:
            issues.append(f"TTS synthesis failures: {snap.tts_failures}")
            suggestions.append("Check Kokoro GPU memory; restart TTS service if OOM.")
            logger.warning("[SENTINEL_ISSUE_FOUND] type=tts_failure count=%d", snap.tts_failures)

        # STT empty rate
        if snap.stt_total > 0:
            empty_rate = snap.stt_empty / snap.stt_total
            if empty_rate > 0.30:
                issues.append(f"STT empty transcript rate: {empty_rate:.0%} ({snap.stt_empty}/{snap.stt_total})")
                suggestions.append("Microphone may be disconnected or muted. Check audio input device.")
                logger.warning("[SENTINEL_ISSUE_FOUND] type=stt_empty_rate rate=%.2f", empty_rate)

        # Repeated tool failures
        for tool, cnt in snap.tool_failures.items():
            if cnt >= _MAX_FAIL_CMD:
                issues.append(f"Tool '{tool}' failed {cnt} times")
                suggestions.append(f"Tool '{tool}' may be misconfigured. Check system_tools.py executor.")
                logger.warning("[SENTINEL_ISSUE_FOUND] type=tool_repeated_fail tool=%s count=%d", tool, cnt)

        # WebSocket disconnects
        if snap.ws_disconnects > 3:
            issues.append(f"WebSocket disconnects: {snap.ws_disconnects}")
            suggestions.append("Network instability or frontend reload loop detected.")
            logger.warning("[SENTINEL_ISSUE_FOUND] type=ws_disconnect count=%d", snap.ws_disconnects)

        # OpenAI quota
        if snap.openai_quota_hits > 0:
            issues.append(f"OpenAI quota/rate-limit hits: {snap.openai_quota_hits}")
            suggestions.append("OpenAI circuit breaker active; using local Ollama fallback.")
            logger.warning("[SENTINEL_ISSUE_FOUND] type=openai_quota count=%d", snap.openai_quota_hits)

        # CPU/memory pressure
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory().percent
            if cpu > 90:
                issues.append(f"CPU usage critical: {cpu:.0f}%")
                suggestions.append("High CPU — consider reducing STT model size or disabling background services.")
                logger.warning("[SENTINEL_ISSUE_FOUND] type=cpu_high pct=%.0f", cpu)
            if mem > 90:
                issues.append(f"Memory usage critical: {mem:.0f}%")
                suggestions.append("High RAM — Kokoro/Whisper models may not unload properly.")
                logger.warning("[SENTINEL_ISSUE_FOUND] type=memory_high pct=%.0f", mem)
        except Exception:
            pass

        # Session staleness
        if self._last_activity_ref:
            try:
                age = time.time() - self._last_activity_ref[0]
                if age > 1800:  # 30 min idle
                    issues.append(f"Voice session idle for {age/60:.0f} minutes")
                    suggestions.append("Session may be stale. Frontend may need reconnect.")
                    logger.warning("[SENTINEL_ISSUE_FOUND] type=session_stale age_min=%.0f", age / 60)
            except Exception:
                pass

        if not issues:
            logger.info("[SENTINEL_HEALTH_OK] no issues detected")
        else:
            logger.warning("[SENTINEL_ISSUE_FOUND] total_issues=%d", len(issues))
            for s in suggestions:
                logger.info("[SENTINEL_SUGGESTION] %s", s)
            self._write_report(issues, suggestions)

    def _write_report(self, issues: list[str], suggestions: list[str]) -> None:
        try:
            _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            lines = [f"# Sentinel Report — {ts}\n"]
            lines.append("## Issues\n")
            for iss in issues:
                lines.append(f"- {iss}\n")
            lines.append("\n## Suggestions\n")
            for sug in suggestions:
                lines.append(f"- {sug}\n")
            report = "".join(lines)
            _REPORT_PATH.write_text(report, encoding="utf-8")
            logger.info("[SENTINEL_REPORT_WRITTEN] path=%s issues=%d", _REPORT_PATH, len(issues))
        except Exception as exc:
            logger.warning("[SENTINEL_REPORT_WRITTEN] failed: %s", exc)


sentinel_service = SentinelService()
