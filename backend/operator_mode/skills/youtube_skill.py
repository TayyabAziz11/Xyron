"""
YouTubeSkill — Windows-Chrome-only implementation.

Opens Windows Chrome (NOT Playwright, NOT Linux Chrome, NOT Chrome for Testing)
with a YouTube search URL, brings it to the foreground, and selects the first result.

Commands:
  "play lofi music"
  "play song you break my heart"
  "open youtube and play chill songs"
  "play [song] X on youtube"
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Sequence

from operator_mode.skills.base_skill import BaseSkill
from operator_mode.operator_types import OperatorAction, VerifySpec, VerifyMethod
import operator_mode.operator_actions as A

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query={query}"

# ── PowerShell scripts ────────────────────────────────────────────────────────
# Use YOUTUBE_URL_PLACEHOLDER so we avoid Python .format() conflicts with PS {}.

_PS_LAUNCH_CHROME = r"""
$url = 'YOUTUBE_URL_PLACEHOLDER'
$paths = @(
    'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'
)
$launched = $false
foreach ($p in $paths) {
    if (Test-Path $p) {
        Write-Output "[YOUTUBE_WINDOWS_CHROME] path=$p"
        Write-Output "[YOUTUBE_OPEN_URL] url=$url"
        Start-Process -FilePath $p -ArgumentList $url
        $launched = $true
        break
    }
}
if (-not $launched) {
    try {
        Write-Output '[YOUTUBE_WINDOWS_CHROME] path=chrome_cmd_fallback'
        Write-Output "[YOUTUBE_OPEN_URL] url=$url"
        Start-Process -FilePath 'chrome' -ArgumentList $url
        $launched = $true
    } catch {
        Write-Output '[YOUTUBE_WINDOWS_CHROME] path=url_direct_fallback'
        Start-Process $url
        $launched = $true
    }
}
if ($launched) { Write-Output 'LAUNCH_OK' }
"""

_PS_VERIFY_WINDOWS_CHROME = r"""
Write-Output '[YOUTUBE_VERIFY_WINDOWS_CHROME]'
$reject = @('Ubuntu', 'Testing', 'chromium', 'msrdc', 'Kali', 'Debian', 'MINGW')
$found = $false
$wins = Get-Process | Where-Object {
    $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne ''
} | Select-Object MainWindowTitle, ProcessName

foreach ($w in $wins) {
    $title = $w.MainWindowTitle
    $proc  = $w.ProcessName
    if ($proc -notmatch '^chrome$') { continue }
    $blocked = $false
    foreach ($r in $reject) {
        if ($title -match $r) {
            Write-Output "[YOUTUBE_REJECT_WSL_BROWSER] title=$title reason=$r"
            $blocked = $true
            break
        }
    }
    if ($blocked) { continue }
    if ($title -match 'YouTube') {
        Write-Output "[YOUTUBE_VERIFY_SUCCESS] title=$title"
        $found = $true
        break
    }
}
if (-not $found) {
    Write-Output '[YOUTUBE_VERIFY_FAIL_REASON] no_windows_chrome_youtube_window'
    exit 1
}
"""

_PS_FOCUS_YOUTUBE = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class YTFocus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
}
"@ -ErrorAction SilentlyContinue

$proc = Get-Process chrome -ErrorAction SilentlyContinue | Where-Object {
    $t = $_.MainWindowTitle
    $t -match 'YouTube' -and
    $t -notmatch 'Ubuntu' -and
    $t -notmatch 'Testing' -and
    $t -notmatch 'chromium' -and
    $t -notmatch 'msrdc'
} | Select-Object -First 1

if ($proc -and $proc.MainWindowHandle -ne 0) {
    [YTFocus]::ShowWindow($proc.MainWindowHandle, 9)
    Start-Sleep -Milliseconds 150
    [YTFocus]::BringWindowToTop($proc.MainWindowHandle)
    Start-Sleep -Milliseconds 150
    [YTFocus]::SetForegroundWindow($proc.MainWindowHandle)
    Write-Output "[YOUTUBE_VERIFY_WINDOWS_CHROME] focused=$($proc.MainWindowTitle)"
} else {
    Write-Output '[YOUTUBE_VERIFY_FAIL_REASON] could_not_focus_chrome_window'
    exit 1
}
"""


class YouTubeSkill(BaseSkill):

    async def build_actions(
        self, params: dict[str, Any], trace_id: str
    ) -> Sequence[OperatorAction]:
        query = params.get("query", "music")
        logger.info("[TRACE %s] [YOUTUBE_QUERY_PARSE] query=%r", trace_id, query)

        encoded = urllib.parse.quote_plus(query)
        url = YOUTUBE_SEARCH_URL.format(query=encoded)
        logger.info("[TRACE %s] [YOUTUBE_OPEN_URL] url=%r", trace_id, url)

        launch_script = _PS_LAUNCH_CHROME.replace("YOUTUBE_URL_PLACEHOLDER", url)

        actions: list[OperatorAction] = []

        # Step 1: Launch Windows Chrome with YouTube search URL (no Playwright)
        logger.info("[TRACE %s] [YOUTUBE_WINDOWS_CHROME] launching via PowerShell Start-Process", trace_id)
        actions.append(A.powershell_cmd(
            description="Launch Windows Chrome → YouTube search",
            script=launch_script,
            wait_ms=2500,
            timeout=8,
        ))

        # Step 2: Verify Windows Chrome opened YouTube — rejects Ubuntu/Testing browsers
        logger.info("[TRACE %s] [YOUTUBE_VERIFY_WINDOWS_CHROME]", trace_id)
        actions.append(A.powershell_cmd(
            description="Verify Windows Chrome has YouTube (reject WSL browsers)",
            script=_PS_VERIFY_WINDOWS_CHROME,
            wait_ms=500,
            timeout=6,
        ))

        # Step 3: Bring YouTube Chrome window to foreground via Win32 API
        actions.append(A.powershell_cmd(
            description="Focus Windows Chrome YouTube window",
            script=_PS_FOCUS_YOUTUBE,
            wait_ms=600,
            timeout=5,
        ))

        # Step 4: Navigate to first video result and play it
        logger.info("[TRACE %s] [YOUTUBE_PLAY_ATTEMPT]", trace_id)
        actions.append(A.press("Tab", description="Focus first video result", wait_ms=400))
        actions.append(A.press("Return", description="Open first video", wait_ms=1500))

        # Step 5: Confirm YouTube video page is active
        actions.append(OperatorAction(
            action_type="wait_for_window",
            params={"app_name": "YouTube", "timeout_s": 5.0},
            description="Confirm YouTube video loaded",
            delay_after_ms=0,
            verify=VerifySpec(VerifyMethod.WINDOW_EXISTS, expected="YouTube", timeout_ms=5000),
        ))

        return actions

    async def get_success_response(self, params: dict[str, Any]) -> str:
        query = params.get("query", "music")
        logger.info("[YOUTUBE_VERIFY_SUCCESS] query=%r", query)
        return f"Playing {query} on YouTube."

    async def get_failure_response(self, params: dict[str, Any]) -> str:
        query = params.get("query", "music")
        logger.warning("[YOUTUBE_VERIFY_FAIL_REASON] query=%r could_not_confirm_playback", query)
        return f"I opened YouTube search for {query}, but I couldn't confirm playback."
