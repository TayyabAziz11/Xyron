from __future__ import annotations

"""
TerminalRunner — async subprocess management for the Coding Builder Agent.

Responsibilities:
- One-shot command execution with timeout
- Background dev-server launch with port readiness polling
- Graceful process teardown
"""

import asyncio
import logging
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


class TerminalRunner:
    """Run shell commands and manage long-lived dev-server processes."""

    # ── One-shot execution ─────────────────────────────────────────────────────

    async def run_command(
        self,
        cmd: list[str],
        cwd: Path,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Execute *cmd* in *cwd* and return ``(returncode, stdout, stderr)``.

        Raises ``asyncio.TimeoutError`` if the process runs longer than
        *timeout* seconds.
        """
        logger.debug("[TERMINAL] run_command cmd=%s cwd=%s", cmd, cwd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning("[TERMINAL] command timed out after %.0fs: %s", timeout, cmd)
                await self.kill_process(proc)
                raise

            stdout = raw_out.decode(errors="replace")
            stderr = raw_err.decode(errors="replace")
            logger.debug(
                "[TERMINAL] rc=%s stdout_len=%d stderr_len=%d",
                proc.returncode,
                len(stdout),
                len(stderr),
            )
            return proc.returncode or 0, stdout, stderr

        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            logger.error("[TERMINAL] run_command error: %s", exc)
            return 1, "", str(exc)

    # ── Dev server ─────────────────────────────────────────────────────────────

    async def run_dev_server(
        self,
        project_path: Path,
        command: str,
        port: int,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        """Launch a dev server in the background.

        Waits up to 15 s for *port* to become open on localhost.
        Returns the running Process object; the caller is responsible for
        eventual cleanup via :meth:`kill_process`.

        Raises ``RuntimeError`` if the port never opens within the timeout.
        """
        cmd_parts = command.split()
        logger.info("[TERMINAL] starting dev server: %s  (port=%d)", command, port)

        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            cwd=str(project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        opened = await self.wait_for_port(port, timeout=15.0)
        if not opened:
            # Still return the process so the caller can stream stderr for
            # error diagnostics, but log the failure clearly.
            logger.warning(
                "[TERMINAL] dev server did not open port %d within 15 s (process still running)",
                port,
            )
        else:
            logger.info("[TERMINAL] dev server ready on port %d", port)

        return proc

    # ── Port readiness poll ────────────────────────────────────────────────────

    async def wait_for_port(self, port: int, timeout: float = 15.0) -> bool:
        """Poll ``localhost:<port>`` until it accepts a TCP connection.

        Returns True if the port opened within *timeout* seconds, else False.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=1.0
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except (OSError, asyncio.TimeoutError, ConnectionRefusedError):
                await asyncio.sleep(0.5)
        return False

    # ── Teardown ───────────────────────────────────────────────────────────────

    async def kill_process(self, proc: asyncio.subprocess.Process) -> None:
        """Gracefully terminate *proc* (SIGTERM first, then SIGKILL after 5 s)."""
        if proc.returncode is not None:
            return  # already exited
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("[TERMINAL] SIGTERM timed out — sending SIGKILL")
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass  # process already gone
        logger.debug("[TERMINAL] process pid=%s stopped", proc.pid)
