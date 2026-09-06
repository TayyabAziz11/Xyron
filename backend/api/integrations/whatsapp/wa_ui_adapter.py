"""
wa_ui_adapter.py — Phase 3 visual surface for WhatsApp conversations.

Separation of responsibilities (milestone rule):

    WhatsAppAgent / intent
          │
          ├── message/file action  → BaileysTransport   (never here)
          └── visual/open action   → WhatsAppUIAdapter  (never sends)

The adapter makes WhatsApp visible to the user instead of leaving it a
hidden backend transport. It is deterministic and contact-safe:

  - Contact targeting uses an EXACT E.164 phone via deep link:
      Desktop:  whatsapp://send?phone=<digits>
      Web:      https://web.whatsapp.com/send?phone=<digits>
  - No fuzzy visual matching, no clicks, no typing. The ONLY interaction
    with WhatsApp is a protocol/browser launch — which cannot send a
    message (the composer opens empty because no text= is ever supplied).
  - A target without an exact phone (LID-only chat, group JID) is REFUSED
    rather than guessed — no wrong-contact fallback.

Reuses Xyron's generic desktop layer instead of duplicating automation:
  - launch : api.tools.system_tools.open_url_native — the proven
    `cmd.exe /c start` path that handles registered URI schemes and
    browser URLs alike.
  - focus  : the deep link itself activates the WhatsApp surface; the
    window title used for verification ("WhatsApp") is the same title
    system_tools._APP_FOCUS_TITLE already registers for app focus.
  - window verification: one-shot PowerShell following window_context.py's
    Add-Type GetForegroundWindow/GetWindowText pattern (win32gui is not a
    dependency of this backend).

Fallback policy: WhatsApp Desktop is the preferred target. If it is not
installed, the adapter REPORTS that and — only when the caller explicitly
opted in via allow_web_fallback=True — uses WhatsApp Web in the default
browser. It never switches silently; every UIActionReport states which
target was used.

Intent distinctions (for the future intent layer — deliberately kept OUT
of this adapter):
    "Send this to Ali."                 → Baileys send only (silent)
    "Send this to Ali and show me."     → Baileys send + adapter.open_chat
    "Open Ali's WhatsApp."              → adapter.open_chat only, no send
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("wa_ui_adapter")

# Window title of the WhatsApp surface (Desktop app window, or the browser
# tab when WhatsApp Web is open). Matches system_tools._APP_FOCUS_TITLE.
_WINDOW_TITLE = "WhatsApp"

# Deep links / URLs. phone is always digits-only (E.164 without '+').
_DESKTOP_CHAT_LINK = "whatsapp://send?phone={phone}"
_DESKTOP_APP_LINK = "whatsapp://"
_WEB_CHAT_URL = "https://web.whatsapp.com/send?phone={phone}"
_WEB_APP_URL = "https://web.whatsapp.com/"

# CDP (Chrome DevTools Protocol) for tab reuse in the user's existing Chrome.
# If Chrome was launched with --remote-debugging-port=9222 (or XYRON_CDP_PORT),
# we can enumerate tabs, activate a specific tab, and navigate it — without
# opening a new tab. This is the preferred path for "reuse my existing WhatsApp
# Web session" UX. When CDP is unavailable, we fall back to window activation
# (bring a WhatsApp-titled Chrome window to foreground) or opening a new tab.
_CDP_DEFAULT_PORT = 9222
_CDP_HOST = "127.0.0.1"
_WA_WEB_ORIGIN = "https://web.whatsapp.com"


# ---------------------------------------------------------------------------
# UI-safe contact target
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WhatsAppUITarget:
    """
    UI-safe representation of a WhatsApp contact for visual actions.

    The backend identity is a Baileys JID (``923001234567@s.whatsapp.net``),
    but the Windows WhatsApp UI does not accept JIDs — it targets chats by
    exact phone number. This dataclass carries both identities plus the
    display name, and derives the UI-safe phone from an ``@s.whatsapp.net``
    JID when possible.

    LID chats (``@lid``) and groups (``@g.us``) yield phone=None — callers
    must refuse visual targeting for them (the UI cannot be safely aimed
    without an exact phone; no fuzzy fallback is permitted).
    """

    chat_id: str = ""
    display_name: Optional[str] = None
    phone: Optional[str] = None        # digits only, E.164 without '+'

    @classmethod
    def from_chat_id(
        cls, chat_id: str, display_name: Optional[str] = None
    ) -> "WhatsAppUITarget":
        jid = (chat_id or "").strip()
        phone: Optional[str] = None
        if jid.endswith("@s.whatsapp.net"):
            digits = re.sub(r"\D", "", jid.split("@", 1)[0])
            if 7 <= len(digits) <= 15:
                phone = digits
        return cls(chat_id=jid, display_name=display_name, phone=phone)

    def normalized_phone(self) -> Optional[str]:
        """Digits-only phone, or None when no safe UI targeting key exists."""
        digits = re.sub(r"\D", "", self.phone or "")
        return digits if 7 <= len(digits) <= 15 else None


# ---------------------------------------------------------------------------
# Action report
# ---------------------------------------------------------------------------

@dataclass
class UIActionReport:
    """
    Structured outcome of a visual WhatsApp action.

    ok            : the launch was accepted by the OS (not proof of render).
    ui_target     : "desktop" | "web" | "none" — which surface was used.
    launch_method : "deep_link" (whatsapp://) | "browser_url" | "cdp_tab_reuse" | "none".
    contact_targeting :
        "exact_phone_deep_link" — deterministic phone targeting, no fuzzy
        matching; "app_only" — no contact targeted (open app root); "none".
    verified      : a WhatsApp-titled window was observed afterwards.
    verification_detail : what verification did and did not establish.

    Phase 3 UX correction — tab reuse:
    cdp_available       : CDP endpoint was reachable (Chrome launched with
                          --remote-debugging-port).
    cdp_tab_reused      : an existing WhatsApp Web tab was found and reused
                          (no new tab opened).
    cdp_tab_url_before  : the tab's URL before navigation (if reused).
    cdp_tab_url_after   : the tab's URL after navigation (if navigated).
    cdp_tab_count       : total tabs visible to CDP at time of action.
    window_activated    : Chrome window was brought to foreground via
                          SetForegroundWindow.
    """

    action: str = ""                   # open_whatsapp | open_chat | focus_chat
    ok: bool = False
    ui_target: str = "none"            # desktop | web | none
    launch_method: str = "none"        # deep_link | browser_url | cdp_tab_reuse | none
    contact_targeting: str = "none"    # exact_phone_deep_link | app_only | none
    deep_link: Optional[str] = None
    verified: bool = False
    verification_detail: str = ""
    whatsapp_window_title: Optional[str] = None
    foreground_title: Optional[str] = None
    detail: str = ""
    # Phase 3 UX — tab reuse fields
    cdp_available: bool = False
    cdp_tab_reused: bool = False
    cdp_tab_url_before: Optional[str] = None
    cdp_tab_url_after: Optional[str] = None
    cdp_tab_count: int = 0
    window_activated: bool = False


# ---------------------------------------------------------------------------
# Default probes (overridable for tests)
# ---------------------------------------------------------------------------

def _default_opener(url: str) -> bool:
    """Delegate to Xyron's generic launcher (handles URI schemes + URLs)."""
    from api.tools.system_tools import open_url_native
    return open_url_native(url)


def _default_desktop_available() -> bool:
    """True when WhatsApp Desktop is installed/registered on this machine."""
    # 1. whatsapp:// protocol registration (classic installer registers it).
    try:
        import winreg
        for root in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"Software\Classes\whatsapp"):
                    return True
            except OSError:
                continue
    except ImportError:
        pass
    # 2. Known executable locations (classic per-user / per-machine installs).
    profile = Path(os.environ.get("USERPROFILE", ""))
    candidates = [
        profile / "AppData" / "Local" / "WhatsApp" / "WhatsApp.exe",
        profile / "AppData" / "Local" / "Programs" / "WhatsApp" / "WhatsApp.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WhatsApp" / "WhatsApp.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "WhatsApp" / "WhatsApp.exe",
        profile / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "whatsapp.exe",
    ]
    if any(p.is_file() for p in candidates):
        return True
    # 3. Microsoft Store (Appx) package — one-shot PowerShell query.
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-AppxPackage | Where-Object { $_.Name -like '*WhatsApp*' }"
             " | Measure-Object).Count"],
            capture_output=True, text=True, timeout=20,
        )
        out = (r.stdout or "").strip()
        return r.returncode == 0 and out not in ("", "0")
    except (OSError, subprocess.SubprocessError):
        return False


# One-shot PowerShell probe: finds any window titled *WhatsApp* and reports
# the foreground window title. Single line, no here-string (here-strings are
# fragile over piped interactive PS sessions — see window_context.py). The
# embedded C# quotes are doubled per PowerShell string-escaping rules.
_PROBE_PS = (
    "if (-not ([System.Management.Automation.PSTypeName]'XyronWaUiProbe').Type) { "
    'Add-Type -TypeDefinition "using System;using System.Runtime.InteropServices;'
    "using System.Text;public class XyronWaUiProbe{"
    '[DllImport(""user32.dll"")]public static extern IntPtr GetForegroundWindow();'
    '[DllImport(""user32.dll"",CharSet=CharSet.Unicode)]public static extern int'
    ' GetWindowText(IntPtr h,StringBuilder s,int c);}" '
    "}; "
    "$w = Get-Process | Where-Object { $_.MainWindowTitle -like '*WhatsApp*' }"
    " | Select-Object -First 1; "
    "$waTitle = if ($w) { $w.MainWindowTitle } else { '' }; "
    "$h=[XyronWaUiProbe]::GetForegroundWindow(); "
    "$sb=New-Object System.Text.StringBuilder(256); "
    "[XyronWaUiProbe]::GetWindowText($h,$sb,256)|Out-Null; "
    'Write-Output ("WA_WINDOW:" + $waTitle + "|FOREGROUND:" + $sb.ToString())'
)


def _default_window_probe() -> Dict[str, Optional[str]]:
    """
    Return {"whatsapp_window_title": str|None, "foreground_title": str|None}.

    WhatsApp Web in a browser is covered too: the browser's main window
    title contains the tab title ("WhatsApp Web"/"WhatsApp").
    """
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PROBE_PS],
            capture_output=True, text=True, timeout=8,
        )
        out = (r.stdout or "").strip()
        m = re.search(r"WA_WINDOW:(.*?)\|FOREGROUND:(.*)", out, re.DOTALL)
        if not m:
            return {"whatsapp_window_title": None, "foreground_title": None}
        wa_title = m.group(1).strip() or None
        fg_title = m.group(2).strip() or None
        return {"whatsapp_window_title": wa_title, "foreground_title": fg_title}
    except (OSError, subprocess.SubprocessError):
        return {"whatsapp_window_title": None, "foreground_title": None}


# ---------------------------------------------------------------------------
# CDP tab reuse — Phase 3 UX correction
# ---------------------------------------------------------------------------
# When Chrome is launched with --remote-debugging-port=9222, we can enumerate
# its tabs, activate a specific tab, and navigate it — without opening a new
# tab. This is the preferred path for "reuse my existing WhatsApp Web session"
# UX. The helpers below are lightweight (urllib for discovery/activation,
# Playwright sync for navigation) and never throw — failures fall through.

def _cdp_port_from_env() -> int:
    """CDP port: XYRON_CDP_PORT env var, else default 9222."""
    return int(os.environ.get("XYRON_CDP_PORT", str(_CDP_DEFAULT_PORT)))


def _cdp_list_tabs(port: int) -> List[Dict]:
    """Fetch Chrome tab list via CDP /json endpoint. Returns [] on failure."""
    try:
        url = f"http://{_CDP_HOST}:{port}/json"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("[WA_UI_CDP] list_tabs failed port=%d: %r", port, exc)
        return []


def _cdp_find_whatsapp_tab(tabs: List[Dict]) -> Optional[Dict]:
    """Find a WhatsApp Web tab in the CDP tab list. Returns tab dict or None.
    Identifies by URL origin (https://web.whatsapp.com), not title."""
    for tab in tabs:
        url = tab.get("url", "")
        if url.startswith(_WA_WEB_ORIGIN):
            return tab
    return None


def _cdp_activate_tab(tab_id: str, port: int) -> bool:
    """Activate a Chrome tab via CDP /json/activate/{id}. Returns True on success.
    This brings the tab to the front within Chrome (Chrome handles the UI switch)."""
    try:
        url = f"http://{_CDP_HOST}:{port}/json/activate/{tab_id}"
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception as exc:
        logger.debug("[WA_UI_CDP] activate_tab failed id=%s: %r", tab_id[:16], exc)
        return False


def _cdp_navigate_tab(target_url: str, port: int) -> bool:
    """Navigate the active WhatsApp Web tab to target_url via Playwright CDP.
    Returns True on success. Does NOT open a new tab — navigates the existing one.

    Uses Playwright sync API (already in Xyron's deps) for clean page.goto().
    """
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        try:
            endpoint = f"http://{_CDP_HOST}:{port}"
            browser = pw.chromium.connect_over_cdp(endpoint, timeout=8000)
            context = browser.contexts[0] if browser.contexts else None
            if not context:
                logger.debug("[WA_UI_CDP] no browser context after connect")
                return False
            # Find the WhatsApp Web page and navigate it
            for page in context.pages:
                if not page.is_closed() and page.url.startswith(_WA_WEB_ORIGIN):
                    page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    logger.info("[WA_UI_CDP] navigated page to %s", target_url)
                    return True
            logger.debug("[WA_UI_CDP] no WhatsApp Web page found in context")
            return False
        finally:
            pw.stop()
    except Exception as exc:
        logger.debug("[WA_UI_CDP] navigate_tab failed: %r", exc)
        return False


def _activate_chrome_window_with_whatsapp() -> bool:
    """Find a Chrome window with 'WhatsApp' in the title and bring it to
    foreground via SetForegroundWindow (reuses system_tools._FOCUS_SHIM_CS
    pattern — the proven AttachThreadInput workaround for Windows focus lock).

    Returns True if a window was activated.
    """
    try:
        from api.tools.system_tools import _ps, _FOCUS_SHIM_CS
        ps_cmd = (
            "if (-not ([System.Management.Automation.PSTypeName]'XyronFG').Type) { "
            f"Add-Type -TypeDefinition '{_FOCUS_SHIM_CS}' "
            "}; "
            "$proc = Get-Process -Name chrome -ErrorAction SilentlyContinue | "
            "Where-Object { $_.MainWindowTitle -like '*WhatsApp*' } | "
            "Select-Object -First 1; "
            "if ($proc) { "
            "  $target = $proc.MainWindowHandle; "
            "  $curTid = [XyronFG]::GetCurrentThreadId(); "
            "  $fgWin  = [XyronFG]::GetForegroundWindow(); "
            "  $dummy  = 0; "
            "  $fgTid  = [XyronFG]::GetWindowThreadProcessId($fgWin, [ref]$dummy); "
            "  [XyronFG]::AttachThreadInput($curTid, $fgTid, $true)  | Out-Null; "
            "  [XyronFG]::ShowWindow($target, 9) | Out-Null; "
            "  [XyronFG]::SetForegroundWindow($target) | Out-Null; "
            "  [XyronFG]::BringWindowToTop($target) | Out-Null; "
            "  [XyronFG]::AttachThreadInput($curTid, $fgTid, $false) | Out-Null; "
            "  Write-Output 'ACTIVATED' "
            "} else { Write-Output 'NOT_FOUND' }"
        )
        ok, out = _ps(ps_cmd, timeout=8)
        return ok and "ACTIVATED" in (out or "")
    except Exception as exc:
        logger.debug("[WA_UI] activate_chrome_window failed: %r", exc)
        return False


# ── Keyboard cycling constants ──────────────────────────────────────────────────
_KEYBOARD_MAX_TAB_CYCLES = 30          # Conservative max to avoid endless cycling
_KEYBOARD_POLL_TIMEOUT_MS = 1800       # Poll title for up to 1.8s per keystroke
_KEYBOARD_POLL_INTERVAL_MS = 200       # Check title every 200ms
_KEYBOARD_FOREGROUND_VERIFY_RETRIES = 3  # Retries to restore foreground before aborting

# Strong WhatsApp title patterns (avoid loose substring matches)
# Chrome puts notification counts at the beginning: "(1) WhatsApp - Google Chrome"
# or at the end: "WhatsApp (1) - Google Chrome"
_WHATSAPP_TITLE_PATTERNS = [
    r"^WhatsApp$",
    r"^\(\d+\)\s*WhatsApp",        # (1) WhatsApp, (3) WhatsApp - ...
    r"^WhatsApp\s*\(\d+\)",        # WhatsApp (1), WhatsApp (2)
    r"^WhatsApp Web$",
    r"^\(\d+\)\s*WhatsApp Web",    # (1) WhatsApp Web
    r"^WhatsApp Web\s*\(\d+\)",    # WhatsApp Web (1)
    r"^WhatsApp\s*[-–—].+$",      # WhatsApp - Contact Name, WhatsApp – Chat
    r"^\(\d+\)\s*WhatsApp\s*[-–—].+$",  # (1) WhatsApp - Contact
]


def _is_whatsapp_title(title: str) -> bool:
    """Check if a window title strongly indicates a WhatsApp Web tab.

    Uses strict patterns to avoid false positives from articles or searches
    about WhatsApp. Returns True only for titles that match known WhatsApp
    Web tab title formats.
    """
    if not title:
        return False
    title = title.strip()
    for pattern in _WHATSAPP_TITLE_PATTERNS:
        if re.match(pattern, title, re.IGNORECASE):
            return True
    return False


def _enumerate_chrome_windows() -> List[Dict]:
    """Enumerate all top-level Chrome browser windows using Win32 EnumWindows.

    Does NOT rely on Process.MainWindowHandle (insufficient for Chrome's
    multiprocess model). Instead:
    1. EnumWindows → all top-level windows
    2. IsWindowVisible → filter visible windows
    3. GetWindowThreadProcessId → owning PID
    4. Get process name → filter chrome.exe
    5. GetClassName → Chrome widget class (Chrome_WidgetWin_1, etc.)

    Returns list of dicts:
        [{"hwnd": int, "pid": int, "process_name": str, "class_name": str,
          "title": str, "visible": bool, "minimized": bool}, ...]
    """
    try:
        # Use subprocess directly for EnumWindows (bypasses persistent session
        # which has issues with multi-line here-strings and C# delegates)
        import subprocess
        import tempfile
        
        # Write the PowerShell script to a temp file
        ps_script = r"""
if (-not ([System.Management.Automation.PSTypeName]'XyronChromeEnum').Type) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;

public class XyronChromeEnum {
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);
    
    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hWnd);
    
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);
    
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextLength(IntPtr hWnd);
    
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    
    public static List<ChromeWindowInfo> FindChromeWindows() {
        var result = new List<ChromeWindowInfo>();
        EnumWindows((hwnd, lParam) => {
            if (!IsWindowVisible(hwnd)) return true;
            
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            
            try {
                var proc = System.Diagnostics.Process.GetProcessById((int)pid);
                if (proc.ProcessName != "chrome") return true;
            } catch { return true; }
            
            var classBuf = new StringBuilder(256);
            GetClassName(hwnd, classBuf, 256);
            var className = classBuf.ToString();
            
            if (!className.StartsWith("Chrome_WidgetWin")) return true;
            
            var titleLen = GetWindowTextLength(hwnd);
            var titleBuf = new StringBuilder(titleLen + 1);
            GetWindowText(hwnd, titleBuf, titleLen + 1);
            var title = titleBuf.ToString();
            
            var isMinimized = IsIconic(hwnd);
            
            result.Add(new ChromeWindowInfo {
                Hwnd = hwnd.ToInt64(),
                Pid = pid,
                ProcessName = "chrome",
                ClassName = className,
                Title = title,
                Visible = true,
                Minimized = isMinimized
            });
            
            return true;
        }, IntPtr.Zero);
        
        return result;
    }
}

public class ChromeWindowInfo {
    public long Hwnd { get; set; }
    public uint Pid { get; set; }
    public string ProcessName { get; set; }
    public string ClassName { get; set; }
    public string Title { get; set; }
    public bool Visible { get; set; }
    public bool Minimized { get; set; }
}
"@
}

$windows = [XyronChromeEnum]::FindChromeWindows()
$windows | ConvertTo-Json -Compress
"""
        
        # Write to temp file and execute. The file must land under
        # /mnt/c/Windows/Temp (not the WSL-only default /tmp) — native
        # powershell.exe -File cannot resolve a bare WSL path like
        # /tmp/tmpXXXX.ps1, so it silently no-ops (prints an error to
        # stderr but still exits 0), and every caller of this function saw
        # an empty window list regardless of what Chrome actually had open.
        # Same convention already used by system_tools._recycle_delete.
        import os
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.ps1', delete=False, encoding='utf-8',
            dir="/mnt/c/Windows/Temp",
        ) as f:
            f.write(ps_script)
            temp_path = f.name
        win_script_path = "C:\\Windows\\Temp\\" + os.path.basename(temp_path)

        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win_script_path],
                capture_output=True, text=True, timeout=15
            )
            out = result.stdout.strip()
            ok = result.returncode == 0
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        
        if not ok or not out:
            logger.debug("[WA_UI_KEYBOARD] EnumWindows failed or returned empty")
            return []

        out = (out or "").strip()
        # PowerShell may return single object or array
        if out.startswith("{") and not out.startswith("["):
            out = "[" + out + "]"
        
        import json as _json
        try:
            windows = _json.loads(out)
            if not isinstance(windows, list):
                return []
            # Normalize keys to lowercase (C# returns PascalCase)
            normalized = []
            for win in windows:
                normalized.append({
                    "hwnd": win.get("Hwnd") or win.get("hwnd", 0),
                    "pid": win.get("Pid") or win.get("pid", 0),
                    "process_name": win.get("ProcessName") or win.get("process_name", "chrome"),
                    "class_name": win.get("ClassName") or win.get("class_name", ""),
                    "title": win.get("Title") or win.get("title", ""),
                    "visible": win.get("Visible") if "Visible" in win else win.get("visible", True),
                    "minimized": win.get("Minimized") if "Minimized" in win else win.get("minimized", False),
                })
            return normalized
        except Exception as exc:
            logger.debug("[WA_UI_KEYBOARD] JSON parse failed: %r", exc)
            return []

    except Exception as exc:
        logger.debug("[WA_UI_KEYBOARD] _enumerate_chrome_windows failed: %r", exc)
        return []


def _keyboard_find_whatsapp_tab(
    max_tabs: int = _KEYBOARD_MAX_TAB_CYCLES,
    diagnostic_only: bool = False,
) -> Optional[Dict]:
    """Find and activate a WhatsApp Web tab in Chrome via keyboard cycling.

    Uses Win32 EnumWindows to discover ALL top-level Chrome windows (not just
    Process.MainWindowHandle), then cycles through tabs in each window
    independently using Ctrl+Tab.

    Args:
        max_tabs: Maximum tab cycles per window (default 30, conservative).
        diagnostic_only: If True, enumerate windows but do NOT send keystrokes.
                         Returns diagnostic report only.

    Returns:
        Success: {"method": "keyboard_cycling", "chrome_pid": int,
                  "hwnd": int, "tabs_cycled": int, "final_title": str,
                  "windows_enumerated": int, "distinct_states": int}
        Not found: {"method": "keyboard_cycling", "found": False,
                    "windows_enumerated": int, "windows_probed": int,
                    "windows_report": [...]}
        Diagnostic: {"method": "diagnostic", "windows": [...]}

    Key improvements over previous implementation:
    - EnumWindows (not MainWindowHandle) → finds ALL Chrome windows
    - HWND-based activation (not process-based) → precise targeting
    - Title polling (up to 1.8s) → handles slow title updates
    - Distinct state tracking → detects wrap-around reliably
    - Foreground verification before each keystroke → safety
    - Original tab restoration on failure → preserve user state
    - Strong WhatsApp title matching → avoids false positives
    """
    # Step 1: Enumerate all Chrome windows
    windows = _enumerate_chrome_windows()
    logger.debug("[WA_UI_KEYBOARD] EnumWindows found %d Chrome windows", len(windows))

    if diagnostic_only:
        return {
            "method": "diagnostic",
            "windows": windows,
            "windows_enumerated": len(windows),
        }

    if not windows:
        logger.debug("[WA_UI_KEYBOARD] No Chrome windows found via EnumWindows")
        return None

    try:
        # Step 2: For each window, probe independently
        windows_probed = 0
        windows_report = []

        for win in windows:
            hwnd = int(win.get("hwnd", 0))
            pid = int(win.get("pid", 0))
            initial_title = win.get("title", "")
            class_name = win.get("class_name", "")

            # Check if WhatsApp already active in this window
            if _is_whatsapp_title(initial_title):
                logger.info(
                    "[WA_UI_KEYBOARD] WhatsApp already active in window "
                    "hwnd=0x%X pid=%d title=%r",
                    hwnd, pid, initial_title,
                )
                # Bring this exact window to foreground
                _activate_hwnd(hwnd)
                return {
                    "method": "keyboard_cycling",
                    "found": True,
                    "chrome_pid": pid,
                    "hwnd": hwnd,
                    "tabs_cycled": 0,
                    "final_title": initial_title,
                    "windows_enumerated": len(windows),
                    "distinct_states": 1,
                }

            # Probe this window: cycle tabs and look for WhatsApp
            windows_probed += 1
            probe_result = _probe_chrome_window_tabs(
                hwnd=hwnd,
                pid=pid,
                initial_title=initial_title,
                max_tabs=max_tabs,
            )

            window_report = {
                "hwnd": hwnd,
                "pid": pid,
                "class_name": class_name,
                "initial_title": initial_title,
                "title_changed": probe_result.get("title_changed", False),
                "distinct_states": probe_result.get("distinct_states", 1),
                "tabs_cycled": probe_result.get("tabs_cycled", 0),
                "whatsapp_found": probe_result.get("found", False),
                "final_title": probe_result.get("final_title", initial_title),
                "restored_original": probe_result.get("restored_original", False),
                "cycle_status": probe_result.get("cycle_status", "unknown"),
            }
            windows_report.append(window_report)

            if probe_result.get("found"):
                logger.info(
                    "[WA_UI_KEYBOARD] WhatsApp found in window hwnd=0x%X "
                    "after %d cycles, title=%r",
                    hwnd, probe_result.get("tabs_cycled"),
                    probe_result.get("final_title"),
                )
                return {
                    "method": "keyboard_cycling",
                    "found": True,
                    "chrome_pid": pid,
                    "hwnd": hwnd,
                    "tabs_cycled": probe_result.get("tabs_cycled"),
                    "final_title": probe_result.get("final_title"),
                    "windows_enumerated": len(windows),
                    "windows_probed": windows_probed,
                    "distinct_states": probe_result.get("distinct_states"),
                }

        # Step 3: Not found in any window
        logger.debug(
            "[WA_UI_KEYBOARD] WhatsApp not found in %d probed windows",
            windows_probed,
        )
        return {
            "method": "keyboard_cycling",
            "found": False,
            "windows_enumerated": len(windows),
            "windows_probed": windows_probed,
            "windows_report": windows_report,
        }

    except Exception as exc:
        logger.debug("[WA_UI_KEYBOARD] keyboard cycling failed: %r", exc)
        return None


def _run_ps_fresh(script: str, timeout: int = 10) -> tuple:
    """Run a PowerShell script in a fresh process (not persistent session).
    
    The persistent PowerShell session crashes when using Add-Type with
    complex C# code (especially with delegates/callbacks). This function
    uses subprocess.run with a temp file to run PowerShell scripts in
    a fresh process each time.
    
    Returns (success: bool, output: str).
    """
    import subprocess
    import tempfile
    import os

    # Same fix as _enumerate_chrome_windows: the script must be written
    # under /mnt/c/Windows/Temp (Windows-visible) rather than the WSL-only
    # default /tmp — native powershell.exe -File cannot resolve a bare WSL
    # path, so every one of this function's 7 call sites (activation,
    # foreground verification, SendKeys tab cycling, omnibox navigation,
    # tab restoration) was silently no-oping under WSL2.
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.ps1', delete=False, encoding='utf-8',
        dir="/mnt/c/Windows/Temp",
    ) as f:
        f.write(script)
        temp_path = f.name
    win_script_path = "C:\\Windows\\Temp\\" + os.path.basename(temp_path)

    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win_script_path],
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception as exc:
        logger.debug("[WA_UI_PS_FRESH] subprocess failed: %r", exc)
        return False, ""
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass


def _activate_hwnd(hwnd: int) -> bool:
    """Bring a specific window to foreground using its HWND.

    Uses AttachThreadInput + SetForegroundWindow + BringWindowToTop + ShowWindow.
    Returns True if the window became the foreground window.
    
    Uses fresh PowerShell process (not persistent session) because the
    persistent session crashes when using Add-Type with complex C# code.
    """
    ps_script = f"""
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class XyronFG {{
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
[DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
[DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@

$hwnd = [IntPtr]{hwnd}
$curTid = [XyronFG]::GetCurrentThreadId()
$fgWin  = [XyronFG]::GetForegroundWindow()
$dummy  = 0
$fgTid  = [XyronFG]::GetWindowThreadProcessId($fgWin, [ref]$dummy)
[XyronFG]::AttachThreadInput($curTid, $fgTid, $true)  | Out-Null
[XyronFG]::ShowWindow($hwnd, 9) | Out-Null
[XyronFG]::SetForegroundWindow($hwnd) | Out-Null
[XyronFG]::BringWindowToTop($hwnd) | Out-Null
[XyronFG]::AttachThreadInput($curTid, $fgTid, $false) | Out-Null
Start-Sleep -Milliseconds 300
$newFg = [XyronFG]::GetForegroundWindow()
if ($newFg -eq $hwnd) {{ Write-Output 'OK' }} else {{ Write-Output 'FAIL' }}
"""
    ok, out = _run_ps_fresh(ps_script, timeout=5)
    return ok and out == "OK"


def _verify_foreground_is_hwnd(hwnd: int) -> bool:
    """Check if the current foreground window matches the expected HWND."""
    ps_script = f"""
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class XyronFG {{
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}}
"@

$fg = [XyronFG]::GetForegroundWindow()
if ($fg -eq [IntPtr]{hwnd}) {{ Write-Output 'YES' }} else {{ Write-Output 'NO' }}
"""
    ok, out = _run_ps_fresh(ps_script, timeout=3)
    return ok and out == "YES"


def _get_window_title(hwnd: int) -> str:
    """Get the current title of a window by HWND."""
    ps_script = f"""
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class XyronFG {{
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextLength(IntPtr hWnd);
}}
"@

$hwnd = [IntPtr]{hwnd}
$titleLen = [XyronFG]::GetWindowTextLength($hwnd)
if ($titleLen -eq 0) {{ Write-Output ''; exit }}
$titleBuf = New-Object System.Text.StringBuilder ($titleLen + 1)
[XyronFG]::GetWindowText($hwnd, $titleBuf, $titleLen + 1) | Out-Null
Write-Output $titleBuf.ToString()
"""
    ok, out = _run_ps_fresh(ps_script, timeout=3)
    return out if ok else ""


def _probe_chrome_window_tabs(
    hwnd: int,
    pid: int,
    initial_title: str,
    max_tabs: int,
) -> Dict:
    """Probe a single Chrome window for WhatsApp tabs via Ctrl+Tab cycling.

    1. Activate the window by HWND
    2. Record original title
    3. For each cycle (up to max_tabs):
       a. Verify foreground is still target HWND
       b. Send Ctrl+Tab
       c. Poll title for up to POLL_TIMEOUT_MS
       d. Track distinct states
       e. If WhatsApp title detected → STOP, leave tab selected
       f. If original title returns after ≥1 different tab → wrapped, STOP
    4. If not found: restore original tab (Ctrl+Shift+Tab × cycles)

    Returns:
        {"found": bool, "tabs_cycled": int, "distinct_states": int,
         "title_changed": bool, "final_title": str, "restored_original": bool,
         "cycle_status": str}

    cycle_status values:
        not_attempted_activation_failed  - could not bring window to foreground
        attempted_no_title_change        - Ctrl+Tab sent but title never changed
        wrapped                          - cycled through all tabs, returned to start
        whatsapp_found                   - WhatsApp tab found and selected
        max_cycles_reached               - hit max_tabs without finding WhatsApp
    """

    # Activate this specific window
    if not _activate_hwnd(hwnd):
        logger.debug("[WA_UI_KEYBOARD] Failed to activate hwnd=0x%X", hwnd)
        return {"found": False, "tabs_cycled": 0, "distinct_states": 0,
                "title_changed": False, "final_title": initial_title,
                "restored_original": False,
                "cycle_status": "not_attempted_activation_failed"}

    # Verify foreground
    if not _verify_foreground_is_hwnd(hwnd):
        logger.debug("[WA_UI_KEYBOARD] Foreground not target after activation")
        return {"found": False, "tabs_cycled": 0, "distinct_states": 0,
                "title_changed": False, "final_title": initial_title,
                "restored_original": False,
                "cycle_status": "not_attempted_activation_failed"}

    # Track state
    distinct_titles = {initial_title}
    tabs_cycled = 0
    title_changed = False
    final_title = initial_title

    # Cycle through tabs
    for cycle in range(1, max_tabs + 1):
        # Verify foreground before keystroke
        if not _verify_foreground_is_hwnd(hwnd):
            # Try to restore foreground
            for retry in range(_KEYBOARD_FOREGROUND_VERIFY_RETRIES):
                _activate_hwnd(hwnd)
                # `time` is already imported at module level (line 53) — a
                # local `import time` here previously shadowed it for this
                # entire function, making every unconditional time.monotonic()/
                # time.sleep() call later in the loop raise UnboundLocalError
                # on any pass that didn't hit this retry branch first.
                time.sleep(0.1)
                if _verify_foreground_is_hwnd(hwnd):
                    break
            else:
                logger.debug(
                    "[WA_UI_KEYBOARD] Lost foreground at cycle %d, aborting",
                    cycle,
                )
                return {"found": False, "tabs_cycled": tabs_cycled,
                        "distinct_states": len(distinct_titles),
                        "title_changed": title_changed, "final_title": final_title,
                        "restored_original": False,
                        "cycle_status": "not_attempted_activation_failed"}

        # Send Ctrl+Tab via fresh PowerShell process
        ps_sendkeys = (
            "$wshell = New-Object -COM WScript.Shell; "
            "$wshell.SendKeys('^{TAB}')"
        )
        _run_ps_fresh(ps_sendkeys, timeout=3)
        tabs_cycled += 1

        # Poll for title change (up to POLL_TIMEOUT_MS)
        poll_start = time.monotonic()
        poll_timeout_s = _KEYBOARD_POLL_TIMEOUT_MS / 1000.0
        poll_interval_s = _KEYBOARD_POLL_INTERVAL_MS / 1000.0
        new_title = initial_title

        while (time.monotonic() - poll_start) < poll_timeout_s:
            time.sleep(poll_interval_s)
            new_title = _get_window_title(hwnd)
            if new_title != final_title:
                break

        # Check for WhatsApp
        if _is_whatsapp_title(new_title):
            logger.debug(
                "[WA_UI_KEYBOARD] WhatsApp found at cycle %d, title=%r",
                cycle, new_title,
            )
            return {"found": True, "tabs_cycled": tabs_cycled,
                    "distinct_states": len(distinct_titles) + 1,
                    "title_changed": True, "final_title": new_title,
                    "restored_original": False,
                    "cycle_status": "whatsapp_found"}

        # Track distinct states
        if new_title != final_title:
            title_changed = True
            distinct_titles.add(new_title)
            final_title = new_title

        # Check for wrap-around (original title returns after seeing different tabs)
        if title_changed and new_title == initial_title:
            logger.debug(
                "[WA_UI_KEYBOARD] Wrapped at cycle %d (saw %d distinct titles)",
                cycle, len(distinct_titles),
            )
            # We're back at original tab, no need to restore
            return {"found": False, "tabs_cycled": tabs_cycled,
                    "distinct_states": len(distinct_titles),
                    "title_changed": True, "final_title": new_title,
                    "restored_original": True,
                    "cycle_status": "wrapped"}

    # Not found after max cycles — try to restore original tab
    if title_changed:
        restored = _restore_original_tab(hwnd, tabs_cycled)
        return {"found": False, "tabs_cycled": tabs_cycled,
                "distinct_states": len(distinct_titles),
                "title_changed": title_changed, "final_title": final_title,
                "restored_original": restored,
                "cycle_status": "max_cycles_reached"}
    else:
        # Title never changed — may mean single tab or SendKeys not reaching Chrome
        return {"found": False, "tabs_cycled": tabs_cycled,
                "distinct_states": len(distinct_titles),
                "title_changed": False, "final_title": final_title,
                "restored_original": True,
                "cycle_status": "attempted_no_title_change"}


def _restore_original_tab(hwnd: int, cycles: int) -> bool:
    """Attempt to restore the original tab by sending Ctrl+Shift+Tab.

    Sends cycles × Ctrl+Shift+Tab to go back to the starting tab.
    Returns True if restoration appears successful (title matches expected).
    """
    if cycles == 0:
        return True

    try:
        # Send Ctrl+Shift+Tab (reverse cycle) × cycles via fresh process
        ps_cmd = (
            "$wshell = New-Object -COM WScript.Shell; "
            f"for ($i = 0; $i -lt {cycles}; $i++) {{ "
            "  $wshell.SendKeys('^+{TAB}'); "
            "  Start-Sleep -Milliseconds 100 "
            "}"
        )
        _run_ps_fresh(ps_cmd, timeout=10)
        time.sleep(0.3)
        return True
    except Exception as exc:
        logger.debug("[WA_UI_KEYBOARD] _restore_original_tab failed: %r", exc)
        return False


def _navigate_active_tab_omnibox(
    target_url: str,
    target_hwnd: int,
    target_pid: int,
) -> Dict:
    """Navigate the active Chrome tab using the omnibox (Ctrl+L + URL + Enter).

    This is used when CDP is unavailable but we've already found and activated
    the WhatsApp tab via keyboard cycling. It navigates THAT SAME TAB without
    opening a new one.

    Safety guards (all must pass before issuing keystrokes):
    1. Target HWND is still valid (process still running)
    2. Target HWND belongs to chrome.exe
    3. Target Chrome window is foreground (GetForegroundWindow == target_hwnd)
    4. Active tab title strongly identifies WhatsApp

    Args:
        target_url: The URL to navigate to (e.g., https://web.whatsapp.com/send?phone=...)
        target_hwnd: The HWND of the Chrome window containing the WhatsApp tab
        target_pid: The PID of the Chrome process owning that window

    Returns:
        {"ok": bool, "detail": str, "title_before": str, "title_after": str,
         "guards_passed": bool, "guard_failures": [str]}
    """
    import subprocess

    result = {
        "ok": False,
        "detail": "",
        "title_before": "",
        "title_after": "",
        "guards_passed": False,
        "guard_failures": [],
    }

    # ── Guard 1: Target HWND still valid ────────────────────────────────────
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {target_pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        if target_pid not in [int(line.split(",")[1].strip('"')) 
                               for line in proc.stdout.strip().split("\n") 
                               if line.strip() and "chrome" in line.lower()]:
            # Simpler check: just verify the process exists
            pass
    except Exception:
        pass

    # ── Guard 2: Target HWND belongs to Chrome ──────────────────────────────
    # Re-enumerate Chrome windows to verify the HWND is still Chrome
    chrome_windows = _enumerate_chrome_windows()
    target_win = None
    for win in chrome_windows:
        if win.get("hwnd") == target_hwnd:
            target_win = win
            break

    if not target_win:
        result["guard_failures"].append(
            f"HWND 0x{target_hwnd:X} no longer found in Chrome windows"
        )
        result["detail"] = "Guard failed: target HWND not found in Chrome windows"
        return result

    # ── Guard 3: Target Chrome window is foreground ─────────────────────────
    if not _verify_foreground_is_hwnd(target_hwnd):
        # Try to restore foreground
        _activate_hwnd(target_hwnd)
        time.sleep(0.3)
        if not _verify_foreground_is_hwnd(target_hwnd):
            result["guard_failures"].append(
                f"Foreground is not HWND 0x{target_hwnd:X}"
            )
            result["detail"] = "Guard failed: could not establish foreground"
            return result

    # ── Guard 4: Active tab title strongly identifies WhatsApp ──────────────
    title_before = _get_window_title(target_hwnd)
    result["title_before"] = title_before

    if not _is_whatsapp_title(title_before):
        result["guard_failures"].append(
            f"Title {title_before!r} does not match WhatsApp patterns"
        )
        result["detail"] = (
            f"Guard failed: active tab title {title_before!r} "
            f"does not strongly indicate WhatsApp"
        )
        return result

    # ── All guards passed ───────────────────────────────────────────────────
    result["guards_passed"] = True
    logger.info(
        "[WA_UI_OMNIBOX] All guards passed. Navigating to %s",
        target_url,
    )

    # ── Navigate using omnibox ──────────────────────────────────────────────
    # Strategy:
    # 1. Ctrl+L → focus omnibox (selects all text)
    # 2. Type the URL (replaces selected text)
    # 3. Enter → navigate
    # 4. Wait for title to update
    try:
        # Ctrl+L to focus omnibox
        ps_cmd = (
            "$wshell = New-Object -COM WScript.Shell; "
            "$wshell.SendKeys('^l'); "
            "Start-Sleep -Milliseconds 300"
        )
        _run_ps_fresh(ps_cmd, timeout=5)
        time.sleep(0.3)

        # Verify foreground is still target (safety check after Ctrl+L)
        if not _verify_foreground_is_hwnd(target_hwnd):
            result["detail"] = (
                "Lost foreground after Ctrl+L; aborting navigation"
            )
            return result

        # Type the URL (SendKeys requires escaping special characters)
        # Use clipboard to avoid SendKeys escaping issues
        ps_cmd = (
            f"$url = '{target_url}'; "
            "Set-Clipboard -Value $url; "
            "$wshell = New-Object -COM WScript.Shell; "
            "$wshell.SendKeys('^v'); "
            "Start-Sleep -Milliseconds 200; "
            "$wshell.SendKeys('{ENTER}')"
        )
        _run_ps_fresh(ps_cmd, timeout=5)

        # Wait for navigation to complete (title should change)
        # WhatsApp Web URLs typically result in titles like:
        # "WhatsApp - Contact Name" or "WhatsApp"
        time.sleep(2.0)

        # Poll for title update
        title_after = ""
        for _ in range(5):
            title_after = _get_window_title(target_hwnd)
            if title_after != title_before:
                break
            time.sleep(0.5)

        result["title_after"] = title_after

        # Verify the window is still WhatsApp (title may have changed to show contact)
        if _is_whatsapp_title(title_after):
            result["ok"] = True
            result["detail"] = (
                f"Navigated active tab to {target_url} via omnibox. "
                f"Title changed from {title_before!r} to {title_after!r}."
            )
        else:
            result["detail"] = (
                f"Navigation attempted but title changed to {title_after!r} "
                f"(does not match WhatsApp patterns). "
                f"URL: {target_url}"
            )

        return result

    except Exception as exc:
        result["detail"] = f"Omnibox navigation failed: {exc!r}"
        return result


class WhatsAppUIAdapter:
    """
    Visual surface adapter for WhatsApp (Desktop preferred, Web fallback).

    All methods are non-throwing and return a structured UIActionReport.
    This adapter NEVER sends messages — message/file actions belong to
    BaileysTransport.
    """

    def __init__(
        self,
        opener: Optional[Callable[[str], bool]] = None,
        window_probe: Optional[Callable[[], Dict[str, Optional[str]]]] = None,
        desktop_probe: Optional[Callable[[], bool]] = None,
        allow_web_fallback: bool = False,
        # Phase 3 UX — CDP tab reuse (injectable for tests)
        cdp_port: Optional[int] = None,
        cdp_list_tabs_fn: Optional[Callable[[int], List[Dict]]] = None,
        cdp_activate_tab_fn: Optional[Callable[[str, int], bool]] = None,
        cdp_navigate_tab_fn: Optional[Callable[[str, int], bool]] = None,
        activate_window_fn: Optional[Callable[[], bool]] = None,
        keyboard_find_tab_fn: Optional[Callable[[], Optional[Dict]]] = None,
        navigate_omnibox_fn: Optional[Callable[[str, int, int], Dict]] = None,
        enumerate_chrome_fn: Optional[Callable[[], List[Dict]]] = None,
    ) -> None:
        # opener launches a URL/URI; default delegates to Xyron's generic
        # system_tools.open_url_native (reused, not duplicated).
        self._opener = opener or _default_opener
        # window_probe reports visible window titles; default is the
        # PowerShell probe modeled on window_context.py.
        self._window_probe = window_probe or _default_window_probe
        # desktop_probe reports whether WhatsApp Desktop is installed.
        self._desktop_probe = desktop_probe or _default_desktop_available
        # Web fallback is opt-in — never a silent switch.
        self._allow_web = allow_web_fallback
        self._desktop_cache: Optional[bool] = None
        # Phase 3 UX — CDP tab reuse + keyboard cycling fallback
        self._cdp_port = cdp_port if cdp_port is not None else _cdp_port_from_env()
        self._cdp_list_tabs = cdp_list_tabs_fn or _cdp_list_tabs
        self._cdp_activate_tab = cdp_activate_tab_fn or _cdp_activate_tab
        self._cdp_navigate_tab = cdp_navigate_tab_fn or _cdp_navigate_tab
        self._activate_window = activate_window_fn or _activate_chrome_window_with_whatsapp
        self._keyboard_find_tab = keyboard_find_tab_fn or _keyboard_find_whatsapp_tab
        self._navigate_omnibox = navigate_omnibox_fn or _navigate_active_tab_omnibox
        self._enumerate_chrome = enumerate_chrome_fn or _enumerate_chrome_windows

    # ── detection ──────────────────────────────────────────────────────────

    def desktop_available(self) -> bool:
        """Whether WhatsApp Desktop is installed (memoized per adapter)."""
        if self._desktop_cache is None:
            try:
                self._desktop_cache = bool(self._desktop_probe())
            except Exception as e:                     # pragma: no cover
                logger.warning("[WA_UI] desktop probe failed: %s", e)
                self._desktop_cache = False
        return self._desktop_cache

    # ── public actions ─────────────────────────────────────────────────────

    def open_whatsapp(self, verify_timeout_s: float = 10.0) -> UIActionReport:
        """Open/focus the WhatsApp app root (no specific chat targeted).

        Phase 3 UX correction: when web fallback is allowed, try CDP tab reuse
        first (activate existing WhatsApp Web tab without opening a new one),
        then window activation, then new tab as last resort.
        """
        if self.desktop_available():
            url, target, method = _DESKTOP_APP_LINK, "desktop", "deep_link"
            return self._launch(
                action="open_whatsapp", url=url, ui_target=target,
                launch_method=method, contact_targeting="app_only",
                verify_timeout_s=verify_timeout_s,
                success_detail="WhatsApp app surface requested (no chat targeted)",
            )

        if not self._allow_web:
            return UIActionReport(
                action="open_whatsapp", ok=False, ui_target="none",
                launch_method="none", contact_targeting="app_only",
                detail="WhatsApp Desktop is not installed and the WhatsApp "
                       "Web fallback is not enabled for this adapter",
            )

        # Web fallback with tab reuse preference
        return self._open_web_surface(
            action="open_whatsapp",
            target_url=None,  # no navigation — just activate existing tab
            contact_targeting="app_only",
            verify_timeout_s=verify_timeout_s,
            success_detail_base="WhatsApp Web surface requested (no chat targeted)",
        )

    def open_chat(
        self, target: WhatsAppUITarget, verify_timeout_s: float = 10.0
    ) -> UIActionReport:
        """Open/show the conversation for an exact contact target.

        Phase 3 UX correction: when web fallback is allowed, try CDP tab reuse
        first (navigate existing WhatsApp Web tab to the exact phone URL without
        opening a new tab), then window activation, then new tab as last resort.
        """
        refusal = self._refusal_for(target, action="open_chat")
        if refusal is not None:
            return refusal

        phone = target.normalized_phone()
        if self.desktop_available():
            url, ui, method = _DESKTOP_CHAT_LINK.format(phone=phone), "desktop", "deep_link"
            how = (f"WhatsApp Desktop deep link with exact phone +{phone} "
                   f"({target.display_name or target.chat_id})")
            return self._launch(
                action="open_chat", url=url, ui_target=ui,
                launch_method=method, contact_targeting="exact_phone_deep_link",
                verify_timeout_s=verify_timeout_s,
                success_detail="conversation surface requested via " + how,
            )

        if not self._allow_web:
            return UIActionReport(
                action="open_chat", ok=False, ui_target="none",
                launch_method="none", contact_targeting="exact_phone_deep_link",
                detail="WhatsApp Desktop is not installed and the WhatsApp "
                       "Web fallback is not enabled for this adapter — "
                       "refusing to target a contact by any fuzzy method",
            )

        # Web fallback with tab reuse preference
        target_url = _WEB_CHAT_URL.format(phone=phone)
        return self._open_web_surface(
            action="open_chat",
            target_url=target_url,
            contact_targeting="exact_phone_deep_link",
            verify_timeout_s=verify_timeout_s,
            success_detail_base=(
                f"WhatsApp Web with exact phone +{phone} "
                f"({target.display_name or target.chat_id})"
            ),
        )

    def focus_chat(
        self, target: WhatsAppUITarget, verify_timeout_s: float = 10.0
    ) -> UIActionReport:
        """
        Focus an already-open WhatsApp surface on the contact's chat.

        The deep link both activates the window and navigates to the chat,
        so this is open_chat with pre-flight window state recorded (the
        caller can tell whether the surface was already open).
        """
        refusal = self._refusal_for(target, action="focus_chat")
        if refusal is not None:
            return refusal

        pre = self._window_probe()
        was_open = bool(pre.get("whatsapp_window_title"))
        report = self.open_chat(target, verify_timeout_s=verify_timeout_s)
        report.action = "focus_chat"
        report.detail = (
            f"WhatsApp surface was {'already open' if was_open else 'not open'}"
            f" — {report.detail}"
        )
        return report

    # ── internals ──────────────────────────────────────────────────────────

    def _refusal_for(
        self, target: Optional[WhatsAppUITarget], action: str
    ) -> Optional[UIActionReport]:
        """Refuse unsafe targets — never guess a contact."""
        if target is None:
            return UIActionReport(
                action=action, ok=False, contact_targeting="none",
                detail="no WhatsAppUITarget provided",
            )
        if not target.chat_id:
            return UIActionReport(
                action=action, ok=False, contact_targeting="none",
                detail="target has no chat_id",
            )
        if target.normalized_phone() is None:
            kind = ("LID chat" if target.chat_id.endswith("@lid")
                    else "group chat" if target.chat_id.endswith("@g.us")
                    else "chat without an exact phone")
            return UIActionReport(
                action=action, ok=False, contact_targeting="none",
                detail=(
                    f"cannot safely target the WhatsApp UI for {kind} "
                    f"'{target.chat_id}' — the UI requires an exact phone "
                    "number and no fuzzy fallback is permitted"
                ),
            )
        return None

    def _open_web_surface(
        self,
        action: str,
        target_url: Optional[str],
        contact_targeting: str,
        verify_timeout_s: float,
        success_detail_base: str,
    ) -> UIActionReport:
        """Phase 3 UX correction: web fallback with CDP tab reuse preference.

        Strategy:
        1. Try CDP: find existing WhatsApp Web tab → activate it → navigate if needed
           → activate Chrome window. No new tab opened.
        2. If no CDP or no WA tab: try window activation (SetForegroundWindow on
           Chrome window with 'WhatsApp' in title). No new tab opened, but can't
           navigate specific tab without CDP.
        3. Last resort: open new tab via opener (explicitly reported).

        target_url: if None, no navigation (just activate existing surface).
        """
        report = UIActionReport(
            action=action, ui_target="web",
            launch_method="none", contact_targeting=contact_targeting,
        )

        # ── Try CDP tab reuse ──────────────────────────────────────────────
        cdp_tabs = self._cdp_list_tabs(self._cdp_port)
        report.cdp_tab_count = len(cdp_tabs)
        report.cdp_available = len(cdp_tabs) > 0

        wa_tab = _cdp_find_whatsapp_tab(cdp_tabs) if cdp_tabs else None

        if wa_tab:
            tab_id = wa_tab.get("id", "")
            report.cdp_tab_url_before = wa_tab.get("url")

            # Activate the tab (brings it to front within Chrome)
            activated = self._cdp_activate_tab(tab_id, self._cdp_port)

            # Navigate if target_url provided
            navigated = False
            if target_url:
                navigated = self._cdp_navigate_tab(target_url, self._cdp_port)
                cdp_url_after = target_url if navigated else report.cdp_tab_url_before
            else:
                cdp_url_after = report.cdp_tab_url_before

            if activated:
                # CDP activation succeeded — report as tab reuse
                report.cdp_tab_reused = True
                report.cdp_tab_url_after = cdp_url_after
                window_ok = self._activate_window()
                report.window_activated = window_ok
                report.ok = True
                report.launch_method = "cdp_tab_reuse"
                report.deep_link = target_url or report.cdp_tab_url_before
                report.detail = (
                    f"{success_detail_base} — reused existing WhatsApp Web tab "
                    f"(CDP tab_id={tab_id[:16]}..., activated={activated}, "
                    f"navigated={navigated}, window_activated={window_ok})"
                )
                return self._verify_and_return(report, verify_timeout_s)
            else:
                # CDP activation failed — reset CDP fields and fall through
                logger.debug("[WA_UI_CDP] tab activation failed, trying window fallback")
                report.cdp_tab_reused = False
                report.cdp_tab_url_before = None

        # ── Fallback 1: window activation (no CDP or CDP failed) ───────────
        window_ok = self._activate_window()
        report.window_activated = window_ok

        if window_ok:
            report.ok = True
            report.launch_method = "browser_url"  # reused existing surface

            # If target_url provided, attempt omnibox navigation on the
            # now-foreground WhatsApp window
            if target_url:
                # Find the WhatsApp window HWND for omnibox navigation
                wa_hwnd = 0
                wa_pid = 0
                for win in self._enumerate_chrome():
                    if _is_whatsapp_title(win.get("title", "")):
                        wa_hwnd = int(win.get("hwnd", 0))
                        wa_pid = int(win.get("pid", 0))
                        break

                if wa_hwnd:
                    nav_result = self._navigate_omnibox(
                        target_url=target_url,
                        target_hwnd=wa_hwnd,
                        target_pid=wa_pid,
                    )
                    if nav_result.get("ok"):
                        report.detail = (
                            f"{success_detail_base} — activated existing WhatsApp "
                            f"Chrome window (hwnd=0x{wa_hwnd:X}), then navigated "
                            f"active tab to {target_url} via omnibox. "
                            f"Title changed from {nav_result.get('title_before')!r} "
                            f"to {nav_result.get('title_after')!r}. "
                            f"No new tab opened."
                        )
                        return self._verify_and_return(report, verify_timeout_s)

                # Omnibox navigation failed or HWND not found
                report.detail = (
                    f"{success_detail_base} — activated existing WhatsApp-titled "
                    f"Chrome window. "
                    f"Omnibox navigation to {target_url} was attempted but failed "
                    f"(WhatsApp HWND={'0x' + format(wa_hwnd, 'X') if wa_hwnd else 'not found'}). "
                    f"The existing tab was activated but not navigated."
                )
            else:
                report.detail = (
                    f"{success_detail_base} — activated existing WhatsApp-titled "
                    f"Chrome window (no CDP; no navigation requested)."
                )
            return self._verify_and_return(report, verify_timeout_s)

        # ── Fallback 2: keyboard cycling (find WhatsApp tab by Ctrl+Tab) ───
        # No Chrome window with "WhatsApp" in title found. Try cycling through
        # tabs in ALL Chrome windows to find it. This is disruptive (changes
        # active tab) but avoids opening a new tab.
        kb_result = self._keyboard_find_tab()

        if kb_result:
            if kb_result.get("found"):
                # WhatsApp found via keyboard cycling
                report.ok = True
                report.launch_method = "keyboard_cycling"
                report.window_activated = True

                kb_hwnd = kb_result.get("hwnd", 0)
                kb_pid = kb_result.get("chrome_pid", 0)

                # If target_url provided, navigate the active tab using omnibox
                if target_url:
                    nav_result = self._navigate_omnibox(
                        target_url=target_url,
                        target_hwnd=kb_hwnd,
                        target_pid=kb_pid,
                    )
                    if nav_result.get("ok"):
                        report.detail = (
                            f"{success_detail_base} — found WhatsApp Web tab via "
                            f"keyboard cycling (Chrome PID={kb_pid}, "
                            f"hwnd=0x{kb_hwnd:X}, "
                            f"tabs_cycled={kb_result.get('tabs_cycled')}, "
                            f"windows_enumerated={kb_result.get('windows_enumerated', 0)}), "
                            f"then navigated active tab to {target_url} via omnibox. "
                            f"Title changed from {nav_result.get('title_before')!r} "
                            f"to {nav_result.get('title_after')!r}. "
                            f"No new tab opened."
                        )
                    else:
                        # Navigation failed but tab was found
                        report.detail = (
                            f"{success_detail_base} — found WhatsApp Web tab via "
                            f"keyboard cycling (Chrome PID={kb_pid}, "
                            f"hwnd=0x{kb_hwnd:X}), but omnibox navigation failed: "
                            f"{nav_result.get('detail', 'unknown error')}. "
                            f"No new tab opened."
                        )
                else:
                    # No navigation requested
                    report.detail = (
                        f"{success_detail_base} — found and activated WhatsApp Web tab "
                        f"via keyboard cycling (Chrome PID={kb_pid}, "
                        f"hwnd=0x{kb_hwnd:X}, "
                        f"tabs_cycled={kb_result.get('tabs_cycled')}, "
                        f"windows_enumerated={kb_result.get('windows_enumerated', 0)}). "
                        f"No new tab opened."
                    )

                return self._verify_and_return(report, verify_timeout_s)
            else:
                # Keyboard cycling completed but WhatsApp NOT found
                # STOP and report — do NOT open new tab automatically
                windows_enum = kb_result.get("windows_enumerated", 0)
                windows_probed = kb_result.get("windows_probed", 0)
                windows_report = kb_result.get("windows_report", [])

                report.ok = False
                report.launch_method = "none"
                report.detail = (
                    f"{success_detail_base} — WhatsApp Web tab NOT FOUND after "
                    f"checking {windows_probed} Chrome windows "
                    f"({windows_enum} enumerated). "
                    f"CDP available={report.cdp_available}, tabs={report.cdp_tab_count}. "
                    f"No new tab opened. User must choose next action deliberately."
                )

                # Append per-window diagnostic summary
                if windows_report:
                    report.detail += "\n\nWindow diagnostic:\n"
                    for i, wr in enumerate(windows_report, 1):
                        report.detail += (
                            f"  Window {i}: hwnd=0x{wr.get('hwnd', 0):X}, "
                            f"pid={wr.get('pid', 0)}, "
                            f"initial_title={wr.get('initial_title', '')!r}, "
                            f"cycle_status={wr.get('cycle_status', 'unknown')}, "
                            f"tabs_cycled={wr.get('tabs_cycled')}, "
                            f"distinct_states={wr.get('distinct_states')}, "
                            f"whatsapp_found={wr.get('whatsapp_found')}, "
                            f"restored_original={wr.get('restored_original')}\n"
                        )

                return report

        # ── Keyboard cycling unavailable or failed ─────────────────────────
        # STOP and report — do NOT open new tab automatically
        report.ok = False
        report.launch_method = "none"
        report.detail = (
            f"{success_detail_base} — WhatsApp Web tab NOT FOUND. "
            f"CDP available={report.cdp_available}, tabs={report.cdp_tab_count}. "
            f"Keyboard cycling unavailable or failed. "
            f"No new tab opened. User must choose next action deliberately."
        )
        return report

    def _verify_and_return(
        self, report: UIActionReport, verify_timeout_s: float
    ) -> UIActionReport:
        """Run window verification and return the report."""
        deadline = time.monotonic() + max(0.0, verify_timeout_s)
        probe: Dict[str, Optional[str]] = {}
        while True:
            probe = self._window_probe()
            if probe.get("whatsapp_window_title"):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(1.0)

        wa_title = probe.get("whatsapp_window_title")
        fg_title = probe.get("foreground_title")
        report.whatsapp_window_title = wa_title
        report.foreground_title = fg_title
        if wa_title:
            report.verified = True
            fg_is_wa = bool(fg_title and "whatsapp" in fg_title.lower())
            report.verification_detail = (
                f"WhatsApp-titled window observed ('{wa_title}')"
                + (" and it is the foreground window" if fg_is_wa
                   else "; foreground is a different window — the surface "
                        "may still be loading or lost focus")
                + ". Chat-level content was NOT machine-verified: targeting "
                  "is deterministic (exact-phone deep link, no fuzzy "
                  "matching), but confirming the rendered conversation "
                  "requires visual/screen reading, which is out of scope."
            )
        else:
            report.verified = False
            report.verification_detail = (
                f"no WhatsApp-titled window observed within "
                f"{verify_timeout_s:.0f}s — the surface may still be "
                "starting, or the launch target did not open"
            )
        return report

    def _launch(
        self,
        action: str,
        url: str,
        ui_target: str,
        launch_method: str,
        contact_targeting: str,
        verify_timeout_s: float,
        success_detail: str,
    ) -> UIActionReport:
        launched = False
        try:
            launched = bool(self._opener(url))
        except Exception as e:
            logger.warning("[WA_UI] opener failed for %r: %s", url, e)
        report = UIActionReport(
            action=action, ok=launched, ui_target=ui_target,
            launch_method=launch_method, contact_targeting=contact_targeting,
            deep_link=url if launched else None,
            detail=success_detail if launched
            else f"launch of {url} was not accepted by the OS",
        )
        if not launched:
            return report

        # Verify a WhatsApp-titled surface appeared (poll — app/web start
        # is asynchronous). Verification is window-level: it proves a
        # WhatsApp surface became visible, not which conversation is
        # rendered (chat-level content is not machine-readable without
        # screen reading, which is out of scope).
        deadline = time.monotonic() + max(0.0, verify_timeout_s)
        probe: Dict[str, Optional[str]] = {}
        while True:
            probe = self._window_probe()
            if probe.get("whatsapp_window_title"):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(1.0)

        wa_title = probe.get("whatsapp_window_title")
        fg_title = probe.get("foreground_title")
        report.whatsapp_window_title = wa_title
        report.foreground_title = fg_title
        if wa_title:
            report.verified = True
            fg_is_wa = bool(fg_title and "whatsapp" in fg_title.lower())
            report.verification_detail = (
                f"WhatsApp-titled window observed ('{wa_title}')"
                + (" and it is the foreground window" if fg_is_wa
                   else "; foreground is a different window — the surface "
                        "may still be loading or lost focus")
                + ". Chat-level content was NOT machine-verified: targeting "
                  "is deterministic (exact-phone deep link, no fuzzy "
                  "matching), but confirming the rendered conversation "
                  "requires visual/screen reading, which is out of scope."
            )
        else:
            report.verified = False
            report.verification_detail = (
                f"no WhatsApp-titled window observed within "
                f"{verify_timeout_s:.0f}s — the surface may still be "
                "starting, or the launch target did not open"
            )
        return report


# ---------------------------------------------------------------------------
# Module-level default adapter (lazy — never instantiated on import).
# ---------------------------------------------------------------------------

_default_adapter: Optional[WhatsAppUIAdapter] = None


def get_default_ui_adapter(allow_web_fallback: bool = True) -> WhatsAppUIAdapter:
    """
    Default adapter instance. Web fallback defaults ON here because the
    machine has no WhatsApp Desktop installed; every report still states
    explicitly which target was used, so the fallback is never silent.
    """
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = WhatsAppUIAdapter(allow_web_fallback=allow_web_fallback)
    return _default_adapter
