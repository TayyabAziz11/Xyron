#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Phase 4.11.1 — repairs the Windows-side CDP bridge for Xyron's
  Chrome control. Must run elevated (invoked via Start-Process -Verb RunAs
  from the non-elevated Xyron backend process).

.DESCRIPTION
  Root cause this repairs: a stale `netsh portproxy` rule listening on
  0.0.0.0:<OldPort> (same port Chrome itself needs) blocks Chrome's own
  DevTools listener from binding, causing Chrome to silently fall back to
  [::1]:<OldPort> (IPv6 loopback only) — unreachable from WSL2. This
  script removes that colliding rule and creates a new bridge on a
  *different* external port (0.0.0.0:<SelectedPort> -> 127.0.0.1:<ChromeLocalPort>),
  scoped to exactly that one port in the firewall. It never disables the
  firewall, never opens a broad port range, and never touches unrelated
  portproxy rules (only the exact listenport values it's told about).

.OUTPUTS
  Writes a single JSON object to -ResultPath:
    { success, bridge_port, portproxy_created, firewall_created, errors: [] }
#>
param(
    [int]$OldPort = 9222,
    [int]$ChromeLocalPort = 9222,
    [int]$PreferredPort = 9223,
    [int[]]$FallbackPorts = @(9224, 9225, 9226, 9227, 9228, 9229, 9230),
    [string]$FirewallRuleName = "XyronCDPBridge",
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = "Continue"
$errors = New-Object System.Collections.Generic.List[string]
$portproxyCreated = $false
$firewallCreated = $false
$selectedPort = $null

function Write-Result {
    param([bool]$Success)
    $result = [ordered]@{
        success           = $Success
        bridge_port       = $selectedPort
        portproxy_created = $portproxyCreated
        firewall_created  = $firewallCreated
        errors            = $errors
    }
    $json = $result | ConvertTo-Json -Compress
    Set-Content -Path $ResultPath -Value $json -Encoding UTF8
    Write-Output $json
}

try {
    # 1. Remove the old colliding rule (0.0.0.0:$OldPort -> 127.0.0.1:$ChromeLocalPort).
    # Only ever targets this exact listenport/listenaddress pair — every
    # other portproxy rule (e.g. the 3001/8000 dashboard bridges) is left
    # completely untouched.
    try {
        netsh interface portproxy delete v4tov4 listenport=$OldPort listenaddress=0.0.0.0 | Out-Null
    } catch {
        $errors.Add("delete_old_rule: $($_.Exception.Message)")
    }

    # 2. Pick the first free external port from Preferred + Fallback list.
    $candidates = @($PreferredPort) + $FallbackPorts
    foreach ($p in $candidates) {
        $inUse = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        if (-not $inUse) {
            $selectedPort = $p
            break
        }
    }
    if (-not $selectedPort) {
        $errors.Add("no_free_port_in_range")
        Write-Result -Success $false
        exit 1
    }

    # 3. Create the new bridge rule on the selected port.
    try {
        netsh interface portproxy add v4tov4 `
            listenaddress=0.0.0.0 listenport=$selectedPort `
            connectaddress=127.0.0.1 connectport=$ChromeLocalPort | Out-Null
        $portproxyCreated = $true
    } catch {
        $errors.Add("add_new_rule: $($_.Exception.Message)")
    }

    # 4. Replace the scoped firewall rule — allow exactly this one TCP port.
    try {
        Remove-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue | Out-Null
        New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound `
            -Action Allow -Protocol TCP -LocalPort $selectedPort | Out-Null
        $firewallCreated = $true
    } catch {
        $errors.Add("firewall_rule: $($_.Exception.Message)")
    }

    $success = $portproxyCreated -and $firewallCreated
    Write-Result -Success $success
    if (-not $success) { exit 1 }
    exit 0
} catch {
    $errors.Add("unexpected: $($_.Exception.Message)")
    Write-Result -Success $false
    exit 1
}
