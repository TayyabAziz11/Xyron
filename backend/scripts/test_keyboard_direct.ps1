# test_keyboard_direct.ps1 — Direct test of keyboard cycling logic
Add-Type -AssemblyName System.Windows.Forms

# Load the focus shim (from system_tools._FOCUS_SHIM_CS)
$focusShimCS = @"
using System;using System.Runtime.InteropServices;
public class XyronFG {
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
[DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
[DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
Add-Type -TypeDefinition $focusShimCS

$chromeProcs = Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
Write-Output "Chrome processes with windows: $($chromeProcs.Count)"

if (-not $chromeProcs) {
    Write-Output "NO_CHROME"
    exit
}

$maxTabs = 10
$wshell = New-Object -COM WScript.Shell

foreach ($proc in $chromeProcs) {
    Write-Output "`n=== Testing Chrome PID $($proc.Id) ==="
    Write-Output "Original title: $($proc.MainWindowTitle)"
    
    $hwnd = $proc.MainWindowHandle
    $curTid = [XyronFG]::GetCurrentThreadId()
    $fgWin = [XyronFG]::GetForegroundWindow()
    $dummy = 0
    $fgTid = [XyronFG]::GetWindowThreadProcessId($fgWin, [ref]$dummy)
    [XyronFG]::AttachThreadInput($curTid, $fgTid, $true) | Out-Null
    [XyronFG]::ShowWindow($hwnd, 9) | Out-Null
    [XyronFG]::SetForegroundWindow($hwnd) | Out-Null
    [XyronFG]::BringWindowToTop($hwnd) | Out-Null
    [XyronFG]::AttachThreadInput($curTid, $fgTid, $false) | Out-Null
    Start-Sleep -Milliseconds 500
    
    $origTitle = $proc.MainWindowTitle
    Write-Output "After activation: $origTitle"
    
    if ($origTitle -like '*WhatsApp*') {
        Write-Output "FOUND|pid=$($proc.Id)|tabs_cycled=0|title=$origTitle"
        exit
    }
    
    for ($i = 1; $i -le $maxTabs; $i++) {
        Write-Output "  Cycling tab $i..."
        $wshell.SendKeys('^{TAB}')
        Start-Sleep -Milliseconds 500
        $proc.Refresh()
        $newTitle = $proc.MainWindowTitle
        Write-Output "  After Ctrl+Tab: $newTitle"
        
        if ($newTitle -like '*WhatsApp*') {
            Write-Output "FOUND|pid=$($proc.Id)|tabs_cycled=$i|title=$newTitle"
            exit
        }
        
        if ($newTitle -eq $origTitle) {
            Write-Output "WRAPPED|pid=$($proc.Id)|tabs_cycled=$i|title=$newTitle"
            break
        }
    }
}

Write-Output "NOT_FOUND"
