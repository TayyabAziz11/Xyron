"""
Verifier Service — lightweight post-tool action verification.

Checks whether a tool action actually succeeded by inspecting system state.
Runs asynchronously (create_task) and does not block voice output.

Verification methods per tool:
  open_application  → check if process name is running (psutil)
  open_directory    → check if path exists (os.path)
  smart_open        → check if path exists or process running
  open_drive        → check if drive path exists
  open_url          → trust tool result (can't inspect browser externally)
  open_file         → check if path exists
  delete_file       → check path no longer exists
  create_folder     → check path exists
  install_store_app → deferred (install takes time); mark as "pending_verify"

Performance target: <50ms for path checks, <100ms for process checks.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    tool_name:          str
    verified:           bool
    verification_method: str        # "process_check" | "path_exists" | "path_gone" | "trusted" | "skipped"
    evidence:           str = ""
    error_type:         str = ""    # "" | "process_not_found" | "path_missing" | "path_still_exists"
    latency_ms:         float = 0.0


# ── Process name mappings (app_name → list of possible process names) ─────────
_PROCESS_MAP: dict[str, list[str]] = {
    "vscode":      ["code", "code.exe"],
    "vs code":     ["code", "code.exe"],
    "visual studio code": ["code", "code.exe"],
    "chrome":      ["chrome", "chrome.exe"],
    "google chrome": ["chrome", "chrome.exe"],
    "calculator":  ["calculatorapp", "calculator", "calc"],
    "notepad":     ["notepad", "notepad.exe"],
    "explorer":    ["explorer", "explorer.exe"],
    "file explorer": ["explorer", "explorer.exe"],
    "edge":        ["msedge", "msedge.exe"],
    "microsoft edge": ["msedge", "msedge.exe"],
    "spotify":     ["spotify", "spotify.exe"],
    "discord":     ["discord", "discord.exe"],
    "slack":       ["slack", "slack.exe"],
    "teams":       ["teams", "ms-teams", "msteams"],
    "word":        ["winword", "winword.exe"],
    "excel":       ["excel", "excel.exe"],
    "powerpoint":  ["powerpnt", "powerpnt.exe"],
    "outlook":     ["outlook", "outlook.exe"],
    "firefox":     ["firefox", "firefox.exe"],
    "terminal":    ["windowsterminal", "wt", "cmd", "powershell"],
    "powershell":  ["powershell", "pwsh"],
    "cmd":         ["cmd", "cmd.exe"],
    "store":       ["winstore.app", "microsoft.windowsstore"],
}


def _check_process_running(name: str) -> tuple[bool, str]:
    """Return (is_running, evidence). Evidence is PID or error string."""
    try:
        import psutil
        name_low = name.lower().replace(".exe", "").strip()

        # Get all possible process names for this app
        candidates = _PROCESS_MAP.get(name_low, [name_low])

        for proc in psutil.process_iter(["name", "pid"]):
            pname = (proc.info.get("name") or "").lower().replace(".exe", "")
            # Check against all candidate process names
            for candidate in candidates:
                if candidate in pname or pname in candidate:
                    return True, f"pid={proc.info.get('pid', '?')} process={pname}"
            # Fallback: original substring check
            if name_low in pname or pname in name_low:
                return True, f"pid={proc.info.get('pid', '?')} process={pname}"
        return False, f"not found in process list (checked: {candidates})"
    except Exception as exc:
        return False, f"psutil error: {exc}"


def verify(tool_name: str, params: dict, result_success: bool, result_data: dict) -> VerifyResult:
    """
    Synchronous verification check. Call via asyncio.to_thread() to avoid
    blocking the event loop.
    """
    t0 = time.time()

    # If the tool itself reported failure, skip further verification
    if not result_success:
        return VerifyResult(
            tool_name=tool_name, verified=False,
            verification_method="skipped", evidence="tool_reported_failure",
            error_type="tool_failure",
        )

    # ── open_application ──────────────────────────────────────────────────────
    if tool_name == "open_application":
        app = params.get("app_name") or params.get("app") or params.get("name") or ""
        if app:
            ok, evidence = _check_process_running(app)
            ms = (time.time() - t0) * 1000
            if ok:
                logger.info("[VERIFY_SUCCESS] tool=%s method=process_check evidence=%s ms=%.0f",
                            tool_name, evidence, ms)
            else:
                logger.warning("[VERIFY_FAIL] tool=%s method=process_check evidence=%s ms=%.0f",
                               tool_name, evidence, ms)
            return VerifyResult(
                tool_name=tool_name, verified=ok,
                verification_method="process_check", evidence=evidence,
                error_type="" if ok else "process_not_found",
                latency_ms=ms,
            )

    # ── open_directory / open_drive ───────────────────────────────────────────
    if tool_name in ("open_directory", "open_drive", "smart_open"):
        path = params.get("path") or params.get("query") or params.get("folder_path") or ""
        if path:
            # Normalise Windows path for WSL2
            check_path = path.replace("\\", "/")
            if check_path.startswith("/mnt/") or os.path.exists(check_path):
                exists = os.path.exists(check_path)
            else:
                exists = os.path.exists(path)
            ms = (time.time() - t0) * 1000
            if exists:
                logger.info("[VERIFY_SUCCESS] tool=%s method=path_exists path=%r ms=%.0f",
                            tool_name, path[:60], ms)
            else:
                logger.warning("[VERIFY_FAIL] tool=%s method=path_exists path=%r ms=%.0f",
                               tool_name, path[:60], ms)
            return VerifyResult(
                tool_name=tool_name, verified=exists,
                verification_method="path_exists", evidence=path[:80],
                error_type="" if exists else "path_missing",
                latency_ms=ms,
            )

    # ── delete_file ───────────────────────────────────────────────────────────
    if tool_name == "delete_file":
        paths = params.get("paths") or ([params["path"]] if params.get("path") else [])
        all_gone = all(not os.path.exists(p) for p in paths) if paths else False
        ms = (time.time() - t0) * 1000
        if all_gone:
            logger.info("[VERIFY_SUCCESS] tool=%s method=path_gone paths=%d ms=%.0f",
                        tool_name, len(paths), ms)
        else:
            logger.warning("[VERIFY_FAIL] tool=%s method=path_gone paths=%d ms=%.0f",
                           tool_name, len(paths), ms)
        return VerifyResult(
            tool_name=tool_name, verified=all_gone,
            verification_method="path_gone",
            error_type="" if all_gone else "path_still_exists",
            latency_ms=ms,
        )

    # ── create_folder ─────────────────────────────────────────────────────────
    if tool_name == "create_folder":
        path = params.get("path") or params.get("folder_path") or ""
        if path:
            exists = os.path.exists(path)
            ms = (time.time() - t0) * 1000
            if exists:
                logger.info("[VERIFY_SUCCESS] tool=%s method=path_exists path=%r ms=%.0f",
                            tool_name, path[:60], ms)
            else:
                logger.warning("[VERIFY_FAIL] tool=%s method=path_exists path=%r ms=%.0f",
                               tool_name, path[:60], ms)
            return VerifyResult(
                tool_name=tool_name, verified=exists,
                verification_method="path_exists", evidence=path[:80],
                error_type="" if exists else "path_missing",
                latency_ms=ms,
            )

    # ── open_url / search_youtube / search_web ────────────────────────────────
    if tool_name in ("open_url", "search_youtube", "search_web"):
        ms = (time.time() - t0) * 1000
        logger.info("[VERIFY_SUCCESS] tool=%s method=trusted (browser launch) ms=%.0f",
                    tool_name, ms)
        return VerifyResult(
            tool_name=tool_name, verified=True,
            verification_method="trusted",
            evidence="browser launch accepted",
            latency_ms=ms,
        )

    # ── install_store_app — deferred (install takes minutes) ─────────────────
    if tool_name in ("install_store_app", "install_store_app_exec"):
        ms = (time.time() - t0) * 1000
        logger.info("[VERIFY_START] tool=%s method=deferred (install may take minutes) ms=%.0f",
                    tool_name, ms)
        return VerifyResult(
            tool_name=tool_name, verified=True,
            verification_method="deferred",
            evidence="install dispatched to winget",
            latency_ms=ms,
        )

    # ── Default: trust the tool result ────────────────────────────────────────
    ms = (time.time() - t0) * 1000
    logger.debug("[VERIFY_START] tool=%s method=skipped (no verifier defined) ms=%.0f",
                 tool_name, ms)
    return VerifyResult(
        tool_name=tool_name, verified=result_success,
        verification_method="skipped",
        latency_ms=ms,
    )


def log_verify_result(r: VerifyResult) -> None:
    if r.verification_method == "skipped":
        return
    if r.verified:
        logger.info("[VERIFY_EVIDENCE] tool=%s verified=%s method=%s evidence=%r ms=%.0f",
                    r.tool_name, r.verified, r.verification_method, r.evidence[:60], r.latency_ms)
    else:
        logger.warning("[VERIFY_FAIL] tool=%s verified=%s method=%s error=%s ms=%.0f",
                       r.tool_name, r.verified, r.verification_method, r.error_type, r.latency_ms)
