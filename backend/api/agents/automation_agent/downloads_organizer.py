from __future__ import annotations

"""
DownloadsOrganizer — categorize and move files in the Downloads folder
into labelled subfolders.

Creates a timestamped JSON undo manifest so every move can be reversed.
Uses shutil.move (not os.rename) for cross-device safety.
send2trash is NOT used here — no files are deleted, only moved.
Always requests approval before reorganizing.

Log tags: [CLEANER_APPROVAL_REQUIRED] [CLEANER_REPORT_CREATED]
"""

import asyncio
import glob
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

try:
    import humanize as _humanize
    _HAS_HUMANIZE = True
except ImportError:
    _HAS_HUMANIZE = False

from api.agents.agent_types import AgentStatus, AgentTask, StepResult

logger = logging.getLogger("api.agents.automation_agent.downloads_organizer")


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} GiB"


class DownloadsOrganizer:
    """Move Downloads folder files into category subfolders."""

    CATEGORIES: dict[str, list[str]] = {
        "Images":      [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".ico", ".raw"],
        "Videos":      [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"],
        "Documents":   [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".odt", ".rtf", ".csv"],
        "Archives":    [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".cab", ".iso"],
        "Audio":       [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus"],
        "Code":        [".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".xml",
                        ".yaml", ".yml", ".sh", ".bat", ".ps1", ".sql", ".rs", ".go", ".java"],
        "Executables": [".exe", ".msi", ".dmg", ".deb", ".rpm", ".appx", ".appimage", ".pkg"],
        "Fonts":       [".ttf", ".otf", ".woff", ".woff2", ".eot"],
        "Ebooks":      [".epub", ".mobi", ".azw", ".azw3", ".fb2"],
    }

    # ── Path helpers ──────────────────────────────────────────────────────────

    def get_downloads_path(self) -> Path:
        """Return the first populated Windows Downloads directory, else ~/Downloads."""
        for pattern in ["/mnt/c/Users/*/Downloads"]:
            for match in glob.glob(pattern):
                p = Path(match)
                try:
                    if p.exists() and any(p.iterdir()):
                        return p
                except (PermissionError, OSError):
                    continue
        fallback = Path.home() / "Downloads"
        fallback.mkdir(exist_ok=True)
        return fallback

    # ── Scan ──────────────────────────────────────────────────────────────────

    async def scan_downloads(self) -> dict[str, list[dict]]:
        """
        Returns {category: [{name, path, size_bytes}], "Uncategorized": [...]}.
        Only top-level files are scanned (not sub-directories).
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._scan_sync)

    def _scan_sync(self) -> dict[str, list[dict]]:
        downloads = self.get_downloads_path()
        result: dict[str, list[dict]] = {cat: [] for cat in self.CATEGORIES}
        result["Uncategorized"] = []

        for entry in downloads.iterdir():
            try:
                if entry.is_dir():
                    continue  # skip existing subdirs
                suffix = entry.suffix.lower()
                size = entry.stat().st_size
                info = {"name": entry.name, "path": str(entry), "size_bytes": size}

                placed = False
                for cat, exts in self.CATEGORIES.items():
                    if suffix in exts:
                        result[cat].append(info)
                        placed = True
                        break
                if not placed:
                    result["Uncategorized"].append(info)
            except (PermissionError, OSError):
                continue

        return result

    # ── Organize ──────────────────────────────────────────────────────────────

    async def organize(self, downloads_path: Path, task: AgentTask) -> StepResult:
        """
        Request approval, then move files into category subfolders.
        Returns WAITING_APPROVAL result immediately; call execute_organize after approval.
        """
        scan = await self.scan_downloads()
        total_files = sum(len(v) for v in scan.values())

        if total_files == 0:
            return StepResult(success=True, output="Downloads folder is already empty.")

        category_counts = {cat: len(files) for cat, files in scan.items() if files}

        logger.info(
            "[CLEANER_APPROVAL_REQUIRED] action=organize_downloads files=%d", total_files
        )

        if task.ws_send_fn:
            try:
                await task.ws_send_fn({
                    "type": "approval_required",
                    "action": "organize_downloads",
                    "summary": (
                        f"I'll organize {total_files} files in {downloads_path.name} "
                        "into category subfolders. Proceed?"
                    ),
                    "details": category_counts,
                })
            except Exception as exc:
                logger.warning("[DOWNLOADS_ORGANIZER] ws_send_fn error: %r", exc)

        task.status = AgentStatus.WAITING_APPROVAL
        return StepResult(
            success=True,
            output=(
                f"Waiting for approval to organize {total_files} files "
                f"in {downloads_path}."
            ),
            needs_approval=True,
            approval_prompt=(
                f"Organize {total_files} Downloads files into category subfolders?"
            ),
            data={"total_files": total_files, "categories": category_counts},
        )

    async def execute_organize(
        self,
        downloads_path: Path,
        task: AgentTask,
    ) -> StepResult:
        """
        Perform the file organization (call only after user approval).
        Creates a timestamped undo manifest.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._organize_sync, downloads_path, task
        )

    def _organize_sync(self, downloads_path: Path, task: AgentTask) -> StepResult:
        scan = self._scan_sync()
        manifest: list[dict] = []
        moved = 0
        errors: list[str] = []

        for cat, files in scan.items():
            if not files:
                continue

            # Skip "Uncategorized" — leave those in place
            if cat == "Uncategorized":
                continue

            cat_dir = downloads_path / cat
            cat_dir.mkdir(exist_ok=True)

            for file_info in files:
                src = Path(file_info["path"])
                if not src.exists():
                    continue

                dst = cat_dir / src.name
                # Resolve name collisions
                if dst.exists():
                    stem, suffix = src.stem, src.suffix
                    counter = 1
                    while dst.exists():
                        dst = cat_dir / f"{stem}_{counter}{suffix}"
                        counter += 1

                try:
                    shutil.move(str(src), str(dst))
                    manifest.append({"from": str(src), "to": str(dst)})
                    moved += 1
                except (OSError, shutil.Error) as exc:
                    errors.append(f"{src}: {exc}")
                    logger.warning(
                        "[DOWNLOADS_ORGANIZER] move failed src=%s err=%r", src, exc
                    )

        # Save undo manifest
        manifest_path = self._save_manifest(manifest, downloads_path)
        if manifest_path:
            logger.info("[CLEANER_REPORT_CREATED] path=%s", manifest_path)

        msg = f"Organized {moved:,} files into category subfolders."
        if errors:
            msg += f" {len(errors)} file(s) could not be moved."
        if manifest_path:
            msg += f" Undo manifest: {manifest_path.name}."

        return StepResult(
            success=True,
            output=msg,
            data={
                "moved": moved,
                "errors": errors,
                "manifest_path": str(manifest_path) if manifest_path else None,
            },
        )

    def _save_manifest(self, manifest: list[dict], base_path: Path) -> Optional[Path]:
        try:
            reports_dir = Path.home() / ".xyron" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            path = reports_dir / f"organize_manifest_{ts}.json"
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return path
        except Exception as exc:
            logger.warning("[DOWNLOADS_ORGANIZER] manifest save failed: %r", exc)
            return None

    # ── Undo ──────────────────────────────────────────────────────────────────

    async def undo_organize(self, manifest_path: Path) -> bool:
        """Restore files to their original locations using a saved manifest."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._undo_sync, manifest_path)

    def _undo_sync(self, manifest_path: Path) -> bool:
        try:
            manifest: list[dict] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(
                "[DOWNLOADS_ORGANIZER] cannot read manifest %s: %r", manifest_path, exc
            )
            return False

        success_count = 0
        for entry in manifest:
            src = Path(entry.get("to", ""))   # where it was moved TO
            dst = Path(entry.get("from", "")) # where it originally came FROM
            if not src.exists():
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                success_count += 1
            except Exception as exc:
                logger.warning(
                    "[DOWNLOADS_ORGANIZER] undo failed src=%s err=%r", src, exc
                )

        logger.info(
            "[DOWNLOADS_ORGANIZER] undo complete restored=%d total=%d",
            success_count,
            len(manifest),
        )
        return success_count > 0
