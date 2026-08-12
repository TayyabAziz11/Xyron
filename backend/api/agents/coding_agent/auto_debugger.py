from __future__ import annotations

"""
AutoDebugger — iterative error-fix-verify loop for the Coding Builder Agent.

Attempts up to MAX_FIX_ATTEMPTS rounds of:
  1. analyse error via ErrorAnalyzer
  2. fetch LLM fix suggestion
  3. write the fix to the affected file
  4. re-run the dev server and check the port
"""

import asyncio
import logging
from pathlib import Path

from api.agents.agent_types import AgentTask
from api.agents.coding_agent.error_analyzer import ErrorAnalyzer
from api.agents.coding_agent.terminal_runner import TerminalRunner

logger = logging.getLogger(__name__)


class AutoDebugger:
    """Auto-fix errors found during project build / dev-server startup."""

    MAX_FIX_ATTEMPTS = 3

    def __init__(self) -> None:
        self._analyzer = ErrorAnalyzer()
        self._runner = TerminalRunner()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def debug_cycle(
        self,
        error: dict,
        project_path: Path,
        task: AgentTask,
    ) -> bool:
        """Attempt to automatically fix *error* in *project_path*.

        Runs up to :attr:`MAX_FIX_ATTEMPTS` cycles of analyse → fix → re-verify.

        Returns True if the project eventually compiles/runs without the error,
        False if all attempts were exhausted.
        """
        for attempt in range(1, self.MAX_FIX_ATTEMPTS + 1):
            logger.info(
                "[AUTO_DEBUGGER] fix attempt %d/%d  error_type=%s",
                attempt,
                self.MAX_FIX_ATTEMPTS,
                error.get("type", "unknown"),
            )
            await self._send(task, f"Auto-debug attempt {attempt}/{self.MAX_FIX_ATTEMPTS}…")

            # 1. Determine target file
            rel_file: str = error.get("file", "")
            target: Path | None = (project_path / rel_file) if rel_file else None

            file_content = ""
            if target and target.exists():
                try:
                    file_content = target.read_text(encoding="utf-8")
                except OSError:
                    pass

            # 2. Get fix suggestion from LLM
            suggestion = await self._analyzer.get_fix_suggestion(error, file_content)
            if not suggestion:
                logger.warning("[AUTO_DEBUGGER] no suggestion from LLM on attempt %d", attempt)
                continue

            logger.info("[AUTO_DEBUGGER] LLM suggestion (first 120 chars): %s", suggestion[:120])

            # 3. Apply fix to the affected file (if we know which one it is)
            fixed = False
            if target and target.exists() and file_content:
                fixed = await self.apply_fix(target, file_content, suggestion)
                if fixed:
                    logger.info("[AUTO_FIX_APPLIED] fix=%s", suggestion[:80])
                    await self._send(task, f"[auto-fix] applied: {suggestion[:80]}")

            # 4. Quick build-check to see if the error is gone
            build_ok = await self._verify_build(project_path)
            if build_ok:
                logger.info("[AUTO_DEBUGGER] error resolved after attempt %d", attempt)
                return True

            if not fixed:
                # Nothing was changed — no point retrying the same error.
                logger.warning("[AUTO_DEBUGGER] fix could not be applied — aborting cycle")
                break

        logger.warning("[AUTO_DEBUGGER] exhausted fix attempts for error: %s", error.get("message", ""))
        return False

    async def apply_fix(self, file_path: Path, original: str, fix: str) -> bool:
        """Write *fix* to *file_path* after backing up *original*.

        The *fix* string is expected to be the LLM's plain-text suggestion.
        If the suggestion is a full file rewrite (>50 chars and looks like code)
        we replace the file entirely; otherwise we append the suggestion as a
        comment so a human can review it later.

        Returns True if the file was modified.
        """
        if not file_path.exists():
            return False

        backup = file_path.with_suffix(file_path.suffix + ".autofixbak")
        try:
            backup.write_text(original, encoding="utf-8")
        except OSError as exc:
            logger.warning("[AUTO_DEBUGGER] could not create backup: %s", exc)

        # Heuristic: if the suggestion is a full source file, use it directly;
        # otherwise wrap it as a comment block appended to the bottom.
        looks_like_code = (
            len(fix) > 80
            and any(tok in fix for tok in ("import ", "export ", "const ", "function ", "return "))
        )

        try:
            if looks_like_code:
                file_path.write_text(fix, encoding="utf-8")
            else:
                amended = (
                    original
                    + f"\n\n// [Xyron AutoFix] Suggested fix:\n// {fix.replace(chr(10), chr(10) + '// ')}\n"
                )
                file_path.write_text(amended, encoding="utf-8")
            return True
        except OSError as exc:
            logger.error("[AUTO_DEBUGGER] apply_fix write failed: %s", exc)
            return False

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _verify_build(self, project_path: Path) -> bool:
        """Run a quick ``npm run build`` and return True on success."""
        try:
            rc, stdout, stderr = await self._runner.run_command(
                ["npm", "run", "build"],
                cwd=project_path,
                timeout=60.0,
            )
            return rc == 0
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False

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
