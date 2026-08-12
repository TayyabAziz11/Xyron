from __future__ import annotations

"""
CodeGenerator — write project files to disk and fix syntax errors via LLM.

Files come from the ProjectPlanner's output dict.  Each file is written with
UTF-8 encoding.  Parent directories are created automatically.  Progress is
streamed to the task WS callback.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from api.agents.agent_types import AgentTask
from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

_WsSendFn = Optional[Callable[[dict[str, Any]], Awaitable[bool]]]


class CodeGenerator:
    """Write, generate, and repair source files for a project."""

    # ── Bulk file writing ──────────────────────────────────────────────────────

    async def generate_files(
        self,
        project_plan: dict,
        project_path: Path,
        task: AgentTask,
    ) -> list[Path]:
        """Write all files listed in *project_plan["files"]* to *project_path*.

        Each entry in ``project_plan["files"]`` must be a dict with at least:
        - ``"path"``    — relative path inside the project (e.g. ``"src/App.tsx"``)
        - ``"content"`` — file content as a string

        Returns the list of absolute paths that were successfully created.
        Updates *task.progress_pct* as files are written.
        """
        files: list[dict] = project_plan.get("files", [])
        if not files:
            logger.warning("[CODE_GENERATOR] project_plan contains no files")
            return []

        created: list[Path] = []
        total = len(files)

        for i, file_spec in enumerate(files):
            rel_path: str = file_spec.get("path", "")
            content: str = file_spec.get("content", "")

            if not rel_path:
                logger.warning("[CODE_GENERATOR] skipping file spec with no path at index %d", i)
                continue

            abs_path = project_path / rel_path

            # Create parent directories.
            await asyncio.to_thread(abs_path.parent.mkdir, parents=True, exist_ok=True)

            # Write file.
            try:
                await asyncio.to_thread(abs_path.write_text, content, "utf-8")
                created.append(abs_path)
                logger.debug("[CODE_GENERATOR] wrote %s (%d chars)", rel_path, len(content))
            except OSError as exc:
                logger.error("[CODE_GENERATOR] failed to write %s: %s", abs_path, exc)
                continue

            # Update progress.
            if task.ws_send_fn:
                task.progress_pct = int((i + 1) / total * 60)  # files phase = 0→60%
                await self._send(task, f"[file] created {rel_path}")

        logger.info("[FILES_WRITTEN] count=%d path=%s", len(created), project_path)
        return created

    # ── Single component generation ────────────────────────────────────────────

    async def generate_component(self, name: str, description: str, stack: str) -> str:
        """Use the LLM to generate a single self-contained React component.

        Args:
            name:        PascalCase component name (e.g. ``"HeroSection"``).
            description: Plain-English description of what the component should do.
            stack:       Stack key (e.g. ``"vite-react-tailwind"``).

        Returns:
            Complete TypeScript source code string for the component, or an
            empty string on failure.
        """
        use_tailwind = "tailwind" in stack.lower()
        styling_note = (
            "Use Tailwind CSS utility classes for all styling."
            if use_tailwind
            else "Use inline styles or a CSS module."
        )

        system_prompt = (
            "You are an expert React / TypeScript developer.\n"
            f"Write a complete, self-contained TypeScript React component.\n"
            f"Styling: {styling_note}\n"
            "Requirements:\n"
            "- Functional component with typed props interface.\n"
            "- export default at the bottom.\n"
            "- No placeholder comments — generate real content.\n"
            "- Return ONLY the TypeScript source code, no markdown fences."
        )

        user_msg = (
            f"Component name: {name}\n"
            f"Description: {description}\n"
            "Generate the complete component now."
        )

        try:
            result = openai_client.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                model="gpt-4o-mini",
            )
            return result or ""
        except Exception as exc:
            logger.error("[CODE_GENERATOR] generate_component failed: %s", exc)
            return ""

    # ── Syntax error repair ────────────────────────────────────────────────────

    async def fix_syntax_error(self, file_path: Path, error: str) -> bool:
        """Ask the LLM to fix a syntax error in *file_path*.

        Creates a ``.bak`` backup before writing the fixed version.
        Returns True if a fix was applied, False on failure.
        """
        if not file_path.exists():
            logger.warning("[CODE_GENERATOR] fix_syntax_error — file not found: %s", file_path)
            return False

        try:
            original_content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("[CODE_GENERATOR] cannot read %s: %s", file_path, exc)
            return False

        system_prompt = (
            "You are a TypeScript/JavaScript expert.\n"
            "Fix the syntax error in the file below.\n"
            "Return ONLY the corrected file content — no explanations, no markdown fences."
        )
        user_msg = (
            f"File: {file_path.name}\n"
            f"Error: {error}\n\n"
            f"Current content:\n{original_content[:3000]}"
        )

        try:
            fixed = openai_client.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                model="gpt-4o-mini",
            )
        except Exception as exc:
            logger.error("[CODE_GENERATOR] fix_syntax_error LLM call failed: %s", exc)
            return False

        if not fixed:
            return False

        # Backup original.
        backup = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            await asyncio.to_thread(backup.write_text, original_content, "utf-8")
            await asyncio.to_thread(file_path.write_text, fixed, "utf-8")
            logger.info("[CODE_GENERATOR] syntax fix applied to %s (backup at %s)", file_path, backup)
            return True
        except OSError as exc:
            logger.error("[CODE_GENERATOR] could not write fixed file: %s", exc)
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
