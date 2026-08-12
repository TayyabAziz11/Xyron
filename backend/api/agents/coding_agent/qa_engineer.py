from __future__ import annotations

"""
QAEngineer — Phase 4.6 quality assurance role for CodingAgent.

Runs the full QA pipeline:
  1. npm run build  — compile error detection
  2. npm run lint   — code quality (non-blocking)
  3. VisualReviewer — screenshot + DOM + console + network inspection

Returns a QAReport dict with overall pass/fail and per-step results.

Log tags: [QA_BUILD] [QA_LINT] [QA_VISUAL] [QA_PASS] [QA_FAIL]
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from api.agents.agent_types import AgentTask
from api.agents.coding_agent.visual_reviewer import VisualReviewer

logger = logging.getLogger(__name__)

_BUILD_TIMEOUT = 120.0
_LINT_TIMEOUT  = 30.0


class QAEngineer:
    """Orchestrate build, lint, and visual verification steps."""

    def __init__(self) -> None:
        self._reviewer = VisualReviewer()

    async def run(
        self,
        project_path: Path,
        url: str,
        task: AgentTask,
        run_build: bool = True,
        run_lint: bool = True,
    ) -> dict[str, Any]:
        """Run full QA pipeline.

        Parameters
        ----------
        project_path: Root of the generated project.
        url:          Dev server URL to visually verify.
        task:         AgentTask for progress reporting.
        run_build:    Whether to run ``npm run build``.
        run_lint:     Whether to run ``npm run lint`` (skipped if no lint script).
        """
        report: dict[str, Any] = {
            "passed":  False,
            "build":   {"ran": False, "passed": False, "output": ""},
            "lint":    {"ran": False, "passed": False, "output": ""},
            "visual":  {},
            "issues":  [],
        }

        await self._send(task, "Running build check…")

        # ── 1. Build ───────────────────────────────────────────────────────────
        if run_build and self._has_build_script(project_path):
            logger.info("[QA_BUILD] running npm run build at %s", project_path)
            build_ok, build_out = await self._run_npm_script(
                "build", project_path, _BUILD_TIMEOUT
            )
            report["build"] = {
                "ran":    True,
                "passed": build_ok,
                "output": build_out[-500:],
            }
            if not build_ok:
                report["issues"].append(f"Build failed: {build_out[-200:]}")
                logger.warning("[QA_BUILD] FAIL — %s", build_out[-100:])
            else:
                logger.info("[QA_BUILD] PASS")

        # ── 2. Lint ────────────────────────────────────────────────────────────
        if run_lint and self._has_lint_script(project_path):
            await self._send(task, "Running lint check…")
            lint_ok, lint_out = await self._run_npm_script(
                "lint", project_path, _LINT_TIMEOUT
            )
            report["lint"] = {
                "ran":    True,
                "passed": lint_ok,
                "output": lint_out[-300:],
            }
            if not lint_ok:
                # Lint failure is non-blocking — report but don't fail QA
                report["issues"].append(f"Lint warnings: {lint_out[-150:]}")
                logger.info("[QA_LINT] warnings found")
            else:
                logger.info("[QA_LINT] PASS")

        # ── 3. Visual review ───────────────────────────────────────────────────
        await self._send(task, "Capturing screenshot and inspecting page…")
        visual = await self._reviewer.review(url, project_path)
        report["visual"] = visual
        report["issues"].extend(visual.get("issues", []))
        logger.info("[QA_VISUAL] passed=%s issues=%d", visual.get("passed"), len(visual.get("issues", [])))

        # ── Overall result ─────────────────────────────────────────────────────
        critical = [i for i in report["issues"] if "[CRITICAL]" in i or "Build failed" in i]
        report["passed"] = not critical

        if report["passed"]:
            logger.info("[QA_PASS] url=%s", url)
        else:
            logger.warning("[QA_FAIL] url=%s critical=%d", url, len(critical))

        return report

    # ── npm helpers ────────────────────────────────────────────────────────────

    async def _run_npm_script(
        self, script: str, cwd: Path, timeout: float
    ) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "run", script,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return False, f"npm run {script} timed out after {timeout:.0f}s"
            output = raw.decode(errors="replace")
            return (proc.returncode == 0), output
        except Exception as exc:
            return False, str(exc)

    def _has_build_script(self, project_path: Path) -> bool:
        return self._has_npm_script(project_path, "build")

    def _has_lint_script(self, project_path: Path) -> bool:
        return self._has_npm_script(project_path, "lint")

    @staticmethod
    def _has_npm_script(project_path: Path, name: str) -> bool:
        import json
        pkg = project_path / "package.json"
        if not pkg.exists():
            return False
        try:
            data = json.loads(pkg.read_text("utf-8"))
            return name in data.get("scripts", {})
        except Exception:
            return False

    @staticmethod
    async def _send(task: AgentTask, message: str) -> None:
        if task.ws_send_fn is None:
            return
        try:
            await task.ws_send_fn({
                "type": "agent_progress",
                "task_id": task.task_id,
                "message": message,
                "progress_pct": task.progress_pct,
            })
        except Exception:
            pass

    # ── Critique formatter ─────────────────────────────────────────────────────

    @staticmethod
    def format_critique(report: dict) -> str:
        """Return a one-paragraph critique suitable for AutoDebugger input."""
        issues = report.get("issues", [])
        if not issues:
            return "All QA checks passed — no issues found."

        build_info = report.get("build", {})
        visual_info = report.get("visual", {})

        parts = []
        if not build_info.get("passed") and build_info.get("ran"):
            parts.append(f"Build error: {build_info.get('output', '')[-200:]}")
        if issues:
            parts.append("Issues found: " + "; ".join(issues[:5]))

        return " | ".join(parts) or "Unknown QA failure."
