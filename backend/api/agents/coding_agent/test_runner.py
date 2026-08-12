from __future__ import annotations

"""
TestRunner — build verification, linting, and HTTP page-content checks.

All subprocess calls are async.  HTTP verification uses the stdlib
``urllib.request`` wrapped in ``asyncio.to_thread`` to avoid adding
an aiohttp/httpx dependency.
"""

import asyncio
import logging
import urllib.error
import urllib.request
from pathlib import Path

from api.agents.agent_types import AgentTask, StepResult

logger = logging.getLogger(__name__)

_BUILD_TIMEOUT = 120.0   # npm run build can take a while
_LINT_TIMEOUT  = 30.0


class TestRunner:
    """Verify that a generated project compiles, lints, and serves correctly."""

    # ── Build verification ─────────────────────────────────────────────────────

    async def run_build(self, project_path: Path, task: AgentTask) -> StepResult:
        """Run ``npm run build`` to ensure the project compiles without errors.

        Returns a :class:`StepResult` indicating success or failure with the
        last 500 chars of build output attached.
        """
        pkg_json = project_path / "package.json"
        if not pkg_json.exists():
            return StepResult(success=True, output="No package.json — skipping build check.")

        import json as _json
        try:
            pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
        except Exception:
            pkg = {}

        scripts = pkg.get("scripts", {})
        if "build" not in scripts:
            return StepResult(success=True, output="No 'build' script defined — skipping.")

        logger.info("[TEST_RUNNER] running npm run build in %s", project_path)
        await self._send(task, "Running build check…")

        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "run", "build",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            collected: list[str] = []
            assert proc.stdout is not None

            async def _drain() -> None:
                async for raw in proc.stdout:  # type: ignore[union-attr]
                    line = raw.decode(errors="replace").rstrip()
                    collected.append(line)
                    await self._send(task, f"[build] {line}")

            try:
                await asyncio.wait_for(_drain(), timeout=_BUILD_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return StepResult(success=False, output="Build timed out after 120 s")

            await proc.wait()
            output = "\n".join(collected[-50:])

            if proc.returncode == 0:
                logger.info("[TEST_RUNNER] build succeeded")
                return StepResult(success=True, output="Build succeeded.", data={"log": output})

            logger.warning("[TEST_RUNNER] build failed rc=%d", proc.returncode)
            return StepResult(
                success=False,
                output=f"Build failed (rc={proc.returncode})",
                data={"stderr": output},
            )

        except FileNotFoundError:
            return StepResult(success=False, output="npm not found in PATH.")
        except Exception as exc:
            logger.exception("[TEST_RUNNER] build error: %s", exc)
            return StepResult(success=False, output=str(exc))

    # ── Linting ───────────────────────────────────────────────────────────────

    async def run_lint(self, project_path: Path) -> StepResult:
        """Run the project linter (``npm run lint``) if configured.

        Silently skips if no lint script is defined — it's not an error.
        """
        import json as _json

        pkg_json = project_path / "package.json"
        if not pkg_json.exists():
            return StepResult(success=True, output="No package.json — skipping lint.")

        try:
            pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
        except Exception:
            return StepResult(success=True, output="Could not parse package.json — skipping lint.")

        if "lint" not in pkg.get("scripts", {}):
            return StepResult(success=True, output="No 'lint' script — skipping.")

        logger.info("[TEST_RUNNER] running npm run lint")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "run", "lint",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            raw_out, _ = await asyncio.wait_for(proc.communicate(), timeout=_LINT_TIMEOUT)
            output = raw_out.decode(errors="replace")

            if proc.returncode == 0:
                return StepResult(success=True, output="Lint passed.", data={"log": output[-300:]})

            return StepResult(
                success=False,
                output=f"Lint failed (rc={proc.returncode})",
                data={"stderr": output[-500:]},
            )
        except asyncio.TimeoutError:
            return StepResult(success=False, output="Lint timed out.")
        except Exception as exc:
            logger.warning("[TEST_RUNNER] lint error: %s", exc)
            return StepResult(success=False, output=str(exc))

    # ── HTTP page verification ─────────────────────────────────────────────────

    async def verify_page_content(self, url: str) -> bool:
        """Return True if *url* responds with HTTP 200 and non-empty HTML.

        Uses stdlib ``urllib.request`` wrapped in a thread (no extra deps).
        """
        def _fetch() -> bool:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Xyron-CodingAgent/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status != 200:
                        return False
                    body = resp.read(4096).decode(errors="replace")
                    # Must contain at least an opening <html> or DOCTYPE tag.
                    return "<html" in body.lower() or "<!doctype" in body.lower()
            except urllib.error.URLError as exc:
                logger.debug("[TEST_RUNNER] verify_page_content url=%s error=%s", url, exc)
                return False

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_fetch), timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("[TEST_RUNNER] verify_page_content timed out for %s", url)
            return False

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def _send(task: AgentTask, message: str) -> None:
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
