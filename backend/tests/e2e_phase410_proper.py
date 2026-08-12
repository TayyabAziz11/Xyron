"""
Phase 4.10 — proper, careful live test through the real voice WebSocket
pipeline against real Windows Chrome. Fresh connection per turn (most
reliable pattern found across prior runs), generous timeouts, settle
delays, and full screenshot/log evidence capture.

Run with: cd backend && python3 -u tests/e2e_phase410_proper.py
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
from playwright.async_api import async_playwright  # noqa: E402

WS_URL = "ws://127.0.0.1:8001/api/v1/voice/ws/session"
LOG_PATH = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/uvicorn6.log")
ARTIFACTS_DIR = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/phase410_artifacts")
CDP_ENDPOINT = "http://172.25.224.1:9222"


def tail_pos() -> int:
    return LOG_PATH.stat().st_size if LOG_PATH.exists() else 0


async def collect_since(pos: int, settle_s: float = 4.0) -> tuple[str, int]:
    await asyncio.sleep(settle_s)
    if not LOG_PATH.exists():
        return "", pos
    with open(LOG_PATH, "r", errors="ignore") as f:
        f.seek(pos)
        chunk = f.read()
        new_pos = f.tell()
    return chunk, new_pos


class ChromeObserver:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def connect(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=8000)

    async def snapshot(self, label: str) -> dict:
        ctx = self._browser.contexts[0] if self._browser.contexts else None
        if ctx is None or not ctx.pages:
            return {"label": label, "url": None, "title": None, "tab_count": 0, "screenshot": None}
        page = ctx.pages[0]
        tab_count = len(ctx.pages)
        url = page.url
        try:
            title = await page.title()
        except Exception:
            title = None
        shot_path = ARTIFACTS_DIR / f"{label}.png"
        try:
            await page.screenshot(path=str(shot_path))
            shot_str = str(shot_path)
        except Exception:
            shot_str = None
        return {"label": label, "url": url, "title": title, "tab_count": tab_count, "screenshot": shot_str}

    async def close(self) -> None:
        if self._pw:
            await self._pw.stop()


async def run_turn(phrase: str, timeout: float = 60.0) -> dict:
    client = WSAudioClient(WS_URL)
    await client.connect()
    try:
        return await client.voice_turn(phrase, timeout=timeout)
    finally:
        await client.close()


async def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    log_pos = tail_pos()

    observer = ChromeObserver()
    await observer.connect()
    print(">>> Observer CDP connected", flush=True)

    # ── Clear any stale session first for a genuinely clean run ──────────
    print("\n>>> PRE-STEP: Cancel any stale session", flush=True)
    await run_turn("Cancel", timeout=30.0)
    await asyncio.sleep(3.0)

    # ── Initial search — use London (proven reliable transcription) ──────
    print("\n>>> TURN 1: Find me a flight from Karachi to London next month", flush=True)
    t0 = time.time()
    turn = await run_turn("Find me a flight from Karachi to London next month", timeout=90.0)
    chunk, log_pos = await collect_since(log_pos, settle_s=3.0)
    deadline = time.time() + 60.0
    while "[FLIGHT_SESSION_CREATED]" not in chunk and time.time() < deadline:
        more, log_pos = await collect_since(log_pos, settle_s=2.0)
        chunk += more
    entity_resolved = re.findall(r"\[TRAVEL_ENTITY_RESOLVED\][^\n]*", chunk)
    goal_created = re.search(r"\[TRAVEL_GOAL_CREATED\][^\n]*", chunk)
    snap = await observer.snapshot("01_initial_search")
    results.append({
        "turn": "Find me a flight from Karachi to London next month",
        "final_text": turn["final_text"],
        "entity_resolutions": entity_resolved,
        "travel_goal": goal_created.group(0) if goal_created else None,
        "session_created": "[FLIGHT_SESSION_CREATED]" in chunk,
        "control_mode": "windows_chrome_cdp" if "windows_chrome_cdp" in chunk else (
            "reused" if "[BROWSER_WORKSPACE_REUSED]" in chunk else "unknown"),
        "snapshot": snap,
        "latency_s": round(time.time() - t0, 1),
        "verdict": "PASS" if "[FLIGHT_SESSION_CREATED]" in chunk else "WARN",
    })
    print(f"    {results[-1]}", flush=True)
    await asyncio.sleep(3.0)

    # ── Follow-ups ─────────────────────────────────────────────────────────
    followups = [
        "Show only direct flights",
        "Sort by cheapest",
        "Which one do you recommend",
        "Cancel",
    ]
    for i, phrase in enumerate(followups, start=2):
        print(f"\n>>> TURN {i}: {phrase}", flush=True)
        t0 = time.time()
        try:
            turn = await run_turn(phrase, timeout=60.0)
        except Exception as exc:
            print(f"    connection error: {exc!r} — retrying once", flush=True)
            await asyncio.sleep(3.0)
            turn = await run_turn(phrase, timeout=60.0)

        chunk, log_pos = await collect_since(log_pos, settle_s=4.0)
        transcript_m = re.search(r"\[STT_END\] ms=[\d.]+ transcript=(.*)", chunk)
        snap = await observer.snapshot(f"{i:02d}_{re.sub(r'[^a-z0-9]+', '_', phrase.lower())[:30]}")
        results.append({
            "turn": phrase,
            "stt_transcript": transcript_m.group(1)[:100] if transcript_m else None,
            "final_text": turn["final_text"],
            "followup_detected": "[FLIGHT_FOLLOWUP_DETECTED]" in chunk,
            "control_result": "success" if "[FLIGHT_CONTROL_SUCCESS]" in chunk else (
                "failed_honestly" if "[FLIGHT_CONTROL_FAILED]" in chunk else "n/a"),
            "action_verified": "[BROWSER_ACTION_VERIFY] " in chunk and "page_state_changed" in chunk,
            "cancelled_logged": "[VOICE_APPROVAL_CANCELLED]" in chunk,
            "snapshot": snap,
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if ("[FLIGHT_FOLLOWUP_DETECTED]" in chunk or "[VOICE_APPROVAL_CANCELLED]" in chunk) else "WARN",
        })
        print(f"    {results[-1]}", flush=True)
        await asyncio.sleep(3.0)

    await observer.close()

    print("\n" + "=" * 80)
    print("PHASE 4.10 — PROPER LIVE TEST")
    print("=" * 80)
    passed = sum(1 for r in results if r.get("verdict") == "PASS")
    for r in results:
        print(f"\n[{r.get('verdict')}] {r['turn']!r}")
        for k, v in r.items():
            if k in ("turn", "verdict"):
                continue
            print(f"    {k}: {v}")
    print(f"\nSUMMARY: {passed} PASS / {len(results)} total")

    with open(ARTIFACTS_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
