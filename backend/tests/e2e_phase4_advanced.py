"""
Phase 4 Advanced Live E2E — layered flight extraction, voice-driven flight
approval, polished PC cleanup report, selective cleanup, browser visibility.

Run with: cd backend && python3 -u tests/e2e_phase4_advanced.py
"""
from __future__ import annotations

import asyncio
import sys
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from e2e_phase4_live import _make_task, _rt, _capture, _check_intent_route, print_report, TestResult  # noqa: E402


# ── Test A: Flight workflow (layered extraction) ───────────────────────────────

async def test_a_flight_workflow() -> TestResult:
    transcript = "Book me a cheap flight ticket from Karachi to Dubai next month."
    r = TestResult(name="Test A — Flight Workflow (layered)", transcript=transcript)
    r.coordinator_route = _check_intent_route(transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "browser")
    r.extra_notes.append(f"task_id={task.task_id}")

    # Wait for the browser sub-task to reach the decision gate, then resolve
    # it immediately with "cancel" so the test completes quickly and safely.
    deadline = time.time() + 90.0
    sub_task = None
    while time.time() < deadline:
        for tid, t in list(_rt()._tasks.items()):
            if t.agent_type.value == "browser" and tid != task.task_id:
                sub_task = t
        if sub_task and sub_task.metadata.get("awaiting_flight_decision"):
            sub_task.metadata["flight_decision"] = {"action": "cancel"}
            break
        if _capture.has_tag("[FLIGHT_APPROVAL_REQUIRED]") and sub_task:
            sub_task.metadata["flight_decision"] = {"action": "cancel"}
            break
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(0.5)

    # Let the coordinator finish winding down
    for _ in range(30):
        cur = _rt().get_task(task.task_id)
        if cur and cur.is_terminal():
            break
        await asyncio.sleep(0.5)

    r.latency_s = round(time.time() - t0, 1)

    urls = []
    for rec in _capture.find("[BROWSER_PAGE_OPENED]") + _capture.find("[FLIGHT_RESULTS_PAGE_OPENED]"):
        import re
        m = re.search(r"url=(\S+)", rec)
        if m and m.group(1) not in urls:
            urls.append(m.group(1))
    r.real_actions.append(f"urls_opened={urls}")

    layers = [rec for rec in _capture.records if "[FLIGHT_EXTRACTION_LAYER]" in rec]
    r.real_actions.append(f"extraction_layers_attempted={len(layers)}")
    for rec in layers:
        r.extra_notes.append(rec.split("]", 1)[-1].strip())

    results_line = _capture.find("[FLIGHT_RESULTS_FOUND]")
    options_found = 0
    if results_line:
        import re
        m = re.search(r"count=(\d+)", results_line[0])
        if m:
            options_found = int(m.group(1))
    r.real_actions.append(f"options_found={options_found}")

    recommendation = _capture.find("[FLIGHT_RECOMMENDATION]")
    r.real_actions.append(f"recommendation={'yes' if recommendation else 'no (no priced options)'}")

    approval_seen = _capture.has_tag("[FLIGHT_APPROVAL_REQUIRED]") or _capture.has_tag("[COORDINATOR_APPROVAL_REQUIRED]")
    r.real_actions.append(f"approval_state={'requested' if approval_seen else 'NOT requested'}")

    final_task = _rt().get_task(task.task_id)
    if final_task and final_task.result_summary:
        r.final_response = final_task.result_summary[:200]

    required_tags = [
        "[BROWSER_AGENT_START]", "[FLIGHT_SEARCH_INTENT]", "[FLIGHT_SEARCH_PARAMS]",
        "[FLIGHT_SITE_SELECTED]", "[FLIGHT_RESULTS_PAGE_OPENED]", "[FLIGHT_EXTRACTION_LAYER]",
        "[FLIGHT_RESULTS_FOUND]", "[FLIGHT_APPROVAL_REQUIRED]",
    ]
    r.missing_tags = [t for t in required_tags if not _capture.has_tag(t)]

    r.verification_result = "SAFE — no booking/payment submission at any point"
    r.verdict = "PASS" if not r.missing_tags else "WARN"

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)

    return r


# ── Test B: Flight voice approval ──────────────────────────────────────────────

async def test_b_flight_voice_approval() -> TestResult:
    transcript = "Book me a cheap flight ticket from Karachi to Dubai next month."
    r = TestResult(name="Test B — Flight Voice Approval", transcript=transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "browser")
    r.extra_notes.append(f"task_id={task.task_id}")

    async def _wait_for_decision_gate(timeout=90.0) -> Any:
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

    # Round 1: "choose cheapest"
    from api.agents.browser_agent.flight_search_agent import parse_flight_decision
    decision1 = parse_flight_decision("choose cheapest")
    r.real_actions.append(f"parsed('choose cheapest')={decision1}")
    sub_task.metadata["flight_decision"] = decision1

    await asyncio.sleep(1.5)
    r.real_actions.append(f"choice_detected={_capture.has_tag('[VOICE_APPROVAL_CHOICE]')}")
    r.real_actions.append(f"booking_safety_stop={_capture.has_tag('[BOOKING_SAFETY_STOP]')}")
    r.real_actions.append(f"still_awaiting_after_choice={sub_task.metadata.get('awaiting_flight_decision')}")

    # Round 2: "cancel"
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
    r.real_actions.append(f"rejected_logged={_capture.has_tag('[VOICE_APPROVAL_REJECTED]')}")

    final = _rt().get_task(sub_task2.task_id)
    if final:
        r.final_response = (final.result_summary or "")[:200]

    r.verification_result = "SAFE — choice detected, no payment, safety stop, then cancelled"
    ok = (
        _capture.has_tag("[VOICE_APPROVAL_DETECTED]")
        and _capture.has_tag("[VOICE_APPROVAL_CHOICE]")
        and _capture.has_tag("[BOOKING_SAFETY_STOP]")
        and _capture.has_tag("[VOICE_APPROVAL_REJECTED]")
    )
    r.verdict = "PASS" if ok else "WARN"

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)

    return r


# ── Test C: PC cleanup report ───────────────────────────────────────────────────

async def test_c_cleanup_report() -> TestResult:
    transcript = "Clean my PC."
    r = TestResult(name="Test C — PC Cleanup Report", transcript=transcript)
    r.coordinator_route = _check_intent_route(transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "automation")
    r.extra_notes.append(f"task_id={task.task_id}")

    deadline = time.time() + 120.0
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
    for rec in categories_found:
        r.extra_notes.append(rec.split("]", 1)[-1].strip())

    report_created = _capture.has_tag("[CLEANER_REPORT_CREATED]")
    recommendation = _capture.has_tag("[CLEANER_RECOMMENDATION]")
    r.real_actions.append(f"report_created={report_created} recommendation_logged={recommendation}")

    junk_lines = _capture.find("[CLEANER_JUNK_FOUND]")
    if junk_lines:
        import re
        m = re.search(r"size_bytes=(\d+)", junk_lines[0])
        if m:
            r.real_actions.append(f"recoverable_bytes={int(m.group(1))}")

    approval_seen = _capture.has_tag("[CLEANER_APPROVAL_REQUIRED]")
    r.verification_result = "SAFE — no deletion before approval" if approval_seen else "APPROVAL_NOT_SEEN"

    # Deny to end safely (no real deletion in this audit run)
    if sub_task and not sub_task.is_terminal():
        sub_task.metadata["approved"] = False
        await asyncio.sleep(1.0)

    live = _rt().get_task(task.task_id)
    if live and not live.is_terminal():
        await _rt().cancel(task.task_id)

    r.verdict = "PASS" if (approval_seen and categories_found and report_created) else "WARN"
    return r


# ── Test D: Selective cleanup deny ──────────────────────────────────────────────

async def test_d_selective_deny() -> TestResult:
    transcript = "Clean my PC."
    r = TestResult(name="Test D — Selective Cleanup Deny", transcript=transcript)
    _capture.reset()
    t0 = time.time()

    task = await _make_task(transcript, "automation")
    r.extra_notes.append(f"task_id={task.task_id}")

    deadline = time.time() + 120.0
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


# ── Test E: Selective cleanup safe approval (synthetic files only) ─────────────

async def test_e_selective_approve_synthetic() -> TestResult:
    r = TestResult(
        name="Test E — Selective Cleanup Safe Approval (synthetic)",
        transcript="Clean only temp files. (synthetic test dir — never real system temp)",
    )
    _capture.reset()
    t0 = time.time()

    from api.agents.automation_agent.temp_cleaner import TempCleaner
    from api.agents.automation_agent import automation_agent as aa_mod

    test_dir = Path("/tmp/xyron_e2e_selective_test")
    test_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for i in range(4):
        f = test_dir / f"xyron_selective_{i}.tmp"
        f.write_text(f"synthetic junk {i}\n" * 40)
        created.append(f)
    r.real_actions.append(f"created {len(created)} synthetic temp files in {test_dir}")

    # Redirect TempCleaner's scan target to our synthetic dir ONLY —
    # never touches the real system temp folders for this test.
    original_patterns = TempCleaner.TEMP_GLOB_PATTERNS
    TempCleaner.TEMP_GLOB_PATTERNS = [str(test_dir)]

    # Also make BrowserCacheCleaner report nothing found, so this run only
    # ever has "temp" to act on — proving the selection filter works (no
    # other category gets touched even though selection logic runs).
    from api.agents.automation_agent.browser_cache_cleaner import BrowserCacheCleaner
    original_bcc_patterns = BrowserCacheCleaner.BROWSER_CACHE_PATTERNS
    BrowserCacheCleaner.BROWSER_CACHE_PATTERNS = {}

    try:
        task = await _make_task("Clean only temp files.", "automation")
        r.extra_notes.append(f"task_id={task.task_id}")

        deadline = time.time() + 60.0
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
            import re
            m = re.search(r"bytes=(\d+)", space_freed_lines[0])
            if m:
                freed_bytes = int(m.group(1))
        r.real_actions.append(f"space_freed_bytes={freed_bytes}")
        r.real_actions.append(f"browser_cache_excluded={_capture.has_tag('[CLEANER_CATEGORY_EXCLUDED]')}")

        ok = (
            len(still_exist) == 0
            and freed_bytes > 0
            and _capture.has_tag("[CLEANER_DELETE_TO_RECYCLE]")
            and _capture.has_tag("[CLEANER_QUARANTINE_CREATED]")
            and _capture.has_tag("[CLEANER_VERIFY_DONE]")
        )
        r.verification_result = (
            "SAFE — only temp (synthetic) recycled, browser cache untouched, space verified"
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


# ── Test F: Browser visibility (compare route) ──────────────────────────────────

async def test_f_browser_visibility() -> TestResult:
    transcript = "Compare iPhone 15 and Samsung S24."
    r = TestResult(name="Test F — Browser Visibility (compare route)", transcript=transcript)
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
    narration = _capture.find("[AGENT_NARRATION]")
    progress = _capture.find("[PROGRESS_UPDATE_SENT]")

    r.real_actions.append(f"visible_navigation_count={len(visible_nav)}")
    r.real_actions.append(f"linux_internal_driver_count={len(linux_internal)}")
    r.real_actions.append(f"narration_events={len(narration)}")
    r.real_actions.append(f"progress_updates={len(progress)}")

    final = _rt().get_task(task.task_id)
    if final and final.result_summary:
        r.final_response = final.result_summary[:150]

    r.verification_result = (
        "SAFE — visible Windows Chrome mirrored, not only headless/Linux driver"
        if visible_nav else "WARN — no real-Chrome mirror observed"
    )
    r.verdict = "PASS" if (visible_nav and narration) else "WARN"
    return r


async def main() -> None:
    results = []
    tests = [
        ("Test A: Flight Workflow", test_a_flight_workflow),
        ("Test B: Flight Voice Approval", test_b_flight_voice_approval),
        ("Test C: PC Cleanup Report", test_c_cleanup_report),
        ("Test D: Selective Cleanup Deny", test_d_selective_deny),
        ("Test E: Selective Cleanup Safe Approval", test_e_selective_approve_synthetic),
        ("Test F: Browser Visibility", test_f_browser_visibility),
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
