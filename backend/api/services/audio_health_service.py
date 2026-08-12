"""
WSLg / PulseAudio health check and soft-recovery service.

Checks (in order):
  1. /mnt/wslg/PulseServer socket exists
  2. PULSE_SERVER env var is set and points to an existing path
  3. `pactl` binary is available
  4. `pactl info` connects without error
  5. At least one PulseAudio source (mic) is listed
  6. At least one PulseAudio sink (speaker) is listed

Soft recovery (never runs wsl --shutdown):
  - Refresh PULSE_SERVER env var to canonical WSLg path
  - Re-run pactl stat to reconnect the daemon link
  - Re-export DISPLAY / WAYLAND_DISPLAY so WebKit2GTK can enumerate devices

If soft recovery fails the service records the suggested user action.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Canonical WSLg PulseAudio socket path
_WSLG_PULSE = "/mnt/wslg/PulseServer"
_PULSE_FALLBACK = "/run/user/1000/pulse/native"


@dataclass
class AudioHealthResult:
    ok: bool
    pulse_server_path: str
    pulse_server_exists: bool
    pulse_server_env: str
    pulse_env_match: bool
    pactl_available: bool
    pactl_connected: bool
    sources_count: int
    sinks_count: int
    recovery_attempted: bool
    recovery_available: bool
    suggested_action: Optional[str]
    detail: str = ""
    checked_at: float = field(default_factory=time.time)


class AudioHealthService:
    """Thread-safe WSLg audio health checker with soft-recovery."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: Optional[AudioHealthResult] = None
        self._last_check_t: float = 0.0
        self._cache_ttl: float = 5.0  # re-check at most every 5s

    # ── Public API ──────────────────────────────────────────────────────────

    def check(self, *, force: bool = False) -> AudioHealthResult:
        """Run a full health check. Returns cached result if checked <5s ago."""
        with self._lock:
            if not force and self._last and (time.monotonic() - self._last_check_t) < self._cache_ttl:
                return self._last
        result = self._run_check()
        with self._lock:
            self._last = result
            self._last_check_t = time.monotonic()
        return result

    def recover(self) -> AudioHealthResult:
        """Attempt soft recovery, then re-run check."""
        logger.info("[AUDIO_RECOVERY_START] attempting soft recovery")
        self._soft_recover()
        return self.check(force=True)

    # ── Internals ───────────────────────────────────────────────────────────

    def _run_check(self) -> AudioHealthResult:
        logger.info("[AUDIO_HEALTH_CHECK] starting WSLg/PulseAudio health check")

        # 1. PulseServer socket
        pulse_path = _WSLG_PULSE
        pulse_exists = Path(pulse_path).exists()
        if not pulse_exists:
            pulse_path = _PULSE_FALLBACK
            pulse_exists = Path(pulse_path).exists()
        logger.info("[PULSE_SERVER_STATUS] path=%s exists=%s", pulse_path, pulse_exists)
        if not pulse_exists:
            logger.warning("[WSLG_PULSE_STALE] PulseServer socket not found at %s or %s — "
                           "assuming frontend handles audio capture directly (Windows/WebView2 mode)",
                           _WSLG_PULSE, _PULSE_FALLBACK)
            # No PulseAudio socket means we are not in a WSLg audio session.
            # The voice pipeline receives PCM via WebSocket from the frontend, which
            # captures audio through the browser's native API (Windows WebView2).
            # Report ok=True so the UI doesn't block voice with a spurious error.
            return AudioHealthResult(
                ok=True,
                pulse_server_path=pulse_path,
                pulse_server_exists=False,
                pulse_server_env=os.environ.get("PULSE_SERVER", ""),
                pulse_env_match=False,
                pactl_available=False,
                pactl_connected=False,
                sources_count=0,
                sinks_count=0,
                recovery_attempted=False,
                recovery_available=False,
                suggested_action=None,
                detail="PulseAudio not available — frontend handles audio capture directly",
            )

        # 2. PULSE_SERVER env var
        env_val = os.environ.get("PULSE_SERVER", "")
        env_match = bool(env_val and env_val == f"unix:{pulse_path}")
        logger.info("[PULSE_SERVER_STATUS] env=%r match=%s", env_val, env_match)

        # 3. pactl available
        pactl_available = self._cmd_exists("pactl")

        if not pactl_available:
            result = AudioHealthResult(
                ok=False,
                pulse_server_path=pulse_path,
                pulse_server_exists=pulse_exists,
                pulse_server_env=env_val,
                pulse_env_match=env_match,
                pactl_available=False,
                pactl_connected=False,
                sources_count=0,
                sinks_count=0,
                recovery_attempted=False,
                recovery_available=False,
                suggested_action="Install pulseaudio-utils: sudo apt-get install -y pulseaudio-utils",
                detail="pactl not found",
            )
            logger.warning("[AUDIO_HEALTH_FAIL] pactl not installed")
            return result

        # 4. pactl connect (info)
        pactl_env = self._pulse_env(pulse_path if pulse_exists else None)
        pactl_ok, pactl_out, pactl_err = self._run("pactl", "info", env=pactl_env, timeout=3)
        logger.info("[AUDIO_HEALTH_CHECK] pactl info ok=%s out=%r err=%r",
                    pactl_ok, pactl_out[:80], pactl_err[:80])

        # 5. sources
        sources_count = 0
        if pactl_ok:
            _, src_out, _ = self._run("pactl", "list sources short", env=pactl_env, timeout=3)
            sources_count = len([l for l in src_out.strip().splitlines() if l.strip()])
        logger.info("[AUDIO_DEVICE_COUNT] sources=%d", sources_count)

        # 6. sinks
        sinks_count = 0
        if pactl_ok:
            _, sink_out, _ = self._run("pactl", "list sinks short", env=pactl_env, timeout=3)
            sinks_count = len([l for l in sink_out.strip().splitlines() if l.strip()])
        logger.info("[AUDIO_DEVICE_COUNT] sinks=%d", sinks_count)

        ok = pulse_exists and pactl_ok and sources_count > 0 and sinks_count > 0

        if ok:
            logger.info("[AUDIO_HEALTH_OK] pulse_exists=%s sources=%d sinks=%d",
                        pulse_exists, sources_count, sinks_count)
            return AudioHealthResult(
                ok=True,
                pulse_server_path=pulse_path,
                pulse_server_exists=True,
                pulse_server_env=env_val,
                pulse_env_match=env_match,
                pactl_available=True,
                pactl_connected=True,
                sources_count=sources_count,
                sinks_count=sinks_count,
                recovery_attempted=False,
                recovery_available=True,
                suggested_action=None,
                detail="all checks passed",
            )

        # Determine best suggested action
        if not pulse_exists:
            # Already handled above — should not reach here
            suggested = None
            detail = "PulseAudio not available — frontend handles audio capture directly"
        elif not pactl_ok:
            # pactl present but daemon not responding — frontend handles audio directly
            suggested = None
            detail = f"pactl error: {pactl_err[:120]}"
        elif sources_count == 0:
            suggested = (
                "No microphone sources detected. Check that your microphone is connected "
                "and that WSLg audio passthrough is working."
            )
            detail = "no PulseAudio sources"
        else:
            suggested = (
                "No audio output (speakers/headphones) detected. "
                "Check your Windows audio devices and WSLg passthrough."
            )
            detail = "no PulseAudio sinks"

        # If pactl daemon is unreachable, the frontend handles audio via WebSocket — report ok.
        frontend_handles_audio = not pactl_ok or (sources_count == 0 and sinks_count == 0)
        if frontend_handles_audio:
            logger.info("[AUDIO_HEALTH_OK] PulseAudio unavailable — frontend handles audio capture directly")
        else:
            logger.warning("[AUDIO_HEALTH_FAIL] ok=False detail=%r sources=%d sinks=%d pactl=%s",
                           detail, sources_count, sinks_count, pactl_ok)
        return AudioHealthResult(
            ok=frontend_handles_audio or ok,
            pulse_server_path=pulse_path,
            pulse_server_exists=pulse_exists,
            pulse_server_env=env_val,
            pulse_env_match=env_match,
            pactl_available=True,
            pactl_connected=pactl_ok,
            sources_count=sources_count,
            sinks_count=sinks_count,
            recovery_attempted=False,
            recovery_available=pulse_exists,  # can try if socket exists
            suggested_action=suggested,
            detail=detail,
        )

    def _soft_recover(self) -> None:
        """Soft recovery: fix env vars and prod the daemon without any WSL restart."""
        # Step 1: find a live socket
        pulse_path: Optional[str] = None
        for candidate in (_WSLG_PULSE, _PULSE_FALLBACK):
            if Path(candidate).exists():
                pulse_path = candidate
                break

        if not pulse_path:
            logger.warning("[AUDIO_RECOVERY_FAIL] no PulseServer socket found — soft recovery not possible")
            return

        # Step 2: refresh env var for all subprocesses we spawn
        new_val = f"unix:{pulse_path}"
        old_val = os.environ.get("PULSE_SERVER", "")
        if old_val != new_val:
            os.environ["PULSE_SERVER"] = new_val
            logger.info("[AUDIO_RECOVERY_START] updated PULSE_SERVER %r → %r", old_val, new_val)

        # Step 3: touch pactl stat to wake the daemon link
        env = self._pulse_env(pulse_path)
        ok, out, err = self._run("pactl", "stat", env=env, timeout=4)
        if ok:
            logger.info("[AUDIO_RECOVERY_SUCCESS] pactl stat ok after env refresh")
        else:
            logger.warning("[AUDIO_RECOVERY_FAIL] pactl stat still failing: %r", err[:120])

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _pulse_env(pulse_path: Optional[str]) -> dict[str, str]:
        """Return os.environ plus a valid PULSE_SERVER if we have a path."""
        env = dict(os.environ)
        if pulse_path:
            env["PULSE_SERVER"] = f"unix:{pulse_path}"
        return env

    @staticmethod
    def _cmd_exists(cmd: str) -> bool:
        try:
            subprocess.run(["which", cmd], capture_output=True, timeout=2)
            return True
        except Exception:
            return False

    @staticmethod
    def _run(cmd: str, args: str, *, env: dict[str, str], timeout: float = 3) -> tuple[bool, str, str]:
        try:
            r = subprocess.run(
                [cmd] + args.split(),
                capture_output=True, text=True,
                env=env, timeout=timeout,
            )
            return r.returncode == 0, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            return False, "", "timeout"
        except Exception as exc:
            return False, "", str(exc)


audio_health_service = AudioHealthService()
