from __future__ import annotations

"""
FolderManager — creates and manages project directories on the filesystem.

Workspace priority:
  1. /mnt/c/Xyron Projects/   (WSL2 mount of C:\\Xyron Projects)
  2. ~/XyronProjects/          (fallback if C drive not accessible)

Safety guarantee: never silently overwrites an existing project folder.
If the target name already exists a timestamped suffix is appended instead.
"""

import asyncio
import glob as _glob
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FolderManager:
    # Legacy fallbacks (kept for backwards compat)
    WORKSPACE = Path("/mnt/c/Xyron Projects")
    FALLBACK_WORKSPACE = Path.home() / "XyronProjects"

    # ── Workspace resolution ───────────────────────────────────────────────────

    def get_workspace(self) -> Path:
        """Return the best writable workspace path.

        Priority:
          1. Windows Desktop  — /mnt/c/Users/*/Desktop/Xyron Projects
          2. C:\\Xyron Projects — /mnt/c/Xyron Projects
          3. Linux home fallback — ~/XyronProjects
        """
        # 1. Windows Desktop
        desktop_globs = _glob.glob("/mnt/c/Users/*/Desktop")
        for desktop_str in sorted(desktop_globs):
            desktop = Path(desktop_str)
            # Skip system accounts
            if any(skip in desktop_str for skip in ("All Users", "Default", "Public")):
                continue
            workspace = desktop / "Xyron Projects"
            try:
                workspace.mkdir(parents=True, exist_ok=True)
                probe = workspace / ".xyron_probe"
                probe.touch()
                probe.unlink()
                logger.info("[FOLDER_MANAGER] using Windows Desktop workspace: %s", workspace)
                return workspace
            except OSError:
                continue

        # 2. C:\Xyron Projects
        if self.WORKSPACE.exists():
            try:
                probe = self.WORKSPACE / ".xyron_probe"
                probe.touch()
                probe.unlink()
                logger.info("[FOLDER_MANAGER] using legacy C-drive workspace: %s", self.WORKSPACE)
                return self.WORKSPACE
            except OSError:
                logger.warning("[FOLDER_MANAGER] primary workspace not writable — using fallback")

        # 3. Linux home
        logger.info("[FOLDER_MANAGER] falling back to Linux home: %s", self.FALLBACK_WORKSPACE)
        self.FALLBACK_WORKSPACE.mkdir(parents=True, exist_ok=True)
        return self.FALLBACK_WORKSPACE

    # ── Name normalisation ─────────────────────────────────────────────────────

    def safe_name(self, goal: str) -> str:
        """Convert a natural-language goal into a valid filesystem folder name.

        Example: "Build me a clothing website!" → "clothing-website"
        """
        # Strip leading verb phrases like "build", "create", "make me a", etc.
        cleaned = re.sub(
            r"^(build|create|make|generate|design|write|develop|set up|setup)(\s+me)?\s+(an?\s+)?",
            "",
            goal.strip(),
            flags=re.IGNORECASE,
        )
        # Lower-case
        cleaned = cleaned.lower()
        # Replace non-alphanumeric runs with a hyphen
        cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
        # Strip leading/trailing hyphens and collapse duplicates
        cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
        # Limit length
        cleaned = cleaned[:60]
        return cleaned or "my-project"

    # ── Folder creation ────────────────────────────────────────────────────────

    async def create_project_folder(self, name: str) -> Path:
        """Create and return the project folder.

        If a folder with *name* already exists, appends a timestamp suffix
        (``name-20260630-143022``) rather than overwriting or raising.
        """
        workspace = self.get_workspace()
        candidate = workspace / name

        if candidate.exists():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            candidate = workspace / f"{name}-{ts}"
            logger.warning(
                "[FOLDER_MANAGER] original path exists — using timestamped path %s", candidate
            )

        await asyncio.to_thread(candidate.mkdir, parents=True, exist_ok=False)
        logger.info("[FOLDER_MANAGER] created project folder %s", candidate)
        return candidate

    # ── Backup ────────────────────────────────────────────────────────────────

    async def backup_if_exists(self, path: Path) -> Optional[Path]:
        """If *path* exists, rename it to a backup path and return that path.

        Returns ``None`` if no backup was needed.
        """
        if not path.exists():
            return None

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.parent / f"{path.name}-backup-{ts}"

        def _do_rename() -> None:
            path.rename(backup_path)

        await asyncio.to_thread(_do_rename)
        logger.info("[FOLDER_MANAGER] backed up %s → %s", path, backup_path)
        return backup_path
