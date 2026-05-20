"""
Async PowerShell runner — single source of truth for all PS execution.

Every tool module must import run_ps from here instead of calling
subprocess.run / subprocess.Popen directly.  This guarantees:
  - True async execution (asyncio.create_subprocess_exec, not to_thread)
  - Per-call timeouts (asyncio.wait_for around communicate())
  - Consistent latency tracking (time.perf_counter)
  - Uniform error codes for the tool layer
  - A unified ToolResult that satisfies both registry.ToolResult callers
    (spoken, action_url, action_app, action_path, risk) and the
    shared/tool_contract.ToolResult interface (message, data, latency_ms,
    warnings, requires_followup).  Resolves the P0 class conflict.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Error codes ───────────────────────────────────────────────────────────────

PS_TIMEOUT    = "PS_TIMEOUT"      # asyncio.TimeoutError
PS_JSON_ERROR = "PS_JSON_ERROR"   # JSON parse failed (parse_json=True)
PS_UNKNOWN    = "PS_UNKNOWN"      # unexpected exception


def ps_exit(n: int) -> str:
    """Return error code for a non-zero PowerShell exit."""
    return f"PS_EXIT_{n}"


# ── PowerShell executable ─────────────────────────────────────────────────────

POWERSHELL_EXE = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


# ── Unified ToolResult ────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """
    Unified result returned by every tool executor.

    Merges registry.ToolResult (spoken/action_*/risk) with
    shared/tool_contract.ToolResult (message/data/latency_ms/warnings/
    requires_followup).  Import from here; do NOT import from either of
    the two legacy locations.
    """
    success:          bool
    message:          str                          # primary human-readable text
    spoken:           str = ""                     # ≤ 80 chars, no markdown
    data:             dict[str, Any] = field(default_factory=dict)
    latency_ms:       float = 0.0
    error_code:       Optional[str] = None
    warnings:         list[str] = field(default_factory=list)
    requires_followup: bool = False
    action_url:       Optional[str] = None         # frontend: open URL
    action_app:       Optional[str] = None         # frontend: launch app
    action_path:      Optional[str] = None         # frontend: open in Explorer
    risk:             str = "low"

    # ── Backward-compat shim for legacy callers that read .text ──────────────

    @property
    def text(self) -> str:
        return self.message

    @text.setter
    def text(self, value: str) -> None:
        self.message = value

    # ── Compatibility with registry.ToolResult SSE layer ─────────────────────

    @property
    def error(self) -> Optional[str]:
        return self.error_code

    def to_task_result(self) -> str:
        return self.message

    def to_sse_action(self) -> dict | None:
        if self.error_code == "confirm_required":
            return {
                "confirm_required": True,
                "confirm_tool":     self.data.get("tool", ""),
                "confirm_params":   self.data.get("params", {}),
                "confirm_prompt":   self.data.get(
                    "prompt", "Are you sure? Say yes to confirm or no to cancel."
                ),
            }
        if self.action_url or self.action_app or self.action_path:
            return {
                "action_url":  self.action_url,
                "action_app":  self.action_app,
                "action_path": self.action_path,
            }
        return None

    # ── Bridge to shared/tool_contract.ToolResult ────────────────────────────

    def to_shared_contract(self) -> Any:
        """Return a shared/tool_contract.ToolResult for ToolDispatcher callers."""
        try:
            from shared.tool_contract import ToolResult as ContractResult
        except ImportError:
            # Fallback: return self (duck-typed; has same fields)
            return self
        return ContractResult(
            success=self.success,
            message=self.message,
            data=self.data,
            latency_ms=self.latency_ms,
            warnings=self.warnings,
            requires_followup=self.requires_followup,
        )

    # ── Constructors ─────────────────────────────────────────────────────────

    @classmethod
    def ok(
        cls,
        message: str,
        data: dict[str, Any] | None = None,
        spoken: str = "",
        **kwargs: Any,
    ) -> "ToolResult":
        return cls(success=True, message=message, spoken=spoken, data=data or {}, **kwargs)

    @classmethod
    def failure(
        cls,
        message: str,
        error_code: str = PS_UNKNOWN,
        spoken: str = "",
        **kwargs: Any,
    ) -> "ToolResult":
        return cls(
            success=False,
            message=message,
            spoken=spoken or message[:80],
            error_code=error_code,
            **kwargs,
        )


# ── run_ps ────────────────────────────────────────────────────────────────────

async def run_ps(
    script: str,
    timeout: float = 8.0,
    parse_json: bool = False,
    risk: str = "low",
) -> ToolResult:
    """
    Execute a PowerShell script asynchronously and return a ToolResult.

    Args:
        script:     PowerShell code to run.
        timeout:    Seconds before PS_TIMEOUT is returned (default 8 s).
        parse_json: If True, decode stdout as JSON into result.data.
                    Returns PS_JSON_ERROR on decode failure.
        risk:       Copied verbatim into the returned ToolResult.risk field.

    The process is started with -NoProfile -NonInteractive to avoid
    loading user profiles or blocking on prompts.
    """
    t_start = time.perf_counter()

    try:
        proc = await asyncio.create_subprocess_exec(
            POWERSHELL_EXE,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()  # drain pipes
            latency = (time.perf_counter() - t_start) * 1000
            return ToolResult.failure(
                f"PowerShell timed out after {timeout}s",
                error_code=PS_TIMEOUT,
                latency_ms=latency,
                risk=risk,
            )

    except Exception as exc:
        latency = (time.perf_counter() - t_start) * 1000
        return ToolResult.failure(
            f"Failed to start PowerShell: {exc}",
            error_code=PS_UNKNOWN,
            latency_ms=latency,
            risk=risk,
        )

    latency = (time.perf_counter() - t_start) * 1000
    stdout = raw_out.decode("utf-8", errors="replace").strip()
    stderr = raw_err.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        return ToolResult.failure(
            stderr or stdout or f"PowerShell exited with code {proc.returncode}",
            error_code=ps_exit(proc.returncode),
            data={"stderr": stderr, "stdout": stdout, "exit_code": proc.returncode},
            latency_ms=latency,
            risk=risk,
        )

    if parse_json:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return ToolResult.failure(
                f"JSON decode error: {exc} — raw: {stdout[:200]}",
                error_code=PS_JSON_ERROR,
                data={"raw": stdout},
                latency_ms=latency,
                risk=risk,
            )
        return ToolResult.ok(
            "OK",
            data=parsed if isinstance(parsed, dict) else {"result": parsed},
            latency_ms=latency,
            risk=risk,
        )

    warnings: list[str] = [stderr] if stderr else []
    return ToolResult.ok(
        stdout or "OK",
        latency_ms=latency,
        warnings=warnings,
        risk=risk,
    )
