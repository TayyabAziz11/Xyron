from __future__ import annotations

"""
AutomationAgent — main entry point for Phase 3.3 Automation / Cleaner Agent.

Routes user goals to the correct sub-module based on keyword detection.

Routing table:
  "clean pc / clear temp / junk / cache"       → TempCleaner + BrowserCacheCleaner
  "organize downloads / organize desktop"       → DownloadsOrganizer
  "find large files"                            → LargeFileFinder
  "find duplicates / duplicate files"           → DuplicateFinder
  "startup / boot"                              → StartupOptimizer
  default                                       → DiskAnalyzer summary + options

Log tags: [CLEANER_SCAN_START] [CLEANER_JUNK_FOUND] [CLEANER_APPROVAL_REQUIRED]
          [CLEANER_DELETE_TO_RECYCLE] [CLEANER_QUARANTINE_CREATED]
          [CLEANER_SPACE_FREED] [CLEANER_REPORT_CREATED]
"""

import asyncio
import glob
import logging
import re
import time
from pathlib import Path
from typing import Any

from api.agents.agent_types import AgentStatus, AgentTask, StepResult

logger = logging.getLogger("api.agents.automation_agent")

# ── Routing patterns ───────────────────────────────────────────────────────────

_CLEAN_PC_RE = re.compile(
    r"\b("
    r"clean\s*(?:pc|computer|system|my\s+(?:pc|computer))|"
    r"clean\s+(?:only\s+|just\s+)?(?:my\s+)?(?:temp(?:orary)?\s*files?|browser\s*cache|cache)|"
    r"clear\s*(?:temp|cache|junk)|"
    r"junk\s*files?|"
    r"free\s*(?:up\s*)?(?:disk\s*)?space|"
    r"clean\s*cache|"
    r"cleanup|clean\s+up"
    r")\b",
    re.IGNORECASE,
)

# ── Category selection (selective cleanup) ─────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, str] = {
    "temp": r"temp(?:orary)?(?:\s*files?)?",
    "browser_cache": r"browser\s*cache|(?<!temp\s)cache",
    "downloads": r"downloads?",
    "desktop": r"desktop",
}

# Dev-tool cache category keys (mirrors system_admin_agent.DeveloperCacheScanner)
# — duplicated as a small constant here to avoid importing the heavier
# module just for phrase parsing.
_DEV_CACHE_KEYS = {"npm_cache", "pnpm_cache", "pip_cache", "conda_cache", "vscode_cache", "visual_studio_cache"}
_SKIP_DEV_CACHES_RE = re.compile(r"skip\s+(?:developer|dev)\s+caches?", re.IGNORECASE)
_SHOW_DUPLICATES_RE = re.compile(r"show\s+duplicate\s+files?", re.IGNORECASE)
_QUERY_SAFE_RE = re.compile(r"what'?s?\s+(?:is\s+)?safe\s+to\s+delete", re.IGNORECASE)

# Voice phrases whose meaning is only defined while a cleanup task is
# actually waiting on a decision (task.metadata["awaiting_cleanup_decision"]).
_CANCEL_CLEANUP_RE = re.compile(
    r"\b(cancel(?:\s+clean(?:up)?)?|no,?\s*don'?t\s*delete|don'?t\s*delete|stop\s*clean(?:ing|up)?"
    r"|don'?t\s+do\s+it)\b"
    r"|^\s*no\s*$",
    re.IGNORECASE,
)
_APPROVE_CLEANUP_RE = re.compile(
    r"\b(yes|approve|go\s+ahead|do\s+it|proceed|clean\s+(?:them|it)|"
    r"move\s+(?:them|it)\s+to\s+(?:the\s+)?recycle\s*bin|"
    r"delete\s+only\s+safe\s+junk)\b"
    r"|^\s*continue\s*$",
    re.IGNORECASE,
)
_SHOW_LARGE_FIRST_RE = re.compile(r"show\s+large\s+files(?:\s+first)?", re.IGNORECASE)


def parse_cleanup_command(text: str) -> dict:
    """
    Parse a voice utterance for cleanup selection/approval/cancellation.
    Stateless — meaningful only while a cleanup task is actually pending.

    Returns {"approved": True|None, "cancel": bool,
             "include": set|None, "exclude": set|None}.
    """
    t = text.lower().strip().rstrip(".!? ")
    result: dict[str, Any] = {
        "approved": None, "cancel": False, "include": None, "exclude": None,
        "show_large": False, "show_duplicates": False, "query_safe": False,
    }

    if _SHOW_LARGE_FIRST_RE.search(t):
        result["show_large"] = True
        logger.info("[VOICE_APPROVAL_INTENT] target=cleanup intent=show_large")

    if _SHOW_DUPLICATES_RE.search(t):
        result["show_duplicates"] = True
        logger.info("[VOICE_APPROVAL_INTENT] target=cleanup intent=show_duplicates")

    if _QUERY_SAFE_RE.search(t):
        result["query_safe"] = True
        logger.info("[VOICE_APPROVAL_INTENT] target=cleanup intent=query_safe")
        logger.info("[CLEANER_SELECTIVE_COMMAND] command=query_safe text=%r", t[:60])
        return result

    if _CANCEL_CLEANUP_RE.search(t):
        result["cancel"] = True
        logger.info("[VOICE_APPROVAL_INTENT] target=cleanup intent=cancel")
        logger.info("[VOICE_APPROVAL_TARGET] target=cleanup")
        logger.info("[CLEANER_CANCELLED] reason=voice_command text=%r", t[:60])
        return result

    if _APPROVE_CLEANUP_RE.search(t):
        result["approved"] = True
        logger.info("[VOICE_APPROVAL_INTENT] target=cleanup intent=approve")
        logger.info("[VOICE_APPROVAL_TARGET] target=cleanup")

    include: set[str] = set()
    exclude: set[str] = set()

    # "only X" and "X only" (e.g. "clean temp and browser cache only") are
    # both valid — "only" doesn't always lead the target list.
    only_match = re.search(r"\bonly\s+(.+?)(?:$|[.,])", t) or re.search(r"^(.+?)\s+only\s*$", t)
    if only_match:
        segment = only_match.group(1)
        for cat, pat in _CATEGORY_KEYWORDS.items():
            if re.search(pat, segment):
                include.add(cat)

    for cat, pat in _CATEGORY_KEYWORDS.items():
        if re.search(rf"(?:don'?t\s*touch|skip|exclude|leave)\s+(?:the\s+)?(?:{pat})", t):
            exclude.add(cat)
        elif re.search(rf"(?:also\s+clean|clean.*\btoo\b|include)\s+(?:the\s+)?(?:{pat})", t):
            include.add(cat)

    if _SKIP_DEV_CACHES_RE.search(t):
        exclude |= _DEV_CACHE_KEYS
        logger.info("[CLEANER_SELECTIVE_COMMAND] command=skip_developer_caches categories=%s", sorted(_DEV_CACHE_KEYS))

    if include:
        result["include"] = include
        logger.info("[CLEANER_CATEGORY_SELECTED] categories=%s", sorted(include))
    if exclude:
        result["exclude"] = exclude
        logger.info("[CLEANER_CATEGORY_EXCLUDED] categories=%s", sorted(exclude))
    if include or exclude or result["show_large"] or result["show_duplicates"]:
        logger.info("[CLEANER_SELECTIVE_COMMAND] text=%r include=%s exclude=%s",
                     t[:60], sorted(include), sorted(exclude))
    if include or exclude:
        logger.info("[CLEANER_SELECTION_PARSED] text=%r include=%s exclude=%s",
                     t[:60], sorted(include), sorted(exclude))

    return result

_ORGANIZE_RE = re.compile(
    r"\b(organis?e\s*(?:downloads?|desktop|files?)|sort\s*(?:downloads?|files?))\b",
    re.IGNORECASE,
)

_LARGE_FILES_RE = re.compile(
    r"\b(large\s*files?|big\s*files?|biggest\s*files?|top\s*\d+\s*files?|heaviest\s*files?)\b",
    re.IGNORECASE,
)

_DUPLICATES_RE = re.compile(
    r"\b(duplicates?|duplicate\s*files?|find\s*dupes?|remove\s*duplicates?)\b",
    re.IGNORECASE,
)

_STARTUP_RE = re.compile(
    r"\b(startup\s*(?:apps?|programs?|items?)?|boot\s*(?:time|speed|apps?|optimization)?|slow\s*boot)\b",
    re.IGNORECASE,
)

_HEALTH_RE = re.compile(
    r"\b("
    r"system\s*health|health\s*report|pc\s*health|"
    r"ram\s*usage|memory\s*usage|how\s*much\s*ram|"
    r"gpu\s*usage|gpu\s*stats|graphics\s*card|"
    r"cpu\s*usage|processor\s*usage|"
    r"network\s*(?:status|speed|diagnostics?|check)|"
    r"internet\s*(?:speed|check|status)|"
    r"ping|latency|"
    r"full\s*report|system\s*report|system\s*stats?"
    r")\b",
    re.IGNORECASE,
)


# ── Utility ────────────────────────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    """Human-readable size without humanize dependency at module level."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


# ── Entry point ────────────────────────────────────────────────────────────────

async def run(
    task: AgentTask,
    runtime: Any,
    cancel_event: asyncio.Event,
    pause_event: asyncio.Event,
) -> str:
    """
    Main entry point called by AgentRuntime.

    Routes to the correct sub-module, streams progress via task.ws_send_fn,
    and returns a human-readable summary string.
    """
    goal = task.goal
    task.status = AgentStatus.RUNNING
    task.started_at = time.time()

    logger.info("[CLEANER_SCAN_START] task_id=%s goal=%r", task.task_id, goal)

    async def _send(payload: dict) -> None:
        if payload.get("type") in ("progress", "approval_required", "result"):
            logger.info(
                "[PROGRESS_UPDATE_SENT] task=%s type=%s message=%r",
                task.task_id, payload.get("type"), str(payload.get("message") or payload.get("summary"))[:80],
            )
        if task.ws_send_fn is not None:
            try:
                await task.ws_send_fn(payload)
            except Exception as exc:
                logger.debug("[AUTOMATION_AGENT] ws_send failed: %r", exc)

    try:
        if cancel_event.is_set():
            task.status = AgentStatus.CANCELLED
            return "Task cancelled before starting."

        from api.agents.personality.personality_engine import personality_engine

        await _send({"type": "progress", "message": personality_engine.narrate_step("automation.analyzing"), "pct": 3})

        # ── Route ──────────────────────────────────────────────────────────────
        if _HEALTH_RE.search(goal):
            result = await _run_system_health(task, cancel_event, _send, personality_engine)
        elif _CLEAN_PC_RE.search(goal):
            result = await _run_pc_clean(task, cancel_event, _send, personality_engine)
        elif _ORGANIZE_RE.search(goal):
            result = await _run_organizer(task, cancel_event, _send)
        elif _LARGE_FILES_RE.search(goal):
            result = await _run_large_file_finder(task, cancel_event, _send)
        elif _DUPLICATES_RE.search(goal):
            result = await _run_duplicate_finder(task, cancel_event, _send)
        elif _STARTUP_RE.search(goal):
            result = await _run_startup_optimizer(task, cancel_event, _send)
        else:
            result = await _run_disk_analysis(task, cancel_event, _send)

        # Only mark completed if we didn't transition to WAITING_APPROVAL
        if task.status not in (AgentStatus.WAITING_APPROVAL, AgentStatus.CANCELLED, AgentStatus.FAILED):
            task.status = AgentStatus.COMPLETED

        task.result_summary = result
        task.completed_at = time.time()
        return result

    except asyncio.CancelledError:
        task.status = AgentStatus.CANCELLED
        return "Automation task cancelled."
    except Exception as exc:
        logger.exception("[AUTOMATION_AGENT] Unhandled error task_id=%s", task.task_id)
        task.status = AgentStatus.FAILED
        task.error_message = str(exc)
        return f"Automation task failed: {exc}"


# ── Sub-module runners ─────────────────────────────────────────────────────────

async def _run_system_health(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
    personality_engine,
) -> str:
    """Run full system health diagnostics (RAM, CPU, GPU, disk, network)."""
    from api.agents.automation_agent.system_health_agent import SystemHealthAgent

    await _send({"type": "progress", "message": personality_engine.narrate_step("automation.analyzing"), "pct": 10})

    agent = SystemHealthAgent()

    await _send({"type": "progress", "message": "Checking RAM usage…", "pct": 20})
    report = await agent.full_report()

    if cancel_event.is_set():
        task.status = AgentStatus.CANCELLED
        return "Cancelled during health analysis."

    text = report.get("text", "Health report unavailable.")
    await _send({"type": "result", "message": text, "report": report})
    return text


async def _run_pc_clean(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
    personality_engine=None,
) -> str:
    """
    System-administrator-style deep scan: temp, browser caches, Windows
    Update/Delivery Optimization/thumbnail/shader/NVIDIA caches, crash
    dumps, log files, Recycle Bin, dev-tool caches (npm/pnpm/pip/conda/
    VS Code/Visual Studio/Docker/WSL), downloads/desktop/large-files/
    duplicates for awareness, plus fixed protected rows (source code,
    documents, photos/videos) that are never touched.

    Every category is risk-classified (safe/review/risky/protected).
    Only "safe" categories are ever recycled, and only after approval —
    selection ("clean only temp files", "don't touch browser cache") can
    come from the original command or a follow-up voice command while
    waiting. A polished Markdown report is saved to
    ~/.xyron/reports/cleanup/YYYY-MM-DD-cleanup-report.md.

    Nothing is deleted or moved before approval. Denial/timeout/cancel
    all end safely with zero side effects.
    """
    from api.agents.automation_agent.temp_cleaner import TempCleaner
    from api.agents.automation_agent.browser_cache_cleaner import BrowserCacheCleaner
    from api.agents.automation_agent.disk_analyzer import DiskAnalyzer
    from api.agents.automation_agent.large_file_finder import LargeFileFinder
    from api.agents.automation_agent.duplicate_finder import DuplicateFinder
    from api.agents.automation_agent import system_admin_agent as sa
    from pathlib import Path as _Path
    import glob as _glob

    scan_msg = "I'm analysing your storage now."
    logger.info("[SYSADMIN_NARRATION] text=%r", scan_msg)
    await _send({"type": "progress", "message": scan_msg, "pct": 10})

    # Selection may already be specified in the original command itself
    # (e.g. "Clean only temp files").
    initial_sel = parse_cleanup_command(task.goal)
    task.metadata["cleanup_selection"] = {
        "include": initial_sel["include"], "exclude": initial_sel["exclude"],
    }

    tc = TempCleaner()
    bcc = BrowserCacheCleaner()
    analyzer = DiskAnalyzer()

    async def _scan_large_files() -> list[dict]:
        large: list[dict] = []
        try:
            finder = LargeFileFinder()
            scan_dirs = [_Path(p) for p in _glob.glob("/mnt/c/Users/*/Downloads")
                         + _glob.glob("/mnt/c/Users/*/Desktop") if _Path(p).is_dir()]
            for d in scan_dirs[:2]:
                large.extend(await finder.find_large(d, min_size_mb=100, top_n=10))
        except Exception as exc:
            logger.debug("[CLEANER_LARGE_FILES] scan skipped: %r", exc)
        return large

    async def _scan_duplicates() -> list[list]:
        try:
            dup_finder = DuplicateFinder()
            dl_dirs = [_Path(p) for p in _glob.glob("/mnt/c/Users/*/Downloads") if _Path(p).is_dir()]
            if dl_dirs:
                return await dup_finder.find_duplicates(dl_dirs[0])
        except Exception as exc:
            logger.debug("[CLEANER_DUPLICATES] scan skipped: %r", exc)
        return []

    # All independent read-only I/O — run concurrently. Sequential awaits
    # here (temp -> browser -> full disk analysis -> large files ->
    # duplicates, each with its own directory walk) were the main reason
    # "Clean my PC" got slow once Phase 4.7/4.8 added more categories.
    temp_scan, browser_scan, disk_report, large_files, duplicate_groups = await asyncio.gather(
        tc.scan_temp(), bcc.scan(), analyzer.analyze(), _scan_large_files(), _scan_duplicates(),
    )
    categories = dict(disk_report.get("categories", {}))
    task.metadata["large_files_list"] = large_files

    if cancel_event.is_set():
        task.status = AgentStatus.CANCELLED
        return "Cancelled during deep scan."

    # ── Deep system-administrator scan (Phase 4.7) ──────────────────────
    deep_msg = "I'm checking system, browser, and developer caches across your drive."
    logger.info("[SYSADMIN_NARRATION] text=%r", deep_msg)
    await _send({"type": "progress", "message": deep_msg, "pct": 30})

    # These scans are all independent I/O (each already runs its blocking
    # walk in a thread-pool executor) — run them concurrently instead of
    # sequentially, since one-at-a-time was pushing "Clean my PC" latency
    # well past what's reasonable for a voice interaction.
    deep_analyzer = sa.DeepDiskAnalyzer()
    system_rows, recycle_bin_row, wsl_row, dev_rows, docker_row, active_project = await asyncio.gather(
        deep_analyzer.scan_system_categories(),
        deep_analyzer.scan_recycle_bin(),
        deep_analyzer.scan_wsl_categories(),
        sa.DeveloperCacheScanner.scan(),
        sa.DeveloperCacheScanner.scan_docker(),
        sa.check_active_project(),
    )

    # Never classify active-development caches as safe without checking
    # whether a dev tool is actually running — if VS Code is currently
    # open, bump these from "review" to "protected" for this scan.
    if active_project:
        for r in dev_rows:
            r["risk"] = "protected"
            r["explanation"] = "VS Code is currently running — protecting active project caches this session."

    # ── Build the unified, risk-labeled row set ─────────────────────────
    browser_total = sum(v.get("size_bytes", 0) for v in browser_scan.values())
    temp_bytes = temp_scan.get("total_bytes", 0)

    rows: list[dict] = [
        {"key": "temp", "label": "Temp files", "risk": "safe",
         "size_bytes": temp_bytes, "count": temp_scan.get("count", 0),
         "paths": temp_scan.get("files", []),
         "explanation": "Temp files are safe because Windows recreates them automatically."},
        {"key": "browser_cache", "label": "Browser cache", "risk": "safe",
         "size_bytes": browser_total, "count": len(browser_scan), "paths": [],
         "explanation": "Browser cache is safe — your browser rebuilds it automatically."},
    ]
    rows.extend(system_rows)
    rows.append(recycle_bin_row)
    rows.append(wsl_row)
    rows.extend(dev_rows)
    if docker_row:
        rows.append(docker_row)

    dl = categories.get("downloads", {})
    rows.append({"key": "downloads", "label": "Downloads", "risk": "review",
                 "size_bytes": dl.get("size", 0), "count": dl.get("count", 0), "paths": [],
                 "explanation": "I'm leaving Downloads untouched because it may contain personal files."})
    dt = categories.get("desktop", {})
    rows.append({"key": "desktop", "label": "Desktop", "risk": "review",
                 "size_bytes": dt.get("size", 0), "count": dt.get("count", 0), "paths": [],
                 "explanation": "I'm leaving Desktop items untouched because it may contain personal files."})
    if large_files:
        lf_bytes = sum(f.get("size_bytes", 0) for f in large_files)
        rows.append({"key": "large_files", "label": "Large files", "risk": "review",
                     "size_bytes": lf_bytes, "count": len(large_files), "paths": [],
                     "explanation": "Large files need a manual look before anything is done with them."})
    if duplicate_groups:
        dup_count = sum(len(g) for g in duplicate_groups)
        rows.append({"key": "duplicates", "label": "Duplicates", "risk": "review",
                     "size_bytes": 0, "count": dup_count, "paths": [],
                     "explanation": "Duplicate files need manual confirmation before removing any copy."})
    rows.extend(sa.PROTECTED_ROWS)

    rows_by_key = {r["key"]: r for r in rows}
    totals = sa.reconcile_totals(rows)
    safe_total = totals["safe_bytes"]

    for r in rows:
        if r["risk"] != "protected" and (r["size_bytes"] or r["count"]):
            logger.info(
                "[CLEANER_CATEGORY_FOUND] category=%s size_bytes=%d desc=%r risk=%s",
                r["key"], r["size_bytes"], f"{r['count']:,} item(s)", r["risk"],
            )

    logger.info("[CLEANER_JUNK_FOUND] size_bytes=%d count=%d", safe_total, temp_scan.get("count", 0))

    # ── Safety explanations + recommendation + report ───────────────────
    safety_lines = sa.CleanupSafetyExplainer.explain(rows)
    recommendation, first_action = sa.HealthRecommendationEngine.recommend(rows)

    # Dedup-by-key view (rows_by_key.values()) is the canonical source for
    # both the table and the totals — the same reconciled data reconcile_totals()
    # already validated against the raw list, so what's displayed always
    # matches what's declared.
    dedup_rows = list(rows_by_key.values())
    table = sa.build_report_table(dedup_rows)
    full_report_text = (
        f"{table}\n\n"
        f"Total safe cleanup: {_fmt_size(totals['safe_bytes'])}\n"
        f"Total review cleanup: {_fmt_size(totals['review_bytes'])}\n"
        f"Total risky/protected: {_fmt_size(totals['risky_protected_bytes'])}\n"
        f"Recommended first action: {first_action}\n"
    )
    report_path = sa.save_report(full_report_text, task.task_id)
    json_report_path = sa.save_json_report(dedup_rows, totals, task.task_id)
    logger.info("[CLEANER_REPORT_CREATED] task_id=%s recoverable_bytes=%d", task.task_id, safe_total)

    # Store for mid-wait voice queries ("show duplicate files", "what is
    # safe to delete") handled in voice_ws.py's Tier 0f3.
    task.metadata["duplicate_groups_list"] = duplicate_groups
    task.metadata["safe_categories_summary"] = ", ".join(
        r["label"] for r in dedup_rows if r["risk"] == "safe" and (r["size_bytes"] or r["count"])
    ) or "nothing significant right now"

    await _send({
        "type": "result",
        "message": full_report_text,
        "report": full_report_text,
        "report_path": str(report_path) if report_path else None,
        "json_report_path": str(json_report_path) if json_report_path else None,
    })

    found_msg = (
        personality_engine.narrate_step(
            "automation.found_junk",
            {"size": _fmt_size(safe_total), "count": temp_scan.get("count", 0)},
        )
        if personality_engine
        else f"I found {_fmt_size(safe_total)} of junk files."
    )
    no_delete_msg = "I'm not touching personal folders without your approval."
    logger.info("[AGENT_NARRATION] step=automation.no_delete text=%r", no_delete_msg)
    for line in safety_lines[:4]:
        logger.info("[SYSADMIN_NARRATION] text=%r", line)

    def _scope_desc() -> str:
        sel = task.metadata.get("cleanup_selection", {})
        include = sel.get("include")
        exclude = sel.get("exclude") or set()
        plan = sa.CleanupPlanBuilder.build(rows_by_key, include, exclude)
        cats = plan["selected"]
        if not cats:
            return "nothing (all excluded)"
        return " and ".join(rows_by_key[c]["label"].lower() for c in sorted(cats) if c in rows_by_key)

    logger.info("[CLEANER_APPROVAL_REQUIRED] action=clean_pc size=%s scope=%s", _fmt_size(safe_total), _scope_desc())
    task.metadata["approved"] = None  # tri-state: None=pending, True=approved, False=denied
    task.metadata["awaiting_cleanup_decision"] = True
    await _send({
        "type": "approval_required",
        "task_id": task.task_id,
        "action": "clean_pc",
        "summary": (
            f"{found_msg} {no_delete_msg} "
            f"I'd clean {_scope_desc()}. Should I clean these files?"
        ),
        "details": {
            "temp": temp_scan,
            "browser": browser_scan,
            "categories": categories,
            "large_files_count": len(large_files),
            "duplicate_groups": len(duplicate_groups),
            "estimated_recoverable_bytes": safe_total,
            "report": full_report_text,
            "report_path": str(report_path) if report_path else None,
        },
    })

    task.status = AgentStatus.WAITING_APPROVAL

    # Block here (do not return) so a real approve/deny/selection can resume
    # this same coroutine — previously this returned immediately, and
    # WAITING_APPROVAL was a dead end since the coroutine had already
    # finished by the time any decision arrived.
    waited = 0.0
    while waited < 180.0:
        if cancel_event.is_set():
            task.metadata["awaiting_cleanup_decision"] = False
            task.status = AgentStatus.CANCELLED
            logger.info("[CLEANER_CANCELLED] reason=cancel_event")
            return "Cleanup cancelled."
        approved = task.metadata.get("approved")
        if approved is True:
            break
        if approved is False:
            task.metadata["awaiting_cleanup_decision"] = False
            task.status = AgentStatus.CANCELLED
            logger.info("[SAFETY_BLOCKED_ACTION] action=clean_pc reason=user_denied")
            return f"Cleanup cancelled — no files were deleted ({_fmt_size(safe_total)} left untouched)."
        await asyncio.sleep(0.5)
        waited += 0.5
    else:
        task.metadata["awaiting_cleanup_decision"] = False
        task.status = AgentStatus.CANCELLED
        return "Approval timed out — no files were deleted."

    task.metadata["awaiting_cleanup_decision"] = False

    # ── Approved: actually recycle only the selected (always safe) rows ────
    sel = task.metadata.get("cleanup_selection", {})
    include = sel.get("include")
    exclude = sel.get("exclude") or set()
    plan = sa.CleanupPlanBuilder.build(rows_by_key, include, exclude)
    selected = plan["selected"]

    task.status = AgentStatus.RUNNING
    clean_msg = f"Approved. Moving {_scope_desc()} to the Recycle Bin now."
    logger.info("[AGENT_NARRATION] step=automation.cleaning text=%r", clean_msg)
    await _send({"type": "progress", "message": clean_msg, "pct": 80})

    freed_bytes = 0
    recycled = 0
    space_before = safe_total

    if "temp" in selected:
        temp_paths = [_Path(p) for p in temp_scan.get("files", [])]
        temp_result = await tc.clean_temp(temp_paths, task)
        if temp_result.data:
            freed_bytes += temp_result.data.get("freed_bytes", 0)
            recycled += temp_result.data.get("recycled", 0)
        logger.info("[CLEANER_QUARANTINE_CREATED] category=temp destination=recycle_bin")
    else:
        logger.info("[CLEANER_CATEGORY_EXCLUDED] category=temp reason=not_selected")

    if "browser_cache" in selected:
        for browser in browser_scan:
            cache_result = await bcc.execute_clean(browser)
            if cache_result.data:
                freed_bytes += cache_result.data.get("freed_bytes", 0)
                recycled += cache_result.data.get("recycled_dirs", 0)
        logger.info("[CLEANER_QUARANTINE_CREATED] category=browser_cache destination=recycle_bin")
    else:
        logger.info("[CLEANER_CATEGORY_EXCLUDED] category=browser_cache reason=not_selected")

    for key in sorted(selected - {"temp", "browser_cache"}):
        row = rows_by_key.get(key)
        if not row or not row.get("paths"):
            continue
        result = await sa.recycle_category(row)
        freed_bytes += result.get("freed_bytes", 0)
        recycled += result.get("recycled", 0)
        logger.info("[CLEANER_QUARANTINE_CREATED] category=%s destination=recycle_bin", key)

    for key in sorted({k for k, r in rows_by_key.items() if r["risk"] == "safe"} - selected):
        logger.info("[CLEANER_CATEGORY_EXCLUDED] category=%s reason=not_selected", key)

    logger.info("[CLEANER_SPACE_FREED] bytes=%d task_id=%s", freed_bytes, task.task_id)
    logger.info(
        "[CLEANER_VERIFY_DONE] freed_bytes=%d expected_bytes=%d recycled=%d",
        freed_bytes, space_before, recycled,
    )

    followups = sa.CleanupFollowupPlanner.suggestions(rows_by_key)
    report = (
        f"Done. Recycled {recycled:,} item(s) and freed {_fmt_size(freed_bytes)} "
        f"(estimated {_fmt_size(space_before)} before cleaning). {followups[0]}"
    )
    await _send({
        "type": "result",
        "message": report,
        "freed_bytes": freed_bytes,
        "recycled": recycled,
        "followup_suggestions": followups,
    })
    return report


async def _run_organizer(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
) -> str:
    """Run DownloadsOrganizer."""
    from api.agents.automation_agent.downloads_organizer import DownloadsOrganizer

    await _send({"type": "progress", "message": "Scanning Downloads folder…", "pct": 10})

    organizer = DownloadsOrganizer()
    scan = await organizer.scan_downloads()
    if cancel_event.is_set():
        task.status = AgentStatus.CANCELLED
        return "Cancelled during downloads scan."

    total = sum(len(v) for v in scan.values())
    downloads_path = organizer.get_downloads_path()

    await _send({
        "type": "progress",
        "message": f"Found {total} files to organize. Requesting approval…",
        "pct": 40,
    })

    result = await organizer.organize(downloads_path, task)
    return result.output


async def _run_large_file_finder(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
) -> str:
    """Run LargeFileFinder across Windows user directories."""
    from api.agents.automation_agent.large_file_finder import LargeFileFinder

    await _send({"type": "progress", "message": "Scanning for large files…", "pct": 5})

    finder = LargeFileFinder()

    # Collect candidate directories
    scan_dirs: list[Path] = []
    for pattern in ["/mnt/c/Users/*/Downloads", "/mnt/c/Users/*/Documents", "/mnt/c/Users/*/Desktop"]:
        scan_dirs.extend(Path(p) for p in glob.glob(pattern) if Path(p).is_dir())
    if not scan_dirs:
        scan_dirs = [Path.home()]

    all_results: list[dict] = []
    for idx, d in enumerate(scan_dirs):
        if cancel_event.is_set():
            break
        pct = 10 + int(idx / len(scan_dirs) * 80)
        await _send({"type": "progress", "message": f"Scanning {d.name}…", "pct": pct})
        results = await finder.find_large(d, min_size_mb=100, top_n=20)
        all_results.extend(results)

    # Deduplicate by path, sort by size
    seen: set[str] = set()
    unique: list[dict] = []
    for r in all_results:
        if r["path"] not in seen:
            seen.add(r["path"])
            unique.append(r)
    unique.sort(key=lambda x: x["size_bytes"], reverse=True)
    top20 = unique[:20]

    if not top20:
        return "No large files (>100 MB) found in the scanned directories."

    lines = [f"Top {len(top20)} large files found:\n"]
    for item in top20:
        lines.append(f"  {item['size_human']:>10}  {item['path']}")

    summary = "\n".join(lines)
    await _send({"type": "result", "message": summary})
    return summary


async def _run_duplicate_finder(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
) -> str:
    """Run DuplicateFinder on the Downloads folder."""
    from api.agents.automation_agent.duplicate_finder import DuplicateFinder

    await _send({"type": "progress", "message": "Scanning for duplicate files…", "pct": 5})

    finder = DuplicateFinder()

    scan_dirs: list[Path] = []
    for pattern in ["/mnt/c/Users/*/Downloads"]:
        scan_dirs.extend(Path(p) for p in glob.glob(pattern) if Path(p).is_dir())
    if not scan_dirs:
        scan_dirs = [Path.home() / "Downloads"]

    all_duplicates: list[list[Path]] = []
    for idx, d in enumerate(scan_dirs):
        if cancel_event.is_set():
            break
        pct = 10 + int(idx / len(scan_dirs) * 80)
        await _send({"type": "progress", "message": f"Hashing files in {d.name}…", "pct": pct})
        dups = await finder.find_duplicates(d)
        all_duplicates.extend(dups)

    report = finder.format_report(all_duplicates)
    await _send({"type": "result", "message": report})
    return report


async def _run_startup_optimizer(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
) -> str:
    """Run StartupOptimizer — list Windows startup items."""
    from api.agents.automation_agent.startup_optimizer import StartupOptimizer

    await _send({"type": "progress", "message": "Reading Windows startup items…", "pct": 10})

    opt = StartupOptimizer()
    apps = await opt.list_startup_apps()

    if cancel_event.is_set():
        task.status = AgentStatus.CANCELLED
        return "Cancelled during startup scan."

    if not apps:
        return "No startup apps found, or PowerShell is not available in this environment."

    lines = [f"Found {len(apps)} startup item(s):\n"]
    for app in apps:
        status_tag = "ON " if app.get("enabled", True) else "OFF"
        delay_ms = app.get("delay_ms", 0)
        lines.append(
            f"  [{status_tag}] {app.get('name', '?'):<30}  ~{delay_ms} ms  —  {app.get('path', '')[:60]}"
        )

    total_ms = sum(a.get("delay_ms", 0) for a in apps if a.get("enabled", True))
    lines.append(f"\nEstimated total startup delay: {total_ms:,} ms ({total_ms // 1000}s)")
    lines.append("\nSay 'disable [app name] from startup' to remove an item.")

    summary = "\n".join(lines)
    await _send({"type": "result", "message": summary, "apps": apps})
    return summary


async def _run_disk_analysis(
    task: AgentTask,
    cancel_event: asyncio.Event,
    _send,
) -> str:
    """Default path: full DiskAnalyzer scan, then present action options."""
    from api.agents.automation_agent.disk_analyzer import DiskAnalyzer

    await _send({"type": "progress", "message": "Analyzing disk usage across your system…", "pct": 5})

    analyzer = DiskAnalyzer()
    analysis = await analyzer.analyze()

    if cancel_event.is_set():
        task.status = AgentStatus.CANCELLED
        return "Cancelled during disk analysis."

    recoverable = analysis.get("estimated_recoverable_bytes", 0)
    report = analysis.get("report", "Disk analysis complete.")

    logger.info(
        "[CLEANER_REPORT_CREATED] task_id=%s recoverable_bytes=%d",
        task.task_id,
        recoverable,
    )

    await _send({
        "type": "result",
        "message": report,
        "options": [
            {"label": "Clean temp files and browser caches", "action": "clean_pc"},
            {"label": "Organize Downloads folder",           "action": "organize_downloads"},
            {"label": "Find large files (>100 MB)",          "action": "large_files"},
            {"label": "Find duplicate files",                "action": "duplicates"},
            {"label": "Manage startup apps",                 "action": "startup"},
        ],
    })
    return report
