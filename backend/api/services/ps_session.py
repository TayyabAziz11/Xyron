"""
Persistent PowerShell session — single reusable powershell.exe process.

Replaces per-call subprocess.run([powershell.exe, ...]) with stdin piping,
cutting per-command latency from ~400 ms to ~30 ms on WSL2.

Thread-safe: commands serialize through a lock. Auto-restarts on crash.
"""
from __future__ import annotations

import logging
import select
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cap on how long a call waits to acquire the lock, independent of its own
# `timeout`. A caller whose own asyncio-level wrapper already gave up (e.g.
# asyncio.wait_for(..., timeout=1.5) on a call passing timeout=6) leaves this
# session still running under the lock for up to its own `timeout` before
# self-resolving — an unrelated caller queued up right after would otherwise
# silently inherit whatever's left of that abandoned wait. Bounding the
# acquire means contention fails fast with "PowerShell busy" instead.
_LOCK_WAIT_CAP = 3.0

_PS_PATHS = [
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe",
]


class _PersistentSession:

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._exe: Optional[str] = None

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _find_exe(self) -> Optional[str]:
        if self._exe:
            return self._exe
        for p in _PS_PATHS:
            if Path(p).exists():
                self._exe = p
                return p
        found = shutil.which("powershell.exe") or shutil.which("powershell")
        if found:
            self._exe = found
        return self._exe

    def _start(self) -> bool:
        exe = self._find_exe()
        if not exe:
            logger.debug("[PSSession] powershell.exe not found")
            return False
        try:
            self._proc = subprocess.Popen(
                [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="ignore",
                bufsize=1,
            )
            logger.info("[PSSession] started (pid=%d)", self._proc.pid)
            return True
        except Exception as exc:
            logger.warning("[PSSession] start failed: %s", exc)
            self._proc = None
            return False

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, command: str, timeout: int = 10) -> tuple[bool, str]:
        """Execute a PowerShell command. Returns (success, stdout)."""
        if not self._lock.acquire(timeout=min(timeout, _LOCK_WAIT_CAP)):
            return False, "PowerShell busy"
        try:
            if not self._alive():
                if not self._start():
                    return False, "PowerShell not available"

            sentinel = f"__XYRON_{uuid.uuid4().hex}__"
            # Wrap in try/catch so errors surface before the sentinel
            payload = (
                "$ErrorActionPreference = 'Continue'\n"
                f"try {{ {command} }} catch {{ Write-Output \"ERROR: $_\" }}\n"
                f"Write-Output '{sentinel}'\n"
            )

            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except Exception as exc:
                self._proc = None
                return False, f"Write failed: {exc}"

            lines: list[str] = []
            deadline = time.monotonic() + timeout

            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
                except (ValueError, OSError):
                    self._proc = None
                    return False, "PowerShell process died"

                if not ready:
                    break  # timeout on select — fall through to kill

                line = self._proc.stdout.readline()
                if not line:
                    self._proc = None
                    return False, "PowerShell process died"

                stripped = line.rstrip("\r\n")
                if stripped == sentinel:
                    return True, "\n".join(lines).strip()
                # Suppress PS prompt noise ("PS C:\> ")
                if not (stripped.startswith("PS ") and stripped.endswith("> ")):
                    lines.append(stripped)

            # Timeout — kill process and reset
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
            return False, "Command timed out"
        finally:
            self._lock.release()

    def shutdown(self) -> None:
        with self._lock:
            if self._proc:
                try:
                    self._proc.stdin.write("exit\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
                logger.info("[PSSession] shut down")


# Module-level singleton — shared across all tool calls
ps_session = _PersistentSession()


def run_ps(command: str, timeout: int = 10) -> tuple[bool, str]:
    """Run a PowerShell command via the persistent session. Returns (success, stdout)."""
    return ps_session.run(command, timeout=timeout)
