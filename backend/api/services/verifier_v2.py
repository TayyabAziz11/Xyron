"""
VerifierV2 — extended post-action verification.

Wraps V1 (verifier_service.verify) and adds:
  windows_process  — query Windows-side process list via PowerShell (WSL2-safe)
  windows_window   — query foreground window title via window_context (PS-bridged)
  store_page       — after install_store_app, check if Store PDP title matches
  browser_page     — after open_url, check browser window title changed

Performance: <400ms total. Blocking I/O — call via asyncio.to_thread().

Log prefixes: [VERIFY2_WINDOWS_PROCESS_CHECK] [VERIFY2_WINDOWS_WINDOW_CHECK]
              [VERIFY2_WINDOWS_SHELL_CHECK] [VERIFY2_SUCCESS] [VERIFY2_FAIL] [VERIFY2_MS]
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from api.services.verifier_service import (
    VerifyResult,
    verify as _v1_verify,
    log_verify_result as _v1_log,
)

logger = logging.getLogger(__name__)

# How long to wait / how many retries for window to appear after launch
_WINDOW_CHECK_DELAY   = 0.4   # seconds between retries
_WINDOW_CHECK_RETRIES = 4     # up to ~1.6s total wait

# ── App verification tables ───────────────────────────────────────────────────
# Maps canonical app name → (Windows process names, window title fragments)
# Process names are lowercase, no .exe, as returned by Get-Process.Name.lower()
# Title fragments are lowercase substrings to search in the window title.

_APP_VERIFY_TABLE: dict[str, dict] = {
    # VS Code
    "vscode":              {"procs": ["code"],            "titles": ["visual studio code", "code"]},
    "vs code":             {"procs": ["code"],            "titles": ["visual studio code", "code"]},
    "visual studio code":  {"procs": ["code"],            "titles": ["visual studio code"]},
    "code":                {"procs": ["code"],            "titles": ["visual studio code", "code"]},
    # Chrome
    "chrome":              {"procs": ["chrome"],          "titles": ["google chrome", "chrome"]},
    "google chrome":       {"procs": ["chrome"],          "titles": ["google chrome"]},
    # Edge
    "edge":                {"procs": ["msedge"],          "titles": ["microsoft edge", "edge"]},
    "microsoft edge":      {"procs": ["msedge"],          "titles": ["microsoft edge"]},
    # Firefox
    "firefox":             {"procs": ["firefox"],         "titles": ["mozilla firefox", "firefox"]},
    # Notepad
    "notepad":             {"procs": ["notepad"],         "titles": ["notepad"]},
    # Calculator — hosted by ApplicationFrameHost, process is CalculatorApp
    "calculator":          {"procs": ["calculatorapp", "applicationframehost"],
                            "titles": ["calculator"]},
    # Explorer
    "explorer":            {"procs": ["explorer"],        "titles": ["file explorer", "explorer"]},
    "file explorer":       {"procs": ["explorer"],        "titles": ["file explorer"]},
    # Spotify
    "spotify":             {"procs": ["spotify"],         "titles": ["spotify"]},
    # Discord
    "discord":             {"procs": ["discord"],         "titles": ["discord"]},
    # Microsoft Store
    "microsoft store":     {"procs": ["windowsstore", "winstore.app"],
                            "titles": ["microsoft store"]},
    "store":               {"procs": ["windowsstore", "winstore.app"],
                            "titles": ["microsoft store"]},
    # Paint
    "paint":               {"procs": ["mspaint"],         "titles": ["paint"]},
    # Word / Excel / PowerPoint
    "word":                {"procs": ["winword"],         "titles": ["word"]},
    "excel":               {"procs": ["excel"],           "titles": ["excel"]},
    "powerpoint":          {"procs": ["powerpnt"],        "titles": ["powerpoint"]},
    # Terminal
    "windows terminal":    {"procs": ["windowsterminal"], "titles": ["windows terminal", "terminal"]},
    "terminal":            {"procs": ["windowsterminal"], "titles": ["terminal"]},
    # Task Manager
    "task manager":        {"procs": ["taskmgr"],         "titles": ["task manager"]},
    # Windows Settings — launched via the ms-settings: URI (open_application
    # "settings" / open_system_settings). The dedicated process is
    # SystemSettings.exe; ApplicationFrameHost is deliberately NOT listed
    # because it hosts every UWP app and would verify "settings" as open
    # whenever ANY UWP app (Calculator, Store, ...) is running. Real-mic
    # Urdu test Issue 5: this entry used to be missing, so _lookup_app's
    # generic fallback expected a process literally named "settings" and
    # every successful ms-settings: launch failed verification.
    "settings":            {"procs": ["systemsettings"],  "titles": ["settings"]},
    "windows settings":    {"procs": ["systemsettings"],  "titles": ["settings"]},
}

# PowerShell script: Get-Process only (no Add-Type compilation — fast, ~350ms)
# Foreground window comes from window_context.py which has its own PS script file.
_PS_GETPROC_SCRIPT = (
    "Get-Process | Select-Object -ExpandProperty Name | ForEach-Object { $_.ToLower() }"
)


def _get_windows_state(timeout: float = 6.0) -> dict:
    """
    Get Windows process list + foreground window.
    - Process list: persistent PS session (Get-Process, ~350ms)
    - Foreground window: window_context.py (cached PS .ps1 file, 2s TTL)
    Returns {"procs": set[str], "fg_title": str, "fg_proc": str}.
    """
    result = {"procs": set(), "fg_title": "", "fg_proc": ""}

    # ── Process list via persistent PS session (no Add-Type) ─────────────────
    try:
        from api.services.ps_session import ps_session as _pss
        ok, out = _pss.run(_PS_GETPROC_SCRIPT, timeout=timeout)
        if ok and out:
            for line in out.splitlines():
                name = line.strip().lower().replace(".exe", "")
                if name:
                    result["procs"].add(name)
            logger.info("[VERIFY2_WINDOWS_PROCESS_CHECK] windows_procs=%d sample=%s",
                        len(result["procs"]),
                        sorted(list(result["procs"]))[:5])
    except Exception as exc:
        logger.debug("[VERIFY2_PS_STATE_PROC] error: %s", exc)

    # ── Foreground window via window_context (PS .ps1 file, 2s cache) ────────
    # Do NOT invalidate — the 2s cache is sufficient for verify purposes and
    # removing this saves 430ms per retry (4 retries × 430ms = 1720ms saved).
    try:
        from api.services.window_context import window_context as _wctx
        _wctx_t0 = time.time()
        _cache_age = time.monotonic() - _wctx._cached_at
        win = _wctx.get_active_window()
        _wctx_ms = (time.time() - _wctx_t0) * 1000
        if _cache_age < 2.0:
            logger.info("[WINDOW_CONTEXT_CACHE_HIT] age_ms=%.0f query_ms=%.0f",
                        _cache_age * 1000, _wctx_ms)
        else:
            logger.info("[WINDOW_CONTEXT_CACHE_MISS] age_ms=%.0f query_ms=%.0f",
                        _cache_age * 1000, _wctx_ms)
        logger.info("[WINDOW_CONTEXT_QUERY_MS] ms=%.0f", _wctx_ms)
        if win:
            result["fg_title"] = win.get("title", "") or ""
            result["fg_proc"]  = (win.get("proc_name") or "").lower().replace(".exe", "")
            logger.info("[VERIFY2_WINDOWS_WINDOW_CHECK] fg_title=%r fg_proc=%r",
                        result["fg_title"][:60], result["fg_proc"])
    except Exception as exc:
        logger.debug("[VERIFY2_PS_STATE_WIN] error: %s", exc)

    return result
# A bare shell/console host window is never proof that an *unrecognized* app
# launched — it's exactly what appears when _launch_app()'s cmd.exe fallback
# can't find the given command and echoes it into a new console window
# (confirmed root cause of "CMD window titled 'perfume folder'" being
# treated as a successful app launch). Only applies to the generic fallback
# entry below — known apps in _APP_VERIFY_TABLE are never shell hosts.
_GENERIC_FALLBACK_EXCLUDE_PROCS = frozenset({
    "cmd", "conhost", "powershell", "pwsh", "windowsterminal",
})


def _lookup_app(app_name: str) -> dict:
    """Return verify table entry for an app name, with fallback."""
    key = app_name.lower().strip().rstrip(".")
    if key in _APP_VERIFY_TABLE:
        return _APP_VERIFY_TABLE[key]
    # The launch path (system_tools._exec_open_application) resolves aliases
    # and strips filler words ("search chrome" → "chrome", "google chrome" →
    # "chrome") before ever calling launch_app — but this verify path was
    # checking the raw, un-normalized app_name from the tool params, so a
    # launch that correctly resolved and succeeded could still fail
    # verification because "search chrome" isn't a real process name. Reuse
    # the exact same normalizer so launch and verify always agree on the
    # canonical app name.
    try:
        from api.tools.system_tools import _normalise_app as _norm_app
        norm_key = _norm_app(app_name)
        if norm_key != key and norm_key in _APP_VERIFY_TABLE:
            return _APP_VERIFY_TABLE[norm_key]
        if norm_key != key:
            key = norm_key
    except Exception:
        pass
    # Generic fallback: use the app name itself. Flagged so
    # _verify_app_launch can refuse to accept a bare shell-host process
    # (cmd/conhost/powershell) as evidence — see
    # _GENERIC_FALLBACK_EXCLUDE_PROCS above.
    safe = re.sub(r'[^a-z0-9]', '', key)
    return {"procs": [safe, key], "titles": [key], "generic_fallback": True}


def verify(
    tool_name: str,
    params: dict,
    result_success: bool,
    result_data: dict,
) -> VerifyResult:
    """
    Extended verifier. Always call via asyncio.to_thread() to avoid blocking the event loop.
    """
    t0 = time.time()

    if not result_success:
        return VerifyResult(
            tool_name=tool_name, verified=False,
            verification_method="skipped", evidence="tool_reported_failure",
            error_type="tool_failure",
        )

    if tool_name == "open_application":
        return _verify_app_launch(tool_name, params, result_data, t0)

    if tool_name in ("open_directory", "open_drive", "smart_open", "create_folder"):
        # Object-type-specific verification (Part 7): a folder/drive open
        # must be confirmed via Explorer's actual path, never via an
        # app-launch heuristic. smart_open can also resolve to a *file* —
        # detected below and delegated to path-exists (v1), since Explorer
        # isn't necessarily involved in opening a file.
        #
        # create_folder added 2026-08-24: it had no verifier_v2 handler at
        # all, so every call fell through to the old _v1_verify, which
        # checks params["path"] (the raw location the caller asked to
        # create the folder IN — e.g. "C:\\", not the new folder itself)
        # via a bare os.path.exists() with no WSL-path translation. On this
        # WSL2 backend os.path.exists("C:\\") is always False (it's not a
        # real Linux path), so every create_folder call was reported as
        # VERIFY_FAIL regardless of whether the folder was actually
        # created. _exec_create_folder's ToolResult already carries the
        # real new-folder path in action_path/data["path"] (e.g.
        # "C:\\neya", not just the parent) — reusing _verify_folder_open
        # here checks that path, through the same _win_to_wsl_path
        # translation every other folder tool already relies on.
        resolved_path = (result_data.get("path") or result_data.get("action_path") or
                         params.get("path") or params.get("query") or "")
        looks_like_file = (
            (params.get("type") or "").lower() == "file"
            or (result_data.get("type") or "").lower() == "file"
            or bool(re.search(r'\.[A-Za-z0-9]{1,8}$', resolved_path))
        )
        if not looks_like_file:
            return _verify_folder_open(tool_name, params, result_data, t0)

    if tool_name in ("open_url", "search_youtube", "search_web"):
        return _verify_browser(tool_name, params, result_data, t0)

    if tool_name in ("install_store_app", "open_store_app_page"):
        return _verify_store_page(tool_name, params, result_data, t0)

    return _v1_verify(tool_name, params, result_success, result_data)


def log_verify_result(r: VerifyResult) -> None:
    _v1_log(r)


# ── App launch verification ───────────────────────────────────────────────────

def _verify_app_launch(
    tool_name: str,
    params: dict,
    result_data: dict,
    t0: float,
) -> VerifyResult:
    """
    Windows-side verification for app launches.
    Strategy (stops at first pass):
      1. Windows process list (via PS bridge) — fastest for most apps
      2. Foreground window title (from same PS call)
      3. Retry loop with increasing delay to give the app time to start
    """
    app_name = (
        params.get("app_name") or params.get("app") or params.get("name") or ""
    ).strip().rstrip(".")

    entry = _lookup_app(app_name)
    expected_procs   = set(entry["procs"])
    expected_titles  = entry["titles"]
    is_generic_fallback = bool(entry.get("generic_fallback"))

    for attempt in range(_WINDOW_CHECK_RETRIES):
        if attempt > 0:
            time.sleep(_WINDOW_CHECK_DELAY)

        win_state = _get_windows_state()
        procs_found = win_state["procs"]
        fg_title    = win_state["fg_title"].lower()
        fg_proc     = win_state["fg_proc"].lower()

        logger.info(
            "[VERIFY2_WINDOWS_PROCESS_CHECK] attempt=%d app=%r expected_procs=%s found=%s",
            attempt, app_name,
            sorted(expected_procs),
            sorted(expected_procs & procs_found) or "none",
        )

        # Check 1: expected process is running on Windows. For the generic
        # (unrecognized-app) fallback, a bare shell host doesn't count — see
        # _GENERIC_FALLBACK_EXCLUDE_PROCS.
        matching_procs = expected_procs & procs_found
        if is_generic_fallback:
            matching_procs -= _GENERIC_FALLBACK_EXCLUDE_PROCS
        if matching_procs:
            ms = (time.time() - t0) * 1000
            evidence = f"windows_proc={sorted(matching_procs)}"
            logger.info("[VERIFY2_SUCCESS] tool=%s method=windows_process evidence=%s ms=%.0f",
                        tool_name, evidence, ms)
            return VerifyResult(
                tool_name=tool_name, verified=True,
                verification_method="windows_process",
                evidence=evidence, latency_ms=ms,
            )

        # Check 2: foreground window title matches. Same guard — an
        # unrecognized app whose only "evidence" is a shell host window
        # (cmd/conhost/powershell) echoing the mistranscribed name back as
        # its title is not proof anything launched. This is exactly what
        # happened for "perfume folder": _launch_app()'s cmd.exe fallback
        # opened a console window titled "perfume folder" after failing to
        # find that program, and the old unconditional substring match
        # treated that as a successful launch.
        logger.info(
            "[VERIFY2_WINDOWS_WINDOW_CHECK] attempt=%d fg_title=%r fg_proc=%r expected_titles=%s",
            attempt, fg_title[:60], fg_proc, expected_titles,
        )
        if is_generic_fallback and fg_proc in _GENERIC_FALLBACK_EXCLUDE_PROCS:
            logger.warning(
                "[VERIFY2_FAIL] tool=%s app=%r rejected shell-host window as evidence "
                "fg_proc=%r fg_title=%r",
                tool_name, app_name, fg_proc, fg_title[:60],
            )
        else:
            for hint in expected_titles:
                if hint in fg_title or hint in fg_proc:
                    ms = (time.time() - t0) * 1000
                    evidence = f"fg_title={win_state['fg_title'][:60]!r}"
                    logger.info("[VERIFY2_SUCCESS] tool=%s method=windows_window evidence=%s ms=%.0f",
                                tool_name, evidence, ms)
                    return VerifyResult(
                        tool_name=tool_name, verified=True,
                        verification_method="windows_window",
                        evidence=evidence, latency_ms=ms,
                    )

    # All attempts failed
    ms = (time.time() - t0) * 1000
    logger.warning(
        "[VERIFY2_FAIL] tool=%s app=%r procs_checked=%s fg_title=%r ms=%.0f",
        tool_name, app_name, sorted(expected_procs), win_state.get("fg_title", "")[:60], ms,
    )
    return VerifyResult(
        tool_name=tool_name, verified=False,
        verification_method="windows_process+window",
        evidence=f"app={app_name!r} not found in Windows processes or foreground window after {_WINDOW_CHECK_RETRIES} checks",
        error_type="app_not_detected", latency_ms=ms,
    )


# ── Folder / drive verification (Part 7) ──────────────────────────────────────

def _verify_folder_open(
    tool_name: str,
    params: dict,
    result_data: dict,
    t0: float,
) -> VerifyResult:
    """
    Folder/drive verification: the path must exist AND, when Explorer's
    focused folder is observable, it must actually match — a foreground
    window whose *title* happens to contain the folder name (a CMD error
    window, for instance) is never sufficient evidence. This is the
    object-type-specific counterpart to _verify_app_launch: folders are
    verified through Explorer state, never through a process/window-title
    heuristic meant for applications.
    """
    path = (result_data.get("path") or result_data.get("action_path") or
            params.get("path") or params.get("query") or "")
    if not path:
        return _v1_verify(tool_name, params, True, result_data)

    # Tools here return Windows-style paths (e.g. "E:\Perfume") — translate
    # to the WSL2 mount before checking existence, same as every other path
    # comparison in this codebase (explorer_context, fs_index). The old v1
    # path_exists check skipped this translation, so a genuinely-correct
    # Windows path could fail existence purely because "E:\Perfume" isn't a
    # real path on the WSL side without it being rewritten to "/mnt/e/perfume".
    try:
        from api.services.explorer_context import _win_to_wsl_path
        target = _win_to_wsl_path(path) if re.match(r'^[A-Za-z]:', path) else Path(path)
    except Exception:
        target = Path(path.replace("\\", "/"))

    exists = target.exists() or os.path.exists(path)
    if not exists:
        ms = (time.time() - t0) * 1000
        logger.warning("[VERIFY_FAIL] tool=%s method=path_exists path=%r ms=%.0f",
                       tool_name, path[:60], ms)
        return VerifyResult(
            tool_name=tool_name, verified=False,
            verification_method="path_exists", evidence=path[:80],
            error_type="path_missing", latency_ms=ms,
        )

    # Stronger, best-effort check: does Explorer's actually-focused folder
    # match the path we just asked it to open? Non-fatal if Explorer isn't
    # the foreground window right now (voice control doesn't force focus) —
    # path existence is still real evidence, just weaker than confirming
    # Explorer got there. What it must NOT do is fall back to any kind of
    # window-title substring match — that's the exact mistake being fixed.
    try:
        from api.services.explorer_context import explorer_context
        focused = explorer_context.get_focused_folder()
        if focused is not None:
            focused_str = str(focused).rstrip("/\\").lower()
            target_str  = str(target).rstrip("/\\").lower()
            ms = (time.time() - t0) * 1000
            if focused_str == target_str or focused_str.startswith(target_str + "/"):
                logger.info("[VERIFY2_SUCCESS] tool=%s method=explorer_path evidence=%r ms=%.0f",
                            tool_name, focused_str, ms)
                return VerifyResult(
                    tool_name=tool_name, verified=True,
                    verification_method="explorer_path", evidence=f"explorer_path={focused}",
                    latency_ms=ms,
                )
            logger.warning(
                "[VERIFY2_FAIL] tool=%s method=explorer_path expected=%r actual=%r ms=%.0f",
                tool_name, target_str, focused_str, ms,
            )
            return VerifyResult(
                tool_name=tool_name, verified=False,
                verification_method="explorer_path",
                evidence=f"Explorer shows {focused!r}, expected {path!r}",
                error_type="path_mismatch", latency_ms=ms,
            )
    except Exception:
        logger.debug("[VERIFY2_EXPLORER_PATH] check unavailable", exc_info=True)

    ms = (time.time() - t0) * 1000
    logger.info("[VERIFY_SUCCESS] tool=%s method=path_exists path=%r ms=%.0f",
                tool_name, path[:60], ms)
    return VerifyResult(
        tool_name=tool_name, verified=True,
        verification_method="path_exists", evidence=path[:80], latency_ms=ms,
    )


# ── Browser verification ──────────────────────────────────────────────────────

def _verify_browser(
    tool_name: str,
    params: dict,
    result_data: dict,
    t0: float,
) -> VerifyResult:
    """Check foreground window is a browser via Windows window query."""
    try:
        win_state = _get_windows_state()
        fg_proc   = win_state["fg_proc"]
        fg_title  = win_state["fg_title"]
        browsers  = {"chrome", "msedge", "firefox"}
        if fg_proc in browsers or any(b in fg_title.lower() for b in ["chrome", "edge", "firefox"]):
            ms = (time.time() - t0) * 1000
            evidence = f"browser={fg_proc} title={fg_title[:50]!r}"
            logger.info("[VERIFY2_SUCCESS] tool=%s method=browser_window evidence=%s ms=%.0f",
                        tool_name, evidence, ms)
            return VerifyResult(
                tool_name=tool_name, verified=True,
                verification_method="browser_page",
                evidence=evidence, latency_ms=ms,
            )
        # Browser not foreground — check process list
        if any(b in win_state["procs"] for b in browsers):
            ms = (time.time() - t0) * 1000
            logger.info("[VERIFY2_SUCCESS] tool=%s method=browser_process ms=%.0f", tool_name, ms)
            return VerifyResult(
                tool_name=tool_name, verified=True,
                verification_method="browser_process",
                evidence="browser running in background",
                latency_ms=ms,
            )
    except Exception as exc:
        logger.debug("[VERIFY2_BROWSER] error: %s", exc)

    return _v1_verify(tool_name, params, True, result_data)


# ── Store page verification ───────────────────────────────────────────────────

def _verify_store_page(
    tool_name: str,
    params: dict,
    result_data: dict,
    t0: float,
) -> VerifyResult:
    """After navigating to a Store page, verify via Windows foreground window title."""
    app_name = (params.get("app_name") or result_data.get("app_name") or "").strip()
    time.sleep(0.3)
    try:
        win_state = _get_windows_state()
        fg_title  = win_state["fg_title"].lower()
        fg_proc   = win_state["fg_proc"].lower()

        logger.info("[VERIFY2_WINDOWS_SHELL_CHECK] fg_title=%r fg_proc=%r", fg_title[:60], fg_proc)

        is_store = (
            "microsoft store" in fg_title
            or "windowsstore" in fg_proc
            or "winstore" in fg_proc
        )
        if is_store:
            if not app_name or app_name.lower() in fg_title:
                ms = (time.time() - t0) * 1000
                evidence = f"store_title={win_state['fg_title'][:60]!r}"
                logger.info("[VERIFY2_SUCCESS] tool=%s method=store_page evidence=%s ms=%.0f",
                            tool_name, evidence, ms)
                return VerifyResult(
                    tool_name=tool_name, verified=True,
                    verification_method="store_page",
                    evidence=evidence, latency_ms=ms,
                )
    except Exception as exc:
        logger.debug("[VERIFY2_STORE] error: %s", exc)

    return _v1_verify(tool_name, params, True, result_data)
