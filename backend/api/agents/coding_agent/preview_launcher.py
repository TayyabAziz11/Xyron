from __future__ import annotations

"""
PreviewLauncher — open browser and VS Code on the Windows host from WSL2.

On WSL2 we cannot call xdg-open or the Linux ``code`` binary to reach the
Windows desktop.  Instead we delegate to ``powershell.exe`` and ``code.exe``
which are in the Windows PATH and are accessible from WSL2.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PreviewLauncher:
    """Open project previews and editors on the Windows host."""

    # ── Browser ───────────────────────────────────────────────────────────────

    async def open_browser_preview(self, url: str) -> bool:
        """Open *url* in the default Windows browser from WSL2.

        Uses ``powershell.exe -Command "Start-Process '<url>'"`` which works
        even when the dev server is bound to localhost inside the WSL2 VM
        (Windows automatically maps WSL localhost → 127.0.0.1).

        Returns True if the PowerShell command exited successfully.
        """
        # Sanitise URL
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"

        cmd = ["powershell.exe", "-Command", f"Start-Process '{url}'"]
        logger.info("[PREVIEW_LAUNCHER] opening browser: %s", url)

        success = await self._run_silent(cmd, timeout=10.0)
        if not success:
            # Fallback: try xdg-open (works on native Linux, may fail in pure WSL2)
            logger.info("[PREVIEW_LAUNCHER] powershell fallback — trying xdg-open")
            success = await self._run_silent(["xdg-open", url], timeout=5.0)

        if success:
            logger.info("[PREVIEW_OPENED] url=%s", url)
        else:
            logger.warning("[PREVIEW_LAUNCHER] could not open browser for %s", url)
        return success

    # ── VS Code ───────────────────────────────────────────────────────────────

    async def open_in_vscode(self, project_path: Path) -> bool:
        """Open *project_path* in VS Code.

        Tries ``code.exe`` first (Windows VS Code accessible from WSL2), then
        falls back to the Linux ``code`` CLI.

        Returns True if a VS Code launch command succeeded.
        """
        path_str = str(project_path)
        # WSL2: convert to Windows-side path so code.exe can understand it.
        # e.g. /mnt/c/Xyron Projects/my-app → C:\Xyron Projects\my-app
        windows_path = self._to_windows_path(path_str)

        logger.info("[PREVIEW_LAUNCHER] opening VS Code at %s", path_str)

        # Prefer code.exe (Windows VS Code from WSL2)
        if windows_path and await self._run_silent(["code.exe", windows_path], timeout=10.0):
            logger.info("[PREVIEW_LAUNCHER] VS Code opened via code.exe")
            return True

        # Fallback: Linux code CLI
        if await self._run_silent(["code", path_str], timeout=10.0):
            logger.info("[PREVIEW_LAUNCHER] VS Code opened via code")
            return True

        logger.warning("[PREVIEW_LAUNCHER] could not open VS Code")
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _run_silent(cmd: list[str], timeout: float = 10.0) -> bool:
        """Run *cmd* and return True if it exits 0, False otherwise."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return False

    @staticmethod
    def _to_windows_path(wsl_path: str) -> str | None:
        """Convert a WSL2 ``/mnt/c/…`` path to ``C:\\…`` for Windows tools.

        Returns None if the path is not under /mnt/.
        """
        if wsl_path.startswith("/mnt/"):
            parts = wsl_path[len("/mnt/"):].split("/", 1)
            drive = parts[0].upper()
            rest = parts[1].replace("/", "\\") if len(parts) > 1 else ""
            return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
        return None
