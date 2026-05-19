"""System control endpoints — open applications, perform web searches."""
from __future__ import annotations

import logging
import os
import re as _re
import subprocess
import sys
import threading
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/system", tags=["system"])
logger = logging.getLogger(__name__)


# ── WSL2 detection (cached — reads /proc/version once at import time) ────────

def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False

_WSL: bool = _is_wsl()          # cache — never changes during a process lifetime
_PLATFORM: str = "wsl" if _WSL else sys.platform  # "wsl" | "linux" | "darwin" | "win32"

_CMDEXE: str | None = None

def _find_cmdexe() -> str:
    global _CMDEXE
    if _CMDEXE:
        return _CMDEXE
    for p in [
        "/mnt/c/Windows/System32/cmd.exe",
        "/mnt/c/WINDOWS/System32/cmd.exe",
        "/mnt/c/WINDOWS/system32/cmd.exe",
    ]:
        if os.path.exists(p):
            _CMDEXE = p
            return p
    _CMDEXE = "cmd.exe"  # fallback
    return _CMDEXE


# ── App launch ────────────────────────────────────────────────────────────────

# Map of normalized app name → command per platform
_APP_MAP: dict[str, dict[str, str]] = {
    "code":        {"wsl": 'cmd.exe /c start "" code', "linux": "code",       "darwin": "open -a 'Visual Studio Code'",   "win32": 'start "" code'},
    "vscode":      {"wsl": 'cmd.exe /c start "" code', "linux": "code",       "darwin": "open -a 'Visual Studio Code'",   "win32": 'start "" code'},
    "chrome":      {"wsl": "chrome.exe",      "linux": "google-chrome",      "darwin": "open -a 'Google Chrome'",        "win32": "start chrome"},
    "firefox":     {"wsl": "firefox.exe",     "linux": "firefox",            "darwin": "open -a Firefox",                "win32": "start firefox"},
    "spotify":     {"wsl": "spotify.exe",     "linux": "spotify",            "darwin": "open -a Spotify",                "win32": "start spotify"},
    "notepad":     {"wsl": "notepad.exe",     "linux": "gedit",              "darwin": "open -a TextEdit",               "win32": "start notepad"},
    "calculator":  {"wsl": "calc.exe",        "linux": "gnome-calculator",   "darwin": "open -a Calculator",             "win32": "start calc"},
    "explorer":    {"wsl": "explorer.exe",    "linux": "nautilus",           "darwin": "open .",                         "win32": "start explorer"},
    "terminal":    {"wsl": "wt.exe",          "linux": "gnome-terminal",     "darwin": "open -a Terminal",               "win32": "start cmd"},
    "cmd":         {"wsl": "cmd.exe",         "linux": "bash",               "darwin": "open -a Terminal",               "win32": "start cmd"},
    "word":        {"wsl": "winword.exe",     "linux": "libreoffice --writer","darwin": "open -a 'Microsoft Word'",      "win32": "start winword"},
    "excel":       {"wsl": "excel.exe",       "linux": "libreoffice --calc", "darwin": "open -a 'Microsoft Excel'",      "win32": "start excel"},
    "powershell":  {"wsl": "powershell.exe",  "linux": "bash",               "darwin": "open -a Terminal",               "win32": "start powershell"},
    "settings":    {"wsl": "cmd.exe /c start ms-settings:", "linux": "gnome-control-center", "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:"},
    "discord":     {"wsl": "discord.exe",    "linux": "discord",            "darwin": "open -a Discord",                "win32": "start discord"},
    "zoom":        {"wsl": "zoom.exe",       "linux": "zoom",               "darwin": "open -a zoom.us",                "win32": "start zoom"},
    "teams":       {"wsl": "ms-teams.exe",   "linux": "teams",              "darwin": "open -a 'Microsoft Teams'",      "win32": "start msteams"},
    "slack":       {"wsl": "slack.exe",      "linux": "slack",              "darwin": "open -a Slack",                  "win32": "start slack"},
    "paint":       {"wsl": "mspaint.exe",    "linux": "gimp",               "darwin": "open -a Preview",                "win32": "start mspaint"},
    "taskmanager": {"wsl": "taskmgr.exe",   "linux": "gnome-system-monitor","darwin": "open -a 'Activity Monitor'",    "win32": "start taskmgr"},
    "edge":        {"wsl": "cmd.exe /c start msedge", "linux": "microsoft-edge", "darwin": "open -a 'Microsoft Edge'", "win32": "start msedge"},
    "powerpoint":  {"wsl": "powerpnt.exe",  "linux": "libreoffice --impress","darwin": "open -a 'Microsoft PowerPoint'", "win32": "start powerpnt"},
    # Windows Settings sub-pages
    "displaysettings":    {"wsl": "cmd.exe /c start ms-settings:display",                 "linux": "gnome-control-center display",   "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:display"},
    "soundsettings":      {"wsl": "cmd.exe /c start ms-settings:sound",                   "linux": "gnome-control-center sound",     "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:sound"},
    "bluetoothsettings":  {"wsl": "cmd.exe /c start ms-settings:bluetooth",               "linux": "gnome-control-center bluetooth", "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:bluetooth"},
    "networksettings":    {"wsl": "cmd.exe /c start ms-settings:network",                 "linux": "gnome-control-center network",   "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:network"},
    "wallpapersettings":  {"wsl": "cmd.exe /c start ms-settings:personalization-background","linux": "gnome-control-center background","darwin": "open x-apple.systempreferences:","win32": "start ms-settings:personalization-background"},
    "privacysettings":    {"wsl": "cmd.exe /c start ms-settings:privacy",                 "linux": "gnome-control-center privacy",   "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:privacy"},
    "updatesettings":     {"wsl": "cmd.exe /c start ms-settings:windowsupdate",           "linux": "gnome-software-update",          "darwin": "open x-apple.systempreferences:SoftwareUpdate", "win32": "start ms-settings:windowsupdate"},
    "appssettings":       {"wsl": "cmd.exe /c start ms-settings:appsfeatures",            "linux": "gnome-control-center",           "darwin": "open x-apple.systempreferences:", "win32": "start ms-settings:appsfeatures"},
}

# Aliases normaliser
_ALIASES: dict[str, str] = {
    "vs code": "vscode",
    "visual studio code": "vscode",
    "virtual studio code": "vscode",
    "virtual studio": "vscode",
    "vs studio": "vscode",
    "google chrome": "chrome",
    "file explorer": "explorer",
    "windows explorer": "explorer",
    "windows terminal": "terminal",
    "command prompt": "cmd",
    "microsoft word": "word",
    "microsoft excel": "excel",
    "setting": "settings",
    "windows settings": "settings",
    "system settings": "settings",
    "system preferences": "settings",
    "task manager": "taskmanager",
    "microsoft teams": "teams",
    # Settings sub-pages
    "display settings": "displaysettings",
    "display setting":  "displaysettings",
    "screen settings":  "displaysettings",
    "screen resolution": "displaysettings",
    "brightness settings": "displaysettings",
    "sound settings":   "soundsettings",
    "audio settings":   "soundsettings",
    "speaker settings": "soundsettings",
    "volume settings":  "soundsettings",
    "microphone settings": "soundsettings",
    "bluetooth settings":  "bluetoothsettings",
    "bluetooth setting":   "bluetoothsettings",
    "wifi settings":    "networksettings",
    "wi-fi settings":   "networksettings",
    "network settings": "networksettings",
    "internet settings": "networksettings",
    "wallpaper settings":  "wallpapersettings",
    "background settings": "wallpapersettings",
    "change wallpaper":    "wallpapersettings",
    "change background":   "wallpapersettings",
    "privacy settings":    "privacysettings",
    "windows update":      "updatesettings",
    "update settings":     "updatesettings",
    "check for updates":   "updatesettings",
    "apps settings":       "appssettings",
    "installed apps":      "appssettings",
    "manage apps":         "appssettings",
}


def _normalise(name: str) -> str:
    n = name.lower().strip()
    return _ALIASES.get(n, n.replace(" ", "").replace("-", "").replace("_", ""))


# ── Start Menu index (built once, background thread) ─────────────────────────
# Maps lower-case display name → Windows path of the .lnk shortcut.
_START_INDEX: dict[str, str] = {}
_START_INDEX_BUILT = False
_START_INDEX_LOCK  = threading.Lock()

def _build_start_index() -> None:
    """Walk Windows Start Menu dirs and index every .lnk by display name."""
    global _START_INDEX, _START_INDEX_BUILT
    index: dict[str, str] = {}
    dirs: list[Path] = []

    if _WSL:
        dirs = [
            Path("/mnt/c/ProgramData/Microsoft/Windows/Start Menu/Programs"),
        ]
        # Add per-user Start Menu
        try:
            r = subprocess.run(
                [_find_cmdexe(), "/c", "echo %USERNAME%"],
                capture_output=True, text=True, timeout=3,
            )
            user = r.stdout.strip()
            if user and user != "%USERNAME%":
                dirs.append(Path(
                    f"/mnt/c/Users/{user}/AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
                ))
        except Exception:
            pass
    # On native Windows we'd use CSIDL paths, but this runs in WSL so skip.

    for d in dirs:
        if not d.exists():
            continue
        for lnk in d.rglob("*.lnk"):
            display = lnk.stem.lower()
            # Strip common noise words to improve fuzzy matching
            clean = _re.sub(r"\s*\(.*?\)", "", display).strip()
            win_path = str(lnk).replace("/mnt/c/", "C:\\").replace("/", "\\")
            index[clean] = win_path
            if clean != display:
                index[display] = win_path

    with _START_INDEX_LOCK:
        _START_INDEX.update(index)
        _START_INDEX_BUILT = True
    logger.info("Start Menu index: %d entries", len(index))


def _ensure_index() -> None:
    if not _START_INDEX_BUILT and _WSL:
        threading.Thread(target=_build_start_index, daemon=True).start()


def _find_start_menu(query: str) -> str | None:
    """Return Windows .lnk path for the best matching Start Menu entry, or None."""
    if not _START_INDEX_BUILT:
        return None
    q = query.lower().strip()
    with _START_INDEX_LOCK:
        # Exact match
        if q in _START_INDEX:
            return _START_INDEX[q]
        # Substring match — prefer shorter names (closer match)
        candidates = [(name, path) for name, path in _START_INDEX.items()
                      if q in name or name in q]
        if candidates:
            best = min(candidates, key=lambda x: abs(len(x[0]) - len(q)))
            return best[1]
    return None


def _popen(argv: list[str]) -> None:
    """Fire-and-forget subprocess.
    In WSL2, use /init to launch Windows PE binaries via the interop socket."""
    if _WSL:
        import shlex
        args = shlex.split(argv[0]) if len(argv) == 1 else list(argv)
        subprocess.Popen(['/init'] + args,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(argv, shell=False,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True)


def _launch(app_name: str) -> tuple[bool, str]:
    """Try to launch an application. Returns (success, message)."""
    _ensure_index()

    # ── Priority 0: app_finder 4-source index (Start Menu + Registry + PATH + Store) ──
    # Has full .lnk / .exe paths so cmd.exe /c start "" <path> always resolves correctly.
    try:
        from api.tools.core.app_finder import _search_index, _launch_via_interop
        _entry, _match = _search_index(app_name)
        if _entry is not None:
            _path = _entry.get("path", "")
            _name = _entry.get("name", app_name)
            if _launch_via_interop(_path, _name):
                logger.info("Launched (app_finder/%s): %s → %s", _match, app_name, _name)
                return True, f"Opening {_name}…"
    except Exception:
        pass

    key   = _normalise(app_name)
    entry = _APP_MAP.get(key)

    try:
        if entry:
            # ── Fast path: known app in _APP_MAP (ms-settings:// URIs, WSL tools) ──
            cmd = entry.get(_PLATFORM) or entry.get("linux", "")
            if not cmd:
                return False, f"'{app_name}' is not supported on this platform."
            if _WSL:
                cmd = cmd.replace("cmd.exe", _find_cmdexe())
                _popen([cmd])
            else:
                _popen(cmd.split())
            logger.info("Launched (map): %s → %s", app_name, cmd)

        elif _WSL or sys.platform == "win32":
            # ── Fallback 1: Start Menu shortcut search ────────────────────────
            lnk = _find_start_menu(app_name)
            if lnk:
                _popen([_find_cmdexe(), "/c", "start", "", lnk])
                logger.info("Launched (start-menu): %s → %s", app_name, lnk)
            else:
                # ── Fallback 2: cmd.exe start (uses App Paths registry) ────────
                _popen([_find_cmdexe(), "/c", "start", "", app_name])
                logger.info("Launched (cmd-start): %s", app_name)
        else:
            return False, f"Unknown app '{app_name}'."

        return True, f"Opening {app_name}…"
    except Exception as exc:
        logger.warning("Launch failed for %s: %s", app_name, exc)
        return False, f"Could not launch {app_name}: {exc}"


# Trigger the index build at import time so it's ready before first voice command
if _WSL:
    threading.Thread(target=_build_start_index, daemon=True).start()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/open-app")
async def open_app(body: dict):
    """Launch a desktop application by name.

    Body: { "app": "VS Code" }
    """
    app_name = body.get("app", "").strip()
    if not app_name:
        raise HTTPException(status_code=400, detail="'app' is required")

    success, message = _launch(app_name)
    return {"success": success, "message": message}


@router.get("/supported-apps")
async def supported_apps():
    """Return the list of launchable application names."""
    return {
        "success": True,
        "data": sorted(_APP_MAP.keys()),
    }


# ── New system control endpoints (powered by tool registry) ───────────────────

@router.post("/open-directory")
async def open_directory(body: dict):
    """Open a folder or drive in the file explorer.

    Body: {"path": "E:\\"} or {"path": "Desktop"}
    """
    from api.tools import registry
    path   = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="'path' is required")
    result = registry.execute("open_directory", {"path": path})
    return {"success": result.success, "message": result.text, "spoken": result.spoken}


@router.post("/create-folder")
async def create_folder(body: dict):
    """Create a new folder.

    Body: {"path": "E:\\\\", "name": "Projects"}
    """
    from api.tools import registry
    from api.services.context_memory import context_memory
    name = body.get("name", "").strip()
    path = body.get("path", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="'name' is required")
    result = registry.execute("create_folder", {"path": path, "name": name})
    created_path = (result.data or {}).get("path", "") if result.success else ""
    if result.success and created_path:
        context_memory.record_action("create_folder", [name], [created_path])
    return {"success": result.success, "message": result.text, "spoken": result.spoken, "path": created_path}


@router.post("/create-subfolders")
async def create_subfolders(body: dict):
    """Create multiple subfolders inside an existing folder.

    Body: {"parent": "E:\\\\Projects", "names": ["Frontend", "Backend", "Database"]}
    """
    from api.tools import registry
    from api.services.context_memory import context_memory
    parent = body.get("parent", "").strip()
    names  = body.get("names", [])
    if not parent:
        raise HTTPException(status_code=400, detail="'parent' is required")
    if not names:
        raise HTTPException(status_code=400, detail="'names' is required")
    result = registry.execute("create_subfolders", {"parent": parent, "names": names})
    if result.success:
        import os as _os
        created_paths = [_os.path.join(parent, n) for n in names]
        context_memory.record_action("create_subfolders", names, created_paths, {"parent": parent})
    return {"success": result.success, "message": result.text, "spoken": result.spoken}


@router.post("/list-directory")
async def list_directory(body: dict):
    """List directory contents.

    Body: {"path": "E:\\\\"}
    """
    from api.tools import registry
    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="'path' is required")
    result = registry.execute("list_directory", {"path": path})
    return {"success": result.success, "message": result.text, "data": result.data, "spoken": result.spoken}


@router.get("/system-info")
async def system_info_endpoint():
    """Return OS, CPU, RAM, and all drives with exact usage figures."""
    from api.tools import registry
    result = registry.execute("system_info", {})
    return {"success": result.success, "message": result.text, "data": result.data}


@router.get("/system-health")
async def system_health_endpoint():
    """Return live CPU %, RAM %, and disk usage % for all drives."""
    from api.tools import registry
    result = registry.execute("system_health", {})
    return {"success": result.success, "message": result.text, "data": result.data}


@router.get("/metrics")
async def system_metrics_endpoint():
    """Return real-time CPU, RAM, disk, network I/O and uptime — polled by the dashboard."""
    import time
    try:
        import psutil
    except ImportError:
        return {"success": False, "data": None, "message": "psutil not installed"}

    GiB = 1024 ** 3
    MiB = 1024 ** 2

    cpu_pct   = psutil.cpu_percent(interval=0.3)
    cpu_cores = psutil.cpu_count(logical=True) or 1
    cpu_freq  = psutil.cpu_freq()

    vm        = psutil.virtual_memory()
    sw        = psutil.swap_memory()

    # disk — aggregate all real partitions
    disk_total_gb = disk_used_gb = 0.0
    try:
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                disk_total_gb += u.total / GiB
                disk_used_gb  += u.used  / GiB
            except (PermissionError, OSError):
                pass
    except Exception:
        pass
    disk_pct = round(disk_used_gb / disk_total_gb * 100, 1) if disk_total_gb else 0

    # network I/O (cumulative counters — caller can diff)
    net  = psutil.net_io_counters()

    # temperatures (not available on all platforms / WSL2)
    temp_c: float | None = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for key in ("coretemp", "cpu_thermal", "k10temp", "acpitz"):
                if key in temps and temps[key]:
                    temp_c = round(temps[key][0].current, 1)
                    break
    except (AttributeError, NotImplementedError):
        pass

    # uptime
    uptime_s = int(time.time() - psutil.boot_time())
    h, rem   = divmod(uptime_s, 3600)
    m, s     = divmod(rem, 60)

    data = {
        "cpu_pct":          round(cpu_pct, 1),
        "cpu_cores":        cpu_cores,
        "cpu_freq_mhz":     round(cpu_freq.current, 0) if cpu_freq else None,
        "ram_pct":          round(vm.percent, 1),
        "ram_used_gb":      round(vm.used  / GiB, 1),
        "ram_total_gb":     round(vm.total / GiB, 1),
        "swap_pct":         round(sw.percent, 1),
        "disk_pct":         disk_pct,
        "disk_used_gb":     round(disk_used_gb, 1),
        "disk_total_gb":    round(disk_total_gb, 1),
        "net_bytes_sent":   net.bytes_sent,
        "net_bytes_recv":   net.bytes_recv,
        "temp_c":           temp_c,
        "uptime_s":         uptime_s,
        "uptime_str":       f"{h}h {m:02d}m {s:02d}s",
    }
    return {"success": True, "data": data, "message": "ok"}


@router.get("/running-apps")
async def running_apps_endpoint():
    """Return list of currently running applications."""
    from api.tools import registry
    result = registry.execute("get_running_apps", {})
    return {"success": result.success, "message": result.text, "data": result.data}


@router.post("/execute-tool")
async def execute_tool(body: dict):
    """Execute a registered tool by name with params (used for confirmed high-risk actions).

    Body: {"tool": "delete_file", "params": {"path": "...", "confirmed": true}}
    Returns: {"success": bool, "spoken": "...", "message": "..."}
    """
    import asyncio
    tool   = (body.get("tool") or "").strip()
    params = body.get("params") or {}
    if not tool:
        raise HTTPException(status_code=400, detail="'tool' is required")
    from api.tools import registry
    if tool not in registry:
        raise HTTPException(status_code=404, detail=f"Tool '{tool}' not found")
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, tool, params, {})
    if result.success and tool in ("delete_file", "delete_folder"):
        from api.services.context_memory import context_memory
        target = params.get("path", params.get("name", ""))
        if target:
            context_memory.record_action(tool, [str(target)], [str(target)])
    return {"success": result.success, "spoken": result.spoken, "message": result.text, "data": result.data}


@router.post("/delete-multiple")
async def delete_multiple(body: dict):
    """Delete multiple files/folders by path.

    Body: {"paths": ["C:\\\\foo", "C:\\\\bar"]}
    Returns: {"success": bool, "deleted": [...], "failed": [...]}
    """
    import asyncio
    from api.tools import registry
    from api.services.context_memory import context_memory

    paths = body.get("paths") or []
    if not paths:
        raise HTTPException(status_code=400, detail="'paths' is required")

    deleted: list[str] = []
    failed:  list[dict] = []
    loop = asyncio.get_event_loop()

    for p in paths:
        p = str(p).strip()
        if not p:
            continue
        result = await loop.run_in_executor(
            None, registry.execute, "delete_file", {"path": p, "confirmed": True}, {}
        )
        if result.success:
            deleted.append(p)
        else:
            failed.append({"path": p, "reason": result.text})

    if deleted:
        import os as _os
        names = [_os.path.basename(p) or p for p in deleted]
        context_memory.record_action("delete_multiple", names, deleted)

    overall = len(failed) == 0
    spoken = (
        f"Deleted {len(deleted)} item{'s' if len(deleted) != 1 else ''}."
        if overall
        else f"Deleted {len(deleted)}, failed {len(failed)}."
    )
    return {
        "success":  overall,
        "spoken":   spoken,
        "deleted":  deleted,
        "failed":   failed,
    }


@router.get("/registered-tools")
async def registered_tools():
    """Return list of all registered tools with their risk levels."""
    from api.tools import registry
    from api.tools.safety import assess_risk
    tools = []
    for name in registry.list_tools():
        defs = [d for d in registry.get_definitions() if d["function"]["name"] == name]
        desc = defs[0]["function"]["description"] if defs else ""
        tools.append({
            "name": name,
            "description": desc,
            "risk": registry.get_risk(name),
        })
    return {"success": True, "data": tools}


# ── YouTube play (direct video URL via yt-dlp) ────────────────────────────────

@router.post("/youtube-play")
async def youtube_play(body: dict):
    """Search YouTube with yt-dlp and return the first direct video URL.

    This gives a /watch?v=... URL that autoplays in the browser,
    unlike /results?search_query=... which requires a click.

    Body:   {"query": "lofi hip hop"}
    Returns: {"success": true, "url": "https://youtube.com/watch?v=...", "title": "..."}
    """
    import asyncio
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="'query' is required")

    try:
        import yt_dlp  # installed via pip

        opts = {
            "quiet":          True,
            "no_warnings":    True,
            "noplaylist":     True,
            "extract_flat":   "in_playlist",
            "skip_download":  True,
            "socket_timeout": 6,
        }

        def _search():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if not info or not info.get("entries"):
                    return None, None
                entry = next((e for e in info["entries"] if e and e.get("id")), None)
                if not entry:
                    return None, None
                return entry.get("id"), entry.get("title", query)

        loop = asyncio.get_event_loop()
        try:
            vid_id, title = await asyncio.wait_for(
                loop.run_in_executor(None, _search), timeout=8.0
            )
        except asyncio.TimeoutError:
            logger.warning("YouTube yt-dlp search timed out for: %s", query)
            vid_id, title = None, None

        if vid_id:
            url = f"https://www.youtube.com/watch?v={vid_id}"
            logger.info("YouTube play: %s → %s (%s)", query, vid_id, title)
            return {"success": True, "url": url, "title": title}

    except ImportError:
        logger.warning("yt-dlp not installed — falling back to search URL")
    except Exception as exc:
        logger.warning("yt-dlp search failed: %s", exc)

    # Fallback: return a search URL (won't autoplay but still works)
    import urllib.parse
    fallback = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    return {"success": False, "url": fallback, "title": None}


# ── News headlines via Google News RSS ───────────────────────────────────────

def _parse_news_rss(xml_text: str, limit: int) -> list[dict]:
    """Parse Google News RSS into {title, source, summary} dicts.

    Google News RSS <description> embeds an HTML list that repeats the title
    and source — we strip all of that and return only novel summary text.
    """
    import xml.etree.ElementTree as ET
    import re as _re
    import html as _html

    tree  = ET.fromstring(xml_text)
    items = tree.findall(".//item")[:limit]
    stories: list[dict] = []

    for item in items:
        raw_title = item.findtext("title") or ""

        # Split "Headline - Source Name" → title + source
        src_m  = _re.search(r"\s+-\s+([^-]{3,60})$", raw_title)
        source = src_m.group(1).strip() if src_m else ""
        title  = raw_title[: src_m.start()].strip() if src_m else raw_title.strip()

        # Description is an HTML blob that looks like:
        #   <ol><li><a href="…">Title - Source</a></li>…</ol>
        # Strip ALL html tags first, then remove the repeated title/source strings.
        raw_desc  = item.findtext("description") or ""
        plain     = _re.sub(r"<[^>]+>", " ", raw_desc)
        plain     = _html.unescape(plain)
        plain     = _re.sub(r"\s+", " ", plain).strip()

        # Remove the title (exact + partial prefix) wherever it appears
        if title:
            plain = plain.replace(title, "")
        # Remove source name wherever it appears as a standalone word
        if source:
            plain = _re.sub(r"\b" + _re.escape(source) + r"\b", "", plain)
        # Remove raw_title remnants and leftover " - " separators
        plain = _re.sub(r"\s+-\s+", " ", plain)
        plain = _re.sub(r"\s{2,}", " ", plain).strip()

        # The remainder is usually a list of related headlines (not a prose summary).
        # Split on sentence boundaries and take up to 2 meaningful sentences.
        sentences = [s.strip() for s in _re.split(r"(?<=[.!?])\s+", plain) if len(s.strip()) > 20]
        summary   = " ".join(sentences[:2])
        if len(summary) > 220:
            summary = summary[:217].rstrip() + "…"

        if title:
            stories.append({"title": title, "source": source, "summary": summary})

    return stories


def _build_spoken(stories: list[dict], date_str: str, is_topic: bool = False) -> str:
    """Format stories into a professional broadcast-style spoken script."""
    transitions = ["Our first story", "Moving on", "In other news", "Next", "And finally"]
    lines: list[str] = []
    for i, s in enumerate(stories):
        lead          = transitions[i] if i < len(transitions) else "Also"
        source_phrase = f", reported by {s['source']}," if s["source"] else ","
        detail        = f" {s['summary']}" if s["summary"] else ""
        lines.append(f"{lead}{source_phrase} {s['title']}.{detail}")

    if is_topic:
        intro = f"Here is what I found on that topic as of {date_str}. "
        outro = " I've opened the full stories in Google News so you can read more."
    else:
        intro = f"Here is your news briefing for {date_str}. "
        outro = " That's your latest news. I've opened the full stories in Google News for you."

    return intro + " ".join(lines) + outro


@router.get("/news")
async def get_news(q: str = "latest news today", limit: int = 5, topic: bool = False):
    """Fetch top headlines from Google News RSS with summaries.

    ?q=search+query  — what to search for
    ?topic=true      — changes intro/outro to topic-query style
    Returns spoken text formatted like a professional news broadcast.
    """
    import urllib.parse
    import asyncio
    from datetime import datetime

    encoded = urllib.parse.quote(q)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"

    def _fetch():
        import urllib.request
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read().decode("utf-8")

    try:
        loop     = asyncio.get_event_loop()
        xml_text = await loop.run_in_executor(None, _fetch)
        stories  = _parse_news_rss(xml_text, limit)

        if not stories:
            return {"success": False, "stories": [], "spoken": "I couldn't find any headlines right now. Please check Google News in your browser."}

        date_str = datetime.now().strftime("%B %d, %Y")
        spoken   = _build_spoken(stories, date_str, is_topic=topic)

        return {
            "success": True,
            "stories": stories,
            "spoken":  spoken,
        }

    except Exception as exc:
        logger.warning("News RSS fetch failed: %s", exc)
        return {
            "success": False,
            "stories": [],
            "spoken":  "I'm having trouble reaching the news feed right now. Google News is open in your browser — you can read the headlines there.",
        }


# ── Price lookup (crypto + stocks) ───────────────────────────────────────────

# CoinGecko coin ID map for common names / tickers
_CRYPTO_IDS: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "binance coin": "binancecoin", "bnb": "binancecoin",
    "xrp": "ripple", "ripple": "ripple",
    "cardano": "cardano", "ada": "cardano",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "litecoin": "litecoin", "ltc": "litecoin",
    "polkadot": "polkadot", "dot": "polkadot",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "shiba inu": "shiba-inu", "shib": "shiba-inu",
    "polygon": "matic-network", "matic": "matic-network",
    "uniswap": "uniswap", "uni": "uniswap",
    "tron": "tron", "trx": "tron",
    "stellar": "stellar", "xlm": "stellar",
    "pepe": "pepe", "floki": "floki",
}


def _fetch_price(query: str) -> dict:
    """Try CoinGecko first, then Yahoo Finance for stocks."""
    import urllib.request, json, re as _re

    q = query.lower().strip()
    coin_id = _CRYPTO_IDS.get(q)

    if coin_id:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read())
            if coin_id in data:
                price  = data[coin_id]["usd"]
                change = data[coin_id].get("usd_24h_change", 0)
                name   = query.title()
                price_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
                direction = "up" if change >= 0 else "down"
                pct = abs(change)
                spoken = (f"The current {name} price is {price_str} USD. "
                          f"It is {direction} {pct:.2f}% in the last 24 hours.")
                return {"success": True, "asset": name, "price": price_str, "change": f"{change:+.2f}%", "spoken": spoken}
        except Exception as e:
            logger.warning("CoinGecko failed: %s", e)

    # Yahoo Finance fallback — works for stocks and indices
    ticker = q.upper().replace(" ", "-")
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        meta  = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev  = meta.get("previousClose", price)
        curr  = meta.get("regularMarketPrice", price)
        change_pct = ((curr - prev) / prev * 100) if prev else 0
        direction  = "up" if change_pct >= 0 else "down"
        name   = meta.get("longName") or meta.get("shortName") or query.title()
        spoken = (f"The current price of {name} is ${curr:,.2f}. "
                  f"It is {direction} {abs(change_pct):.2f}% today.")
        return {"success": True, "asset": name, "price": f"${curr:,.2f}", "change": f"{change_pct:+.2f}%", "spoken": spoken}
    except Exception as e:
        logger.warning("Yahoo Finance failed for %s: %s", ticker, e)

    return {"success": False, "spoken": f"I couldn't fetch the live price for {query} right now. I've opened Google so you can check it there."}


@router.get("/price")
async def get_price(q: str):
    """Fetch live price for a crypto coin or stock ticker.

    ?q=bitcoin  or  ?q=AAPL
    Returns: {"success": bool, "spoken": "The current Bitcoin price is $84,000..."}
    """
    import asyncio
    if not q.strip():
        raise HTTPException(status_code=400, detail="'q' is required")
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch_price, q.strip())
    return result


# ── Wikipedia quick-fact endpoint ─────────────────────────────────────────────

@router.get("/wiki")
async def wiki_lookup(topic: str = ""):
    """Return a 2-sentence Wikipedia summary for a topic.

    ?topic=Python programming language
    Returns: {"success": bool, "spoken": "...", "data": {...}}
    """
    import asyncio
    if not topic.strip():
        raise HTTPException(status_code=400, detail="'topic' is required")
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "wiki_summary", {"topic": topic.strip()}, {})
    return {"success": result.success, "spoken": result.spoken, "data": result.data}


# ── Clipboard endpoints ───────────────────────────────────────────────────────

@router.get("/clipboard")
async def clipboard_read():
    """Read the current Windows clipboard text via PowerShell.

    Returns: {"success": bool, "data": {"text": "..."}}
    """
    import asyncio
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "read_clipboard", {}, {})
    return {"success": result.success, "data": result.data, "text": result.text}


@router.post("/clipboard")
async def clipboard_write(body: dict):
    """Write text to the Windows clipboard via PowerShell.

    Body: {"text": "..."}
    Returns: {"success": bool}
    """
    import asyncio
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="'text' is required")
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "write_clipboard", {"text": text}, {})
    return {"success": result.success, "message": result.text}


# ── Screen reading endpoint ───────────────────────────────────────────────────

@router.post("/screen-read")
async def screen_read(body: dict):
    """Take a screenshot, send to GPT-4o Vision, return spoken description.

    Body: {"question": "What's on the screen?"} (optional)
    Returns: {"success": bool, "spoken": "...", "message": "..."}
    """
    import asyncio
    question = (body.get("question") or "What is on the screen?").strip()
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "read_screen", {"question": question}, {})
    return {"success": result.success, "spoken": result.spoken, "message": result.text}


# ── Typing endpoint ───────────────────────────────────────────────────────────

@router.post("/type-text")
async def type_text_endpoint(body: dict):
    """Type text into the currently focused Windows application via PowerShell SendKeys.

    Body: {"text": "Hello world"}
    Returns: {"success": bool}
    """
    import asyncio
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="'text' is required")
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "type_text", {"text": text}, {})
    return {"success": result.success, "message": result.text}


# ── Window control endpoint ───────────────────────────────────────────────────

@router.post("/window")
async def window_control(body: dict):
    """Control windows: minimize, maximize, close, or switch.

    Body: {"action": "minimize"|"maximize"|"close"|"switch", "title": "Chrome"}
    Returns: {"success": bool}
    """
    import asyncio
    action = (body.get("action") or "").strip().lower()
    title  = (body.get("title") or "").strip()

    tool_map = {
        "minimize": ("minimize_window", {}),
        "maximize": ("maximize_window", {}),
        "close":    ("close_window",    {}),
        "switch":   ("switch_window",   {"title": title}),
    }
    if action not in tool_map:
        raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")

    tool_name, tool_params = tool_map[action]
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, tool_name, tool_params, {})
    return {"success": result.success, "message": result.text}


# ── Gmail inbox endpoint ──────────────────────────────────────────────────────

@router.get("/gmail/inbox")
async def gmail_inbox(max_results: int = 5):
    """Read recent unread Gmail messages.

    Returns: {"success": bool, "spoken": "...", "data": {"emails": [...]}}
    """
    import asyncio
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "read_inbox", {"max_results": max_results}, {})
    return {"success": result.success, "spoken": result.spoken, "message": result.text, "data": result.data}


# ── Calendar events endpoint ──────────────────────────────────────────────────

@router.get("/calendar/events")
async def calendar_events(days_ahead: int = 1):
    """List upcoming Google Calendar events.

    Returns: {"success": bool, "spoken": "...", "data": {"events": [...]}}
    """
    import asyncio
    from api.tools import registry
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, registry.execute, "list_events", {"days_ahead": days_ahead}, {})
    return {"success": result.success, "spoken": result.spoken, "message": result.text, "data": result.data}


# ── I'm Home Briefing Endpoint ─────────────────────────────────────────────────

_WMO_CODES: dict[int, str] = {
    0: "clear sky",       1: "mainly clear",      2: "partly cloudy",       3: "overcast",
    45: "foggy",          48: "freezing fog",
    51: "light drizzle",  53: "drizzle",           55: "heavy drizzle",
    56: "icy drizzle",    57: "heavy icy drizzle",
    61: "light rain",     63: "rain",              65: "heavy rain",
    66: "freezing rain",  67: "heavy freezing rain",
    71: "light snow",     73: "snow",              75: "heavy snow",         77: "snow grains",
    80: "light showers",  81: "showers",           82: "violent showers",
    85: "snow showers",   86: "heavy snow showers",
    95: "thunderstorm",   96: "thunderstorm with hail", 99: "severe thunderstorm",
}

@router.get("/home-briefing")
async def home_briefing():
    """Aggregate real-time data for the I'm Home cinematic protocol.

    Returns system metrics, Karachi weather (Open-Meteo), and AI news (HN Algolia).
    All external calls have 4s timeouts and fail gracefully.
    """
    import time as _time
    import asyncio as _asyncio

    # wttr.in extended weather codes → human description
    _WTTR_CODES: dict[int, str] = {
        113: "sunny",          116: "partly cloudy",  119: "cloudy",
        122: "overcast",       143: "hazy",            248: "foggy",
        260: "freezing fog",   176: "light showers",   179: "sleet showers",
        182: "light sleet",    200: "thundery nearby", 227: "blowing snow",
        230: "blizzard",       263: "light drizzle",   266: "light drizzle",
        281: "freezing drizzle", 284: "heavy freezing drizzle",
        293: "light rain",     296: "light rain",      299: "moderate rain",
        302: "moderate rain",  305: "heavy rain",      308: "heavy rain",
        311: "freezing rain",  317: "light sleet",     320: "moderate sleet",
        323: "light snow",     326: "light snow",      329: "moderate snow",
        332: "moderate snow",  335: "heavy snow",      338: "heavy snow",
        350: "ice pellets",    353: "light showers",   356: "heavy showers",
        359: "torrential rain", 362: "sleet showers",  368: "snow showers",
        371: "heavy snow showers", 386: "thundery rain", 389: "heavy thunderstorm",
        392: "snowy thunderstorm", 395: "heavy snowy thunderstorm",
    }

    async def _fetch_weather() -> dict:
        """Primary: wttr.in (real observation data). Fallback: Open-Meteo (model data)."""
        import httpx

        async def _wttr(client: httpx.AsyncClient) -> dict | None:
            r = await client.get("https://wttr.in/Karachi?format=j1", timeout=5.0)
            if r.status_code != 200:
                return None
            d   = r.json()
            cc  = d.get("current_condition", [{}])[0]
            code = int(cc.get("weatherCode", 0))
            desc = cc.get("weatherDesc", [{}])[0].get("value", "")
            if not desc:
                desc = _WTTR_CODES.get(code, "clear")
            # wttr rain chance: from hourly[0] or nearest3Hours
            rain_pct = None
            try:
                rain_pct = int(d["weather"][0]["hourly"][0].get("chanceofrain", 0))
            except Exception:
                pass
            return {
                "temp_c":      int(cc.get("temp_C", 0)),
                "humidity":    int(cc.get("humidity", 0)),
                "wind_kmh":    int(cc.get("windspeedKmph", 0)),
                "rain_pct":    rain_pct,
                "code":        code,
                "description": desc.lower(),
            }

        async def _openmeteo(client: httpx.AsyncClient) -> dict | None:
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": 24.8607, "longitude": 67.0011,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": "precipitation_probability_max",
                    "timezone": "Asia/Karachi", "forecast_days": 1,
                },
                timeout=5.0,
            )
            if r.status_code != 200:
                return None
            data  = r.json()
            c     = data.get("current", {})
            daily = data.get("daily", {})
            code  = int(c.get("weather_code") or 0)
            rain_list = daily.get("precipitation_probability_max") or []
            return {
                "temp_c":      c.get("temperature_2m"),
                "humidity":    c.get("relative_humidity_2m"),
                "wind_kmh":    c.get("wind_speed_10m"),
                "rain_pct":    rain_list[0] if rain_list else None,
                "code":        code,
                "description": _WMO_CODES.get(code, "clear"),
            }

        try:
            async with httpx.AsyncClient() as client:
                result = await _wttr(client)
                if result and result.get("temp_c"):
                    return result
                result2 = await _openmeteo(client)
                if result2 and result2.get("temp_c"):
                    return result2
        except Exception:
            pass
        return {"temp_c": None, "humidity": None, "wind_kmh": None, "rain_pct": None, "code": None, "description": "unavailable"}

    async def _fetch_news() -> list:
        """Fetch latest AI news from HN Algolia (most-recent-first) with Dev.to fallback."""
        import httpx

        async def _hn(client: httpx.AsyncClient) -> list:
            r = await client.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={
                    "query": "AI LLM GPT Claude Gemini Anthropic OpenAI Mistral agent",
                    "tags": "story",
                    "hitsPerPage": 8,
                    "attributesToRetrieve": "title,url,points",
                },
            )
            if r.status_code != 200:
                return []
            hits = r.json().get("hits", [])
            # Filter out empty titles and non-AI stories
            return [
                {"title": h["title"], "url": h.get("url", ""), "points": h.get("points", 0)}
                for h in hits
                if h.get("title") and len(h["title"]) > 20
            ][:6]

        async def _devto(client: httpx.AsyncClient) -> list:
            r = await client.get(
                "https://dev.to/api/articles",
                params={"tag": "ai", "per_page": 5, "top": 1},
            )
            if r.status_code != 200:
                return []
            arts = r.json()
            return [
                {"title": a.get("title", ""), "url": a.get("url", ""), "points": a.get("positive_reactions_count", 0)}
                for a in arts if a.get("title")
            ][:5]

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                hn_results = await _hn(client)
                if hn_results:
                    return hn_results
                # HN returned nothing useful — try Dev.to
                return await _devto(client)
        except Exception:
            pass
        return []

    def _system_metrics() -> dict:
        try:
            import psutil
            GiB = 1024 ** 3
            cpu = psutil.cpu_percent(interval=0.2)
            vm  = psutil.virtual_memory()
            up  = int(_time.time() - psutil.boot_time())
            h, rem = divmod(up, 3600)
            m, s   = divmod(rem, 60)
            processes = len(psutil.pids())
            return {
                "cpu_pct": round(cpu, 1),
                "ram_pct": round(vm.percent, 1),
                "ram_used_gb": round(vm.used / GiB, 1),
                "ram_total_gb": round(vm.total / GiB, 1),
                "uptime_s": up,
                "uptime_str": f"{h}h {m:02d}m {s:02d}s",
                "process_count": processes,
            }
        except Exception:
            return {"cpu_pct": 0, "ram_pct": 0, "ram_used_gb": 0, "ram_total_gb": 16, "uptime_s": 0, "uptime_str": "unknown", "process_count": 0}

    weather, news, system = await _asyncio.gather(
        _fetch_weather(), _fetch_news(), _asyncio.get_event_loop().run_in_executor(None, _system_metrics)
    )

    return {
        "success": True,
        "data": {
            "system": system,
            "weather": weather,
            "news": news,
            "timestamp": _time.time(),
        },
        "message": "ok",
    }
