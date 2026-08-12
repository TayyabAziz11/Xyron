from __future__ import annotations

"""
GitManager — async git operations for Coding Builder Agent projects.

All subprocess calls use asyncio.create_subprocess_exec so they never
block the event loop.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class GitManager:
    """Thin async wrapper around the git CLI."""

    # ── Internal helper ────────────────────────────────────────────────────────

    async def _run(
        self,
        args: list[str],
        cwd: Path,
        check: bool = True,
    ) -> tuple[int, str, str]:
        """Run ``git <args>`` in *cwd*.

        Returns (returncode, stdout, stderr).  Raises RuntimeError if
        *check* is True and the process exits with a non-zero code.
        """
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        raw_out, raw_err = await proc.communicate()
        stdout = raw_out.decode(errors="replace").strip()
        stderr = raw_err.decode(errors="replace").strip()

        if check and proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr or stdout}"
            )
        return proc.returncode, stdout, stderr

    # ── Public API ─────────────────────────────────────────────────────────────

    async def init(self, project_path: Path) -> bool:
        """Run ``git init`` followed by an initial commit.

        Returns True on success.
        """
        try:
            await self._run(["init"], cwd=project_path)
            logger.info("[GIT_MANAGER] git init OK at %s", project_path)

            # Set local user config so commits work even in CI/WSL2 with no
            # global git config.
            await self._run(
                ["config", "user.email", "xyron@local.dev"],
                cwd=project_path,
                check=False,
            )
            await self._run(
                ["config", "user.name", "Xyron Coding Agent"],
                cwd=project_path,
                check=False,
            )

            # Create a .gitignore so node_modules / __pycache__ are excluded.
            gitignore = project_path / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "node_modules/\ndist/\n.env\n__pycache__/\n*.pyc\n.DS_Store\n",
                    encoding="utf-8",
                )

            committed = await self.initial_commit(project_path)
            return committed
        except Exception as exc:
            logger.error("[GIT_MANAGER] init failed: %s", exc)
            return False

    async def initial_commit(
        self,
        project_path: Path,
        message: str = "Initial commit — Xyron generated",
    ) -> bool:
        """Stage all files and create the first commit.

        Returns True on success.
        """
        try:
            await self._run(["add", "-A"], cwd=project_path)
            rc, stdout, stderr = await self._run(
                ["commit", "-m", message],
                cwd=project_path,
                check=False,
            )
            if rc != 0:
                # Nothing to commit is not an error for us.
                if "nothing to commit" in (stdout + stderr).lower():
                    logger.info("[GIT_MANAGER] nothing to commit — skipping initial commit")
                    return True
                logger.error("[GIT_MANAGER] commit failed: %s", stderr or stdout)
                return False
            logger.info("[GIT_MANAGER] initial commit created")
            return True
        except Exception as exc:
            logger.error("[GIT_MANAGER] initial_commit failed: %s", exc)
            return False

    async def get_status(self, project_path: Path) -> str:
        """Return ``git status`` output (porcelain format)."""
        try:
            _, stdout, _ = await self._run(
                ["status", "--short"],
                cwd=project_path,
                check=False,
            )
            return stdout
        except Exception as exc:
            logger.warning("[GIT_MANAGER] get_status failed: %s", exc)
            return ""
