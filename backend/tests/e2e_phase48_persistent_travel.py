"""
Phase 4.8 live E2E — persistent browser workspace + advanced travel
consultant, driven through the REAL WebSocket + synthesized-audio
pipeline (see ws_audio_harness.py), not task.metadata injection.

The actual work happens inside the separately-running uvicorn server
process (must already be up on --port 8001), so verification is done the
same way as the server's own logs would be read by an operator: by
tailing the log file for the required tags after each real voice turn.

Run with: cd backend && python3 -u tests/e2e_phase48_persistent_travel.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from ws_audio_harness import WSAudioClient  # noqa: E402

WS_URL = "ws://127.0.0.1:8001/api/v1/voice/ws/session"
LOG_PATH = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/uvicorn2.log")


def read_new_log(since_pos: int) -> tuple[str, int]:
    if not LOG_PATH.exists():
        return "", since_pos
    with open(LOG_PATH, "r", errors="ignore") as f:
        f.seek(since_pos)
        data = f.read()
        return data, f.tell()


def has_tag(text: str, tag: str) -> bool:
    return tag in text


def find_all(text: str, tag: str) -> list[str]:
    return [line for line in text.splitlines() if tag in line]


async def wait_for_tag(tag: str, pos: int, timeout: float = 90.0) -> tuple[bool, int, str]:
    """Poll the log file until *tag* appears or timeout. Returns (found, new_pos, all_new_text)."""
    deadline = time.time() + timeout
    accumulated = ""
    while time.time() < deadline:
        chunk, pos = read_new_log(pos)
        accumulated += chunk
        if tag in chunk:
            return True, pos, accumulated
        await asyncio.sleep(1.0)
    return False, pos, accumulated


async def main() -> None:
    results: dict[str, dict] = {}
    log_pos = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0

    client = WSAudioClient(WS_URL)
    await client.connect()
    print(">>> WS connected, greeting drained", flush=True)

    try:
        # ── TEST 1: single persistent flight tab ────────────────────────
        print("\n>>> TEST 1: initial flight search (real voice turn)", flush=True)
        t0 = time.time()
        turn1 = await client.voice_turn("Find me a flight from Karachi to Dubai next month", timeout=45.0)
        print(f"    turn1 final_text: {turn1['final_text']!r}", flush=True)

        found_session, log_pos, chunk1 = await wait_for_tag("[FLIGHT_SESSION_CREATED]", log_pos, timeout=120.0)
        found_workspace = has_tag(chunk1, "[BROWSER_WORKSPACE_CREATED]")
        found_control_mode = has_tag(chunk1, "[BROWSER_CONTROL_MODE]")
        tab_counts = [int(m.group(1)) for m in re.finditer(r"\[BROWSER_TAB_COUNT\] count=(\d+)", chunk1)]
        no_alt_tabs = not has_tag(chunk1, "[BROWSER_NEW_TAB_APPROVED]")
        no_google_mirror = "mirror_suppressed" in chunk1 or not has_tag(chunk1, "[BROWSER_REAL_CHROME_OPENED]")
        results["test1"] = {
            "latency_s": round(time.time() - t0, 1),
            "flight_session_created": found_session,
            "browser_workspace_created": found_workspace,
            "control_mode_logged": found_control_mode,
            "tab_counts_seen": tab_counts,
            "max_tab_count": max(tab_counts) if tab_counts else None,
            "no_alt_site_tabs": no_alt_tabs,
            "no_intermediate_google_tabs": no_google_mirror,
            "verdict": "PASS" if (found_session and found_workspace and tab_counts and max(tab_counts) == 1) else "WARN",
        }
        print(f"    {results['test1']}", flush=True)

        # ── TEST 2: same-tab follow-ups (separate real voice turns) ──────
        print("\n>>> TEST 2: same-tab follow-ups (4 real voice turns)", flush=True)
        followups = ["Check Emirates", "Show only direct flights", "Morning flights only", "Sort by cheapest"]
        followup_evidence = []
        for phrase in followups:
            t0 = time.time()
            turn = await client.voice_turn(phrase, timeout=45.0)
            await asyncio.sleep(2.0)
            _, log_pos, chunk = await wait_for_tag("[FLIGHT_FOLLOWUP_DETECTED]", log_pos, timeout=20.0)
            detected = has_tag(chunk, "[FLIGHT_FOLLOWUP_DETECTED]")
            reused = has_tag(chunk, "[BROWSER_WORKSPACE_REUSED]") or has_tag(chunk, "[BROWSER_ACTIVE_PAGE]")
            new_tabs = re.findall(r"\[BROWSER_TAB_COUNT\] count=(\d+)", chunk)
            control_result = "success" if "[FLIGHT_CONTROL_SUCCESS]" in chunk else (
                "failed_honestly" if "[FLIGHT_CONTROL_FAILED]" in chunk else "n/a")
            followup_evidence.append({
                "phrase": phrase, "final_text": turn["final_text"], "latency_s": round(time.time() - t0, 1),
                "followup_detected": detected, "workspace_reused": reused,
                "tab_counts": [int(x) for x in new_tabs], "control_result": control_result,
            })
            print(f"    {phrase!r} -> {followup_evidence[-1]}", flush=True)

        all_detected = all(e["followup_detected"] for e in followup_evidence)
        all_one_tab = all((not e["tab_counts"]) or max(e["tab_counts"]) == 1 for e in followup_evidence)
        results["test2"] = {
            "turns": followup_evidence,
            "verdict": "PASS" if (all_detected and all_one_tab) else "WARN",
        }

        # ── TEST 3: baggage ───────────────────────────────────────────────
        print("\n>>> TEST 3: baggage query + official-site approval", flush=True)
        t0 = time.time()
        turn_baggage = await client.voice_turn("Which flights include 20 kg baggage", timeout=45.0)
        _, log_pos, chunk_bag = await wait_for_tag("[BAGGAGE_CHECK_START]", log_pos, timeout=20.0)
        baggage_started = has_tag(chunk_bag, "[BAGGAGE_CHECK_START]")
        baggage_found = has_tag(chunk_bag, "[BAGGAGE_INFO_FOUND]")
        baggage_unavailable = has_tag(chunk_bag, "[BAGGAGE_INFO_UNAVAILABLE]")

        await asyncio.sleep(2.0)
        turn_official = await client.voice_turn("Yes, check the official airline site", timeout=45.0)
        _, log_pos, chunk_off = await wait_for_tag("[FLIGHT_FOLLOWUP_DETECTED]", log_pos, timeout=15.0)
        official_tab_approved = has_tag(chunk_off, "[BROWSER_NEW_TAB_APPROVED]") or has_tag(chunk_off, "[OFFICIAL_AIRLINE_CHECK_REQUIRED]")
        tab_reused_after = has_tag(chunk_off, "[BROWSER_TAB_REUSED]")

        results["test3"] = {
            "baggage_final_text": turn_baggage["final_text"],
            "official_final_text": turn_official["final_text"],
            "baggage_check_started": baggage_started,
            "baggage_found": baggage_found,
            "baggage_unavailable_honest": baggage_unavailable,
            "official_site_flow_triggered": official_tab_approved,
            "restored_to_one_tab_after": tab_reused_after,
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if baggage_started and (baggage_found or baggage_unavailable) else "WARN",
        }
        print(f"    {results['test3']}", flush=True)

        # ── TEST 4: recommendation ────────────────────────────────────────
        print("\n>>> TEST 4: recommendation", flush=True)
        t0 = time.time()
        await asyncio.sleep(2.0)
        turn_rec = await client.voice_turn("Which one do you recommend", timeout=45.0)
        _, log_pos, chunk_rec = await wait_for_tag("[FLIGHT_FOLLOWUP_DETECTED]", log_pos, timeout=15.0)
        results["test4"] = {
            "final_text": turn_rec["final_text"],
            "recommend_intent_detected": has_tag(chunk_rec, 'intent=recommend'),
            "no_booking_language": "book" not in turn_rec["final_text"].lower() or "won't" in turn_rec["final_text"].lower(),
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if has_tag(chunk_rec, 'intent=recommend') else "WARN",
        }
        print(f"    {results['test4']}", flush=True)

        # ── TEST 5: cancellation ──────────────────────────────────────────
        print("\n>>> TEST 5: cancel flight session", flush=True)
        t0 = time.time()
        await asyncio.sleep(2.0)
        turn_cancel = await client.voice_turn("Cancel", timeout=45.0)
        _, log_pos, chunk_cancel = await wait_for_tag("[VOICE_APPROVAL_CANCELLED]", log_pos, timeout=15.0)
        results["test5"] = {
            "final_text": turn_cancel["final_text"],
            "cancelled_logged": has_tag(chunk_cancel, "[VOICE_APPROVAL_CANCELLED]"),
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if has_tag(chunk_cancel, "[VOICE_APPROVAL_CANCELLED]") else "WARN",
        }
        print(f"    {results['test5']}", flush=True)

    finally:
        await client.close()

    # ── TEST 6 + 7: PC cleanup — separate connection ─────────────────────
    print("\n>>> TEST 6: PC cleanup report reconciliation (real voice turn)", flush=True)
    client2 = WSAudioClient(WS_URL)
    await client2.connect()
    try:
        t0 = time.time()
        turn_clean = await client2.voice_turn("Clean my PC", timeout=45.0)
        found_approval, log_pos, chunk6 = await wait_for_tag("[CLEANER_APPROVAL_REQUIRED]", log_pos, timeout=150.0)
        reconciled = has_tag(chunk6, "status=OK")
        json_saved = has_tag(chunk6, "[CLEANER_JSON_REPORT_SAVED]")
        md_saved = has_tag(chunk6, "[CLEANER_REPORT_SAVED]")
        results["test6"] = {
            "final_text": turn_clean["final_text"],
            "approval_requested": found_approval,
            "totals_reconciled": reconciled,
            "json_report_saved": json_saved,
            "md_report_saved": md_saved,
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if (found_approval and reconciled and json_saved and md_saved) else "WARN",
        }
        print(f"    {results['test6']}", flush=True)

        # ── TEST 7: selective follow-ups (deny to stay safe) ──────────────
        print("\n>>> TEST 7: selective cleanup follow-ups (real voice turns)", flush=True)
        t0 = time.time()
        turn_a = await client2.voice_turn("Don't touch Downloads", timeout=45.0)
        _, log_pos, chunk7a = await wait_for_tag("[CLEANER_CATEGORY_EXCLUDED]", log_pos, timeout=15.0)
        await asyncio.sleep(2.0)
        turn_b = await client2.voice_turn("No, don't delete", timeout=45.0)
        _, log_pos, chunk7b = await wait_for_tag("[CLEANER_CANCELLED]", log_pos, timeout=15.0)
        results["test7"] = {
            "downloads_excluded_ack": turn_a["final_text"],
            "exclude_logged": has_tag(chunk7a, "[CLEANER_CATEGORY_EXCLUDED]") or has_tag(chunk7a, "downloads"),
            "deny_ack": turn_b["final_text"],
            "cancelled_safely": has_tag(chunk7b, "[CLEANER_CANCELLED]"),
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if has_tag(chunk7b, "[CLEANER_CANCELLED]") else "WARN",
        }
        print(f"    {results['test7']}", flush=True)
    finally:
        await client2.close()

    print("\n" + "=" * 80)
    print("PHASE 4.8 LIVE E2E — REAL WEBSOCKET + AUDIO PIPELINE")
    print("=" * 80)
    passed = sum(1 for r in results.values() if r.get("verdict") == "PASS")
    for name, r in results.items():
        print(f"\n{name.upper()}: {r.get('verdict')}")
        for k, v in r.items():
            if k == "verdict":
                continue
            print(f"  {k}: {v}")
    print(f"\nSUMMARY: {passed} PASS / {len(results)} total")


if __name__ == "__main__":
    asyncio.run(main())
