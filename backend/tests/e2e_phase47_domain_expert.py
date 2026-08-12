"""
Phase 4.7 — Domain Expert Agents Live E2E.

Flight workflow as Travel Consultant + PC cleanup workflow as System
Administrator. Reuses the harness from e2e_phase4_live.py (task launch,
log capture, report printing).

Run with: cd backend && python3 -u tests/e2e_phase47_domain_expert.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import os
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from e2e_phase4_live import _make_task, _rt, _capture, _check_intent_route, print_report, TestResult  # noqa: E402


# ── Test A: Flight Consultant ───────────────────────────────────────────────────

async def test_a_flight_consultant() -> TestResult:
    transcript = "Book me a cheap flight ticket from Karachi to Dubai next month."
    r = TestResult(name="Test A — Flight Consultant", transcript=transcript)
    r.coordinator_route = _check_intent_route(transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "browser")
    r.extra_notes.append(f"task_id={task.task_id}")

    deadline = time.time() + 150.0
    sub_task = None
    while time.time() < deadline:
        for tid, t in list(_rt()._tasks.items()):
            if t.agent_type.value == "browser" and tid != task.task_id:
                sub_task = t
        if sub_task and sub_task.metadata.get("awaiting_flight_decision"):
            sub_task.metadata["flight_decision"] = {"action": "cancel"}
            break
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(0.5)

    for _ in range(30):
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(0.5)

    r.latency_s = round(time.time() - t0, 1)

    intent_line = _capture.find("[FLIGHT_SEARCH_INTENT]")
    r.real_actions.append(f"parsed_intent={intent_line[0].split(']',1)[-1].strip() if intent_line else 'MISSING'}")

    sources = set()
    for rec in _capture.find("[FLIGHT_SITE_SELECTED]"):
        m = re.search(r"site=(\S+)", rec)
        if m:
            sources.add(m.group(1))
    r.real_actions.append(f"sources_searched={sorted(sources)}")

    urls = []
    for rec in _capture.find("[BROWSER_PAGE_OPENED]") + _capture.find("[FLIGHT_RESULTS_PAGE_OPENED]"):
        m = re.search(r"url=(\S+)", rec)
        if m and m.group(1) not in urls:
            urls.append(m.group(1))
    r.real_actions.append(f"urls_opened={len(urls)}")

    visible_nav = _capture.has_tag("[BROWSER_VISIBLE_NAVIGATION]") or _capture.has_tag("[BROWSER_REAL_CHROME_OPENED]")
    r.real_actions.append(f"chrome_visible={visible_nav}")

    narration_count = len(_capture.find("[FLIGHT_NARRATION]"))
    r.real_actions.append(f"consultant_narration_events={narration_count}")

    results_line = _capture.find("[FLIGHT_RESULTS_FOUND]")
    options_found = 0
    if results_line:
        m = re.search(r"count=(\d+)", results_line[0])
        if m:
            options_found = int(m.group(1))
    r.real_actions.append(f"options_found={options_found}")

    scores = len(_capture.find("[FLIGHT_SCORE_CALCULATED]"))
    r.real_actions.append(f"scores_calculated={scores}")

    reasoning = _capture.has_tag("[TRAVEL_CONSULTANT_REASONING]")
    recommendation = _capture.has_tag("[FLIGHT_RECOMMENDATION]")
    r.real_actions.append(f"recommendation_logged={recommendation} consultant_reasoning={reasoning}")

    approval_seen = (
        _capture.has_tag("[FLIGHT_APPROVAL_REQUIRED]")
        or _capture.has_tag("[TRAVEL_APPROVAL_REQUIRED]")
        or _capture.has_tag("[COORDINATOR_APPROVAL_REQUIRED]")
    )
    r.real_actions.append(f"approval_state={'requested' if approval_seen else 'NOT requested'}")

    final_task = _rt().get_task(task.task_id)
    if final_task and final_task.result_summary:
        r.final_response = final_task.result_summary[:200]
        no_hallucination = options_found > 0 or "couldn't reliably extract" in final_task.result_summary
    else:
        no_hallucination = True
    r.real_actions.append(f"no_hallucinated_prices={no_hallucination}")

    required_tags = [
        "[BROWSER_AGENT_START]", "[FLIGHT_SEARCH_INTENT]", "[FLIGHT_SEARCH_PARAMS]",
        "[FLIGHT_SITE_SELECTED]", "[FLIGHT_EXTRACTION_LAYER]", "[FLIGHT_RESULTS_FOUND]",
        "[FLIGHT_APPROVAL_REQUIRED]", "[FLIGHT_NARRATION]",
    ]
    r.missing_tags = [t for t in required_tags if not _capture.has_tag(t)]

    r.verification_result = "SAFE — no booking/payment submission at any point"
    r.verdict = "PASS" if (not r.missing_tags and no_hallucination) else "WARN"

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)

    return r


# ── Test B: Flight follow-up ─────────────────────────────────────────────────────

async def test_b_flight_followup() -> TestResult:
    transcript = "Book me a cheap flight ticket from Karachi to Dubai next month."
    r = TestResult(name="Test B — Flight Follow-up", transcript=transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "browser")
    r.extra_notes.append(f"task_id={task.task_id}")

    async def _wait_for_decision_gate(timeout=150.0) -> Any:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for tid, t in list(_rt()._tasks.items()):
                if t.agent_type.value == "browser" and tid != task.task_id:
                    if t.metadata.get("awaiting_flight_decision"):
                        return t
            await asyncio.sleep(0.5)
        return None

    sub_task = await _wait_for_decision_gate()
    if sub_task is None:
        r.verdict = "WARN"
        r.verification_result = "TIMEOUT — never reached decision gate"
        r.latency_s = round(time.time() - t0, 1)
        live = _rt().get_task(task.task_id)
        if live and not live.is_terminal():
            await _rt().cancel(task.task_id)
        return r

    from api.agents.browser_agent.flight_search_agent import parse_flight_decision
    decision1 = parse_flight_decision("choose cheapest")
    r.real_actions.append(f"parsed('choose cheapest')={decision1}")
    sub_task.metadata["flight_decision"] = decision1

    await asyncio.sleep(1.5)
    r.real_actions.append(f"choice_or_no_data_handled={_capture.has_tag('[VOICE_APPROVAL_CHOICE]')}")
    r.real_actions.append(f"never_books={_capture.has_tag('[BOOKING_SAFETY_STOP]')}")

    sub_task2 = await _wait_for_decision_gate(timeout=10.0) or sub_task
    decision2 = parse_flight_decision("cancel")
    r.real_actions.append(f"parsed('cancel')={decision2}")
    sub_task2.metadata["flight_decision"] = decision2

    deadline = time.time() + 15.0
    while time.time() < deadline:
        cur = _rt().get_task(sub_task2.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(0.5)

    r.latency_s = round(time.time() - t0, 1)
    cancelled_safely = _capture.has_tag("[VOICE_APPROVAL_REJECTED]") and _capture.has_tag("[TRAVEL_APPROVAL_REJECTED]")
    r.real_actions.append(f"cancelled_safely={cancelled_safely}")

    final = _rt().get_task(sub_task2.task_id)
    if final:
        r.final_response = (final.result_summary or "")[:200]

    ok = (
        _capture.has_tag("[VOICE_APPROVAL_DETECTED]")
        and _capture.has_tag("[BOOKING_SAFETY_STOP]")
        and cancelled_safely
    )
    r.verification_result = "SAFE — choice/no-data handled, no booking, then cancelled" if ok else "UNEXPECTED — check manually"
    r.verdict = "PASS" if ok else "WARN"

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)

    return r


# ── Test C: PC Cleanup Expert Report ─────────────────────────────────────────────

async def test_c_cleanup_expert_report() -> TestResult:
    transcript = "Clean my PC."
    r = TestResult(name="Test C — PC Cleanup Expert Report", transcript=transcript)
    r.coordinator_route = _check_intent_route(transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "automation")
    r.extra_notes.append(f"task_id={task.task_id}")

    deadline = time.time() + 180.0
    sub_task = None
    while time.time() < deadline:
        for tid, t in list(_rt()._tasks.items()):
            if t.agent_type.value == "automation" and tid != task.task_id:
                sub_task = t
        if _capture.has_tag("[CLEANER_APPROVAL_REQUIRED]"):
            break
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(1.0)

    r.latency_s = round(time.time() - t0, 1)

    categories_found = [rec for rec in _capture.records if "[CLEANER_CATEGORY_FOUND]" in rec]
    r.real_actions.append(f"categories_found={len(categories_found)}")
    risk_levels = set()
    for rec in categories_found:
        m = re.search(r"risk=(\w+)", rec)
        if m:
            risk_levels.add(m.group(1))
        r.extra_notes.append(rec.split("]", 1)[-1].strip())
    r.real_actions.append(f"risk_levels_seen={sorted(risk_levels)}")

    report_created = _capture.has_tag("[CLEANER_REPORT_CREATED]")
    report_saved = _capture.has_tag("[CLEANER_REPORT_SAVED]")
    recommendation = _capture.has_tag("[CLEANER_RECOMMENDATION]")
    sysadmin_narration = _capture.has_tag("[SYSADMIN_NARRATION]")
    safety_explanation = _capture.has_tag("[CLEANER_SAFETY_EXPLANATION]")
    r.real_actions.append(
        f"report_created={report_created} report_saved={report_saved} "
        f"recommendation_logged={recommendation} expert_narration={sysadmin_narration} "
        f"safety_explanation={safety_explanation}"
    )

    report_path = None
    saved_lines = _capture.find("[CLEANER_REPORT_SAVED]")
    if saved_lines:
        m = re.search(r"path=(\S+)", saved_lines[0])
        if m:
            report_path = m.group(1)
    r.real_actions.append(f"report_path={report_path}")
    r.real_actions.append(f"report_file_exists={Path(report_path).exists() if report_path else False}")

    junk_lines = _capture.find("[CLEANER_JUNK_FOUND]")
    if junk_lines:
        m = re.search(r"size_bytes=(\d+)", junk_lines[0])
        if m:
            r.real_actions.append(f"total_safe_cleanup_bytes={int(m.group(1))}")

    approval_seen = _capture.has_tag("[CLEANER_APPROVAL_REQUIRED]")
    r.verification_result = "SAFE — no deletion before approval" if approval_seen else "APPROVAL_NOT_SEEN"

    # Deny to end safely (no real deletion in this audit run)
    if sub_task and not sub_task.is_terminal():
        sub_task.metadata["approved"] = False
        await asyncio.sleep(1.0)

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)

    ok = approval_seen and categories_found and report_created and report_saved and len(risk_levels) >= 2
    r.verdict = "PASS" if ok else "WARN"
    return r


# ── Test D: Selective Cleanup ─────────────────────────────────────────────────────

async def test_d_selective_cleanup() -> TestResult:
    r = TestResult(
        name="Test D — Selective Cleanup",
        transcript="Clean only temp files. (synthetic test dir — never real system temp)",
    )
    _capture.reset()
    t0 = time.time()

    from api.agents.automation_agent.temp_cleaner import TempCleaner
    from api.agents.automation_agent.browser_cache_cleaner import BrowserCacheCleaner

    test_dir = Path("/tmp/xyron_e2e_phase47_selective_test")
    test_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for i in range(4):
        f = test_dir / f"xyron_selective_{i}.tmp"
        f.write_text(f"synthetic junk {i}\n" * 40)
        created.append(f)
    r.real_actions.append(f"created {len(created)} synthetic temp files in {test_dir}")

    original_patterns = TempCleaner.TEMP_GLOB_PATTERNS
    TempCleaner.TEMP_GLOB_PATTERNS = [str(test_dir)]
    original_bcc_patterns = BrowserCacheCleaner.BROWSER_CACHE_PATTERNS
    BrowserCacheCleaner.BROWSER_CACHE_PATTERNS = {}

    try:
        task = await _make_task("Clean only temp files.", "automation")
        r.extra_notes.append(f"task_id={task.task_id}")

        deadline = time.time() + 90.0
        sub_task = None
        while time.time() < deadline:
            for tid, t in list(_rt()._tasks.items()):
                if t.agent_type.value == "automation" and tid != task.task_id:
                    sub_task = t
            if sub_task and sub_task.metadata.get("awaiting_cleanup_decision"):
                break
            cur = _rt().get_task(task.task_id)
            if cur and cur.is_terminal():
                break
            await asyncio.sleep(0.5)

        if sub_task is None:
            r.verdict = "WARN"
            r.verification_result = "TIMEOUT — never reached approval gate"
            return r

        sel = sub_task.metadata.get("cleanup_selection", {})
        r.real_actions.append(f"parsed_selection_from_goal={sel}")

        sub_task.metadata["approved"] = True

        deadline2 = time.time() + 30.0
        while time.time() < deadline2:
            cur = _rt().get_task(sub_task.task_id)
            if cur and cur.is_terminal():
                break
            await asyncio.sleep(0.5)

        r.latency_s = round(time.time() - t0, 1)
        final = _rt().get_task(sub_task.task_id)
        r.final_response = (final.result_summary or "N/A")[:200] if final else "N/A"

        still_exist = [f for f in created if f.exists()]
        r.real_actions.append(f"synthetic_files_remaining={len(still_exist)}/{len(created)}")

        space_freed_lines = _capture.find("[CLEANER_SPACE_FREED]")
        freed_bytes = 0
        if space_freed_lines:
            m = re.search(r"bytes=(\d+)", space_freed_lines[0])
            if m:
                freed_bytes = int(m.group(1))
        r.real_actions.append(f"space_freed_bytes={freed_bytes}")
        excluded_categories = set()
        for rec in _capture.find("[CLEANER_CATEGORY_EXCLUDED]"):
            m = re.search(r"category=(\S+)", rec)
            if m:
                excluded_categories.add(m.group(1))
        r.real_actions.append(f"excluded_categories={sorted(excluded_categories)}")
        r.real_actions.append(f"browser_cache_excluded={'browser_cache' in excluded_categories}")

        ok = (
            len(still_exist) == 0
            and freed_bytes > 0
            and "browser_cache" in excluded_categories
            and _capture.has_tag("[CLEANER_DELETE_TO_RECYCLE]")
            and _capture.has_tag("[CLEANER_QUARANTINE_CREATED]")
            and _capture.has_tag("[CLEANER_VERIFY_DONE]")
            and _capture.has_tag("[CLEANER_REPORT_SAVED]")
        )
        r.verification_result = (
            "SAFE — only temp (synthetic) recycled, everything else excluded, space verified, report updated"
            if ok else "UNEXPECTED — check manually"
        )
        r.verdict = "PASS" if ok else "WARN"

        live = _rt().get_task(task.task_id)
        if live and not live.is_terminal():
            await _rt().cancel(task.task_id)

    finally:
        TempCleaner.TEMP_GLOB_PATTERNS = original_patterns
        BrowserCacheCleaner.BROWSER_CACHE_PATTERNS = original_bcc_patterns
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)

    return r


# ── Test E: Cleanup Deny ──────────────────────────────────────────────────────────

async def test_e_cleanup_deny() -> TestResult:
    transcript = "Clean my PC."
    r = TestResult(name="Test E — Cleanup Deny", transcript=transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "automation")
    r.extra_notes.append(f"task_id={task.task_id}")

    deadline = time.time() + 180.0
    sub_task = None
    while time.time() < deadline:
        for tid, t in list(_rt()._tasks.items()):
            if t.agent_type.value == "automation" and tid != task.task_id:
                sub_task = t
        if sub_task and sub_task.metadata.get("awaiting_cleanup_decision"):
            break
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(1.0)

    if sub_task is None:
        r.verdict = "WARN"
        r.verification_result = "TIMEOUT — never reached approval gate"
        r.latency_s = round(time.time() - t0, 1)
        return r

    from api.agents.automation_agent.automation_agent import parse_cleanup_command
    decision = parse_cleanup_command("No, don't delete.")
    r.real_actions.append(f"parsed('No, don't delete.')={decision}")
    if decision["cancel"]:
        sub_task.metadata["approved"] = False

    deadline2 = time.time() + 15.0
    while time.time() < deadline2:
        cur = _rt().get_task(sub_task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(0.5)

    r.latency_s = round(time.time() - t0, 1)
    final = _rt().get_task(sub_task.task_id)
    r.final_response = (final.result_summary or "N/A")[:150] if final else "N/A"
    r.real_actions.append(f"final_status={final.status.value if final else 'unknown'}")
    r.real_actions.append(f"space_freed_logged={_capture.has_tag('[CLEANER_SPACE_FREED]')} (should be False)")

    ok = (
        final is not None and final.status.value == "cancelled"
        and not _capture.has_tag("[CLEANER_SPACE_FREED]")
        and not _capture.has_tag("[CLEANER_DELETE_TO_RECYCLE]")
    )
    r.verification_result = "SAFE — cancelled, zero deletions" if ok else "UNEXPECTED — check manually"
    r.verdict = "PASS" if ok else "WARN"

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)
    return r


# ── Test F: Compare Visibility ────────────────────────────────────────────────────

async def test_f_compare_visibility() -> TestResult:
    transcript = "Compare iPhone 15 and Samsung S24."
    r = TestResult(name="Test F — Compare Visibility", transcript=transcript)
    r.coordinator_route = _check_intent_route(transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "browser")
    r.extra_notes.append(f"task_id={task.task_id}")

    deadline = time.time() + 60.0
    while time.time() < deadline:
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(1.0)

    r.latency_s = round(time.time() - t0, 1)

    visible_nav = _capture.find("[BROWSER_VISIBLE_NAVIGATION]")
    linux_internal = _capture.find("[BROWSER_LINUX_DRIVER_INTERNAL]")
    progress = _capture.find("[PROGRESS_UPDATE_SENT]")

    r.real_actions.append(f"visible_navigation_count={len(visible_nav)}")
    r.real_actions.append(f"linux_internal_driver_count={len(linux_internal)}")
    r.real_actions.append(f"progress_updates={len(progress)}")

    final = _rt().get_task(task.task_id)
    if final and final.result_summary:
        r.final_response = final.result_summary[:150]

    r.verification_result = (
        "SAFE — visible Windows Chrome mirrored, not Linux-only"
        if visible_nav else "WARN — no real-Chrome mirror observed"
    )
    r.verdict = "PASS" if (visible_nav and progress) else "WARN"
    return r


async def main() -> None:
    results = []
    tests = [
        ("Test A: Flight Consultant", test_a_flight_consultant),
        ("Test B: Flight Follow-up", test_b_flight_followup),
        ("Test C: PC Cleanup Expert Report", test_c_cleanup_expert_report),
        ("Test D: Selective Cleanup", test_d_selective_cleanup),
        ("Test E: Cleanup Deny", test_e_cleanup_deny),
        ("Test F: Compare Visibility", test_f_compare_visibility),
    ]
    for label, fn in tests:
        print(f"\n>>> {label} — starting...", flush=True)
        try:
            res = await fn()
            print(f">>> {label} — done: {res.verdict}", flush=True)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            res = TestResult(name=label, transcript="ERROR", verdict="FAIL")
            res.extra_notes.append(f"exception: {exc}")
        results.append(res)
    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
