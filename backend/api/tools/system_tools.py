"""
System Tools — OS-level file, folder, and application control.

Supports Windows natively and WSL2 (converts paths automatically).
All path-destructive operations run through the safety layer first.

Registered tools:
  open_directory    open_file         create_folder
  list_directory    search_files      open_application
  system_info       write_file
"""
from __future__ import annotations

import difflib
import fnmatch
import logging
import os
import platform
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict

import shutil

from .registry import ToolResult, registry
from .safety import is_safe_path, is_safe_write
from utils.path_utils import resolve_wsl_path, safe_path, wsl_to_win

logger = logging.getLogger(__name__)


# ── Platform detection ─────────────────────────────────────────────────────────

def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


_ON_WINDOWS = sys.platform == "win32"
_ON_WSL     = _is_wsl()


# ── Windows home directory resolution ────────────────────────────────────────

_CMDEXE_PATH: str | None = None

def _find_cmdexe() -> str | None:
    global _CMDEXE_PATH
    if _CMDEXE_PATH is not None:
        return _CMDEXE_PATH
    for candidate in [
        "/mnt/c/Windows/System32/cmd.exe",
        "/mnt/c/WINDOWS/System32/cmd.exe",
        "/mnt/c/WINDOWS/system32/cmd.exe",
    ]:
        if Path(candidate).exists():
            _CMDEXE_PATH = candidate
            return _CMDEXE_PATH
    return None


def _windows_home() -> str:
    """Return the Windows user home directory (e.g. C:\\Users\\Tayyab)."""
    if _ON_WINDOWS:
        return os.environ.get("USERPROFILE", "C:\\Users\\User")
    if _ON_WSL:
        cmd = _find_cmdexe() or "cmd.exe"
        try:
            res = subprocess.run(
                [cmd, "/c", "echo %USERPROFILE%"],
                capture_output=True, text=True, timeout=3,
            )
            p = res.stdout.strip()
            if p and "USERPROFILE" not in p:
                return p
        except Exception:
            pass
        # Fallback: glob for any user Desktop under /mnt/c/Users/
        try:
            users = sorted(Path("/mnt/c/Users").glob("*/Desktop"))
            # Skip system accounts
            skip = {"public", "default", "default user", "all users"}
            for desk in users:
                if desk.parent.name.lower() not in skip:
                    win = str(desk.parent).replace("/mnt/c/", "C:\\").replace("/", "\\")
                    return win
        except Exception:
            pass
    return "C:\\Users\\User"


_WIN_SPECIAL: Dict[str, str] = {}


def _get_win_special() -> Dict[str, str]:
    global _WIN_SPECIAL
    if _WIN_SPECIAL:
        return _WIN_SPECIAL
    home = _windows_home().rstrip("\\")
    _WIN_SPECIAL = {
        "desktop":    home + "\\Desktop",
        "documents":  home + "\\Documents",
        "document":   home + "\\Documents",
        "downloads":  home + "\\Downloads",
        "download":   home + "\\Downloads",
        "pictures":   home + "\\Pictures",
        "picture":    home + "\\Pictures",
        "photos":     home + "\\Pictures",
        "photo":      home + "\\Pictures",
        "videos":     home + "\\Videos",
        "video":      home + "\\Videos",
        "music":      home + "\\Music",
        "home":       home,
        "user":       home,
        "appdata":    home + "\\AppData",
        "temp":       home + "\\AppData\\Local\\Temp",
        "temporary":  home + "\\AppData\\Local\\Temp",
    }
    return _WIN_SPECIAL


# ── Path normalisation ────────────────────────────────────────────────────────

def resolve_path(raw: str) -> str:
    """
    Convert natural-language or WSL path expressions to an OS path string.

    Examples:
      "E drive"         → "E:\\"
      "e:\\"            → "E:\\"
      "desktop"         → "C:\\Users\\Tayyab\\Desktop"
      "/mnt/e/Projects" → "E:\\Projects"   (WSL2)
      "E:\\Projects"    → "E:\\Projects"   (pass-through on Windows)
    """
    p = raw.strip()

    # Already a Windows absolute path (e.g. E:\... or C:\...)
    if re.match(r'^[A-Za-z]:[\\\/]', p):
        return p[0].upper() + p[1:].replace("/", "\\")

    # Just a drive letter with optional "drive" word: "E drive", "E:", "e"
    m = re.match(r'^([A-Za-z])\s*(?:drive|disk|:)?$', p, re.IGNORECASE)
    if m:
        return m.group(1).upper() + ":\\"

    # WSL mount path: /mnt/e/... → E:\...
    m = re.match(r'^/mnt/([A-Za-z])(/.*)?$', p)
    if m:
        drive = m.group(1).upper()
        rest  = (m.group(2) or "").replace("/", "\\")
        return drive + ":\\" + rest.lstrip("\\")

    # Named special directories (exact match: "desktop" → "C:\Users\...\Desktop")
    special = _get_win_special()
    key = p.lower().strip("/\\")
    if key in special:
        return special[key]

    # Named special directory + subpath: "desktop\Games" → "C:\Users\...\Desktop\Games"
    # On WSL/Linux, backslash is NOT a path separator, so Path() won't split it —
    # we must handle it manually here.
    for sep in ('\\', '/'):
        if sep in p:
            head, tail = p.split(sep, 1)
            head_key = head.lower().strip()
            if head_key in special:
                return special[head_key].rstrip('\\') + '\\' + tail.replace('/', '\\')
            break  # separator found but prefix not special — stop checking

    # Linux absolute path (best-effort pass-through)
    if p.startswith("/"):
        return p

    return p


def _fs_path(win_path: str) -> Path:
    """
    Return a Python Path usable by this process.

    • Windows        → Path(win_path) directly.
    • WSL2           → convert E:\\ to /mnt/e/.
    • Pure Linux     → Path as-is.
    """
    if _ON_WINDOWS:
        return Path(win_path)

    if _ON_WSL:
        m = re.match(r'^([A-Za-z]):[\\\/](.*)', win_path.replace("\\", "/"))
        if m:
            drive   = m.group(1).lower()
            rest    = m.group(2)
            return Path(f"/mnt/{drive}/{rest}")

    return Path(win_path)


# ── Shell helpers (open in OS UI) ─────────────────────────────────────────────

def _open_in_explorer(win_path: str) -> tuple[bool, str]:
    """Open a directory in Explorer. Uses cmd.exe /c start on WSL/Windows for reliability."""
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            if _ON_WSL:
                _cmd = _find_cmdexe() or "cmd.exe"
                subprocess.Popen(
                    ['/init', _cmd, '/c', 'start', '', 'explorer.exe', win_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif _ON_WINDOWS:
                subprocess.Popen(
                    ["explorer.exe", win_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(["xdg-open", win_path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, f"Opened {win_path} in Explorer"
        except Exception as exc:
            last_exc = exc
    return False, f"Could not open path: {last_exc}"


def _open_file_default(win_path: str) -> tuple[bool, str]:
    """Open a file with its default application."""
    try:
        if _ON_WINDOWS:
            os.startfile(win_path)  # type: ignore[attr-defined]
        elif _ON_WSL:
            _cmd = _find_cmdexe() or "cmd.exe"
            subprocess.Popen(['/init', _cmd, '/c', 'start', '', win_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", win_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, f"Opened file: {win_path}"
    except Exception as exc:
        return False, f"Could not open file: {exc}"


# ── Application launcher (reuse system router logic) ──────────────────────────

_APP_MAP: Dict[str, Dict[str, str]] = {
    "code":        {"wsl": "code.cmd",        "linux": "code",               "win32": "code"},
    "vscode":      {"wsl": "code.cmd",        "linux": "code",               "win32": "code"},
    "chrome":      {"wsl": "chrome.exe",      "linux": "google-chrome",      "win32": "chrome"},
    "firefox":     {"wsl": "firefox.exe",     "linux": "firefox",            "win32": "firefox"},
    "spotify":     {"wsl": "spotify.exe",     "linux": "spotify",            "win32": "spotify"},
    "notepad":     {"wsl": "notepad.exe",     "linux": "gedit",              "win32": "notepad"},
    "calculator":  {"wsl": "calc.exe",        "linux": "gnome-calculator",   "win32": "calc"},
    "explorer":    {"wsl": "explorer.exe",    "linux": "nautilus",           "win32": "explorer"},
    "terminal":    {"wsl": "wt.exe",          "linux": "gnome-terminal",     "win32": "wt"},
    "cmd":         {"wsl": "cmd.exe",         "linux": "bash",               "win32": "cmd"},
    "powershell":  {"wsl": "powershell.exe",  "linux": "bash",               "win32": "powershell"},
    "word":        {"wsl": "winword.exe",     "linux": "libreoffice --writer","win32": "winword"},
    "excel":       {"wsl": "excel.exe",       "linux": "libreoffice --calc", "win32": "excel"},
    "powerpoint":  {"wsl": "powerpnt.exe",    "linux": "libreoffice --impress","win32": "powerpnt"},
    "outlook":     {"wsl": "outlook.exe",     "linux": "",                   "win32": "outlook"},
    "teams":       {"wsl": "teams.exe",       "linux": "teams",              "win32": "teams"},
    "slack":       {"wsl": "slack.exe",       "linux": "slack",              "win32": "slack"},
    "discord":     {"wsl": "discord.exe",     "linux": "discord",            "win32": "discord"},
    "zoom":        {"wsl": "zoom.exe",        "linux": "zoom",               "win32": "zoom"},
    "vlc":         {"wsl": "vlc.exe",         "linux": "vlc",                "win32": "vlc"},
    "paint":       {"wsl": "mspaint.exe",     "linux": "gimp",               "win32": "mspaint"},
    "taskmanager": {"wsl": "taskmgr.exe",     "linux": "",                   "win32": "taskmgr"},
    "settings":    {"wsl": "ms-settings:",    "linux": "gnome-control-center","win32": "ms-settings:"},
    "brave":       {"wsl": "brave.exe",       "linux": "brave-browser",      "win32": "brave"},
    "steam":       {"wsl": "steam.exe",       "linux": "steam",              "win32": "steam"},
    "telegram":    {"wsl": "telegram.exe",    "linux": "telegram-desktop",   "win32": "telegram"},
    "postman":     {"wsl": "postman.exe",     "linux": "postman",            "win32": "postman"},
    "obsidian":    {"wsl": "obsidian.exe",    "linux": "obsidian",           "win32": "obsidian"},
    "notion":      {"wsl": "notion.exe",      "linux": "notion-app",         "win32": "notion"},
    # Browser shortcuts — opened via default browser
    "youtube":     {"wsl": "https://www.youtube.com",  "win32": "https://www.youtube.com"},
    "gmail":       {"wsl": "https://mail.google.com",  "win32": "https://mail.google.com"},
    "github":      {"wsl": "https://github.com",       "win32": "https://github.com"},
    "chatgpt":     {"wsl": "https://chatgpt.com",      "win32": "https://chatgpt.com"},
    "google":      {"wsl": "https://www.google.com",   "win32": "https://www.google.com"},
}

_APP_ALIASES: Dict[str, str] = {
    "vs code": "vscode", "visual studio code": "vscode",
    "google chrome": "chrome", "file explorer": "explorer",
    "windows explorer": "explorer", "windows terminal": "terminal",
    "command prompt": "cmd", "microsoft word": "word",
    "microsoft excel": "excel", "microsoft powerpoint": "powerpoint",
    "microsoft teams": "teams", "task manager": "taskmanager",
    "ms paint": "paint",
    # Settings variants — including singular + spoken/misheard forms
    "setting": "settings",
    "system setting": "settings", "system settings": "settings",
    "systemsettings": "settings", "systemsetting": "settings",
    "windows setting": "settings", "windows settings": "settings",
    "windowssettings": "settings",
    "pc setting": "settings", "pc settings": "settings", "pcsettings": "settings",
    "control panel": "settings",
}


def _normalise_app(name: str) -> str:
    n = name.lower().strip()
    # Strip leading determiners: "any setting" → "setting", "my chrome" → "chrome"
    n = re.sub(r'^(?:an?\s+|the\s+|any\s+|my\s+|some\s+)', '', n)
    # Strip trailing filler: "settings app" → "settings", "chrome please" → "chrome"
    n = re.sub(r'\s+(?:app|application|program|software|please|now|for\s+me)\s*$', '', n)
    # Normalize singular → plural for known nouns with known plural forms
    n = re.sub(r'^setting$', 'settings', n)
    return _APP_ALIASES.get(n, re.sub(r'[\s\-_]', '', n))


# Window title / WScript.AppActivate search strings for common apps
_APP_FOCUS_TITLE: Dict[str, str] = {
    "settings":     "Settings",
    "chrome":       "Google Chrome",
    "firefox":      "Mozilla Firefox",
    "edge":         "Microsoft Edge",
    "spotify":      "Spotify",
    "discord":      "Discord",
    "vscode":       "Visual Studio Code",
    "code":         "Visual Studio Code",
    "word":         "Word",
    "excel":        "Excel",
    "outlook":      "Outlook",
    "teams":        "Microsoft Teams",
    "slack":        "Slack",
    "notepad":      "Notepad",
    "explorer":     "File Explorer",
    "youtube":      "YouTube",
    "whatsapp":     "WhatsApp",
}


def _bring_to_front(app_key: str, delay: float = 1.5) -> None:
    """Background thread: focus the app window ~delay seconds after launch."""
    if not (_ON_WSL or _ON_WINDOWS):
        return

    title = _APP_FOCUS_TITLE.get(app_key, "")
    if not title:
        return

    def _do_focus() -> None:
        import time
        time.sleep(delay)
        try:
            ps_cmd = (
                f"$s = New-Object -COM WScript.Shell; "
                f"$null = $s.AppActivate('{title}')"
            )
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-Command", ps_cmd],
                capture_output=True, timeout=6,
            )
        except Exception as exc:
            logger.debug("_bring_to_front(%r) failed: %s", title, exc)

    threading.Thread(target=_do_focus, daemon=True).start()


def _launch_app(app_name: str) -> tuple[bool, str]:
    key      = _normalise_app(app_name)
    platform = "wsl" if _ON_WSL else ("win32" if _ON_WINDOWS else "linux")
    entry    = _APP_MAP.get(key)

    if not entry:
        # Unknown app — try via cmd.exe start (handles .exe, shortcuts, UWP, protocols)
        try:
            exe = app_name.strip()
            if _ON_WSL:
                _cmd = _find_cmdexe() or "cmd.exe"
                subprocess.Popen(
                    ['/init', _cmd, '/c', 'start', '', exe],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif _ON_WINDOWS:
                _cmd = _find_cmdexe() or "cmd.exe"
                subprocess.Popen(
                    [_cmd, '/c', 'start', '', exe],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(exe.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _bring_to_front(key)
            return True, f"Launching {app_name}…"
        except Exception as exc:
            return False, f"App '{app_name}' not found. Known apps: vscode, chrome, spotify, terminal, word, excel, teams, slack, discord."

    cmd = entry.get(platform, "")
    if not cmd:
        return False, f"'{app_name}' is not available on this platform."

    try:
        _is_uri = cmd.startswith("ms-settings:") or cmd.startswith("http://") or cmd.startswith("https://")
        if _ON_WSL:
            _cmd = _find_cmdexe() or "cmd.exe"
            subprocess.Popen(['/init', _cmd, '/c', 'start', '', cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif _is_uri:
            subprocess.Popen(["start", cmd], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif _ON_WINDOWS:
            subprocess.Popen(cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Launched: %s → %s", app_name, cmd)
        _bring_to_front(key)
        return True, f"Launched {app_name}"
    except Exception as exc:
        logger.warning("Launch failed for %s: %s", app_name, exc)
        return False, f"Could not launch {app_name}: {exc}"


# ── Tool executors ────────────────────────────────────────────────────────────

def _exec_open_directory(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw      = params.get("path", "").strip()
    win_path = resolve_path(raw)

    if not is_safe_path(win_path):
        return ToolResult(
            success=False, text=f"Access denied: {win_path}",
            spoken=f"Sorry, I can't open that system directory for safety reasons.",
            error="Blocked by safety layer",
        )

    # For drive roots (e.g. E:\) skip the WSL mount check — Explorer can open
    # drives that aren't mounted in WSL. For deeper paths, verify existence.
    is_drive_root = bool(re.match(r'^[A-Za-z]:[\\\/]?$', win_path))
    if not is_drive_root:
        fs = _fs_path(win_path)
        if not fs.exists():
            return ToolResult(
                success=False, text=f"Path not found: {win_path}",
                spoken=f"I couldn't find that directory. Does it exist?",
                error="Path does not exist",
            )

    ok, msg = _open_in_explorer(win_path)
    spoken  = f"Opening {raw}." if ok else msg

    # Store last action in memory
    _store_last_action(ctx, "open_directory", params, win_path)

    return ToolResult(
        success=ok, text=msg, spoken=spoken,
        action_path=win_path if ok else None,
        data={"path": win_path},
    )


def _exec_create_folder(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    base_raw = params.get("path", "").strip()
    name     = params.get("name", "").strip()

    if not name:
        return ToolResult(success=False, text="Folder name is required.", spoken="What should I name the folder?")

    name = re.sub(r'[<>:"|?*]', "", name)

    # No location given → ask user; never silently default to Desktop
    if not base_raw:
        return ToolResult(
            success=False,
            text=f"Where should I create '{name}'?",
            spoken=f"Where should I create \"{name}\"? Say Desktop, D drive, or give me a path.",
            data={"needs_clarification": True, "clarification_type": "location", "name": name},
        )

    # Resolve to WSL2 path — handles "D drive", "myd drive", "desktop", etc.
    wsl_base = resolve_wsl_path(base_raw)
    if wsl_base is None:
        return ToolResult(
            success=False,
            text=f"Unknown location: {base_raw!r}",
            spoken=f"I couldn't find that location. Try Desktop, D drive, or a specific path.",
            data={"needs_clarification": True, "clarification_type": "location", "name": name},
        )

    wsl_target = wsl_base.rstrip("/") + "/" + name.replace("\\", "/")
    win_target = wsl_to_win(wsl_target)
    logger.info("[EXEC] create_folder: %s  (win: %s)", wsl_target, win_target)

    if not is_safe_path(win_target):
        return ToolResult(success=False, text=f"Cannot create folder here: {win_target}",
                          spoken="That location is restricted for safety.", error="Blocked path")

    try:
        os.makedirs(wsl_target, exist_ok=True)
        if not os.path.exists(wsl_target):
            logger.error("create_folder: makedirs succeeded but path missing: %r", wsl_target)
            return ToolResult(success=False, text=f"FAILED: {wsl_target}",
                              spoken="Couldn't create the folder. Please try again.",
                              error="mkdir succeeded but path missing")
        _store_last_action(ctx, "create_folder", params, win_target)
        # Register in persistent folder memory so future subfolder commands resolve the path.
        try:
            from ..services.history_service import history_service as _hs
            _hs.remember_folder(name.lower(), wsl_target, win_target)
        except Exception:
            pass
        ok_open, _ = _open_in_explorer(win_target)
        spoken = (f"Done! Created '{name}' at {win_target} and opened it."
                  if ok_open else f"Done! Created '{name}' at {win_target}.")
        msg = f"Created folder '{name}' at {win_target}"
        return ToolResult(success=True, text=msg, spoken=spoken,
                          action_path=win_target,
                          data={"path": win_target, "name": name, "wsl_path": wsl_target})
    except PermissionError:
        return ToolResult(success=False, text=f"Permission denied: {win_target}",
                          spoken="I don't have permission to create a folder there.", error="PermissionError")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't create the folder.", error=str(exc))


# ── Subfolder name normalization ─────────────────────────────────────────────
# Whisper often clips or mishears common project folder names.
# "front" → "Frontend", "b" → "Backend", etc.
_SUBFOLDER_EXPANSIONS: Dict[str, str] = {
    "front": "Frontend",    "frontend": "Frontend",
    "back": "Backend",      "backend": "Backend",     "b": "Backend",
    "db": "Database",       "database": "Database",
    "api": "API",
    "auth": "Auth",         "authentication": "Authentication",
    "config": "Config",     "configs": "Config",
    "utils": "Utils",       "util": "Utils",
    "helpers": "Helpers",   "helper": "Helpers",
    "docs": "Docs",         "doc": "Docs",
    "tests": "Tests",       "test": "Tests",
    "models": "Models",     "views": "Views",
    "controllers": "Controllers",   "ctrl": "Controllers",
    "components": "Components",     "comps": "Components",
    "scripts": "Scripts",   "static": "Static",
    "public": "Public",     "private": "Private",
    "assets": "Assets",     "media": "Media",
    "images": "Images",     "img": "Images",
    "uploads": "Uploads",   "logs": "Logs",       "log": "Logs",
    "build": "Build",       "src": "Src",         "lib": "Lib",
    "core": "Core",         "data": "Data",       "cache": "Cache",
    "finance": "Finance",   "hr": "HR",           "marketing": "Marketing",
    "sales": "Sales",       "admin": "Admin",
}


def _normalize_subfolder_name(name: str) -> str:
    """Expand clipped subfolder names: 'front' → 'Frontend', 'b' → 'Backend'."""
    return _SUBFOLDER_EXPANSIONS.get(name.lower().strip(), name)


def _exec_create_subfolders(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    parent_raw = params.get("parent", "").strip()
    names      = params.get("names", [])
    count      = int(params.get("count", 0) or 0)

    # No parent given → check last action for the folder we just created
    if not parent_raw:
        try:
            from ..services.memory_service import memory_service
            last = memory_service.get_last_action()
            if last and last.get("tool") in ("create_folder", "open_directory", "create_subfolders"):
                parent_raw = (last.get("result") or "").strip()
                if not parent_raw:
                    parent_raw = (last.get("params") or {}).get("path", "")
        except Exception:
            pass

    if not parent_raw:
        return ToolResult(
            success=False,
            text="Which folder should I create the subfolders in?",
            spoken="Which folder should I create these in? Say the folder name or path.",
            data={"needs_clarification": True},
        )

    if not names and count > 0:
        names = [f"Folder {i + 1}" for i in range(min(count, 20))]
    elif len(names) == 1 and count > 1:
        # "create 3 named documents" → documents_1, documents_2, documents_3
        base = names[0]
        names = [f"{base}_{i + 1}" for i in range(min(count, 20))]
    elif not names:
        return ToolResult(success=False, text="No subfolder names provided.",
                          spoken="What should I name the subfolders?")

    # Resolve parent to WSL path
    parent_wsl = resolve_wsl_path(parent_raw)
    if parent_wsl is None:
        # Bare folder name — try FolderMemory before giving up
        try:
            from ..services.history_service import history_service as _hs_early
            _fm_early = _hs_early.lookup_folder(parent_raw.lower().strip())
            if _fm_early and _fm_early.get("full_wsl"):
                parent_wsl = _fm_early["full_wsl"]
                logger.info("FolderMemory early lookup: %r → %r", parent_raw, parent_wsl)
        except Exception:
            pass
    if parent_wsl is None:
        return ToolResult(success=False, text=f"Unknown parent location: {parent_raw!r}",
                          spoken="I couldn't find that folder. Please tell me where to put the subfolders.")

    # Defense-in-depth: if the resolved path doesn't exist, try FolderMemory by base name.
    # This catches cases where the LLM computed "D:\project" but the folder is "D:\workspace\project".
    if not os.path.exists(parent_wsl):
        _base = os.path.basename(parent_wsl.rstrip("/"))
        try:
            from ..services.history_service import history_service as _hs
            _fm = _hs.lookup_folder(_base.lower())
            if _fm and os.path.exists(_fm["full_wsl"]):
                logger.info("FolderMemory path fallback: %r → %r", parent_raw, _fm["full_wsl"])
                parent_wsl = _fm["full_wsl"]
            else:
                return ToolResult(success=False,
                    text=f"Parent folder not found: {parent_raw!r}",
                    spoken=f"I couldn't find the folder '{_base}'. Please tell me where to put the subfolders.")
        except Exception:
            pass

    parent_win = wsl_to_win(parent_wsl)
    if not is_safe_path(parent_win):
        return ToolResult(success=False, text=f"Blocked: {parent_win}",
                          spoken="That location is restricted.", error="Blocked path")

    logger.info("create_subfolders: parent_wsl=%r", parent_wsl)

    created: list[str] = []
    failed:  list[str] = []
    for name in names[:20]:
        name = re.sub(r'[<>:"|?*]', "", name).strip()
        if not name:
            continue
        # Expand clipped names: "front" → "Frontend", "b" → "Backend"
        name = _normalize_subfolder_name(name)
        wsl_target = parent_wsl.rstrip("/") + "/" + name
        try:
            os.makedirs(wsl_target, exist_ok=True)
            if os.path.exists(wsl_target):
                created.append(name)
                logger.info("[EXEC] create_subfolders: %s", wsl_target)
                # Register in persistent folder memory
                try:
                    from ..services.history_service import history_service as _hs
                    _hs.remember_folder(name.lower(), wsl_target, wsl_to_win(wsl_target))
                except Exception:
                    pass
            else:
                logger.error("create_subfolders: makedirs ok but missing: %r", wsl_target)
                failed.append(name)
        except Exception as exc:
            logger.error("create_subfolders: error creating %r: %s", name, exc)
            failed.append(name)

    if created:
        names_str = ", ".join(f"'{n}'" for n in created)
        spoken = f"Done. Created {len(created)} subfolder{'s' if len(created) != 1 else ''}: {names_str} in {parent_win}."
        return ToolResult(success=True, text=spoken, spoken=spoken,
                          data={"created": created, "parent": parent_win, "wsl_parent": parent_wsl})
    return ToolResult(success=False, text="No subfolders were created.",
                      spoken="Couldn't create the subfolders.", error="All failed")


def _exec_list_directory(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw      = params.get("path", "").strip()
    win_path = resolve_path(raw)

    if not is_safe_path(win_path):
        return ToolResult(success=False, text=f"Access denied: {win_path}",
                          spoken="I can't list that directory.", error="Blocked path")

    fs = _fs_path(win_path)
    if not fs.exists():
        return ToolResult(success=False, text=f"Path not found: {win_path}",
                          spoken="That path doesn't exist.", error="Path not found")

    try:
        entries  = sorted(fs.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        dirs     = [e.name for e in entries if e.is_dir()]
        files    = [e.name for e in entries if e.is_file()]
        count    = len(dirs) + len(files)
        preview  = (dirs[:5] + files[:5])[:10]
        summary  = f"{count} items in {win_path}: " + ", ".join(preview)
        if count > 10:
            summary += f" (and {count - 10} more)"
        spoken   = f"Found {count} items. " + (f"Folders: {', '.join(dirs[:3])}. " if dirs[:3] else "") + (f"Files: {', '.join(files[:3])}." if files[:3] else "")
        return ToolResult(success=True, text=summary, spoken=spoken,
                          data={"path": win_path, "dirs": dirs, "files": files, "count": count})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't list that directory.", error=str(exc))


def _exec_open_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw      = params.get("path", "").strip()
    win_path = resolve_path(raw)

    if not is_safe_path(win_path):
        return ToolResult(success=False, text=f"Access denied: {win_path}",
                          spoken="That file is restricted.", error="Blocked path")

    ok, msg = _open_file_default(win_path)
    if ok:
        _store_last_action(ctx, "open_file", params, win_path)
    return ToolResult(success=ok, text=msg, spoken=f"Opening {Path(win_path).name}." if ok else msg,
                      action_path=win_path if ok else None)


def _exec_open_application(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    app_name = params.get("app_name", "").strip()
    if not app_name:
        return ToolResult(success=False, text="App name required.", spoken="Which application should I open?")

    # Try app_finder: 4-source index + hardcoded aliases + fuzzy match (fully sync — no asyncio.run)
    try:
        from api.tools.core.app_finder import _search_index, _launch_via_interop as _interop
        _entry, _match = _search_index(app_name)
        if _entry is not None:
            _path = _entry.get("path", "")
            _name = _entry.get("name", app_name)
            if _interop(_path, _name):
                msg = f"Opening {_name}."
                _store_last_action(ctx, "open_application", params, _name)
                return ToolResult(success=True, text=msg, spoken=msg, action_app=_name)
    except Exception:
        pass

    # Legacy fallback: hardcoded _APP_MAP + Start Menu index + PowerShell
    ok, msg = _launch_app(app_name)
    if ok:
        _store_last_action(ctx, "open_application", params, app_name)
    return ToolResult(success=ok, text=msg, spoken=msg, action_app=app_name if ok else None)


def _exec_search_files(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    query    = params.get("query", "").strip()
    raw_path = params.get("path", "").strip()
    win_base = resolve_path(raw_path) if raw_path else _windows_home()

    if not is_safe_path(win_base):
        return ToolResult(success=False, text=f"Access denied: {win_base}",
                          spoken="I can't search in that directory.", error="Blocked path")

    fs_base  = _fs_path(win_base)
    try:
        pattern  = f"*{query}*" if not any(c in query for c in "*?[") else query
        matches  = list(fs_base.rglob(pattern))[:20]
        if not matches:
            return ToolResult(success=True, text=f"No files matching '{query}' in {win_base}",
                              spoken=f"I didn't find any files matching '{query}'.",
                              data={"matches": [], "count": 0})
        names    = [m.name for m in matches]
        summary  = f"Found {len(matches)} file(s) matching '{query}': " + ", ".join(names[:5])
        if len(matches) > 5:
            summary += f" and {len(matches) - 5} more"
        spoken   = f"Found {len(matches)} files matching '{query}'."
        return ToolResult(success=True, text=summary, spoken=spoken,
                          data={"matches": [str(m) for m in matches], "count": len(matches)})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="File search failed.", error=str(exc))


# ── Multi-drive detection ─────────────────────────────────────────────────────

def _get_all_windows_drives() -> list[tuple[str, str]]:
    """Return (drive_label, fs_path) for every accessible Windows drive.

    On WSL2  → scans /mnt/<letter> directories.
    On Windows → uses psutil.disk_partitions() with shutil fallback.
    On Linux  → returns root only.
    """
    drives: list[tuple[str, str]] = []

    if _ON_WSL:
        try:
            mnt = Path("/mnt")
            for d in sorted(mnt.iterdir()):
                if d.is_dir() and len(d.name) == 1 and d.name.isalpha():
                    letter = d.name.upper()
                    # Quick accessibility check — skip if mount is empty/dead
                    try:
                        next(d.iterdir(), None)
                        drives.append((f"{letter}:", str(d)))
                    except PermissionError:
                        drives.append((f"{letter}:", str(d)))  # still include
                    except Exception:
                        pass  # dead mount
        except Exception:
            # Hard fallback — try C D E individually
            for letter in "CDE":
                p = Path(f"/mnt/{letter.lower()}")
                if p.exists():
                    drives.append((f"{letter}:", str(p)))

    elif _ON_WINDOWS:
        try:
            import psutil
            for part in psutil.disk_partitions(all=False):
                if part.device and len(part.device) >= 2 and part.device[1] == ':':
                    letter = part.device[0].upper()
                    drives.append((f"{letter}:", part.mountpoint))
        except Exception:
            for letter in "CDEFGH":
                if Path(f"{letter}:\\").exists():
                    drives.append((f"{letter}:", f"{letter}:\\"))

    else:
        drives.append(("/", "/"))

    return drives


def _get_windows_os() -> str:
    """Return the real Windows OS caption, or empty string if unavailable."""
    if _ON_WSL:
        # Primary: PowerShell CIM — correctly reports "Windows 11 Pro" (wmic can say "Windows 10")
        try:
            r = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_OperatingSystem).Caption"],
                capture_output=True, text=True, timeout=10,
            )
            caption = r.stdout.strip()
            if caption and "windows" in caption.lower():
                return caption
        except Exception:
            pass
        # Fallback 1: wmic Caption
        try:
            r = subprocess.run(
                ["cmd.exe", "/c", "wmic os get Caption /value"],
                capture_output=True, text=True, timeout=6,
            )
            m = re.search(r'Caption=(.+)', r.stdout)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        # Fallback 2: registry ProductName, corrected by build number for Windows 11
        try:
            r = subprocess.run(
                ["reg.exe", "query",
                 r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                 "/v", "ProductName"],
                capture_output=True, text=True, timeout=6,
            )
            m = re.search(r'ProductName\s+REG_SZ\s+(.+)', r.stdout)
            if m:
                product = m.group(1).strip()
                # Windows 11 has build >= 22000 but ProductName may still say "Windows 10"
                if "10" in product:
                    try:
                        rb = subprocess.run(
                            ["reg.exe", "query",
                             r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                             "/v", "CurrentBuild"],
                            capture_output=True, text=True, timeout=6,
                        )
                        mb = re.search(r'CurrentBuild\s+REG_SZ\s+(\d+)', rb.stdout)
                        if mb and int(mb.group(1)) >= 22000:
                            product = product.replace("Windows 10", "Windows 11")
                    except Exception:
                        pass
                return product
        except Exception:
            pass
    elif _ON_WINDOWS:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_OperatingSystem).Caption"],
                capture_output=True, text=True, timeout=10,
            )
            caption = r.stdout.strip()
            if caption:
                return caption
        except Exception:
            pass
        return platform.win32_ver()[0] or ""
    return ""


def _get_cpu_name() -> str:
    """Return the full CPU model name."""
    if _ON_WSL:
        try:
            r = subprocess.run(
                ["cmd.exe", "/c", "wmic cpu get Name /value"],
                capture_output=True, text=True, timeout=6,
            )
            m = re.search(r'Name=(.+)', r.stdout)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        m = re.search(r'model name\s*:\s*(.+)', cpuinfo)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def _drive_usage_gb(fs_path: str) -> tuple[float, float, float, float]:
    """Return (total_gb, used_gb, free_gb, pct_used) using 1024**3 base."""
    try:
        import psutil
        u = psutil.disk_usage(fs_path)
    except Exception:
        import shutil
        raw = shutil.disk_usage(fs_path)
        class _U:
            total = raw.total; used = raw.used; free = raw.free
            percent = (raw.used / raw.total * 100) if raw.total else 0.0
        u = _U()
    GiB = 1024 ** 3
    total = u.total / GiB
    used  = u.used  / GiB
    free  = u.free  / GiB
    pct   = round((u.used / u.total * 100), 1) if u.total else 0.0
    return round(total, 1), round(used, 1), round(free, 1), pct


# ── system_info (production-grade) ───────────────────────────────────────────

def _exec_system_info(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    info_lines: list[str] = []
    data: Dict[str, Any] = {}

    # ── OS ─────────────────────────────────────────────────────────────────────
    os_label = _get_windows_os() or f"{platform.uname().system} {platform.uname().release}"
    info_lines.append(f"OS: {os_label}")
    data["os"] = os_label

    # ── CPU ────────────────────────────────────────────────────────────────────
    cpu_name  = _get_cpu_name()
    cpu_cores = os.cpu_count() or 0
    cpu_phys  = cpu_cores
    try:
        import psutil
        cpu_cores = psutil.cpu_count(logical=True)  or cpu_cores
        cpu_phys  = psutil.cpu_count(logical=False) or cpu_phys
    except ImportError:
        pass

    cpu_label = f"{cpu_name} ({cpu_cores} logical / {cpu_phys} physical cores)" if cpu_name \
                else f"{cpu_cores} logical cores"
    info_lines.append(f"CPU: {cpu_label}")
    data["cpu"] = {"name": cpu_name, "logical_cores": cpu_cores, "physical_cores": cpu_phys}

    # ── RAM (exact, no rounding error) ────────────────────────────────────────
    GiB = 1024 ** 3
    ram_total = ram_avail = ram_used = ram_pct = 0.0
    try:
        import psutil
        vm        = psutil.virtual_memory()
        ram_total = vm.total     / GiB
        ram_avail = vm.available / GiB
        ram_used  = vm.used      / GiB
        ram_pct   = vm.percent
    except ImportError:
        if _ON_WSL:
            try:
                r = subprocess.run(
                    ["cmd.exe", "/c", "wmic computersystem get TotalPhysicalMemory /value"],
                    capture_output=True, text=True, timeout=6,
                )
                m = re.search(r'TotalPhysicalMemory=(\d+)', r.stdout)
                if m:
                    ram_total = int(m.group(1)) / GiB
            except Exception:
                pass

    if ram_total:
        ram_label = (f"RAM: {ram_total:.1f} GB total, {ram_avail:.1f} GB free, "
                     f"{ram_used:.1f} GB used ({ram_pct:.0f}%)")
        info_lines.append(ram_label)
        data["ram"] = {
            "total_gb": round(ram_total, 1),
            "used_gb":  round(ram_used,  1),
            "avail_gb": round(ram_avail, 1),
            "pct_used": round(ram_pct,   1),
        }

    # ── Drives (all detected, exact values) ───────────────────────────────────
    drives_data: list[dict] = []
    drives_spoken: list[str] = []

    for drive_label, fs_path in _get_all_windows_drives():
        try:
            total, used, free, pct = _drive_usage_gb(fs_path)
            info_lines.append(
                f"Drive {drive_label}: {total:.1f} GB total, {free:.1f} GB free ({pct:.0f}% used)"
            )
            drives_data.append({
                "name": drive_label, "total_gb": total,
                "used_gb": used, "free_gb": free, "pct_used": pct,
            })
            drives_spoken.append(f"{drive_label} {free:.1f} GB free")
        except Exception:
            pass

    data["drives"] = drives_data

    # ── Spoken response (short, accurate) ────────────────────────────────────
    # Strip "(R)", "(TM)" etc. for cleaner speech
    cpu_short = re.sub(r'\s*\(R\)|\s*\(TM\)|\s*\(C\)', '', cpu_name or "").strip()
    cpu_short = re.sub(r'\s*CPU\s*@\s*[\d.]+GHz', '', cpu_short).strip()
    cpu_short = re.sub(r'\s{2,}', ' ', cpu_short).strip()

    spoken = f"You are running {os_label}"
    if cpu_short:
        spoken += f" with {cpu_short}"
    if ram_total:
        spoken += f", {ram_total:.0f} GB of RAM"
    if drives_spoken:
        spoken += ". Storage: " + "; ".join(drives_spoken[:3])
    spoken += "."

    _store_last_action(ctx, "system_info", params, spoken)

    return ToolResult(
        success=True, text=" | ".join(info_lines) or "System info unavailable.",
        spoken=spoken, data=data,
    )


# ── system_health (live CPU / RAM / disk usage %) ────────────────────────────

def _exec_system_health(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    try:
        import psutil
    except ImportError:
        return ToolResult(
            success=False, text="psutil not available.",
            spoken="I need psutil to check system health. Run: pip install psutil",
            error="psutil missing",
        )

    # CPU: 0.5-second sample (fast but non-zero interval for accuracy)
    cpu_pct = psutil.cpu_percent(interval=0.5)

    vm       = psutil.virtual_memory()
    GiB      = 1024 ** 3
    ram_pct  = vm.percent
    ram_used = vm.used  / GiB
    ram_tot  = vm.total / GiB

    drives_health: list[dict] = []
    for drive_label, fs_path in _get_all_windows_drives():
        try:
            total, used, free, pct = _drive_usage_gb(fs_path)
            drives_health.append({
                "name": drive_label, "pct_used": pct,
                "free_gb": free, "total_gb": total,
            })
        except Exception:
            pass

    lines  = [f"CPU: {cpu_pct:.0f}%", f"RAM: {ram_pct:.0f}% ({ram_used:.1f}/{ram_tot:.1f} GB)"]
    lines += [f"{d['name']} disk: {d['pct_used']:.0f}% used" for d in drives_health]

    spoken = f"CPU is at {cpu_pct:.0f}%, RAM at {ram_pct:.0f}%"
    if drives_health:
        spoken += ". " + ", ".join(
            f"{d['name']} drive is {d['pct_used']:.0f}% full" for d in drives_health[:2]
        )
    spoken += "."

    _store_last_action(ctx, "system_health", params, spoken)

    return ToolResult(
        success=True, text=" | ".join(lines), spoken=spoken,
        data={"cpu_pct": cpu_pct, "ram_pct": ram_pct,
              "ram_used_gb": round(ram_used, 1), "ram_total_gb": round(ram_tot, 1),
              "drives": drives_health},
    )


# ── open_drive ────────────────────────────────────────────────────────────────

def _exec_open_drive(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw    = params.get("drive", "C").strip().upper().replace(":", "").replace("\\", "")
    letter = raw[0] if raw else "C"
    if not letter.isalpha():
        return ToolResult(success=False, text="Invalid drive letter.",
                          spoken="Please specify a drive letter like C, D, or E.")

    win_path = f"{letter}:\\"
    if not is_safe_path(win_path):
        return ToolResult(success=False, text=f"Access blocked: {win_path}",
                          spoken=f"Access to {letter} drive is restricted.")

    fs = _fs_path(win_path)
    if not fs.exists():
        return ToolResult(success=False, text=f"Drive {letter}: not found.",
                          spoken=f"{letter} drive doesn't exist or isn't mounted.")

    ok, msg = _open_in_explorer(win_path)
    if ok:
        _store_last_action(ctx, "open_drive", params, win_path)
    return ToolResult(success=ok, text=msg, spoken=f"Opening {letter} drive." if ok else msg,
                      action_path=win_path if ok else None,
                      data={"drive": f"{letter}:", "path": win_path})


# ── get_running_apps ──────────────────────────────────────────────────────────

_SYSTEM_PROC_PREFIXES = frozenset([
    "system", "svchost", "dwm", "csrss", "smss", "lsass", "wininit",
    "winlogon", "services", "registry", "memory compression", "ntoskrnl",
    "fontdrvhost", "runtimebroker", "conhost", "dllhost",
])


def _exec_get_running_apps(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    apps: list[dict] = []

    if _ON_WSL:
        try:
            r = subprocess.run(
                ["tasklist.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            seen: set[str] = set()
            for line in r.stdout.strip().splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if not parts or not parts[0]:
                    continue
                name   = parts[0].replace(".exe", "").strip()
                key    = name.lower()
                if key in seen or not name or len(name) < 2:
                    continue
                if any(key.startswith(p) for p in _SYSTEM_PROC_PREFIXES):
                    continue
                seen.add(key)
                try:
                    mem_kb = int(parts[4].replace(",", "").replace(" K", "")) if len(parts) > 4 else 0
                except ValueError:
                    mem_kb = 0
                apps.append({"name": name, "mem_mb": round(mem_kb / 1024, 1)})
        except Exception as exc:
            return ToolResult(success=False, text=str(exc),
                              spoken="Couldn't list running apps.", error=str(exc))

    elif _ON_WINDOWS:
        try:
            import psutil
            seen = set()
            for proc in psutil.process_iter(["name", "memory_info"]):
                try:
                    name = (proc.info["name"] or "").replace(".exe", "")
                    key  = name.lower()
                    if key in seen or not name or len(name) < 2:
                        continue
                    if any(key.startswith(p) for p in _SYSTEM_PROC_PREFIXES):
                        continue
                    seen.add(key)
                    mi   = proc.info.get("memory_info")
                    mem  = (mi.rss / 1024 / 1024) if mi else 0
                    apps.append({"name": name, "mem_mb": round(mem, 1)})
                except Exception:
                    pass
        except Exception as exc:
            return ToolResult(success=False, text=str(exc),
                              spoken="Couldn't list running apps.", error=str(exc))

    apps.sort(key=lambda x: x["mem_mb"], reverse=True)
    top = apps[:12]
    names = [a["name"] for a in top]

    text   = f"{len(top)} apps running: " + ", ".join(names)
    spoken = f"I see {len(top)} running apps: " + ", ".join(names[:7])
    if len(names) > 7:
        spoken += f", and {len(names) - 7} more"
    spoken += "."

    return ToolResult(success=True, text=text, spoken=spoken,
                      data={"count": len(top), "apps": top})


# ── kill_app ──────────────────────────────────────────────────────────────────

_PROTECTED_PROCS = frozenset([
    "explorer", "winlogon", "csrss", "smss", "lsass", "system",
    "svchost", "dwm", "taskmgr", "wininit", "services",
])


def _exec_kill_app(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    app_name = params.get("app_name", "").strip()
    # If no explicit name, fall back to the active foreground window
    if not app_name:
        aw = ctx.get("active_window") or {}
        app_name = aw.get("proc_name", "").strip()
    if not app_name:
        return ToolResult(success=False, text="App name required.",
                          spoken="Which application should I close?")

    key = app_name.lower().replace(".exe", "")
    if key in _PROTECTED_PROCS:
        return ToolResult(success=False, text=f"Protected: {app_name}",
                          spoken=f"I can't close {app_name} — it's a protected system process.")

    exe = app_name if app_name.lower().endswith(".exe") else app_name + ".exe"

    if _ON_WSL:
        try:
            r = subprocess.run(
                ["taskkill.exe", "/F", "/IM", exe],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0 or "SUCCESS" in r.stdout:
                return ToolResult(success=True, text=f"Closed {app_name}.",
                                  spoken=f"Closed {app_name}.", data={"app": app_name})
            return ToolResult(success=False,
                              text=r.stderr.strip() or f"Could not close {app_name}.",
                              spoken=f"I couldn't find or close {app_name}.")
        except Exception as exc:
            return ToolResult(success=False, text=str(exc),
                              spoken=f"Failed to close {app_name}.", error=str(exc))

    elif _ON_WINDOWS:
        try:
            import psutil
            killed = 0
            for proc in psutil.process_iter(["name"]):
                if (proc.info.get("name") or "").lower() == exe.lower():
                    proc.kill()
                    killed += 1
            if killed:
                return ToolResult(success=True,
                                  text=f"Closed {killed} instance(s) of {app_name}.",
                                  spoken=f"Done. {app_name} closed.",
                                  data={"app": app_name, "instances": killed})
            return ToolResult(success=False,
                              text=f"{app_name} is not running.",
                              spoken=f"{app_name} doesn't appear to be running.")
        except Exception as exc:
            return ToolResult(success=False, text=str(exc),
                              spoken=f"Failed to close {app_name}.", error=str(exc))

    return ToolResult(success=False, text="Not supported on this platform.",
                      spoken="App control is not available on this platform.")


# ── open_system_settings (specific pages) ────────────────────────────────────

_SETTINGS_PAGES: Dict[str, str] = {
    "home":         "ms-settings:",
    "display":      "ms-settings:display",
    "sound":        "ms-settings:sound",
    "network":      "ms-settings:network",
    "wifi":         "ms-settings:network-wifi",
    "bluetooth":    "ms-settings:bluetooth",
    "apps":         "ms-settings:appsfeatures",
    "privacy":      "ms-settings:privacy",
    "update":       "ms-settings:windowsupdate",
    "power":        "ms-settings:powersleep",
    "storage":      "ms-settings:storagesense",
    "accounts":     "ms-settings:accounts",
    "time":         "ms-settings:dateandtime",
    "language":     "ms-settings:regionlanguage",
    "accessibility":"ms-settings:easeofaccess",
    "taskbar":      "ms-settings:taskbar",
    "startup":      "ms-settings:startupapps",
    "mouse":        "ms-settings:mousetouchpad",
    "keyboard":     "ms-settings:keyboard",
    "camera":       "ms-settings:camera",
    "notifications":"ms-settings:notifications",
    "personalization": "ms-settings:personalization",
    "themes":       "ms-settings:themes",
}


def _exec_open_system_settings(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    page = params.get("page", "home").lower().strip()
    uri  = _SETTINGS_PAGES.get(page, "ms-settings:")
    label = page.replace("_", " ").capitalize() if page != "home" else "Windows Settings"

    try:
        if _ON_WSL:
            _cmd = _find_cmdexe() or "cmd.exe"
            subprocess.Popen(f'{_cmd} /c start "" "{uri}"', shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif _ON_WINDOWS:
            subprocess.Popen(["start", "", uri], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return ToolResult(success=False, text="Settings only available on Windows.",
                              spoken="System settings are only available on Windows.")

        return ToolResult(success=True, text=f"Opened {label}",
                          spoken=f"Opening {label}.",
                          data={"page": page, "uri": uri})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Couldn't open Settings.", error=str(exc))


def _exec_write_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw      = params.get("path", "").strip()
    content  = params.get("content", "")
    win_path = resolve_path(raw)

    if not is_safe_path(win_path):
        return ToolResult(success=False, text=f"Access denied: {win_path}",
                          spoken="That location is protected. I can't write there.", error="Blocked path")

    if not is_safe_write(win_path):
        return ToolResult(success=False, text=f"Write blocked: {win_path}",
                          spoken="Writing to that file type is restricted.", error="Unsafe write")

    fs_path = _fs_path(win_path)
    try:
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fs_path.write_text(content, encoding="utf-8")
        msg = f"Wrote {len(content)} chars to {win_path}"
        return ToolResult(success=True, text=msg, spoken=f"Done. File saved to {fs_path.name}.",
                          data={"path": win_path, "bytes": len(content.encode())})
    except PermissionError:
        return ToolResult(success=False, text="Permission denied.", spoken="I don't have permission to write there.", error="PermissionError")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't write the file.", error=str(exc))


# ── Move file ─────────────────────────────────────────────────────────────────

def _exec_move_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    src_raw  = params.get("source", "").strip()
    dst_raw  = params.get("destination", "").strip()
    if not src_raw or not dst_raw:
        return ToolResult(success=False, text="Source and destination are required.",
                          spoken="Please tell me both the file to move and where to move it.")

    src_win = resolve_path(src_raw)
    dst_win = resolve_path(dst_raw)

    if not is_safe_path(src_win) or not is_safe_path(dst_win):
        return ToolResult(success=False, text="Path blocked by safety layer.",
                          spoken="That path is restricted. I can't move files there.", error="Blocked")

    src_fs = _fs_path(src_win)
    dst_fs = _fs_path(dst_win)

    if not src_fs.exists():
        return ToolResult(success=False, text=f"Source not found: {src_win}",
                          spoken=f"I couldn't find the file: {src_fs.name}")

    try:
        # If destination is a directory, move the file into it
        if dst_fs.is_dir():
            dst_fs = dst_fs / src_fs.name
        dst_fs.parent.mkdir(parents=True, exist_ok=True)
        src_fs.rename(dst_fs)
        return ToolResult(success=True, text=f"Moved {src_win} → {dst_win}",
                          spoken=f"Done — moved {src_fs.name} to {dst_fs.parent.name}.",
                          data={"source": src_win, "destination": str(dst_fs)})
    except PermissionError:
        return ToolResult(success=False, text="Permission denied.", spoken="I don't have permission to move that file.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't move the file.", error=str(exc))


# ── Rename file / folder ───────────────────────────────────────────────────────

def _exec_rename_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    path_raw = params.get("path", "").strip()
    new_name = params.get("new_name", "").strip()

    if not path_raw or not new_name:
        return ToolResult(success=False, text="Path and new name are required.",
                          spoken="Please tell me what to rename and what to call it.")

    new_name = re.sub(r'[<>:"|?*\\/]', "", new_name).strip()
    if not new_name:
        return ToolResult(success=False, text="Invalid new name.", spoken="That name isn't valid.")

    src_win = resolve_path(path_raw)
    if not is_safe_path(src_win):
        return ToolResult(success=False, text="Path blocked by safety layer.",
                          spoken="That path is restricted.", error="Blocked")

    src_fs = _fs_path(src_win)
    if not src_fs.exists():
        return ToolResult(success=False, text=f"Not found: {path_raw}",
                          spoken=f"I couldn't find {src_fs.name or path_raw}.")

    dst_fs = src_fs.parent / new_name
    if dst_fs.exists():
        return ToolResult(success=False,
                          text=f"'{new_name}' already exists in that location.",
                          spoken=f"There's already something named {new_name} there.")
    try:
        src_fs.rename(dst_fs)
        _store_last_action(ctx, "rename_file", params, str(dst_fs))
        return ToolResult(
            success=True,
            text=f"Renamed '{src_fs.name}' → '{new_name}'",
            spoken=f"Done — renamed to {new_name}.",
            data={"old_name": src_fs.name, "new_name": new_name, "path": wsl_to_win(str(dst_fs))},
        )
    except PermissionError:
        return ToolResult(success=False, text="Permission denied.",
                          spoken="I don't have permission to rename that.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Rename failed.", error=str(exc))


# ── Copy file / folder ────────────────────────────────────────────────────────

def _exec_copy_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    src_raw = params.get("source", "").strip()
    dst_raw = params.get("destination", "").strip()

    if not src_raw or not dst_raw:
        return ToolResult(success=False, text="Source and destination are required.",
                          spoken="Please tell me what to copy and where to put it.")

    src_win = resolve_path(src_raw)
    dst_win = resolve_path(dst_raw)

    if not is_safe_path(src_win) or not is_safe_path(dst_win):
        return ToolResult(success=False, text="Path blocked by safety layer.",
                          spoken="That path is restricted.", error="Blocked")

    src_fs = _fs_path(src_win)
    if not src_fs.exists():
        return ToolResult(success=False, text=f"Source not found: {src_raw}",
                          spoken=f"I couldn't find {src_fs.name or src_raw}.")

    dst_fs = _fs_path(dst_win)
    try:
        if dst_fs.is_dir():
            dst_fs = dst_fs / src_fs.name
        dst_fs.parent.mkdir(parents=True, exist_ok=True)

        import shutil as _shutil
        if src_fs.is_dir():
            _shutil.copytree(str(src_fs), str(dst_fs))
        else:
            _shutil.copy2(str(src_fs), str(dst_fs))

        _store_last_action(ctx, "copy_file", params, str(dst_fs))
        return ToolResult(
            success=True,
            text=f"Copied '{src_fs.name}' → '{dst_fs}'",
            spoken=f"Done — copied {src_fs.name}.",
            data={"source": src_win, "destination": wsl_to_win(str(dst_fs))},
        )
    except PermissionError:
        return ToolResult(success=False, text="Permission denied.",
                          spoken="I don't have permission to copy there.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Copy failed.", error=str(exc))


# ── Delete file ───────────────────────────────────────────────────────────────

def _exec_delete_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw = params.get("path", "").strip()

    if not raw:
        return ToolResult(success=False, text="File path required.", spoken="Which file should I delete?")

    # Fix AI-generated paths like "c\games" (letter + backslash, no colon) → "C:\games"
    # resolve_path requires "C:\" prefix; without the colon it returns the string unchanged.
    _dm = re.match(r'^([A-Za-z])\\(.+)$', raw)
    if _dm:
        raw = f"{_dm.group(1).upper()}:\\{_dm.group(2)}"
        logger.info("[delete_file] normalized drive path → %r", raw)

    # For bare names (no drive/slash) resolve via FolderMemory before guessing path
    if raw and not any(c in raw for c in ('\\', '/', ':')):
        try:
            from ..services.history_service import history_service as _hs_del
            _fm_del = _hs_del.lookup_folder(raw.lower().strip())
            if _fm_del and _fm_del.get("full_wsl") and os.path.exists(_fm_del["full_wsl"]):
                raw = wsl_to_win(_fm_del["full_wsl"])
                logger.info("[delete_file] FolderMemory resolved %r → %r", params.get("path"), raw)
        except Exception:
            pass

    win_path = resolve_path(raw)
    if not is_safe_path(win_path):
        return ToolResult(success=False, text=f"Blocked: {win_path}",
                          spoken="That path is restricted. I won't delete it.", error="Blocked")

    fs = _fs_path(win_path)
    logger.info("[delete_file] raw=%r  win_path=%r  fs=%r  fs.exists=%s",
                raw, win_path, str(fs), fs.exists())
    if not fs.exists():
        # Use WSL2 fs path for parent/name — Path() on Linux can't parse Windows backslashes.
        target_name = fs.name if (fs.name and fs.name != str(fs)) else Path(raw.replace("\\", "/")).name
        fs_parent   = fs.parent if str(fs.parent) != "." else None

        logger.info("[delete_file] SCAN: fs_parent=%r  target_name=%r", str(fs_parent), target_name)

        # If no path separators in raw — also search common locations
        if not any(c in raw for c in ('\\', '/', ':')):
            special = _get_win_special()
            extra_bases_win = [
                _windows_home(),
                special.get("desktop", ""),
                special.get("documents", ""),
                special.get("downloads", ""),
            ]
            extra_bases = [_fs_path(b) for b in extra_bases_win if b]
        else:
            extra_bases = []

        # Build deduped list of parent directories to scan (WSL2 Path objects)
        scan_parents = ([fs_parent] if fs_parent else []) + extra_bases
        seen: set[str] = set()
        scan_parents = [p for p in scan_parents if str(p) not in seen and not seen.add(str(p))]
        logger.info("[delete_file] scan_parents=%r", [str(p) for p in scan_parents])


        for base_fs in scan_parents:
            if not base_fs or not base_fs.is_dir():
                continue
            try:
                children = [c.name for c in base_fs.iterdir()]
            except PermissionError:
                continue
            logger.info("[delete_file] scanning base=%r  found %d items: %r",
                        str(base_fs), len(children), children[:20])
            # 1. Case-insensitive exact match
            for child in children:
                if child.lower() == target_name.lower():
                    fs       = base_fs / child
                    win_path = wsl_to_win(str(fs))
                    break
            if fs.exists():
                break
            # 2. Fuzzy match (cutoff 0.6)
            matches = difflib.get_close_matches(
                target_name.lower(),
                [c.lower() for c in children],
                n=1, cutoff=0.6,
            )
            if matches:
                matched_lower = matches[0]
                child = next(c for c in children if c.lower() == matched_lower)
                fs       = base_fs / child
                win_path = wsl_to_win(str(fs))
                break

        if not fs.exists():
            # Last resort: FolderMemory lookup on base name
            try:
                from ..services.history_service import history_service as _hs_fb
                _fm_fb = _hs_fb.lookup_folder(target_name.lower().strip())
                if _fm_fb and _fm_fb.get("full_wsl") and os.path.exists(_fm_fb["full_wsl"]):
                    fs       = Path(_fm_fb["full_wsl"])
                    win_path = wsl_to_win(str(fs))
                    logger.info("[delete_file] FolderMemory fallback: %r → %r", target_name, str(fs))
            except Exception:
                pass

        if not fs.exists():
            return ToolResult(success=False, text=f"Not found: {win_path}",
                              spoken=f"I couldn't find '{raw}'. Does it exist?")

    wsl_path = safe_path(str(fs))
    try:
        if fs.is_dir():
            shutil.rmtree(wsl_path)
        else:
            os.remove(wsl_path)
        # Verify deletion actually happened
        if os.path.exists(wsl_path):
            return ToolResult(success=False, text=f"Delete failed — still exists: {wsl_path}",
                              spoken=f"Couldn't delete {fs.name}, it still exists.",
                              error="path still exists after delete")
        return ToolResult(success=True, text=f"Deleted: {win_path}",
                          spoken=f"Deleted {fs.name}.",
                          data={"path": win_path, "deleted": True})
    except PermissionError:
        return ToolResult(success=False, text="Permission denied.", spoken="I don't have permission to delete that.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't delete the file.", error=str(exc))


# ── Context helper ────────────────────────────────────────────────────────────

def _store_last_action(ctx: Dict[str, Any], tool: str, params: dict, result: str) -> None:
    try:
        from ..services.memory_service import memory_service
        memory_service.set_last_action(tool, params, result)
    except Exception:
        pass


# ── Volume control ────────────────────────────────────────────────────────────

_SET_VOLUME_PS1_TMPL = r"""
try {
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    void EnumAudioEndpoints(int df, int st, out IntPtr c);
    void GetDefaultAudioEndpoint(int df, int role, [MarshalAs(UnmanagedType.Interface)] out IMMDevice d);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object pp);
}
[ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    void Reg(IntPtr n); void Unreg(IntPtr n); void GetCC(out uint c);
    void SetLvl(float v, ref Guid g);
    [PreserveSig] int SetMasterVolumeLevelScalar(float v, ref Guid g);
}
public class VolSetter {
    static Guid CLSID = new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E");
    static Guid EIID  = new Guid("A95664D2-9614-4F35-A746-DE8DB63617E6");
    [DllImport("ole32.dll")] static extern int CoCreateInstance(ref Guid r, IntPtr u, int c, ref Guid i, out IntPtr p);
    [DllImport("ole32.dll")] static extern int CoInitializeEx(IntPtr r, int f);
    [DllImport("ole32.dll")] static extern void CoUninitialize();
    public static void Set(float level) {
        CoInitializeEx(IntPtr.Zero, 0);
        try {
            IntPtr pE; CoCreateInstance(ref CLSID, IntPtr.Zero, 1, ref EIID, out pE);
            IMMDeviceEnumerator en = (IMMDeviceEnumerator)Marshal.GetObjectForIUnknown(pE);
            IMMDevice dev; en.GetDefaultAudioEndpoint(0, 1, out dev);
            Guid vIID = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
            object vo; dev.Activate(ref vIID, 1, IntPtr.Zero, out vo);
            IAudioEndpointVolume vol = (IAudioEndpointVolume)vo;
            Guid empty = Guid.Empty; vol.SetMasterVolumeLevelScalar(level, ref empty);
        } finally { CoUninitialize(); }
    }
}
"@ -ErrorAction Stop
[VolSetter]::Set(LEVEL_FLOAT)
Write-Output "OK"
} catch { Write-Output "ERR:$($_.Exception.Message)" }
"""


def _pycaw_volume(action: str, level: int = 50, steps: int = 5) -> bool:
    """
    Control Windows volume via pycaw (native COM, ~10ms).
    Works only when running native Windows Python (sys.platform == 'win32').
    On WSL2 this always returns False — caller uses PowerShell fallback.
    """
    if sys.platform != "win32":
        return False
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
        from comtypes import CLSCTX_ALL  # type: ignore
        devices   = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol       = interface.QueryInterface(IAudioEndpointVolume)
        if action == "set":
            vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level / 100.0)), None)
        elif action == "mute":
            vol.SetMute(1, None)
        elif action == "unmute":
            vol.SetMute(0, None)
        elif action == "toggle":
            vol.SetMute(int(not vol.GetMute()), None)
        elif action == "up":
            cur = vol.GetMasterVolumeLevelScalar()
            vol.SetMasterVolumeLevelScalar(min(1.0, cur + steps * 0.02), None)
        else:  # down
            cur = vol.GetMasterVolumeLevelScalar()
            vol.SetMasterVolumeLevelScalar(max(0.0, cur - steps * 0.02), None)
        return True
    except Exception:
        return False


def _exec_volume_control(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    action = params.get("action", "up")   # up | down | mute | unmute | toggle | set
    steps  = max(1, min(20, int(params.get("steps", 5))))
    level  = max(0, min(100, int(params.get("level", 50))))

    # ── Tier 1: pycaw (native Windows Python — ~10ms) ─────────────────────────
    if _pycaw_volume(action, level, steps):
        label_map = {
            "set": f"set to {level}%", "up": "increased", "down": "decreased",
            "mute": "muted", "unmute": "unmuted", "toggle": "toggled",
        }
        spoken = f"Volume {label_map.get(action, action)}."
        _store_last_action(ctx, "volume_control", params, spoken)
        return ToolResult(success=True, text=spoken, spoken=spoken,
                          data={"action": action, "level": level})

    # ── Tier 2: PowerShell fallback (WSL2) ────────────────────────────────────
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="Volume control unavailable.",
                          spoken="Volume control is not available on this system.")

    if action == "set":
        # Lighter PS than the C# inline: use Windows.Media.Audio via reflection.
        # Falls back gracefully if WinRT not available.
        ps = (
            f"try {{"
            f" Add-Type -AssemblyName System.Runtime.WindowsRuntime -EA Stop;"
            f" $vol=[Windows.Media.Devices.MediaDevice,Windows.Media,ContentType=WindowsRuntime];"
            f" [void]$vol; Write-Output 'WRT_OK'"
            f"}} catch {{"
            f" $w=New-Object -ComObject WScript.Shell;"
            f" $cur=0; $target=[int]({level}/2);"
            f" 1..$target|%{{$w.SendKeys([char]175)}}; Write-Output 'KEY_OK'"
            f"}}"
        )
        # Absolute set via C# COM — invoke powershell.exe directly (no cmd.exe quoting issues)
        ps1_path = "/mnt/c/Windows/Temp/_xyron_setvol.ps1"
        win_path = "C:\\Windows\\Temp\\_xyron_setvol.ps1"
        script = _SET_VOLUME_PS1_TMPL.replace("LEVEL_FLOAT", f"{level / 100:.3f}")
        ps_exe = _find_powershell()
        try:
            Path(ps1_path).write_text(script)
            r = subprocess.run(
                [ps_exe, "-NoProfile", "-NonInteractive", "-File", win_path],
                capture_output=True, text=True, timeout=20, errors="ignore",
            )
            out = (r.stdout or "").strip()
            if "ERR:" in out or (r.returncode != 0 and not out):
                raise RuntimeError(out or r.stderr or "PS1 failed")
            spoken = f"Volume set to {level}%."
            _store_last_action(ctx, "volume_control", params, spoken)
            return ToolResult(success=True, text=spoken, spoken=spoken,
                              data={"action": "set", "level": level})
        except Exception as exc:
            return ToolResult(success=False, text=str(exc), spoken="Volume set failed.", error=str(exc))

    # up / down / mute / unmute via media key simulation (fast, no compile)
    key_map = {
        "mute":   (173, "muted"),
        "unmute": (173, "unmuted"),
        "toggle": (173, "toggled"),
        "up":     (175, "increased"),
        "down":   (174, "decreased"),
    }
    key_code, label = key_map.get(action, (175, "changed"))
    if action in ("mute", "unmute", "toggle"):
        ps = f"(New-Object -ComObject WScript.Shell).SendKeys([char]{key_code})"
    else:
        ps = f"$w=New-Object -ComObject WScript.Shell; 1..{steps}|%{{$w.SendKeys([char]{key_code})}}"

    try:
        subprocess.run(
            [cmd_exe, "/c", f'powershell -NoProfile -NonInteractive -Command "{ps}"'],
            check=True, capture_output=True, timeout=8,
        )
        spoken = f"Volume {label}."
        _store_last_action(ctx, "volume_control", params, spoken)
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"action": action})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Volume control failed.", error=str(exc))


# ── Brightness control ────────────────────────────────────────────────────────

def _sbc_brightness(action: str, level: int = 50, delta: int = 20) -> bool:
    """
    Control screen brightness via screen-brightness-control (cross-platform).
    Returns False if the library is unavailable or the display is unsupported.
    """
    try:
        import screen_brightness_control as sbc  # type: ignore
        if action == "set":
            sbc.set_brightness(max(0, min(100, level)))
        elif action == "up":
            cur = sbc.get_brightness(display=0)
            cur = cur[0] if isinstance(cur, list) else cur
            sbc.set_brightness(min(100, cur + delta), display=0)
        else:  # down
            cur = sbc.get_brightness(display=0)
            cur = cur[0] if isinstance(cur, list) else cur
            sbc.set_brightness(max(0, cur - delta), display=0)
        return True
    except Exception:
        return False


def _exec_brightness_control(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    action = params.get("action", "up")   # up | down | set
    delta  = max(5, min(50, int(params.get("delta", 20))))
    level  = int(params.get("level", 50))

    label = f"set to {level}%" if action == "set" else ("increased" if action == "up" else "decreased")

    # ── Tier 1: screen-brightness-control (~20ms, works on WSL2 via DDC/CI) ───
    if _sbc_brightness(action, level, delta):
        spoken = f"Brightness {label}."
        _store_last_action(ctx, "brightness_control", params, spoken)
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"action": action})

    # ── Tier 2: WMI PowerShell fallback (internal display only) ──────────────
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="Brightness control unavailable.",
                          spoken="I couldn't adjust the brightness. This may not be supported on your display.")

    if action == "set":
        ps = (
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods"
            f" -ErrorAction Stop).WmiSetBrightness(1,{level})"
        )
    elif action == "up":
        ps = (
            f"$cur=(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness"
            f" -ErrorAction Stop).CurrentBrightness;"
            f"$new=[Math]::Min(100,$cur+{delta});"
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods"
            f" -ErrorAction Stop).WmiSetBrightness(1,$new)"
        )
    else:
        ps = (
            f"$cur=(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness"
            f" -ErrorAction Stop).CurrentBrightness;"
            f"$new=[Math]::Max(0,$cur-{delta});"
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods"
            f" -ErrorAction Stop).WmiSetBrightness(1,$new)"
        )

    try:
        subprocess.run(
            [cmd_exe, "/c", f'powershell -NoProfile -NonInteractive -Command "{ps}"'],
            check=True, capture_output=True, timeout=10,
        )
        spoken = f"Brightness {label}."
        _store_last_action(ctx, "brightness_control", params, spoken)
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"action": action})
    except Exception as exc:
        return ToolResult(
            success=False, text=str(exc),
            spoken="I couldn't adjust the brightness. This may not be supported on your display.",
            error=str(exc),
        )


# ── Shutdown / Restart ────────────────────────────────────────────────────────

def _exec_sleep_system(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Sleep is not available on this system.")
    try:
        # Fire-and-forget: the sleep command suspends the OS so it never
        # returns within a blocking timeout — use Popen to avoid that hang.
        subprocess.Popen(
            [cmd_exe, "/c", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        spoken = "Going to sleep. Sweet dreams."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Sleep command failed.", error=str(exc))


def _exec_hibernate_system(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Hibernate is not available on this system.")
    try:
        subprocess.run(
            [cmd_exe, "/c", "shutdown /h"],
            check=True, capture_output=True, timeout=5,
        )
        spoken = "Hibernating now. See you when you're back."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Hibernate failed. Hibernate may be disabled on this system.",
                          error=str(exc))


def _exec_lock_system(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Lock is not available on this system.")
    try:
        subprocess.run(
            [cmd_exe, "/c", "rundll32.exe user32.dll,LockWorkStation"],
            check=True, capture_output=True, timeout=5,
        )
        spoken = "Screen locked."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Lock command failed.", error=str(exc))


def _exec_shutdown_system(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    delay   = max(0, int(params.get("delay", 0)))
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Shutdown is not available on this system.")
    try:
        subprocess.run(
            [cmd_exe, "/c", f"shutdown /s /t {delay}"],
            check=True, capture_output=True, timeout=5,
        )
        spoken = "Shutting down. Goodbye." if delay == 0 else f"Shutting down in {delay} seconds."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"delay": delay})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Shutdown command failed.", error=str(exc))


def _exec_restart_system(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    delay   = max(0, int(params.get("delay", 0)))
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Restart is not available on this system.")
    try:
        subprocess.run(
            [cmd_exe, "/c", f"shutdown /r /t {delay}"],
            check=True, capture_output=True, timeout=5,
        )
        spoken = "Restarting now. See you soon." if delay == 0 else f"Restarting in {delay} seconds."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"delay": delay})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Restart command failed.", error=str(exc))


# ── Register all system tools ─────────────────────────────────────────────────

registry.register(
    name="open_directory",
    definition={
        "type": "function",
        "function": {
            "name": "open_directory",
            "description": "Open a folder or drive in the file explorer. Use for: 'open E drive', 'open Documents', 'open downloads folder'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path or natural language like 'E drive', 'E:\\\\', 'Desktop', 'Documents', 'Downloads', 'C:\\\\Users\\\\...'",
                    }
                },
                "required": ["path"],
            },
        },
    },
    executor=_exec_open_directory,
    risk="low",
    category="system",
)

registry.register(
    name="create_folder",
    definition={
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": (
                "Create a new folder/directory. Use for: 'create folder X', 'make a folder called X on Y'. "
                "Extract ONLY the folder name — not the full sentence. "
                "Examples: 'create folder TestXyron on Desktop' → name='TestXyron', path='Desktop'; "
                "'make a folder called Projects in D drive' → name='Projects', path='D:\\\\'; "
                "'create folder Reports' → name='Reports', path omitted (defaults to Desktop)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Folder name only — a single word or short phrase, NOT the full sentence."},
                    "path": {"type": "string", "description": "Parent directory (e.g. 'Desktop', 'D:\\\\', 'E:\\\\Projects'). Defaults to Desktop."},
                },
                "required": ["name"],
            },
        },
    },
    executor=_exec_create_folder,
    risk="medium",
    category="system",
)

registry.register(
    name="create_subfolders",
    definition={
        "type": "function",
        "function": {
            "name": "create_subfolders",
            "description": (
                "Create multiple subfolders inside a parent directory. "
                "Use when user says 'create 3 subfolders', 'make subfolders named Work Health Finance', "
                "'create subfolders in it'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "parent": {"type": "string", "description": "Parent folder path. Leave empty to use last created folder."},
                    "names":  {"type": "array", "items": {"type": "string"}, "description": "Subfolder names to create."},
                    "count":  {"type": "integer", "description": "Number of generic subfolders if names not provided."},
                },
                "required": [],
            },
        },
    },
    executor=_exec_create_subfolders,
    risk="medium",
    category="system",
)

registry.register(
    name="list_directory",
    definition={
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a folder or drive. Use for: 'what's in E drive', 'show files in Documents'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to list (e.g. 'E:\\\\', 'Desktop')"},
                },
                "required": ["path"],
            },
        },
    },
    executor=_exec_list_directory,
    risk="low",
    category="system",
)

registry.register(
    name="open_file",
    definition={
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "Open a specific file with its default application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full path to the file to open"},
                },
                "required": ["path"],
            },
        },
    },
    executor=_exec_open_file,
    risk="low",
    category="system",
)

registry.register(
    name="open_application",
    definition={
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Launch a desktop application. Use for: 'open VS Code', 'launch Chrome', 'start Spotify'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "App to launch: vscode, chrome, firefox, spotify, notepad, calculator, explorer, terminal, word, excel, powerpoint, outlook, teams, slack, discord, zoom, vlc, paint, brave, steam, telegram.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    executor=_exec_open_application,
    risk="low",
    category="system",
)

registry.register(
    name="search_files",
    definition={
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files matching a name or pattern within a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename or pattern to search for (e.g. '*.pdf', 'report')"},
                    "path":  {"type": "string", "description": "Directory to search in (defaults to user home)"},
                },
                "required": ["query"],
            },
        },
    },
    executor=_exec_search_files,
    risk="low",
    category="system",
)

registry.register(
    name="system_info",
    definition={
        "type": "function",
        "function": {
            "name": "system_info",
            "description": (
                "ALWAYS use this tool for ANY question about the user's computer hardware or OS. "
                "Triggers: 'what CPU', 'how much RAM', 'what are my specs', 'what OS', 'disk space', "
                "'my computer', 'my laptop', 'my PC', 'system info', 'processor', 'how much RAM', 'RAM size', 'storage'. "
                "Returns real Windows CPU name, RAM size, OS version, and drive space. "
                "Do NOT use for conversational uses of the word 'memory' — only use for hardware RAM questions. "
                "Do NOT use search_web for these questions — use this tool instead."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_system_info,
    risk="low",
    category="system",
)

registry.register(
    name="write_file",
    definition={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file. Only allowed for user-owned directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Full file path to write"},
                    "content": {"type": "string", "description": "Text content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    executor=_exec_write_file,
    risk="medium",
    category="system",
)

registry.register(
    name="system_health",
    definition={
        "type": "function",
        "function": {
            "name": "system_health",
            "description": (
                "Get LIVE system health: CPU usage %, RAM usage %, disk usage % for all drives. "
                "Use for: 'how is my system doing', 'CPU usage', 'RAM usage', 'is my disk full', "
                "'system performance', 'is my computer slow'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_system_health,
    risk="low",
    category="system",
)

registry.register(
    name="open_drive",
    definition={
        "type": "function",
        "function": {
            "name": "open_drive",
            "description": "Open a specific drive (C, D, E, etc.) in File Explorer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "drive": {
                        "type": "string",
                        "description": "Drive letter to open: 'C', 'D', 'E', etc.",
                    }
                },
                "required": ["drive"],
            },
        },
    },
    executor=_exec_open_drive,
    risk="low",
    category="system",
)

registry.register(
    name="get_running_apps",
    definition={
        "type": "function",
        "function": {
            "name": "get_running_apps",
            "description": (
                "List all currently running applications. "
                "Use for: 'what apps are running', 'show running programs', 'what is open'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_get_running_apps,
    risk="low",
    category="system",
)

registry.register(
    name="kill_app",
    definition={
        "type": "function",
        "function": {
            "name": "kill_app",
            "description": "Close/kill a running application by name. Safe — cannot kill system processes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application name to close, e.g. 'chrome', 'spotify', 'notepad'",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    executor=_exec_kill_app,
    risk="medium",
    category="system",
)

registry.register(
    name="open_system_settings",
    definition={
        "type": "function",
        "function": {
            "name": "open_system_settings",
            "description": (
                "Open Windows Settings to a specific page. "
                "Pages: home, display, sound, network, wifi, bluetooth, apps, privacy, "
                "update, power, storage, accounts, time, language, accessibility, "
                "taskbar, startup, mouse, keyboard, notifications, personalization, themes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": "Settings page name (e.g. 'display', 'sound', 'update'). Default: 'home'.",
                    }
                },
                "required": [],
            },
        },
    },
    executor=_exec_open_system_settings,
    risk="low",
    category="system",
)

registry.register(
    name="move_file",
    definition={
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move a file or folder from one location to another. Use when user says 'move X to Y', 'transfer X to Y folder'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source":      {"type": "string", "description": "Path of file/folder to move"},
                    "destination": {"type": "string", "description": "Destination path or folder"},
                },
                "required": ["source", "destination"],
            },
        },
    },
    executor=_exec_move_file,
    risk="medium",
    category="system",
)

registry.register(
    name="delete_file",
    definition={
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": (
                "Permanently delete a file or folder. Use when user says 'delete X', 'remove X', 'delete folder X'. "
                "Extract ONLY the file/folder name or path — not the full sentence. "
                "Examples: 'delete TestXyron' → path='TestXyron'; "
                "'delete the folder on Desktop named Projects' → path='Desktop\\\\Projects'; "
                "'remove file report.txt from Downloads' → path='Downloads\\\\report.txt'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or folder name/path to delete. Extract only the name, not the full sentence."},
                },
                "required": ["path"],
            },
        },
    },
    executor=_exec_delete_file,
    risk="high",
    category="system",
)

registry.register(
    name="rename_file",
    definition={
        "type": "function",
        "function": {
            "name": "rename_file",
            "description": (
                "Rename a file or folder. Use when user says 'rename X to Y', 'change name of X to Y'. "
                "Examples: 'rename report.txt to final_report.txt' → path='report.txt', new_name='final_report.txt'; "
                "'rename the Projects folder to Work' → path='Projects', new_name='Work'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":     {"type": "string", "description": "Current path or name of the file/folder to rename"},
                    "new_name": {"type": "string", "description": "New name only (not a full path)"},
                },
                "required": ["path", "new_name"],
            },
        },
    },
    executor=_exec_rename_file,
    risk="medium",
    category="system",
)

registry.register(
    name="copy_file",
    definition={
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": (
                "Copy a file or folder to another location. Use when user says 'copy X to Y', 'duplicate X in Y', 'make a copy of X'. "
                "Examples: 'copy report.pdf to D drive' → source='report.pdf', destination='D:\\\\'; "
                "'copy the Projects folder to Desktop' → source='Projects', destination='Desktop'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source":      {"type": "string", "description": "Path of file/folder to copy"},
                    "destination": {"type": "string", "description": "Destination folder (e.g. 'Desktop', 'D:\\\\', 'E:\\\\Backup')"},
                },
                "required": ["source", "destination"],
            },
        },
    },
    executor=_exec_copy_file,
    risk="medium",
    category="system",
)

registry.register(
    name="volume_control",
    definition={
        "type": "function",
        "function": {
            "name": "volume_control",
            "description": (
                "Control system volume. "
                "Use for: 'turn up the volume', 'volume down', 'mute', 'unmute'. "
                "action: up | down | mute | unmute. steps: 1-20 (each step ~2%)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["up", "down", "mute", "unmute"],
                               "description": "Volume action to perform"},
                    "steps":  {"type": "integer", "description": "Number of volume steps (default 5)"},
                },
                "required": ["action"],
            },
        },
    },
    executor=_exec_volume_control,
    risk="low",
    category="system",
)

registry.register(
    name="brightness_control",
    definition={
        "type": "function",
        "function": {
            "name": "brightness_control",
            "description": (
                "Control screen brightness. "
                "Use for: 'increase brightness', 'brightness down', 'dim the screen', 'make it brighter'. "
                "action: up | down | set. delta: percent change (default 20). level: absolute 0-100 for 'set'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["up", "down", "set"],
                               "description": "Brightness action"},
                    "delta":  {"type": "integer", "description": "Percentage to change (default 20)"},
                    "level":  {"type": "integer", "description": "Absolute brightness level 0-100 (for 'set' action)"},
                },
                "required": ["action"],
            },
        },
    },
    executor=_exec_brightness_control,
    risk="low",
    category="system",
)

registry.register(
    name="sleep_system",
    definition={
        "type": "function",
        "function": {
            "name": "sleep_system",
            "description": "Put the Windows system to sleep (suspend). Use for: 'sleep', 'go to sleep', 'put to sleep'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_sleep_system,
    risk="low",
    category="system",
)

registry.register(
    name="hibernate_system",
    definition={
        "type": "function",
        "function": {
            "name": "hibernate_system",
            "description": "Hibernate the Windows system. Use for: 'hibernate', 'put in hibernate mode'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_hibernate_system,
    risk="low",
    category="system",
)

registry.register(
    name="lock_system",
    definition={
        "type": "function",
        "function": {
            "name": "lock_system",
            "description": "Lock the Windows workstation screen. Use for: 'lock', 'lock screen', 'lock the computer'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    executor=_exec_lock_system,
    risk="low",
    category="system",
)

registry.register(
    name="shutdown_system",
    definition={
        "type": "function",
        "function": {
            "name": "shutdown_system",
            "description": "Shut down the Windows system. ONLY call after explicit user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {"type": "integer",
                              "description": "Seconds before shutdown (0 = immediate). Default 0."},
                },
                "required": [],
            },
        },
    },
    executor=_exec_shutdown_system,
    risk="high",
    category="system",
)

registry.register(
    name="restart_system",
    definition={
        "type": "function",
        "function": {
            "name": "restart_system",
            "description": "Restart the Windows system. ONLY call after explicit user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {"type": "integer",
                              "description": "Seconds before restart (0 = immediate). Default 0."},
                },
                "required": [],
            },
        },
    },
    executor=_exec_restart_system,
    risk="high",
    category="system",
)


# =============================================================================
# EXTENDED SYSTEM CONTROL — 30 new tools
# Process, Display, Network/WiFi, Battery/Power, Storage, Audio, Maintenance
# Zero OpenAI — all pure OS/subprocess/psutil calls
# =============================================================================

_POWERSHELL_PATH: str | None = None


def _find_powershell() -> str | None:
    global _POWERSHELL_PATH
    if _POWERSHELL_PATH is not None:
        return _POWERSHELL_PATH
    for p in [
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe",
    ]:
        if Path(p).exists():
            _POWERSHELL_PATH = p
            return _POWERSHELL_PATH
    return None


def _ps(command: str, timeout: int = 10) -> tuple[bool, str]:
    """Run a Windows PowerShell command via persistent session (~30 ms vs ~400 ms)."""
    try:
        from ..services.ps_session import run_ps
        return run_ps(command, timeout=timeout)
    except Exception as exc:
        return False, str(exc)


# ── PROCESS MANAGEMENT ────────────────────────────────────────────────────────

def _exec_list_processes(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    top_n = int(params.get("top", 15))
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                mem_mb = round(p.info["memory_info"].rss / 1024 / 1024, 1)
                procs.append((p.info["name"] or "?", p.info["pid"], mem_mb))
            except Exception:
                pass
        procs.sort(key=lambda x: x[2], reverse=True)
        top = procs[:top_n]
        lines = [f"{n} (PID {pid}) — {mem}MB" for n, pid, mem in top]
        spoken = f"Top {len(top)} processes by RAM: " + "; ".join(
            f"{n} {mem}MB" for n, pid, mem in top[:5]) + "."
        return ToolResult(success=True, text="\n".join(lines), spoken=spoken,
                          data={"processes": [{"name": n, "pid": pid, "mem_mb": mem} for n, pid, mem in top]})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't list processes.", error=str(exc))


def _exec_kill_process(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    name = params.get("name", "").strip()
    pid  = params.get("pid")
    if not name and not pid:
        return ToolResult(success=False, text="name or pid required.", spoken="Which process should I kill?")
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.", spoken="Can't kill process — cmd.exe missing.")
    try:
        if pid:
            arg   = f"/pid {pid}"
            label = f"PID {pid}"
        else:
            exe   = name if name.lower().endswith(".exe") else name + ".exe"
            arg   = f"/im {exe}"
            label = name
        r = subprocess.run([cmd_exe, "/c", f"taskkill /f {arg}"],
                           capture_output=True, timeout=8)
        out = (r.stdout or b"").decode("utf-8", errors="ignore").strip()
        err_out = (r.stderr or b"").decode("utf-8", errors="ignore").strip()
        if r.returncode == 0 or "SUCCESS" in out.upper():
            spoken = f"Killed {label}."
            return ToolResult(success=True, text=spoken, spoken=spoken, data={"killed": label})
        err = out or err_out or "Process not found."
        return ToolResult(success=False, text=err, spoken=f"Couldn't kill {label}. It may not be running.", error=err)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Kill failed.", error=str(exc))


def _exec_get_startup_apps(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps(
        "Get-CimInstance Win32_StartupCommand | "
        "Select-Object Name,Command,Location | ConvertTo-Json -Compress",
        timeout=15,
    )
    if ok and out:
        try:
            import json as _j
            items = _j.loads(out)
            if isinstance(items, dict):
                items = [items]
            names = [i.get("Name", "?") for i in items]
            spoken = f"{len(names)} startup app{'s' if len(names)!=1 else ''}: " + \
                     ", ".join(names[:8]) + ("…" if len(names) > 8 else "") + "."
            return ToolResult(success=True, text="\n".join(names), spoken=spoken, data={"apps": items})
        except Exception:
            return ToolResult(success=True, text=out, spoken="Here are your startup apps.", data={})
    return ToolResult(success=False, text=out, spoken="Couldn't read startup apps.", error=out)


def _exec_disable_startup_app(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    name = params.get("name", "").strip()
    if not name:
        return ToolResult(success=False, text="name required.", spoken="Which startup app should I disable?")
    ok, out = _ps(
        f'$removed=$false; '
        f'$keys=@("HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run",'
        f'"HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"); '
        f'foreach($k in $keys){{'
        f' $v=Get-ItemProperty $k -ErrorAction SilentlyContinue; '
        f' if($v.PSObject.Properties.Name -contains "{name}"){{Remove-ItemProperty $k -Name "{name}" -ErrorAction SilentlyContinue; $removed=$true}} '
        f'}}; Write-Output $removed'
    )
    if ok and "True" in out:
        spoken = f"Removed '{name}' from startup."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"removed": name})
    ok2, _ = _ps(f'Disable-ScheduledTask -TaskName "{name}" -ErrorAction SilentlyContinue; Write-Output done')
    if ok2:
        spoken = f"Disabled startup task '{name}'."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"disabled": name})
    return ToolResult(success=False, text=out,
                      spoken=f"Couldn't find '{name}' in startup. Check the exact name.",
                      error=out)


# ── DISPLAY CONTROL ───────────────────────────────────────────────────────────

_DISPLAY_TYPEDEF = r"""
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string dmDeviceName;
    public short dmSpecVersion,dmDriverVersion,dmSize,dmDriverExtra;
    public int dmFields,dmPositionX,dmPositionY,dmDisplayOrientation,dmDisplayFixedOutput;
    public short dmColor,dmDuplex,dmYResolution,dmTTOption,dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string dmFormName;
    public short dmLogPixels;
    public int dmBitsPerPel,dmPelsWidth,dmPelsHeight,dmDisplayFlags,dmDisplayFrequency;
    public int dmICMMethod,dmICMIntent,dmMediaType,dmDitherType,dmR1,dmR2,dmPW,dmPH;
}
public class Display {
    [DllImport("user32.dll")] public static extern bool EnumDisplaySettings(string n,int m,ref DEVMODE d);
    [DllImport("user32.dll")] public static extern int ChangeDisplaySettings(ref DEVMODE d,int f);
}
"@ -ErrorAction SilentlyContinue
"""


def _exec_set_display_resolution(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    w = int(params.get("width",  1920))
    h = int(params.get("height", 1080))
    script = _DISPLAY_TYPEDEF + f"""
$dm = New-Object DEVMODE
$dm.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($dm)
[Display]::EnumDisplaySettings($null, -1, [ref]$dm) | Out-Null
$dm.dmPelsWidth = {w}; $dm.dmPelsHeight = {h}
$dm.dmFields = 0x80000 -bor 0x100000
Write-Output ([Display]::ChangeDisplaySettings([ref]$dm, 0))
"""
    ok, out = _ps(script, timeout=15)
    if ok and out.strip() == "0":
        spoken = f"Resolution set to {w}×{h}."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"width": w, "height": h})
    return ToolResult(success=False, text=out,
                      spoken=f"Couldn't set {w}×{h}. That resolution may not be supported.", error=out)


def _exec_set_refresh_rate(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    rate = int(params.get("rate", 60))
    script = _DISPLAY_TYPEDEF + f"""
$dm = New-Object DEVMODE
$dm.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($dm)
[Display]::EnumDisplaySettings($null, -1, [ref]$dm) | Out-Null
$dm.dmDisplayFrequency = {rate}
$dm.dmFields = 0x400000
Write-Output ([Display]::ChangeDisplaySettings([ref]$dm, 0))
"""
    ok, out = _ps(script, timeout=15)
    if ok and out.strip() == "0":
        spoken = f"Refresh rate set to {rate}Hz."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"rate": rate})
    return ToolResult(success=False, text=out,
                      spoken=f"Couldn't set {rate}Hz. Check if your monitor supports it.", error=out)


def _exec_virtual_desktop_create(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    _ps(
        'Add-Type -MemberDefinition \'[DllImport("user32.dll")] public static extern void keybd_event(byte v,byte s,uint f,UIntPtr e);\' '
        '-Name VDKB -Namespace vd -ErrorAction SilentlyContinue; '
        'vd.VDKB.keybd_event(0x5B,0,0,[UIntPtr]::Zero); '  # Win down
        'vd.VDKB.keybd_event(0x11,0,0,[UIntPtr]::Zero); '  # Ctrl down
        'vd.VDKB.keybd_event(0x44,0,0,[UIntPtr]::Zero); '  # D down
        'Start-Sleep -Milliseconds 80; '
        'vd.VDKB.keybd_event(0x44,0,2,[UIntPtr]::Zero); '  # D up
        'vd.VDKB.keybd_event(0x11,0,2,[UIntPtr]::Zero); '  # Ctrl up
        'vd.VDKB.keybd_event(0x5B,0,2,[UIntPtr]::Zero); '  # Win up
        'Write-Output done'
    )
    spoken = "New virtual desktop created."
    return ToolResult(success=True, text=spoken, spoken=spoken, data={})


def _exec_virtual_desktop_switch(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    direction = params.get("direction", "right").lower()
    vk        = 0x27 if direction == "right" else 0x25  # VK_RIGHT / VK_LEFT
    _ps(
        f'Add-Type -MemberDefinition \'[DllImport("user32.dll")] public static extern void keybd_event(byte v,byte s,uint f,UIntPtr e);\' '
        f'-Name VDKB2 -Namespace vd2 -ErrorAction SilentlyContinue; '
        f'vd2.VDKB2.keybd_event(0x5B,0,0,[UIntPtr]::Zero); '
        f'vd2.VDKB2.keybd_event(0x11,0,0,[UIntPtr]::Zero); '
        f'vd2.VDKB2.keybd_event({vk},0,0,[UIntPtr]::Zero); '
        f'Start-Sleep -Milliseconds 80; '
        f'vd2.VDKB2.keybd_event({vk},0,2,[UIntPtr]::Zero); '
        f'vd2.VDKB2.keybd_event(0x11,0,2,[UIntPtr]::Zero); '
        f'vd2.VDKB2.keybd_event(0x5B,0,2,[UIntPtr]::Zero); '
        f'Write-Output done'
    )
    label = "next" if direction == "right" else "previous"
    spoken = f"Switched to {label} virtual desktop."
    return ToolResult(success=True, text=spoken, spoken=spoken, data={"direction": direction})


def _exec_take_screenshot(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    import time as _t
    ts   = int(_t.time())
    dest = (params.get("path") or "").strip() or f"C:\\Users\\Public\\Pictures\\screenshot_{ts}.png"
    script = f"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing -ErrorAction SilentlyContinue
$s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($s.Width, $s.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($s.Location, [System.Drawing.Point]::Empty, $s.Size)
$bmp.Save("{dest}")
$g.Dispose(); $bmp.Dispose()
Write-Output OK
"""
    ok, out = _ps(script, timeout=15)
    if ok and "OK" in out:
        spoken = f"Screenshot saved to {dest}."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"path": dest})
    return ToolResult(success=False, text=out, spoken="Screenshot failed.", error=out)


# ── NETWORK / WIFI ────────────────────────────────────────────────────────────

def _exec_wifi_list(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.", spoken="WiFi list unavailable.")
    try:
        r = subprocess.run([cmd_exe, "/c", "netsh wlan show networks mode=bssid"],
                           capture_output=True, timeout=12)
        out = (r.stdout or b"").decode("utf-8", errors="ignore")
        ssids = list(dict.fromkeys(re.findall(r'(?<!\w)SSID\s+\d+\s*:\s*(.+)', out)))
        ssids = [s.strip() for s in ssids if s.strip()]
        if ssids:
            spoken = f"Found {len(ssids)} network{'s' if len(ssids)!=1 else ''}: " + \
                     ", ".join(ssids[:8]) + ("…" if len(ssids) > 8 else "") + "."
            return ToolResult(success=True, text="\n".join(ssids), spoken=spoken, data={"networks": ssids})
        return ToolResult(success=False, text="No networks found.",
                          spoken="No WiFi networks detected. Make sure WiFi is turned on.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't scan WiFi.", error=str(exc))


def _exec_wifi_connect(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ssid = params.get("ssid", "").strip()
    if not ssid:
        return ToolResult(success=False, text="ssid required.", spoken="Which WiFi network should I connect to?")
    cmd_exe = _find_cmdexe()
    try:
        r = subprocess.run([cmd_exe, "/c", f'netsh wlan connect name="{ssid}"'],
                           capture_output=True, timeout=10)
        out = (r.stdout or b"").decode("utf-8", errors="ignore").strip()
        if "successfully" in out.lower() or r.returncode == 0:
            spoken = f"Connecting to {ssid}."
            return ToolResult(success=True, text=spoken, spoken=spoken, data={"ssid": ssid})
        return ToolResult(success=False, text=out,
                          spoken=f"Couldn't connect to {ssid}. Make sure the profile exists first.", error=out)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="WiFi connect failed.", error=str(exc))


def _exec_wifi_disconnect(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    try:
        subprocess.run([cmd_exe, "/c", "netsh wlan disconnect"], capture_output=True, timeout=8)
        spoken = "WiFi disconnected."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't disconnect WiFi.", error=str(exc))


def _exec_network_speed_test(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    import time as _t, urllib.request as _ur
    url = "http://speedtest.tele2.net/1MB.zip"
    try:
        start = _t.time()
        with _ur.urlopen(url, timeout=20) as resp:
            data = resp.read()
        elapsed = max(_t.time() - start, 0.01)
        mb      = len(data) / 1024 / 1024
        mbps    = round((mb * 8) / elapsed, 2)
        spoken  = f"Download speed is about {mbps} Mbps."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"download_mbps": mbps})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Speed test failed. Check your connection.", error=str(exc))


def _exec_get_ip_info(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    try:
        import socket, urllib.request as _ur
        local_ip = socket.gethostbyname(socket.gethostname())
        try:
            pub = _ur.urlopen("https://api.ipify.org", timeout=5).read().decode().strip()
        except Exception:
            pub = "unavailable"
        spoken = f"Local IP is {local_ip}, public IP is {pub}."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"local": local_ip, "public": pub})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't get IP info.", error=str(exc))


def _exec_flush_dns(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.", spoken="DNS flush unavailable.")
    try:
        subprocess.run([cmd_exe, "/c", "ipconfig /flushdns"], capture_output=True, timeout=10, check=True)
        spoken = "DNS cache flushed."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="DNS flush failed.", error=str(exc))


# ── BATTERY & POWER PLANS ─────────────────────────────────────────────────────

def _exec_get_battery_status(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    # Try psutil first (works natively on Windows and some Linux kernels)
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat is not None:
            pct      = round(bat.percent, 1)
            charging = bat.power_plugged
            secs     = bat.secsleft if bat.secsleft and bat.secsleft > 0 else None
            if charging:
                status   = "charging"
                time_str = (f", fully charged in about {secs//3600}h {(secs%3600)//60}m"
                            if secs else "")
            else:
                status   = "discharging"
                time_str = (f", roughly {secs//3600}h {(secs%3600)//60}m remaining"
                            if secs else "")
            spoken = f"Battery is at {pct}% and {status}{time_str}."
            return ToolResult(success=True, text=spoken, spoken=spoken,
                              data={"percent": pct, "charging": charging, "secs_left": secs})
    except Exception:
        pass

    # PowerShell fallback (required on WSL2 where psutil returns None)
    try:
        ps_cmd = (
            "$b = Get-CimInstance Win32_Battery -ErrorAction Stop; "
            "if ($b) { '{0}|{1}' -f $b.EstimatedChargeRemaining, $b.BatteryStatus } "
            "else { 'none' }"
        )
        ok, out = _ps(ps_cmd, timeout=8)
        out = out.strip()
        if ok and out and out != "none":
            pct_str, status_code = out.split("|", 1)
            pct      = float(pct_str.strip())
            charging = int(status_code.strip()) == 2
            status   = "charging" if charging else "discharging"
            spoken   = f"Battery is at {round(pct)}% and {status}."
            return ToolResult(success=True, text=spoken, spoken=spoken,
                              data={"percent": pct, "charging": charging})
        return ToolResult(success=False, text="No battery detected.",
                          spoken="No battery detected — this machine may not have one.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc),
                          spoken="Couldn't read battery status.", error=str(exc))


_POWER_PLAN_GUIDS: Dict[str, str] = {
    "balanced":         "381b4222-f694-41f0-9685-ff5bb260df2e",
    "power saver":      "a1841308-3541-4fab-bc81-f71556f20b4a",
    "saver":            "a1841308-3541-4fab-bc81-f71556f20b4a",
    "performance":      "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "high performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
}
_POWER_PLAN_LABELS: Dict[str, str] = {
    "balanced": "Balanced", "power saver": "Power Saver", "saver": "Power Saver",
    "performance": "High Performance", "high performance": "High Performance",
}


def _exec_set_power_plan(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    plan = params.get("plan", "balanced").strip().lower()
    guid = _POWER_PLAN_GUIDS.get(plan)
    if not guid:
        return ToolResult(success=False, text=f"Unknown plan: {plan}",
                          spoken="Unknown power plan. Try balanced, performance, or power saver.")
    cmd_exe = _find_cmdexe()
    try:
        subprocess.run([cmd_exe, "/c", f"powercfg /setactive {guid}"],
                       capture_output=True, timeout=10, check=True)
        label  = _POWER_PLAN_LABELS.get(plan, plan.title())
        spoken = f"Power plan switched to {label}."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"plan": label, "guid": guid})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't change power plan.", error=str(exc))


def _exec_schedule_shutdown(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    minutes = int(params.get("minutes", 0))
    hours   = int(params.get("hours",   0))
    seconds = minutes * 60 + hours * 3600
    if seconds <= 0:
        return ToolResult(success=False, text="minutes or hours required.",
                          spoken="How many minutes until shutdown?")
    cmd_exe = _find_cmdexe()
    try:
        subprocess.run([cmd_exe, "/c", f"shutdown /s /t {seconds}"],
                       capture_output=True, timeout=5, check=True)
        label = (f"{hours}h {minutes}m" if hours and minutes else
                 f"{hours}h" if hours else f"{minutes} minute{'s' if minutes!=1 else ''}")
        spoken = f"Shutdown scheduled in {label}."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"seconds": seconds})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't schedule shutdown.", error=str(exc))


# ── STORAGE & DISK ────────────────────────────────────────────────────────────

def _exec_get_disk_usage(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    # Use PowerShell to get Windows drives directly — avoids WSL2 duplicate /dev/sdd mounts
    ok, out = _ps(
        "Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Root -match '^[A-Z]:\\\\'} | "
        "ForEach-Object { "
        "  $total=[math]::Round(($_.Used+$_.Free)/1GB,1); "
        "  $used=[math]::Round($_.Used/1GB,1); "
        "  $free=[math]::Round($_.Free/1GB,1); "
        "  $pct=if($total -gt 0){[math]::Round($_.Used/($_.Used+$_.Free)*100,1)}else{0}; "
        "  Write-Output \"$($_.Root)|$used|$total|$free|$pct\" "
        "}",
        timeout=15,
    )
    lines: list[str] = []
    data:  list[dict] = []
    if ok and out:
        for row in out.splitlines():
            parts = row.strip().split("|")
            if len(parts) == 5:
                drive, used, total, free, pct = parts
                try:
                    lines.append(f"{drive}  {used}GB / {total}GB  ({pct}% full, {free}GB free)")
                    data.append({"device": drive, "total_gb": float(total), "used_gb": float(used),
                                 "free_gb": float(free), "percent": float(pct)})
                except ValueError:
                    pass
    if lines:
        spoken = "Disk usage — " + "; ".join(lines) + "."
        return ToolResult(success=True, text="\n".join(lines), spoken=spoken, data={"drives": data})
    # Fallback to psutil if PowerShell unavailable
    try:
        import psutil
        seen: set[str] = set()
        for p in psutil.disk_partitions(all=False):
            if p.mountpoint in seen:
                continue
            seen.add(p.mountpoint)
            try:
                u     = psutil.disk_usage(p.mountpoint)
                total = round(u.total / 1024**3, 1)
                used  = round(u.used  / 1024**3, 1)
                free  = round(u.free  / 1024**3, 1)
                lines.append(f"{p.mountpoint}  {used}GB / {total}GB  ({u.percent}% full, {free}GB free)")
                data.append({"device": p.mountpoint, "total_gb": total, "used_gb": used,
                             "free_gb": free, "percent": u.percent})
            except Exception:
                pass
        if lines:
            spoken = "Disk usage — " + "; ".join(lines) + "."
            return ToolResult(success=True, text="\n".join(lines), spoken=spoken, data={"drives": data})
    except Exception:
        pass
    return ToolResult(success=False, text="No drives found.", spoken="Couldn't read disk usage.")


def _exec_empty_recycle_bin(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Write-Output done", timeout=30)
    if ok:
        spoken = "Recycle bin emptied."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    return ToolResult(success=False, text=out, spoken="Couldn't empty the recycle bin.", error=out)


def _exec_get_temp_files_size(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps(
        "$s=(Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue | "
        "Measure-Object -Property Length -Sum).Sum; "
        "[math]::Round($s/1MB, 1)",
        timeout=20,
    )
    if ok and out:
        try:
            mb     = float(out.strip())
            spoken = f"Your temp folder contains {mb} MB of files."
            return ToolResult(success=True, text=spoken, spoken=spoken, data={"size_mb": mb})
        except Exception:
            pass
    return ToolResult(success=False, text=out, spoken="Couldn't measure temp files.", error=out)


def _exec_clear_temp_files(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps(
        'Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue; Write-Output done',
        timeout=60,
    )
    if ok:
        spoken = "Temp files cleared."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    return ToolResult(success=False, text=out, spoken="Couldn't clear temp files.", error=out)


# ── AUDIO ─────────────────────────────────────────────────────────────────────

_GET_VOLUME_PS1 = r"""
try {
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class _AudioHelper {
    [DllImport("ole32.dll")] static extern int CoCreateInstance(ref Guid clsid,IntPtr pUnk,int ctx,ref Guid iid,out IntPtr ppv);
    [DllImport("ole32.dll")] static extern int CoInitializeEx(IntPtr r,int f);
    [DllImport("ole32.dll")] static extern void CoUninitialize();
    static Guid _CLSID = new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E");
    static Guid _IID   = new Guid("A95664D2-9614-4F35-A746-DE8DB63617E6");
    [ComImport,Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {
        void EnumAudioEndpoints(int df,int st,out IntPtr c);
        void GetDefaultAudioEndpoint(int df,int role,[MarshalAs(UnmanagedType.Interface)]out IMMDevice d);
    }
    [ComImport,Guid("D666063F-1587-4E43-81F1-B948E807363F"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {
        [PreserveSig] int Activate(ref Guid iid,int ctx,IntPtr p,[MarshalAs(UnmanagedType.IUnknown)]out object pp);
        void OpenPropertyStore(int a,out IntPtr s);
        void GetId([MarshalAs(UnmanagedType.LPWStr)]out string id);
        void GetState(out int s);
    }
    [ComImport,Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioEndpointVolume {
        void Reg(IntPtr n);void Unreg(IntPtr n);void GetCC(out uint c);
        void SetLvl(float v,ref Guid g);
        [PreserveSig] int SetMasterVolumeLevelScalar(float v,ref Guid g);
        void GetLvl(out float v);
        [PreserveSig] int GetMasterVolumeLevelScalar(out float v);
        void SetChLvl(uint c,float v,ref Guid g);
        [PreserveSig] int SetChVolScalar(uint c,float v,ref Guid g);
        void GetChLvl(uint c,out float v);
        [PreserveSig] int GetChVolScalar(uint c,out float v);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)]bool m,ref Guid g);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)]out bool m);
    }
    public static string Get() {
        CoInitializeEx(IntPtr.Zero,0);
        try {
            var clsid=_CLSID; var iid=_IID; IntPtr pv;
            CoCreateInstance(ref clsid,IntPtr.Zero,1,ref iid,out pv);
            var en=(IMMDeviceEnumerator)Marshal.GetObjectForIUnknown(pv);
            IMMDevice dev; en.GetDefaultAudioEndpoint(0,1,out dev);
            var vIid=new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
            object vo; dev.Activate(ref vIid,1,IntPtr.Zero,out vo);
            var ev=(IAudioEndpointVolume)vo;
            float lv; ev.GetMasterVolumeLevelScalar(out lv);
            bool mt; ev.GetMute(out mt);
            return string.Format("VOL:{0}|MUTE:{1}",(int)Math.Round(lv*100),mt);
        } finally { CoUninitialize(); }
    }
}
"@ -ErrorAction Stop
Write-Output ([_AudioHelper]::Get())
} catch { Write-Output "ERROR:$_" }
"""


def _exec_get_volume(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    # Write to a temp PS1 file — avoids all string-escaping issues with subprocess
    ps1_path = "/mnt/c/Windows/Temp/_xyron_vol.ps1"
    win_path = "C:\\Windows\\Temp\\_xyron_vol.ps1"
    try:
        Path(ps1_path).write_text(_GET_VOLUME_PS1)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't write volume script.", error=str(exc))
    ps = _find_powershell()
    if not ps:
        return ToolResult(success=False, text="PowerShell not found.", spoken="Couldn't read volume.")
    try:
        r = subprocess.run([ps, "-NonInteractive", "-NoProfile", "-File", win_path],
                           capture_output=True, text=True, timeout=12, errors="ignore")
        out = (r.stdout or "").strip()
        if "VOL:" in out:
            line  = [l for l in out.splitlines() if "VOL:" in l][-1]
            parts = dict(p.split(":", 1) for p in line.split("|"))
            vol   = int(parts.get("VOL", "0"))
            muted = parts.get("MUTE", "False").strip().lower() == "true"
            suffix = " and muted" if muted else ""
            spoken = f"Volume is at {vol}%{suffix}."
            return ToolResult(success=True, text=spoken, spoken=spoken,
                              data={"volume": vol, "muted": muted})
        return ToolResult(success=False, text=out, spoken="Couldn't read volume.", error=out)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Volume read failed.", error=str(exc))


def _exec_mute_unmute(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    _ps(
        'Add-Type -MemberDefinition \'[DllImport("user32.dll")] '
        'public static extern void keybd_event(byte v,byte s,uint f,UIntPtr e);\' '
        '-Name MUTKB -Namespace mutns -ErrorAction SilentlyContinue; '
        'mutns.MUTKB.keybd_event(0xAD,0,0,[UIntPtr]::Zero); '
        'Start-Sleep -Milliseconds 60; '
        'mutns.MUTKB.keybd_event(0xAD,0,2,[UIntPtr]::Zero); Write-Output done',
        timeout=8,
    )
    action = params.get("action", "toggle").lower()
    spoken = "Muted." if action == "mute" else ("Unmuted." if action == "unmute" else "Audio toggled.")
    return ToolResult(success=True, text=spoken, spoken=spoken, data={"action": action})


# ── Media controls (play/pause, next, prev, stop) ────────────────────────────
# VK codes: NEXT=0xB0(176), PREV=0xB1(177), STOP=0xB2(178), PLAY_PAUSE=0xB3(179)

_MEDIA_VK: dict[str, int] = {
    "play_pause": 0xB3,
    "next":       0xB0,
    "prev":       0xB1,
    "stop":       0xB2,
}

_MEDIA_LABELS: dict[str, str] = {
    "play_pause": "Playing / paused.",
    "next":       "Skipped to next track.",
    "prev":       "Went back to previous track.",
    "stop":       "Playback stopped.",
}


def _send_media_key(action: str) -> bool:
    """
    Send a Windows media key.
    Tier 1: pynput (native Windows Python, ~1 ms).
    Tier 2: keybd_event via PowerShell P/Invoke (~400 ms, WSL2).
    """
    vk = _MEDIA_VK.get(action)
    if vk is None:
        return False

    # Tier 1 — pynput (works only on native Windows Python)
    if sys.platform == "win32":
        try:
            from pynput.keyboard import Key, Controller  # type: ignore
            _KEY_MAP = {
                "play_pause": Key.media_play_pause,
                "next":       Key.media_next,
                "prev":       Key.media_previous,
                "stop":       Key.media_volume_mute,  # pynput has no stop key; fallback
            }
            kb = Controller()
            kb.press(_KEY_MAP[action])
            kb.release(_KEY_MAP[action])
            return True
        except Exception:
            pass

    # Tier 2 — PowerShell keybd_event (WSL2)
    ps_snippet = (
        f'Add-Type -MemberDefinition \'[DllImport("user32.dll")] '
        f'public static extern void keybd_event(byte v,byte s,uint f,UIntPtr e);\' '
        f'-Name _MK -Namespace _mkns -ErrorAction SilentlyContinue; '
        f'_mkns._MK.keybd_event({vk},0,0,[UIntPtr]::Zero); '
        f'Start-Sleep -Milliseconds 60; '
        f'_mkns._MK.keybd_event({vk},0,2,[UIntPtr]::Zero); Write-Output done'
    )
    ok, _ = _ps(ps_snippet, timeout=8)
    return ok


def _exec_media_control(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    action = params.get("action", "play_pause").lower().replace("-", "_").replace(" ", "_")
    # Accept aliases
    aliases = {"play": "play_pause", "pause": "play_pause", "next_track": "next",
               "previous": "prev", "previous_track": "prev", "rewind": "prev"}
    action = aliases.get(action, action)

    if action not in _MEDIA_VK:
        return ToolResult(success=False,
                          text=f"Unknown media action: {action}",
                          spoken="I don't know that media command.")

    ok = _send_media_key(action)
    spoken = _MEDIA_LABELS.get(action, "Done.")
    if ok:
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"action": action})
    return ToolResult(success=False, text="Media key failed.",
                      spoken="Couldn't send the media key.", error="keybd_event failed")


def _exec_list_audio_devices(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps(
        "Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Status -eq 'OK'} | "
        "Select-Object FriendlyName | ConvertTo-Json -Compress"
    )
    if ok and out:
        try:
            import json as _j
            items = _j.loads(out)
            if isinstance(items, dict):
                items = [items]
            names = [i.get("FriendlyName", "?") for i in items]
            spoken = f"{len(names)} audio device{'s' if len(names)!=1 else ''}: " + ", ".join(names) + "."
            return ToolResult(success=True, text="\n".join(names), spoken=spoken, data={"devices": names})
        except Exception:
            return ToolResult(success=True, text=out, spoken="Here are your audio devices.", data={})
    return ToolResult(success=False, text=out, spoken="Couldn't list audio devices.", error=out)


def _exec_set_default_audio(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    name = params.get("name", "").strip()
    if not name:
        return ToolResult(success=False, text="name required.", spoken="Which audio device should I set as default?")
    nircmd_candidates = [
        "/mnt/c/Windows/nircmd.exe", "/mnt/c/nircmd.exe",
        "/mnt/c/Program Files/NirCmd/nircmd.exe",
        "/mnt/c/Users/Public/nircmd.exe",
    ]
    nircmd = next((p for p in nircmd_candidates if Path(p).exists()), None)
    if nircmd:
        subprocess.Popen([nircmd, "setdefaultsounddevice", name],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spoken = f"Default audio set to {name}."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={"device": name})
    ok, out = _ps(
        f'$d=Get-PnpDevice -Class AudioEndpoint | Where-Object {{$_.FriendlyName -like "*{name}*"}} | Select-Object -First 1; '
        f'if($d){{Write-Output "FOUND:$($d.FriendlyName)"}}else{{Write-Output "NOTFOUND"}}'
    )
    if ok and out.startswith("FOUND:"):
        found = out.replace("FOUND:", "").strip()
        spoken = (f"Found '{found}' but switching default audio requires NirCmd. "
                  "Drop nircmd.exe into C:\\Windows and it'll work next time.")
        return ToolResult(success=False, text=spoken, spoken=spoken, error="nircmd not installed")
    return ToolResult(success=False, text=out,
                      spoken=f"No audio device matching '{name}' found.", error=out)


# ── SYSTEM MAINTENANCE ────────────────────────────────────────────────────────

def _exec_clear_clipboard(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps(
        "Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue; "
        "[System.Windows.Forms.Clipboard]::Clear(); Write-Output done"
    )
    if ok:
        spoken = "Clipboard cleared."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    return ToolResult(success=False, text=out, spoken="Couldn't clear clipboard.", error=out)


_WIN_TZ_ABBR: Dict[str, str] = {
    "Pakistan Standard Time":     "PKT",
    "India Standard Time":        "IST",
    "UTC":                        "UTC",
    "Eastern Standard Time":      "EST",
    "Central Standard Time":      "CST",
    "Mountain Standard Time":     "MST",
    "Pacific Standard Time":      "PST",
    "Arabian Standard Time":      "GST",
    "Saudi Arabia Standard Time": "AST",
    "Bangladesh Standard Time":   "BST",
    "SE Asia Standard Time":      "ICT",
    "China Standard Time":        "CST",
    "Tokyo Standard Time":        "JST",
    "W. Europe Standard Time":    "CET",
    "GMT Standard Time":          "GMT",
    "AUS Eastern Standard Time":  "AEST",
    "Afghanistan Standard Time":  "AFT",
}


_WIN_TO_IANA: Dict[str, str] = {
    "Pakistan Standard Time":     "Asia/Karachi",
    "India Standard Time":        "Asia/Kolkata",
    "UTC":                        "UTC",
    "Eastern Standard Time":      "America/New_York",
    "Central Standard Time":      "America/Chicago",
    "Mountain Standard Time":     "America/Denver",
    "Pacific Standard Time":      "America/Los_Angeles",
    "Arabian Standard Time":      "Asia/Dubai",
    "Saudi Arabia Standard Time": "Asia/Riyadh",
    "Bangladesh Standard Time":   "Asia/Dhaka",
    "SE Asia Standard Time":      "Asia/Bangkok",
    "China Standard Time":        "Asia/Shanghai",
    "Tokyo Standard Time":        "Asia/Tokyo",
    "W. Europe Standard Time":    "Europe/Berlin",
    "GMT Standard Time":          "Europe/London",
    "AUS Eastern Standard Time":  "Australia/Sydney",
    "Afghanistan Standard Time":  "Asia/Kabul",
}


def _exec_get_date_time(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Return current local date and time with timezone abbreviation.

    Uses WSL2 system clock (NTP-synced) for accuracy.
    Detects Windows timezone label via PowerShell for display only.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    # Step 1: detect Windows timezone label (fast, label-only)
    iana_tz   = "Asia/Karachi"  # default
    tz_label  = "PKT"
    try:
        ok, out = _ps("(Get-TimeZone).Id", timeout=3)
        if ok and out.strip():
            win_tz  = out.strip()
            iana_tz = _WIN_TO_IANA.get(win_tz, iana_tz)
            tz_label = _WIN_TZ_ABBR.get(win_tz, tz_label)
    except Exception:
        pass

    # Step 2: time from WSL2 NTP-synced clock, converted to detected timezone
    now      = _dt.now(ZoneInfo(iana_tz))
    date_str = now.strftime("%A, %B %-d, %Y at %I:%M %p")
    spoken   = f"It's {date_str} {tz_label}."
    return ToolResult(success=True, text=spoken, spoken=spoken,
                      data={"datetime": now.isoformat(), "timezone": tz_label})


def _exec_get_uptime(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    try:
        import psutil, time as _t
        elapsed = _t.time() - psutil.boot_time()
        days    = int(elapsed // 86400)
        hours   = int((elapsed % 86400) // 3600)
        mins    = int((elapsed % 3600) // 60)
        if days:
            spoken = f"The system has been running for {days} day{'s' if days!=1 else ''} and {hours} hour{'s' if hours!=1 else ''}."
        else:
            spoken = f"System uptime is {hours} hour{'s' if hours!=1 else ''} and {mins} minute{'s' if mins!=1 else ''}."
        return ToolResult(success=True, text=spoken, spoken=spoken,
                          data={"days": days, "hours": hours, "minutes": mins})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't get system uptime.", error=str(exc))


def _exec_run_disk_cleanup(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.", spoken="Disk Cleanup unavailable.")
    try:
        subprocess.Popen([cmd_exe, "/c", "start cleanmgr /d C:"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spoken = "Disk Cleanup launched for C drive."
        return ToolResult(success=True, text=spoken, spoken=spoken, data={})
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't launch Disk Cleanup.", error=str(exc))


def _exec_check_windows_updates(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    ok, out = _ps(
        "try { "
        "$s=(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher(); "
        "$r=$s.Search(\"IsInstalled=0 and Type='Software'\"); "
        "$cnt=$r.Updates.Count; "
        "$names=($r.Updates | ForEach-Object {$_.Title} | Select-Object -First 5) -join '||'; "
        "Write-Output \"COUNT:$cnt|NAMES:$names\" "
        "} catch { Write-Output \"ERROR:$_\" }",
        timeout=60,
    )
    if ok and "COUNT:" in out:
        try:
            head, _, tail = out.partition("|NAMES:")
            count  = int(head.replace("COUNT:", "").strip())
            names  = [n.strip() for n in tail.split("||") if n.strip()]
            if count == 0:
                spoken = "Your system is up to date. No pending updates."
            else:
                spoken = f"{count} update{'s' if count!=1 else ''} available"
                if names:
                    spoken += ": " + ", ".join(names[:3])
                    if count > 3:
                        spoken += f" and {count-3} more"
                spoken += "."
            return ToolResult(success=True, text=spoken, spoken=spoken,
                              data={"pending": count, "updates": names})
        except Exception:
            pass
    spoken = "Couldn't check updates. Windows Update service may be restricted."
    return ToolResult(success=False, text=out, spoken=spoken, error=out)


# =============================================================================
# REGISTRY — register all 30 extended tools
# =============================================================================

registry.register(name="list_processes",
    definition={"type":"function","function":{"name":"list_processes",
        "description":"List running processes sorted by memory usage. Use for: 'what processes are running', 'task manager', 'what\\'s using RAM'.",
        "parameters":{"type":"object","properties":{"top":{"type":"integer","description":"How many to return (default 15)"}},
        "required":[]}}},
    executor=_exec_list_processes, risk="low", category="system")

registry.register(name="kill_process",
    definition={"type":"function","function":{"name":"kill_process",
        "description":"Force-kill a running process by name or PID. Use for: 'kill Chrome', 'stop Notepad', 'end process X'.",
        "parameters":{"type":"object","properties":{
            "name":{"type":"string","description":"Process name (e.g. 'chrome', 'notepad.exe')"},
            "pid": {"type":"integer","description":"Process ID"}},
        "required":[]}}},
    executor=_exec_kill_process, risk="medium", category="system")

registry.register(name="get_startup_apps",
    definition={"type":"function","function":{"name":"get_startup_apps",
        "description":"List programs that run on Windows startup. Use for: 'show startup apps', 'what starts on boot'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_startup_apps, risk="low", category="system")

registry.register(name="disable_startup_app",
    definition={"type":"function","function":{"name":"disable_startup_app",
        "description":"Disable a program from running on startup. Use for: 'disable Spotify from startup', 'remove Teams from startup'.",
        "parameters":{"type":"object","properties":{
            "name":{"type":"string","description":"Exact startup entry name"}},
        "required":["name"]}}},
    executor=_exec_disable_startup_app, risk="medium", category="system")

registry.register(name="set_display_resolution",
    definition={"type":"function","function":{"name":"set_display_resolution",
        "description":"Change the screen resolution. Use for: 'set resolution to 1920x1080', 'change resolution to 4K'.",
        "parameters":{"type":"object","properties":{
            "width": {"type":"integer","description":"Width in pixels"},
            "height":{"type":"integer","description":"Height in pixels"}},
        "required":["width","height"]}}},
    executor=_exec_set_display_resolution, risk="low", category="system")

registry.register(name="set_refresh_rate",
    definition={"type":"function","function":{"name":"set_refresh_rate",
        "description":"Change the monitor refresh rate. Use for: 'set refresh rate to 144hz', 'change to 60hz'.",
        "parameters":{"type":"object","properties":{
            "rate":{"type":"integer","description":"Refresh rate in Hz (e.g. 60, 120, 144, 165, 240)"}},
        "required":["rate"]}}},
    executor=_exec_set_refresh_rate, risk="low", category="system")

registry.register(name="virtual_desktop_create",
    definition={"type":"function","function":{"name":"virtual_desktop_create",
        "description":"Create a new Windows virtual desktop (Win+Ctrl+D). Use for: 'create new desktop', 'new virtual desktop'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_virtual_desktop_create, risk="low", category="system")

registry.register(name="virtual_desktop_switch",
    definition={"type":"function","function":{"name":"virtual_desktop_switch",
        "description":"Switch to next or previous virtual desktop. Use for: 'switch to next desktop', 'previous virtual desktop'.",
        "parameters":{"type":"object","properties":{
            "direction":{"type":"string","enum":["left","right"],"description":"left=previous, right=next"}},
        "required":[]}}},
    executor=_exec_virtual_desktop_switch, risk="low", category="system")

registry.register(name="take_screenshot",
    definition={"type":"function","function":{"name":"take_screenshot",
        "description":"Capture a screenshot of the entire screen to a file. Use for: 'take a screenshot', 'capture the screen', 'screenshot'.",
        "parameters":{"type":"object","properties":{
            "path":{"type":"string","description":"Save path (optional, defaults to Public Pictures)"}},
        "required":[]}}},
    executor=_exec_take_screenshot, risk="low", category="system")

registry.register(name="wifi_list",
    definition={"type":"function","function":{"name":"wifi_list",
        "description":"Scan and list available WiFi networks. Use for: 'show wifi', 'available networks', 'scan wifi'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_wifi_list, risk="low", category="system")

registry.register(name="wifi_connect",
    definition={"type":"function","function":{"name":"wifi_connect",
        "description":"Connect to a WiFi network. Use for: 'connect to HomeWifi', 'join network X'.",
        "parameters":{"type":"object","properties":{
            "ssid":{"type":"string","description":"Network name (SSID)"}},
        "required":["ssid"]}}},
    executor=_exec_wifi_connect, risk="medium", category="system")

registry.register(name="wifi_disconnect",
    definition={"type":"function","function":{"name":"wifi_disconnect",
        "description":"Disconnect from the current WiFi network. Use for: 'disconnect wifi', 'turn off wifi'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_wifi_disconnect, risk="low", category="system")

registry.register(name="network_speed_test",
    definition={"type":"function","function":{"name":"network_speed_test",
        "description":"Test internet download speed. Use for: 'speed test', 'how fast is my internet', 'check internet speed'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_network_speed_test, risk="low", category="system")

registry.register(name="get_ip_info",
    definition={"type":"function","function":{"name":"get_ip_info",
        "description":"Get local and public IP addresses. Use for: 'what\\'s my IP', 'show IP address', 'what\\'s my public IP'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_ip_info, risk="low", category="system")

registry.register(name="flush_dns",
    definition={"type":"function","function":{"name":"flush_dns",
        "description":"Flush the Windows DNS cache. Use for: 'flush DNS', 'clear DNS cache', 'reset DNS'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_flush_dns, risk="low", category="system")

registry.register(name="get_date_time",
    definition={"type":"function","function":{"name":"get_date_time",
        "description":"Get the current local date and time. Use for: 'what time is it', 'what's the date', 'what day is it today'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_date_time, risk="low", category="system")

registry.register(name="get_battery_status",
    definition={"type":"function","function":{"name":"get_battery_status",
        "description":"Get battery percentage, charging state, and time remaining. Use for: 'battery level', 'how\\'s my battery', 'is it charging'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_battery_status, risk="low", category="system")

registry.register(name="set_power_plan",
    definition={"type":"function","function":{"name":"set_power_plan",
        "description":"Switch Windows power plan. Use for: 'switch to performance mode', 'enable power saver', 'balanced mode'.",
        "parameters":{"type":"object","properties":{
            "plan":{"type":"string","enum":["balanced","performance","high performance","power saver","saver"],
                    "description":"Power plan name"}},
        "required":["plan"]}}},
    executor=_exec_set_power_plan, risk="low", category="system")

registry.register(name="schedule_shutdown",
    definition={"type":"function","function":{"name":"schedule_shutdown",
        "description":"Schedule a shutdown after a set time. Use for: 'shutdown in 30 minutes', 'turn off in 2 hours'.",
        "parameters":{"type":"object","properties":{
            "minutes":{"type":"integer","description":"Minutes from now"},
            "hours":  {"type":"integer","description":"Hours from now"}},
        "required":[]}}},
    executor=_exec_schedule_shutdown, risk="medium", category="system")

registry.register(name="get_disk_usage",
    definition={"type":"function","function":{"name":"get_disk_usage",
        "description":"Get disk/storage usage for all drives. Use for: 'how much space do I have', 'disk usage', 'storage on E drive'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_disk_usage, risk="low", category="system")

registry.register(name="empty_recycle_bin",
    definition={"type":"function","function":{"name":"empty_recycle_bin",
        "description":"Empty the Windows Recycle Bin. Use for: 'empty the trash', 'empty recycle bin', 'delete trash'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_empty_recycle_bin, risk="medium", category="system")

registry.register(name="get_temp_files_size",
    definition={"type":"function","function":{"name":"get_temp_files_size",
        "description":"Check how much disk space temp files are using. Use for: 'how big is temp folder', 'temp files size'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_temp_files_size, risk="low", category="system")

registry.register(name="clear_temp_files",
    definition={"type":"function","function":{"name":"clear_temp_files",
        "description":"Delete all files in the Windows temp folder. Use for: 'clear temp files', 'clean temp folder', 'delete junk'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_clear_temp_files, risk="medium", category="system")

registry.register(name="get_volume",
    definition={"type":"function","function":{"name":"get_volume",
        "description":"Get the current system volume level and mute status. Use for: 'what\\'s the volume', 'current volume', 'is it muted'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_volume, risk="low", category="system")

registry.register(name="mute_unmute",
    definition={"type":"function","function":{"name":"mute_unmute",
        "description":"Toggle, mute, or unmute system audio. Use for: 'mute audio', 'unmute', 'toggle mute'.",
        "parameters":{"type":"object","properties":{
            "action":{"type":"string","enum":["mute","unmute","toggle"],"description":"What to do (default toggle)"}},
        "required":[]}}},
    executor=_exec_mute_unmute, risk="low", category="system")

registry.register(name="media_control",
    definition={"type": "function", "function": {
        "name": "media_control",
        "description": (
            "Control media playback (Spotify, YouTube, VLC, Windows Media Player, etc.). "
            "Use for: 'play', 'pause', 'play pause', 'next song', 'next track', "
            "'previous song', 'go back', 'stop music', 'stop playback'. "
            "action: play_pause | next | prev | stop."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {
                "type": "string",
                "enum": ["play_pause", "next", "prev", "stop"],
                "description": "Media action to perform",
            }},
            "required": ["action"],
        },
    }},
    executor=_exec_media_control, risk="low", category="system")

registry.register(name="list_audio_devices",
    definition={"type":"function","function":{"name":"list_audio_devices",
        "description":"List available audio output/input devices. Use for: 'show audio devices', 'list speakers and headphones'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_list_audio_devices, risk="low", category="system")

registry.register(name="set_default_audio",
    definition={"type":"function","function":{"name":"set_default_audio",
        "description":"Set the default audio output device. Use for: 'switch to headphones', 'use speakers as default', 'change audio output'.",
        "parameters":{"type":"object","properties":{
            "name":{"type":"string","description":"Partial device name (e.g. 'Headphones', 'Speakers', 'Realtek')"}},
        "required":["name"]}}},
    executor=_exec_set_default_audio, risk="low", category="system")

registry.register(name="clear_clipboard",
    definition={"type":"function","function":{"name":"clear_clipboard",
        "description":"Clear the Windows clipboard contents. Use for: 'clear clipboard', 'wipe clipboard', 'empty clipboard'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_clear_clipboard, risk="low", category="system")

registry.register(name="get_uptime",
    definition={"type":"function","function":{"name":"get_uptime",
        "description":"Get how long the system has been running since last boot. Use for: 'system uptime', 'how long has the PC been on'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_get_uptime, risk="low", category="system")

registry.register(name="run_disk_cleanup",
    definition={"type":"function","function":{"name":"run_disk_cleanup",
        "description":"Launch the Windows Disk Cleanup utility for C drive. Use for: 'run disk cleanup', 'clean up disk', 'free up space'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_run_disk_cleanup, risk="low", category="system")

registry.register(name="check_windows_updates",
    definition={"type":"function","function":{"name":"check_windows_updates",
        "description":"Check for pending Windows updates. Use for: 'check for updates', 'any Windows updates', 'is Windows up to date'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_check_windows_updates, risk="low", category="system")


# ── WiFi panel ────────────────────────────────────────────────────────────────

def _exec_open_wifi_panel(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.", spoken="Can't open WiFi panel.")
    try:
        subprocess.Popen([cmd_exe, "/c", "start ms-availablenetworks:"])
        spoken = "Opening available WiFi networks."
        _store_last_action(ctx, "open_wifi_panel", {}, spoken)
        return ToolResult(success=True, text=spoken, spoken=spoken)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't open WiFi panel.", error=str(exc))

registry.register(name="open_wifi_panel",
    definition={"type":"function","function":{"name":"open_wifi_panel",
        "description":"Open the Windows WiFi available networks panel. Use for: 'show wifi networks', 'open wifi', 'show available wifi', 'nearby wifi', 'connect to wifi'.",
        "parameters":{"type":"object","properties":{},"required":[]}}},
    executor=_exec_open_wifi_panel, risk="low", category="system")


# ── Smart open: search and open files/folders/videos/pictures by name ────────

def _exec_smart_open(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Find anything on the system by name and open it."""
    query     = params.get("query", "").strip()
    open_type = params.get("type", "any").lower()   # folder | file | video | image | any

    if not query:
        return ToolResult(success=False, text="Query required.", spoken="What would you like me to open?")

    # Normalize "X folder" → query="X", type="folder" so voice commands work naturally.
    # Handles: "ios folder", "music folder", "downloads directory", "project file", etc.
    _SUFFIX_TYPE: dict[str, str] = {
        " folders": "folder", " folder": "folder",
        " directories": "folder", " directory": "folder", " dir": "folder",
        " files": "file", " file": "file",
    }
    for _sfx, _tp in _SUFFIX_TYPE.items():
        if query.lower().endswith(_sfx):
            query = query[:-len(_sfx)].strip()
            open_type = _tp
            break

    if not query:
        return ToolResult(success=False, text="Query required.", spoken="What would you like me to open?")

    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.", spoken="Can't open files on this system.")

    # ── Fast path: check the file-system index first (<5ms) ──────────────────
    try:
        from api.services.fs_index import fs_index
        if fs_index.is_ready:
            _type_filter = "folder" if open_type == "folder" else (
                "file" if open_type in ("file", "video", "image") else None
            )
            _hits = fs_index.search(query, type_filter=_type_filter, limit=8)
            # Filter out cache/temp/system paths that are not user content
            _JUNK_PARTS = {"appdata", "cache", "temp", "temporary internet files",
                           "node_modules", "programdata", "$recycle.bin", ".tmp"}
            _hits = [
                h for h in _hits
                if not any(p.lower() in _JUNK_PARTS for p in h.parts)
            ]
            _hits = _hits[:3]
            if _hits:
                # Apply extension filter for video/image
                VIDEO_EXTS_IDX = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
                IMAGE_EXTS_IDX = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}
                for _hit in _hits:
                    if open_type == "video" and _hit.suffix.lower() not in VIDEO_EXTS_IDX:
                        continue
                    if open_type == "image" and _hit.suffix.lower() not in IMAGE_EXTS_IDX:
                        continue
                    # Found — open it directly (/mnt/e/foo → E:\foo)
                    _p_str = str(_hit)
                    _mnt_m = re.match(r'^/mnt/([a-z])/(.*)', _p_str)
                    if _mnt_m:
                        _win_path = _mnt_m.group(1).upper() + ":\\" + _mnt_m.group(2).replace("/", "\\")
                    else:
                        _win_path = _p_str
                    subprocess.Popen([cmd_exe, "/c", "start", "", _win_path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    spoken = f"Opening {_hit.name}."
                    _store_last_action(ctx, "smart_open", {**params, "_found_path": _win_path}, spoken)
                    return ToolResult(success=True, text=spoken, spoken=spoken,
                                      data={"path": str(_hit), "source": "index"})
    except Exception as _idx_exc:
        import logging as _l
        _l.getLogger(__name__).debug("fs_index fast-path skipped: %s", _idx_exc)
    # ── Slow path: incremental find ───────────────────────────────────────────

    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}

    # Search data drives first (D:, E:, F:, G:) — user content lives here.
    # Then the user profile on C: (not all of C: — too large).
    home_fs = _fs_path(_windows_home())
    search_roots_fs = []
    for drive in ("d", "e", "f", "g"):
        mount = Path(f"/mnt/{drive}")
        if mount.exists():
            search_roots_fs.append(str(mount))
    # User profile on C: (Desktop, Documents, Downloads, etc.)
    if home_fs.exists():
        search_roots_fs.append(str(home_fs))

    # Incremental depth search — finds shallow matches first (prefers "IT Course" over
    # deeply nested "course" folders inside project trees). Stops immediately on first hit.
    _prune_names = [
        "node_modules", ".git", "__pycache__", ".next", ".venv", "venv",
        "AppData", "Windows", "ProgramData", "$RECYCLE.BIN",
    ]
    _type_clause = (
        ["-type", "d"] if open_type == "folder" else
        ["-type", "f"] if open_type in ("file", "video", "image") else []
    )

    def _prune_clause():
        expr = ["("]
        for i, name in enumerate(_prune_names):
            if i > 0:
                expr.append("-o")
            expr += ["-name", name]
        expr += [")", "-prune", "-o"]
        return expr

    found_path: Path | None = None
    deadline = __import__("time").time() + 8

    for max_depth in range(1, 9):
        if __import__("time").time() > deadline:
            break
        find_cmd = (
            ["find"] + search_roots_fs +
            ["-maxdepth", str(max_depth)] +
            _prune_clause() +
            ["(", "-iname", f"*{query}*"] + _type_clause + ["-print", ")"]
        )
        proc = subprocess.Popen(
            find_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace",
        )
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                p = Path(line)
                if open_type == "video" and p.suffix.lower() not in VIDEO_EXTS:
                    continue
                if open_type == "image" and p.suffix.lower() not in IMAGE_EXTS:
                    continue
                found_path = p
                proc.kill()
                break
            else:
                proc.wait()
        except Exception:
            proc.kill()
        if found_path:
            break

    if not found_path:
        return ToolResult(
            success=False, text=f"Nothing found matching '{query}'.",
            spoken=f"I couldn't find anything named '{query}' on your system.",
        )

    # Convert WSL path → Windows path
    win_target = (str(found_path)
                  .replace("/mnt/c/", "C:\\")
                  .replace("/mnt/d/", "D:\\")
                  .replace("/mnt/e/", "E:\\")
                  .replace("/mnt/f/", "F:\\")
                  .replace("/mnt/g/", "G:\\")
                  .replace("/", "\\"))

    try:
        subprocess.Popen([cmd_exe, "/c", f'start "" "{win_target}"'])
        name = found_path.name
        spoken = f"Opening {name}."
        _store_last_action(ctx, "smart_open", {**params, "_found_path": win_target}, spoken)
        return ToolResult(success=True, text=f"Opened: {win_target}", spoken=spoken,
                          action_path=win_target)
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't open that.", error=str(exc))

# ── Takeover Mode ─────────────────────────────────────────────────────────────

def _exec_takeover_mode(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Cinematic takeover: open VS Code and fire TAKEOVER_START to the frontend."""
    _launch_app("vscode")   # non-blocking, fire-and-forget
    return ToolResult(
        success=True,
        text="Takeover initiated.",
        spoken="Control granted.",
        data={"frontend_action": "TAKEOVER_START"},
    )

registry.register(name="takeover_mode",
    definition={"type": "function", "function": {
        "name": "takeover_mode",
        "description": (
            "Activate Xyron cinematic takeover mode. Opens VS Code, triggers a "
            "cinematic activation sequence. Use for: 'takeover', 'xyron takeover', "
            "'take over', 'take control', 'focus mode', 'workspace mode'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    executor=_exec_takeover_mode, risk="low", category="system")

registry.register(name="smart_open",
    definition={"type":"function","function":{"name":"smart_open",
        "description":"Search the user's system for a file, folder, video, or picture by name and open it. Use for: 'open my course folder', 'play that video', 'show that picture', 'open the Downloads folder', 'play the movie'.",
        "parameters":{"type":"object","properties":{
            "query":{"type":"string","description":"Name or partial name of the file/folder to find"},
            "type":{"type":"string","enum":["folder","file","video","image","any"],"description":"Type to search for"}
        },"required":["query"]}}},
    executor=_exec_smart_open, risk="low", category="system")
