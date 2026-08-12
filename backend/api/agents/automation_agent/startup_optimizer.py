from __future__ import annotations

"""
StartupOptimizer — list and manage Windows startup applications.

Uses PowerShell via powershell.exe (WSL2 bridge) to read startup items
from Win32_StartupCommand.

SAFETY: Disabling any startup item requires explicit user approval.
        Registry modifications are scoped to HKCU (current user only).

Log tags: [CLEANER_APPROVAL_REQUIRED]
"""

import asyncio
import json
import logging
from typing import Optional

from api.agents.agent_types import AgentStatus, AgentTask, StepResult

logger = logging.getLogger("api.agents.automation_agent.startup_optimizer")

# PowerShell command — list enabled startup items as JSON
_PS_LIST = [
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    (
        "Get-CimInstance Win32_StartupCommand "
        "| Select-Object Name, Command, Location, User "
        "| ConvertTo-Json -Depth 2 -Compress"
    ),
]

# Rough boot-delay estimates per known app name substring (ms)
_DELAY_MAP: dict[str, int] = {
    "OneDrive":     2000,
    "Spotify":      1500,
    "Discord":      1800,
    "Slack":        1600,
    "Teams":        2500,
    "Skype":        1200,
    "Steam":        2000,
    "Adobe":        1000,
    "Dropbox":      1000,
    "Google Drive": 1000,
    "Epic":         1500,
    "iTunes":       1200,
    "Cortana":       800,
}


class StartupOptimizer:
    """List and manage Windows startup applications via PowerShell."""

    # ── List ──────────────────────────────────────────────────────────────────

    async def list_startup_apps(self) -> list[dict]:
        """
        Query Windows startup items via PowerShell.
        Returns [{name, path, enabled, location, publisher, delay_ms}].
        Returns [] if PowerShell is unavailable or returns no data.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *_PS_LIST,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("[STARTUP_OPTIMIZER] PowerShell timed out after 15s")
            return []
        except FileNotFoundError:
            logger.warning(
                "[STARTUP_OPTIMIZER] powershell.exe not found — not running in WSL2?"
            )
            return []
        except Exception as exc:
            logger.warning("[STARTUP_OPTIMIZER] unexpected error: %r", exc)
            return []

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            logger.info("[STARTUP_OPTIMIZER] PowerShell returned empty output")
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "[STARTUP_OPTIMIZER] JSON parse failed raw=%r…", raw[:200]
            )
            return []

        # ConvertTo-Json wraps single objects as dict, multiple as list
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        apps: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("Name") or "Unknown"
            command = item.get("Command") or ""
            apps.append({
                "name": name,
                "path": command,
                "location": item.get("Location") or "",
                "enabled": True,  # Win32_StartupCommand lists only enabled items
                "publisher": self._guess_publisher(command),
                "delay_ms": self._estimate_delay(name),
            })

        logger.info("[STARTUP_OPTIMIZER] found %d startup app(s)", len(apps))
        return apps

    # ── Disable (approval gate) ────────────────────────────────────────────────

    async def disable_startup_app(self, name: str, task: AgentTask) -> StepResult:
        """
        Request approval to disable a startup app.
        Returns WAITING_APPROVAL — caller must await user confirmation.
        """
        logger.info(
            "[CLEANER_APPROVAL_REQUIRED] action=disable_startup app=%r", name
        )

        if task.ws_send_fn:
            try:
                await task.ws_send_fn({
                    "type": "approval_required",
                    "action": "disable_startup",
                    "summary": f"Disable '{name}' from starting with Windows?",
                    "details": {"app_name": name},
                })
            except Exception as exc:
                logger.warning("[STARTUP_OPTIMIZER] ws_send_fn error: %r", exc)

        task.status = AgentStatus.WAITING_APPROVAL
        return StepResult(
            success=True,
            output=f"Waiting for approval to disable '{name}' from Windows startup.",
            needs_approval=True,
            approval_prompt=f"Disable '{name}' from Windows startup?",
            data={"app_name": name},
        )

    async def execute_disable(self, name: str) -> StepResult:
        """
        Actually disable the startup entry (call only after user approval).
        Removes the HKCU Run registry key for the named app.
        """
        safe_name = name.replace("'", "''")  # escape single quotes for PS
        ps_cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                f"$reg = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';"
                f"if (Get-ItemProperty -Path $reg -Name '{safe_name}' "
                f"    -ErrorAction SilentlyContinue) {{"
                f"  Remove-ItemProperty -Path $reg -Name '{safe_name}';"
                f"  Write-Output 'disabled'"
                f"}} else {{ Write-Output 'not_found' }}"
            ),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *ps_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            output = stdout.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            logger.warning(
                "[STARTUP_OPTIMIZER] execute_disable failed name=%r err=%r", name, exc
            )
            return StepResult(success=False, output=f"Failed to disable '{name}': {exc}")

        if output == "disabled":
            return StepResult(
                success=True,
                output=f"'{name}' has been removed from Windows startup.",
            )
        return StepResult(
            success=False,
            output=(
                f"'{name}' was not found in the current-user startup registry. "
                "It may be in the machine-wide registry (requires admin rights)."
            ),
        )

    # ── Boot savings estimate ─────────────────────────────────────────────────

    async def estimate_boot_savings_ms(self, apps_to_disable: list[str]) -> int:
        """Rough estimate of boot time saved by disabling the listed apps."""
        return sum(self._estimate_delay(app) for app in apps_to_disable)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _estimate_delay(self, name: str) -> int:
        name_lower = name.lower()
        for key, ms in _DELAY_MAP.items():
            if key.lower() in name_lower:
                return ms
        return 500  # generic estimate for unknown apps

    def _guess_publisher(self, command: str) -> str:
        lower = command.lower()
        if "microsoft" in lower:
            return "Microsoft"
        if "google" in lower:
            return "Google"
        if "spotify" in lower:
            return "Spotify AB"
        if "discord" in lower:
            return "Discord Inc."
        if "steam" in lower:
            return "Valve"
        if "adobe" in lower:
            return "Adobe Inc."
        if "dropbox" in lower:
            return "Dropbox"
        if "slack" in lower:
            return "Salesforce / Slack"
        if "epic" in lower:
            return "Epic Games"
        return "Unknown"
