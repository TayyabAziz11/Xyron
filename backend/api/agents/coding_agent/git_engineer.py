from __future__ import annotations

"""
GitEngineer — Phase 4.6 git role module for CodingAgent.

Extends the existing GitManager with checkpoint commits and rollback support.

Workflow:
  1. init()                     — git init + .gitignore + user config
  2. checkpoint("scaffold")     — commit after files written
  3. checkpoint("built")        — commit after successful build
  4. checkpoint("verified")     — commit after visual verification
  5. rollback()                 — restore last good checkpoint on error

Log tags: [GIT_INIT] [GIT_COMMIT] [GIT_ROLLBACK] [GIT_STATUS_CLEAN]
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitEngineer:
    """Async git operations with checkpoint/rollback for the coding pipeline."""

    # ── Internal git runner ────────────────────────────────────────────────────

    async def _git(
        self,
        args: list[str],
        cwd: Path,
        check: bool = False,
    ) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            raw_out, raw_err = await proc.communicate()
            return (
                proc.returncode or 0,
                raw_out.decode(errors="replace").strip(),
                raw_err.decode(errors="replace").strip(),
            )
        except Exception as exc:
            return 1, "", str(exc)

    # ── Initialisation ─────────────────────────────────────────────────────────

    async def init(self, project_path: Path) -> bool:
        """git init + .gitignore + user config.

        Returns True on success.
        """
        rc, _, err = await self._git(["init"], cwd=project_path)
        if rc != 0:
            logger.error("[GIT_INIT] failed: %s", err)
            return False

        # User config (no-op if already set globally)
        await self._git(["config", "user.email", "xyron@local.dev"], cwd=project_path)
        await self._git(["config", "user.name", "Xyron Engineer"], cwd=project_path)

        # .gitignore
        gitignore = project_path / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "node_modules/\ndist/\n.vite/\n.env\n"
                "__pycache__/\n*.pyc\n.DS_Store\n*.bak\n*.autofixbak\n",
                encoding="utf-8",
            )

        logger.info("[GIT_INIT] %s", project_path)
        return True

    # ── Checkpoint ─────────────────────────────────────────────────────────────

    async def checkpoint(self, project_path: Path, label: str) -> Optional[str]:
        """Stage all changes and create a labelled checkpoint commit.

        Returns the short commit SHA on success, None on failure.
        """
        await self._git(["add", "-A"], cwd=project_path)

        msg = f"[Xyron] {label}"
        rc, stdout, stderr = await self._git(
            ["commit", "-m", msg], cwd=project_path
        )

        if rc != 0:
            combined = (stdout + stderr).lower()
            if "nothing to commit" in combined:
                # Return the current HEAD SHA instead
                _, sha, _ = await self._git(
                    ["rev-parse", "--short", "HEAD"], cwd=project_path
                )
                logger.info("[GIT_COMMIT] nothing new to commit at label=%r", label)
                return sha or None
            logger.warning("[GIT_COMMIT] commit failed label=%r err=%s", label, stderr)
            return None

        _, sha, _ = await self._git(
            ["rev-parse", "--short", "HEAD"], cwd=project_path
        )
        logger.info("[GIT_COMMIT] label=%r sha=%s", label, sha)
        return sha

    # ── Rollback ───────────────────────────────────────────────────────────────

    async def rollback(self, project_path: Path) -> bool:
        """Reset working tree to HEAD (the last checkpoint).

        Use this when a fix attempt broke the project — it restores the state
        of the last checkpoint commit so we can retry from a clean slate.
        """
        # Stage everything so git checkout can run cleanly
        await self._git(["add", "-A"], cwd=project_path)
        rc, _, err = await self._git(
            ["checkout", "--", "."], cwd=project_path
        )
        if rc != 0:
            logger.error("[GIT_ROLLBACK] checkout failed: %s", err)
            return False

        # Also clear any untracked generated files
        await self._git(["clean", "-fd"], cwd=project_path)
        logger.info("[GIT_ROLLBACK] reset to HEAD at %s", project_path)
        return True

    # ── Rollback to specific SHA ───────────────────────────────────────────────

    async def rollback_to(self, project_path: Path, sha: str) -> bool:
        """Hard-reset to a specific commit SHA."""
        rc, _, err = await self._git(
            ["reset", "--hard", sha], cwd=project_path
        )
        if rc != 0:
            logger.error("[GIT_ROLLBACK] reset to %s failed: %s", sha, err)
            return False
        await self._git(["clean", "-fd"], cwd=project_path)
        logger.info("[GIT_ROLLBACK] reset to sha=%s", sha)
        return True

    # ── Status ─────────────────────────────────────────────────────────────────

    async def is_clean(self, project_path: Path) -> bool:
        """Return True if the working tree has no uncommitted changes."""
        _, stdout, _ = await self._git(["status", "--short"], cwd=project_path)
        clean = not stdout.strip()
        if clean:
            logger.info("[GIT_STATUS_CLEAN] %s", project_path)
        return clean

    async def get_commit_count(self, project_path: Path) -> int:
        """Return the number of commits in the repository."""
        _, stdout, _ = await self._git(
            ["rev-list", "--count", "HEAD"], cwd=project_path
        )
        try:
            return int(stdout.strip())
        except ValueError:
            return 0

    async def get_log(self, project_path: Path, n: int = 5) -> list[str]:
        """Return last *n* commit messages."""
        _, stdout, _ = await self._git(
            ["log", f"--oneline", f"-{n}"], cwd=project_path
        )
        return [line for line in stdout.splitlines() if line]
