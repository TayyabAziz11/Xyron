from __future__ import annotations

"""
DependencyInstaller — async npm / pip dependency installation.

Streams stdout/stderr lines to the task's ws_send_fn so the user sees
live progress in the dashboard.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from api.agents.agent_types import AgentTask, StepResult

logger = logging.getLogger(__name__)

_WsSendFn = Optional[Callable[[dict[str, Any]], Awaitable[bool]]]

_INSTALL_TIMEOUT = 180.0  # seconds — npm install can be slow on first run


class DependencyInstaller:
    """Install Node or Python project dependencies asynchronously."""

    # ── Package-manager detection ──────────────────────────────────────────────

    async def detect_package_manager(self, project_path: Path) -> str:
        """Return ``'npm'``, ``'pnpm'``, or ``'yarn'`` for the project."""
        if (project_path / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (project_path / "yarn.lock").exists():
            return "yarn"
        return "npm"

    # ── Node install ───────────────────────────────────────────────────────────

    async def install(self, project_path: Path, task: AgentTask) -> StepResult:
        """Run ``npm install`` (or equivalent) in *project_path*.

        Streams each stdout/stderr line to *task.ws_send_fn*.  Waits up to
        180 s before timing out.
        """
        pm = await self.detect_package_manager(project_path)
        cmd = [pm, "install"]
        logger.info("[DEP_INSTALLER] running %s in %s", " ".join(cmd), project_path)

        await self._send(task, f"Running {' '.join(cmd)}…")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
            )

            collected: list[str] = []
            assert proc.stdout is not None

            async def _drain() -> None:
                async for raw_line in proc.stdout:  # type: ignore[union-attr]
                    line = raw_line.decode(errors="replace").rstrip()
                    collected.append(line)
                    # Stream every non-empty line to the WebSocket.
                    if line.strip():
                        await self._send(task, f"[install] {line}")

            try:
                await asyncio.wait_for(_drain(), timeout=_INSTALL_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return StepResult(
                    success=False,
                    output="npm install timed out after 180 s",
                    data={"stderr": "\n".join(collected[-20:])},
                )

            await proc.wait()
            output = "\n".join(collected)

            if proc.returncode == 0:
                logger.info("[DEP_INSTALLER] install succeeded")
                await self._send(task, "Dependencies installed successfully.")
                return StepResult(success=True, output="Dependencies installed.", data={"log": output[-500:]})

            # Non-zero exit — look for npm ERR lines.
            err_lines = [l for l in collected if "npm ERR!" in l or "error" in l.lower()]
            error_text = "\n".join(err_lines[-10:]) or output[-300:]
            logger.error("[DEP_INSTALLER] install failed rc=%d: %s", proc.returncode, error_text[:200])
            await self._send(task, f"[install] FAILED — {error_text[:120]}")
            return StepResult(
                success=False,
                output=f"npm install failed (rc={proc.returncode})",
                data={"stderr": error_text},
            )

        except FileNotFoundError:
            msg = f"'{pm}' not found — Node.js may not be installed."
            logger.error("[DEP_INSTALLER] %s", msg)
            return StepResult(success=False, output=msg)
        except Exception as exc:
            logger.exception("[DEP_INSTALLER] unexpected error: %s", exc)
            return StepResult(success=False, output=str(exc))

    # ── Python install ─────────────────────────────────────────────────────────

    async def install_python_deps(
        self,
        project_path: Path,
        requirements: list[str],
    ) -> StepResult:
        """Install Python packages via ``pip install``.

        Writes a temporary ``requirements.txt`` if *requirements* is non-empty,
        then runs ``pip install -r requirements.txt``.
        """
        req_file = project_path / "requirements.txt"

        # Honour an existing requirements.txt; otherwise write one.
        if not req_file.exists():
            req_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
            logger.info("[DEP_INSTALLER] wrote requirements.txt (%d packages)", len(requirements))

        cmd = ["pip", "install", "-r", str(req_file)]
        logger.info("[DEP_INSTALLER] running %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            collected: list[str] = []
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").rstrip()
                collected.append(line)

            await proc.wait()
            output = "\n".join(collected)

            if proc.returncode == 0:
                return StepResult(success=True, output="Python dependencies installed.", data={"log": output[-500:]})

            return StepResult(
                success=False,
                output=f"pip install failed (rc={proc.returncode})",
                data={"stderr": output[-300:]},
            )
        except FileNotFoundError:
            return StepResult(success=False, output="pip not found — Python not in PATH.")
        except Exception as exc:
            logger.exception("[DEP_INSTALLER] pip error: %s", exc)
            return StepResult(success=False, output=str(exc))

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def _send(task: AgentTask, message: str) -> None:
        """Fire-and-forget a progress message through the task WS callback."""
        if task.ws_send_fn is None:
            return
        try:
            await task.ws_send_fn(
                {
                    "type": "agent_progress",
                    "task_id": task.task_id,
                    "message": message,
                    "progress_pct": task.progress_pct,
                }
            )
        except Exception:
            pass
