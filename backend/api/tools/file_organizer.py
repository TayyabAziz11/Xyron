"""
File Organizer — sorts loose files in a folder (Desktop, Downloads, ...) into
type-based subfolders (Pictures, Documents, Videos, ...), with duplicate
detection, collision-safe renaming, and full undo support.

Registered tools:
  organize_files         plan + (on confirmation) execute an organize run
  undo_organize_files     reverse the most recent organize run

Design notes:
  • Two-phase: the first call always builds a plan and returns it via the
    confirm_required protocol (same mechanism smart_open already uses —
    see registry.ToolResult.to_sse_action / voice_ws.py pending_confirmation).
    Nothing moves on disk until the caller re-invokes with _confirmed=True.
  • Non-recursive and files-only: subfolders and shortcuts (.lnk/.url) are
    left untouched, so apps and existing project folders are never touched.
  • Every executed run writes an undo manifest to
    ~/.ai-operator/organize_runs/last_run.json before any errors could lose
    the mapping — undo_organize_files reads it back and reverses the moves.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import ToolResult, registry
from .safety import is_safe_path
from .system_tools import _open_in_explorer, _store_last_action
from utils.path_utils import resolve_wsl_path, wsl_to_win

logger = logging.getLogger(__name__)

_RULES_PATH   = Path(__file__).parent / "organize_rules.json"
_MANIFEST_DIR = Path.home() / ".ai-operator" / "organize_runs"
_MANIFEST_LAST = _MANIFEST_DIR / "last_run.json"

_DEFAULT_RULES: Dict[str, Any] = {
    "categories": {
        "Pictures":      [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".tiff", ".tif", ".ico"],
        "Documents":     [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"],
        "Spreadsheets":  [".xls", ".xlsx", ".csv", ".ods"],
        "Presentations": [".ppt", ".pptx", ".odp"],
        "Videos":        [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"],
        "Audio":         [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
        "Archives":      [".zip", ".rar", ".7z", ".tar", ".gz"],
        "Installers":    [".exe", ".msi"],
        "Code":          [".py", ".js", ".ts", ".json", ".html", ".css"],
    },
    "other_folder": "Other",
    "duplicates_folder": "Duplicates",
    "screenshots_folder": "Screenshots",
    "screenshot_name_patterns": ["screenshot", "screen shot", "screen_shot", "screen-shot"],
    "ignore_filenames": ["desktop.ini", "thumbs.db", ".ds_store"],
    "hash_size_cap_bytes": 314572800,  # 300 MB — larger files skip hashing (treated as unique)
}

_rules_cache: Optional[Dict[str, Any]] = None


def _load_rules() -> Dict[str, Any]:
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache
    try:
        _rules_cache = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("[ORGANIZE_FILES] rules file missing/invalid, using built-in defaults", exc_info=True)
        _rules_cache = _DEFAULT_RULES
    return _rules_cache


def _ext_to_category(ext: str, rules: Dict[str, Any]) -> str:
    ext = ext.lower()
    for category, exts in rules["categories"].items():
        if ext in exts:
            return category
    return rules.get("other_folder", "Other")


def _is_screenshot(name: str, rules: Dict[str, Any]) -> bool:
    lowered = name.lower()
    return any(pat in lowered for pat in rules.get("screenshot_name_patterns", []))


def _hash_file(path: Path, cap: int) -> Optional[str]:
    try:
        if path.stat().st_size > cap:
            return None  # too large to hash cheaply — treated as unique below
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _unique_destination(dest: Path) -> Path:
    """Collision-safe destination — 'file.txt' → 'file (2).txt' etc."""
    if not dest.exists():
        return dest
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    n = 2
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


class _Plan:
    def __init__(self, target: Path):
        self.target = target
        # category -> list of (src_path, planned_name)
        self.by_category: Dict[str, List[str]] = {}
        self.duplicates: List[str] = []
        self.skipped: List[str] = []
        self.total = 0

    def add(self, category: str, filename: str) -> None:
        self.by_category.setdefault(category, []).append(filename)
        self.total += 1


def _build_plan(target: Path, rules: Dict[str, Any]) -> _Plan:
    plan = _Plan(target)
    ignore = {n.lower() for n in rules.get("ignore_filenames", [])}
    cap = rules.get("hash_size_cap_bytes", 314572800)
    seen_hashes: Dict[str, str] = {}  # hash -> first filename that owns it

    entries = sorted(
        (e for e in target.iterdir() if e.is_file()),
        key=lambda p: p.name.lower(),
    )
    for entry in entries:
        name = entry.name
        if name.lower() in ignore or name.startswith("."):
            continue
        if entry.suffix.lower() in (".lnk", ".url"):
            continue  # shortcuts point at apps — not "files" the user meant

        digest = _hash_file(entry, cap)
        if digest is not None:
            if digest in seen_hashes:
                plan.duplicates.append(name)
                continue
            seen_hashes[digest] = name

        ext = entry.suffix.lower()
        if ext in rules["categories"].get("Pictures", []) and _is_screenshot(name, rules):
            plan.add(rules.get("screenshots_folder", "Screenshots"), name)
            continue

        category = _ext_to_category(ext, rules)
        plan.add(category, name)

    return plan


def _spoken_plan_summary(plan: _Plan, win_target: str) -> str:
    if plan.total == 0 and not plan.duplicates:
        return f"There's nothing loose to organize in {win_target} — it's already tidy."
    parts = [f"{len(files)} {cat.lower()}" for cat, files in plan.by_category.items()]
    summary = ", ".join(parts) if parts else "no new files"
    extra = f", and {len(plan.duplicates)} duplicate{'s' if len(plan.duplicates) != 1 else ''}" if plan.duplicates else ""
    return (f"I found {plan.total + len(plan.duplicates)} files in {Path(win_target).name}: "
            f"{summary}{extra}. I'll create folders and sort them — shall I proceed?")


def _exec_organize_files(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw_path  = (params.get("path") or "Desktop").strip() or "Desktop"
    confirmed = bool(params.get("_confirmed"))

    wsl_target = resolve_wsl_path(raw_path)
    if wsl_target is None:
        return ToolResult(
            success=False,
            text=f"Unknown location: {raw_path!r}",
            spoken="I couldn't find that location. Try Desktop or Downloads.",
        )

    win_target = wsl_to_win(wsl_target)
    if not is_safe_path(win_target):
        return ToolResult(success=False, text=f"Blocked: {win_target}",
                          spoken="That location is restricted for safety.", error="Blocked path")

    target = Path(wsl_target)
    if not target.exists() or not target.is_dir():
        return ToolResult(success=False, text=f"Not found: {win_target}",
                          spoken="I couldn't find that folder.")

    rules = _load_rules()

    try:
        plan = _build_plan(target, rules)
    except PermissionError:
        return ToolResult(success=False, text="Permission denied.",
                          spoken="I don't have permission to read that folder.")
    except Exception as exc:
        logger.exception("[ORGANIZE_FILES] plan failed")
        return ToolResult(success=False, text=str(exc), spoken="I couldn't scan that folder.", error=str(exc))

    if plan.total == 0 and not plan.duplicates:
        return ToolResult(success=True, text=f"Nothing to organize in {win_target}.",
                          spoken=_spoken_plan_summary(plan, win_target),
                          data={"target": win_target, "moved_by_folder": {}, "duplicates": [], "folders_created": []})

    if not confirmed:
        prompt = _spoken_plan_summary(plan, win_target)
        logger.info("[ORGANIZE_FILES_PLAN] target=%s total=%d duplicates=%d categories=%s",
                    win_target, plan.total, len(plan.duplicates), list(plan.by_category.keys()))
        return ToolResult(
            success=False,
            text=prompt,
            spoken=prompt,
            error="confirm_required",
            data={
                "tool": "organize_files",
                "params": {"path": raw_path, "_confirmed": True},
                "prompt": prompt,
                "preview": {"by_category": plan.by_category, "duplicates": plan.duplicates},
            },
        )

    # ── Execute ────────────────────────────────────────────────────────────
    folders_created: List[str] = []
    moved_by_folder: Dict[str, List[str]] = {}
    skipped: List[Dict[str, str]] = []
    manifest_moves: List[Dict[str, str]] = []

    def _ensure_folder(folder_name: str) -> Path:
        dest_dir = target / folder_name
        if not dest_dir.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            folders_created.append(folder_name)
        return dest_dir

    def _move_one(filename: str, folder_name: str) -> None:
        src = target / filename
        if not src.exists():
            skipped.append({"file": filename, "reason": "vanished before move"})
            return
        dest_dir = _ensure_folder(folder_name)
        dest = _unique_destination(dest_dir / filename)
        try:
            src.rename(dest)
            moved_by_folder.setdefault(folder_name, []).append(dest.name)
            manifest_moves.append({"src": str(src), "dst": str(dest)})
        except PermissionError:
            skipped.append({"file": filename, "reason": "in use or permission denied"})
        except Exception as exc:
            skipped.append({"file": filename, "reason": str(exc)})

    for category, files in plan.by_category.items():
        for filename in files:
            _move_one(filename, category)

    if plan.duplicates:
        for filename in plan.duplicates:
            _move_one(filename, rules.get("duplicates_folder", "Duplicates"))

    # ── Undo manifest ─────────────────────────────────────────────────────
    try:
        _MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        manifest = {
            "target_wsl": str(target),
            "target_win": win_target,
            "timestamp": time.time(),
            "folders_created": folders_created,
            "moves": manifest_moves,
        }
        _MANIFEST_LAST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("[ORGANIZE_FILES] failed to write undo manifest", exc_info=True)

    _store_last_action(ctx, "organize_files", params, win_target)

    total_moved = sum(len(v) for v in moved_by_folder.values())
    folder_bits = "; ".join(f"{cat} ({len(files)})" for cat, files in moved_by_folder.items())
    created_bits = f" Created: {', '.join(folders_created)}." if folders_created else ""
    skipped_bits = f" {len(skipped)} file{'s' if len(skipped) != 1 else ''} skipped." if skipped else ""
    spoken = (f"Done — organized {total_moved} file{'s' if total_moved != 1 else ''} in "
              f"{Path(win_target).name} into {len(moved_by_folder)} folder{'s' if len(moved_by_folder) != 1 else ''}: "
              f"{folder_bits}.{created_bits}{skipped_bits}")

    ok_open, _ = _open_in_explorer(win_target)
    if ok_open:
        spoken += " I've opened the folder so you can see it."

    logger.info("[ORGANIZE_FILES_DONE] target=%s moved=%d folders_created=%s skipped=%d",
                win_target, total_moved, folders_created, len(skipped))

    return ToolResult(
        success=True,
        text=spoken,
        spoken=spoken,
        action_path=win_target,
        data={
            "target": win_target,
            "moved_by_folder": moved_by_folder,
            "folders_created": folders_created,
            "duplicates": plan.duplicates,
            "skipped": skipped,
            "total_moved": total_moved,
        },
    )


def _exec_undo_organize_files(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    if not _MANIFEST_LAST.exists():
        return ToolResult(success=False, text="No organize run to undo.",
                          spoken="There's nothing to undo — I haven't organized any files recently.")

    try:
        manifest = json.loads(_MANIFEST_LAST.read_text(encoding="utf-8"))
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="I couldn't read the last organize run.", error=str(exc))

    moves = manifest.get("moves", [])
    restored = 0
    failed: List[str] = []

    for move in reversed(moves):
        src = Path(move["src"])
        dst = Path(move["dst"])
        if not dst.exists():
            failed.append(dst.name)
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            target_back = src if not src.exists() else _unique_destination(src)
            dst.rename(target_back)
            restored += 1
        except Exception:
            failed.append(dst.name)

    # Remove folders we created, if now empty
    removed_folders: List[str] = []
    target_wsl = manifest.get("target_wsl")
    if target_wsl:
        base = Path(target_wsl)
        for folder_name in manifest.get("folders_created", []):
            folder = base / folder_name
            try:
                if folder.exists() and folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
                    removed_folders.append(folder_name)
            except Exception:
                pass

    try:
        _MANIFEST_LAST.unlink()
    except Exception:
        pass

    win_target = manifest.get("target_win", "")
    spoken = f"Undone — restored {restored} file{'s' if restored != 1 else ''} in {Path(win_target).name if win_target else 'that folder'}."
    if removed_folders:
        spoken += f" Removed empty folders: {', '.join(removed_folders)}."
    if failed:
        spoken += f" {len(failed)} file{'s' if len(failed) != 1 else ''} couldn't be restored (moved or renamed since)."

    return ToolResult(
        success=restored > 0 or not moves,
        text=spoken,
        spoken=spoken,
        action_path=win_target or None,
        data={"restored": restored, "failed": failed, "removed_folders": removed_folders},
    )


registry.register(
    name="organize_files",
    definition={
        "type": "function",
        "function": {
            "name": "organize_files",
            "description": (
                "Sort loose files in a folder (Desktop, Downloads, Documents, ...) into "
                "type-based subfolders: Pictures, Screenshots, Documents, Spreadsheets, "
                "Presentations, Videos, Audio, Archives, Installers, Code, Other, and "
                "Duplicates. Only touches files directly in that folder — subfolders, "
                "project folders, and app shortcuts are left alone. Always asks for "
                "confirmation before moving anything. Use for: 'organize my desktop', "
                "'clean up my downloads', 'sort my files', 'tidy up my desktop'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder to organize, e.g. 'Desktop', 'Downloads', 'Documents'. Defaults to Desktop.",
                    },
                },
                "required": [],
            },
        },
    },
    executor=_exec_organize_files,
    risk="medium",
    category="system",
)

registry.register(
    name="undo_organize_files",
    definition={
        "type": "function",
        "function": {
            "name": "undo_organize_files",
            "description": (
                "Reverse the most recent organize_files run — moves every file back to "
                "where it was and removes any folders that were created and are now "
                "empty. Use for: 'undo the organize', 'undo last organize', 'put my files back'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_undo_organize_files,
    risk="medium",
    category="system",
)
