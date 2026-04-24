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

import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from .registry import ToolResult, registry
from .safety import is_safe_path, is_safe_write

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
        "desktop":   home + "\\Desktop",
        "documents": home + "\\Documents",
        "downloads": home + "\\Downloads",
        "pictures":  home + "\\Pictures",
        "videos":    home + "\\Videos",
        "music":     home + "\\Music",
        "home":      home,
        "user":      home,
        "appdata":   home + "\\AppData",
        "temp":      "C:\\Windows\\Temp",
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

    # Named special directories
    special = _get_win_special()
    key = p.lower().strip("/\\")
    if key in special:
        return special[key]

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
}

_APP_ALIASES: Dict[str, str] = {
    "vs code": "vscode", "visual studio code": "vscode",
    "google chrome": "chrome", "file explorer": "explorer",
    "windows explorer": "explorer", "windows terminal": "terminal",
    "command prompt": "cmd", "microsoft word": "word",
    "microsoft excel": "excel", "microsoft powerpoint": "powerpoint",
    "microsoft teams": "teams", "task manager": "taskmanager",
    "ms paint": "paint",
    # Settings variants
    "system settings": "settings", "systemsettings": "settings",
    "windows settings": "settings", "windowssettings": "settings",
    "pc settings": "settings", "pcsettings": "settings",
    "control panel": "settings",
}


def _normalise_app(name: str) -> str:
    n = name.lower().strip()
    return _APP_ALIASES.get(n, re.sub(r'[\s\-_]', '', n))


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
            return True, f"Launching {app_name}…"
        except Exception as exc:
            return False, f"App '{app_name}' not found. Known apps: vscode, chrome, spotify, terminal, word, excel, teams, slack, discord."

    cmd = entry.get(platform, "")
    if not cmd:
        return False, f"'{app_name}' is not available on this platform."

    try:
        if _ON_WSL:
            _cmd = _find_cmdexe() or "cmd.exe"
            subprocess.Popen(['/init', _cmd, '/c', 'start', '', cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif "ms-settings:" in cmd:
            subprocess.Popen(["start", cmd], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif _ON_WINDOWS:
            subprocess.Popen(cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Launched: %s → %s", app_name, cmd)
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

    # Sanitize folder name
    name = re.sub(r'[<>:"|?*]', "", name)

    base_path  = resolve_path(base_raw) if base_raw else _windows_home()
    # Build Windows-style path without os.path.join (which uses / on Linux/WSL)
    win_target = base_path.rstrip('\\').rstrip('/') + '\\' + name

    if not is_safe_path(win_target):
        return ToolResult(success=False, text=f"Cannot create folder here: {win_target}",
                          spoken="That location is restricted for safety.", error="Blocked path")

    fs_target = _fs_path(win_target)
    try:
        fs_target.mkdir(parents=True, exist_ok=True)
        _store_last_action(ctx, "create_folder", params, win_target)
        # Auto-open the newly created folder in Explorer
        ok_open, _ = _open_in_explorer(win_target)
        loc_label = base_path if base_path else "your Desktop"
        if ok_open:
            spoken = f"Done! I created the '{name}' folder in {loc_label} and opened it for you."
        else:
            spoken = f"Done! I created the '{name}' folder in {loc_label}. Say 'open it' to open it."
        msg = f"Created folder '{name}' at {base_path}"
        return ToolResult(success=True, text=msg, spoken=spoken,
                          action_path=base_path, data={"path": win_target, "name": name})
    except PermissionError:
        return ToolResult(success=False, text=f"Permission denied: {win_target}",
                          spoken="I don't have permission to create a folder there.", error="PermissionError")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't create the folder.", error=str(exc))


def _exec_create_subfolders(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    parent_raw = params.get("parent", "").strip()
    names      = params.get("names", [])
    count      = int(params.get("count", 0) or 0)

    parent_path = resolve_path(parent_raw) if parent_raw else _windows_home()

    if not names and count > 0:
        names = [f"Folder {i + 1}" for i in range(min(count, 20))]
    elif not names:
        return ToolResult(success=False, text="No subfolder names provided.",
                          spoken="What should I name the subfolders?")

    if not is_safe_path(parent_path):
        return ToolResult(success=False, text=f"Blocked: {parent_path}",
                          spoken="That location is restricted.", error="Blocked path")

    created: list[str] = []
    failed:  list[str] = []
    for name in names[:20]:
        name = re.sub(r'[<>:"|?*]', "", name).strip()
        if not name:
            continue
        target = os.path.join(parent_path, name)
        fs_target = _fs_path(target)
        try:
            fs_target.mkdir(parents=True, exist_ok=True)
            created.append(name)
        except Exception:
            failed.append(name)

    if created:
        names_str = ", ".join(f"'{n}'" for n in created)
        spoken = f"Done. Created {len(created)} subfolder{'s' if len(created) != 1 else ''}: {names_str}."
        return ToolResult(success=True, text=spoken, spoken=spoken,
                          data={"created": created, "parent": parent_path})
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

    ok, msg = _launch_app(app_name)
    if ok:
        _store_last_action(ctx, "open_application", params, app_name)
    return ToolResult(success=ok, text=msg, spoken=msg,
                      action_app=app_name if ok else None)


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


# ── Delete file (requires confirmation_confirmed=True in params) ───────────────

def _exec_delete_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    raw       = params.get("path", "").strip()
    confirmed = params.get("confirmed", False)

    if not raw:
        return ToolResult(success=False, text="File path required.", spoken="Which file should I delete?")

    win_path = resolve_path(raw)
    if not is_safe_path(win_path):
        return ToolResult(success=False, text=f"Blocked: {win_path}",
                          spoken="That path is restricted. I won't delete it.", error="Blocked")

    fs = _fs_path(win_path)
    if not fs.exists():
        return ToolResult(success=False, text=f"Not found: {win_path}",
                          spoken=f"I couldn't find {fs.name}. Does it exist?")

    # Safety gate — must be explicitly confirmed before deletion
    if not confirmed:
        prompt = f"I'm about to permanently delete {fs.name}. Say yes to confirm or no to cancel."
        return ToolResult(
            success=False,
            text=f"CONFIRM_REQUIRED: delete {win_path}",
            spoken=prompt,
            data={
                "requires_confirmation": True,
                "path": win_path,
                "name": fs.name,
                "tool": "delete_file",
                "params": {"path": raw},
                "prompt": prompt,
            },
            error="confirm_required",
        )

    try:
        if fs.is_dir():
            import shutil
            shutil.rmtree(fs)
        else:
            fs.unlink()
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

def _exec_volume_control(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    action = params.get("action", "up")   # up | down | mute | unmute
    steps  = max(1, min(20, int(params.get("steps", 5))))

    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Volume control is not available on this system.")

    if action in ("mute", "unmute"):
        key_code = 173   # VK_VOLUME_MUTE toggle
        ps = f"(New-Object -ComObject WScript.Shell).SendKeys([char]{key_code})"
        label = "muted" if action == "mute" else "unmuted"
    elif action == "up":
        key_code = 175   # VK_VOLUME_UP
        ps = f"$w=New-Object -ComObject WScript.Shell; 1..{steps}|%{{$w.SendKeys([char]{key_code})}}"
        label = "increased"
    else:
        key_code = 174   # VK_VOLUME_DOWN
        ps = f"$w=New-Object -ComObject WScript.Shell; 1..{steps}|%{{$w.SendKeys([char]{key_code})}}"
        label = "decreased"

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

def _exec_brightness_control(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    action = params.get("action", "up")   # up | down | set
    delta  = max(5, min(50, int(params.get("delta", 20))))
    level  = params.get("level")          # absolute 0-100, only for "set"

    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return ToolResult(success=False, text="cmd.exe not found.",
                          spoken="Brightness control is not available on this system.")

    if action == "set" and level is not None:
        ps = (
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods"
            f" -ErrorAction Stop).WmiSetBrightness(1,{int(level)})"
        )
        label = f"set to {level}%"
    elif action == "up":
        ps = (
            f"$cur=(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness"
            f" -ErrorAction Stop).CurrentBrightness;"
            f"$new=[Math]::Min(100,$cur+{delta});"
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods"
            f" -ErrorAction Stop).WmiSetBrightness(1,$new)"
        )
        label = "increased"
    else:
        ps = (
            f"$cur=(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness"
            f" -ErrorAction Stop).CurrentBrightness;"
            f"$new=[Math]::Max(0,$cur-{delta});"
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods"
            f" -ErrorAction Stop).WmiSetBrightness(1,$new)"
        )
        label = "decreased"

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
        subprocess.run(
            [cmd_exe, "/c", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"],
            check=True, capture_output=True, timeout=5,
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
            "description": "Create a new folder/directory. Use for: 'create folder called X in Y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Folder name to create"},
                    "path": {"type": "string", "description": "Parent directory path (e.g. 'E:\\\\' or 'Desktop'). Defaults to user home."},
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
                "'my computer', 'my laptop', 'my PC', 'system info', 'processor', 'memory', 'storage'. "
                "Returns real Windows CPU name, RAM size, OS version, and drive space. "
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
            "description": "Permanently delete a file or folder. ALWAYS requires voice confirmation before executing. Use when user says 'delete X', 'remove X file'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":      {"type": "string", "description": "Path to delete"},
                    "confirmed": {"type": "boolean", "description": "Must be true to actually delete — set only after user voice-confirms"},
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
