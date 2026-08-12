"""
Phase 8 — Explorer Skill.

Human-like File Explorer navigation for:
  - "open E drive"           → open_drive  {drive: "E"}
  - "open python folder"     → smart_open  {query: "python", type: "folder"}
  - "open E drive + python"  → smart_open  {query: "python", drive: "E", type: "folder"}
  - "open E:\\ directory"    → open_directory {path: "E:\\"}

Fast path (Phase 4):
  1. FS_INDEX lookup → resolve exact path in <5ms
  2. Open Explorer at exact path
  3. Verify with Shell.Application
  Fallback to UI search if FS_INDEX misses.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Sequence

from operator_mode.skills.base_skill import BaseSkill
from operator_mode.operator_types import OperatorAction
import operator_mode.operator_actions as A

logger = logging.getLogger(__name__)

# Type-word suffixes that should NOT be part of a search query.
_SUFFIX_TYPE_RE = re.compile(
    r'\s+(?P<suffix>folder|directory|dir|file|document)\s*$',
    re.IGNORECASE,
)


def _sanitize_query(query: str, obj_type: str) -> tuple[str, str]:
    """
    Strip object-type suffix from query and return (clean_query, inferred_type).
    'python folder' → ('python', 'folder')
    'resume file'   → ('resume', 'file')
    'python'        → ('python', obj_type)
    """
    m = _SUFFIX_TYPE_RE.search(query)
    if m:
        suffix = m.group("suffix").lower()
        clean  = _SUFFIX_TYPE_RE.sub("", query).strip()
        inferred = "folder" if suffix in ("folder", "directory", "dir") else "file"
        logger.info("[FILE_OBJECT_PARSE] raw=%r query=%r type=%r drive=implicit (suffix_stripped)",
                    query, clean, inferred)
        return clean, inferred
    return query, obj_type


def _extract_drive(params: dict[str, Any]) -> str:
    """Derive drive letter from params regardless of tool name shape."""
    if params.get("drive"):
        return str(params["drive"]).upper().rstrip(":\\")
    if params.get("path"):
        m = re.match(r'^([A-Za-z]):', str(params["path"]))
        if m:
            return m.group(1).upper()
    return ""


def _wsl_to_win(wsl_path: str) -> str:
    """Convert /mnt/e/Python/Projects → E:\\Python\\Projects."""
    parts = Path(wsl_path).parts  # ('/', 'mnt', 'e', 'Python', ...)
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        drive = parts[2].upper()
        rest = "\\".join(parts[3:]) if len(parts) > 3 else ""
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return wsl_path  # already a Windows path or unknown format


class ExplorerSkill(BaseSkill):

    async def build_actions(
        self, params: dict[str, Any], trace_id: str
    ) -> Sequence[OperatorAction]:
        raw_query = (params.get("query") or "").strip()
        obj_type  = (params.get("type") or "any").lower()
        drive     = _extract_drive(params)

        # Defensive sanitization: strip type-word suffixes that leaked into query
        query, obj_type = _sanitize_query(raw_query, obj_type)
        if query != raw_query:
            logger.info("[FILE_OBJECT_PARSE] raw=%r query=%r type=%r drive=%r",
                        raw_query, query, obj_type, drive or "any")

        drive_path = f"{drive}:\\" if drive else ""

        logger.info("[TRACE %s] [SKILL_START] skill=ExplorerSkill drive=%r query=%r type=%r",
                    trace_id, drive, query, obj_type)

        actions: list[OperatorAction] = []

        # ── Phase 4: FS_INDEX fast path ───────────────────────────────────────
        fs_hit_path: str | None = None
        if query:
            try:
                from api.services.fs_index import fs_index
                type_filter = obj_type if obj_type in ("file", "folder") else None
                results = fs_index.search(
                    query, type_filter=type_filter, drive=drive or None, limit=3
                )
                if results:
                    fs_hit_path = str(results[0])
                    win_path    = _wsl_to_win(fs_hit_path)
                    folder_name = results[0].name
                    logger.info("[FS_INDEX_FAST_PATH] query=%r drive=%r", query, drive)
                    logger.info("[FS_INDEX_FAST_HIT] path=%s win=%s", fs_hit_path, win_path)

                    # Open Explorer at the exact resolved path
                    actions.append(A.powershell_cmd(
                        f"Open {win_path} in File Explorer",
                        script=f"Start-Process explorer.exe '{win_path}'",
                        wait_ms=2000,
                    ))

                    # Verify: Shell.Application check for an Explorer window with the path
                    safe_name = folder_name.replace("'", "\\'")
                    verify_script = (
                        "$sh = New-Object -ComObject Shell.Application; "
                        "$wins = @($sh.Windows()); "
                        f"$found = $wins | Where-Object {{ "
                        f"  try {{ $_.Document.Folder.Self.Path -like '*{safe_name}*' }} "
                        f"  catch {{ $false }} "
                        f"}}; "
                        "if ($found) { 'VERIFIED' } else { 'PENDING' }"
                    )
                    logger.info("[EXPLORER_VERIFY_START] query=%r expected=%s", query, folder_name)
                    actions.append(A.powershell_cmd(
                        f"Verify Explorer opened {folder_name}",
                        script=verify_script,
                        wait_ms=600,
                    ))
                else:
                    logger.info("[FS_INDEX_FAST_MISS] query=%r drive=%r — using UI fallback",
                                query, drive)
            except Exception as exc:
                logger.debug("[FS_INDEX_FAST_MISS] query=%r error=%s — using UI fallback",
                             query, exc)

        # ── Fallback: UI-driven Explorer search ──────────────────────────────
        if fs_hit_path is None:
            logger.info("[OPERATOR_UI_FALLBACK] query=%r drive=%r", query, drive)
            target_path = drive_path or "C:\\"
            logger.info("[TRACE %s] [EXPLORER_NAVIGATE] drive=%r path=%s",
                        trace_id, drive, target_path)
            actions.append(A.powershell_cmd(
                f"Open File Explorer at {target_path}",
                script=f"Start-Process explorer.exe '{target_path}'",
                wait_ms=1800,
            ))

            if query:
                logger.info("[TRACE %s] [EXPLORER_OPEN_FOLDER] query=%r", trace_id, query)
                actions.append(A.hotkey("Focus Explorer search box", "Ctrl", "f", wait_ms=500))
                actions.append(A.type_text(query, description=f"Search for '{query}'", wait_ms=600))
                actions.append(A.press("Return", description="Submit search", wait_ms=1200))
                actions.append(A.press("Return", description="Open first result", wait_ms=500))

        return actions

    async def get_success_response(self, params: dict[str, Any]) -> str:
        raw_query = (params.get("query") or "").strip()
        obj_type  = (params.get("type") or "any").lower()
        query, _  = _sanitize_query(raw_query, obj_type)
        drive     = _extract_drive(params)
        logger.info("[EXPLORER_VERIFY_SUCCESS] query=%r drive=%r", query, drive)
        if drive and query:
            return f"Opened {query.title()} on {drive} drive."
        if drive:
            return f"Opened {drive} drive in File Explorer."
        if query:
            return f"Opened the {query.title()} folder."
        return "File Explorer is open."

    async def get_failure_response(self, params: dict[str, Any]) -> str:
        raw_query = (params.get("query") or "").strip()
        obj_type  = (params.get("type") or "any").lower()
        query, _  = _sanitize_query(raw_query, obj_type)
        drive     = _extract_drive(params)
        logger.info("[EXPLORER_VERIFY_FAIL] query=%r drive=%r", query, drive)
        if drive and query:
            return f"I searched {drive} drive but couldn't confirm {query.title()} opened."
        if drive:
            return f"I couldn't open {drive} drive in File Explorer."
        return "I couldn't open File Explorer."
