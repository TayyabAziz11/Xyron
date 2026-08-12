"""
Phase 4.9 — live flight workflow test against REAL Windows Chrome, driven
through the real voice WebSocket pipeline (synthesized audio -> real
Whisper STT -> real routing -> real CDP-controlled Windows Chrome).

Maintains its own read-only CDP observer connection to the same Chrome
debug port to capture URL/title/tab-count/screenshots after each turn,
independent of the backend's own control connection (CDP supports
multiple simultaneous debugger clients).

Run with: cd backend && python3 -u tests/e2e_phase49_real_chrome.py
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
LOG_PATH = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/uvicorn3.log")
ARTIFACTS_DIR = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/chrome_test_artifacts")
CDP_ENDPOINT = "http://172.25.224.1:9222"


def tail_pos() -> int:
    return LOG_PATH.stat().st_size if LOG_PATH.exists() else 0


async def collect_since(pos: int, settle_s: float = 3.0) -> tuple[str, int]:
    """Single settle-then-read: voice_turn() already blocked until the
    server sent 'done', so the corresponding log lines are essentially
    already written; a short settle covers any trailing async flush."""
    await asyncio.sleep(settle_s)
    if not LOG_PATH.exists():
        return "", pos
    with open(LOG_PATH, "r", errors="ignore") as f:
        f.seek(pos)
        chunk = f.read()
        new_pos = f.tell()
    return chunk, new_pos


class ChromeObserver:
    """Read-only CDP connection to the SAME real Windows Chrome, used only
    to capture URL/title/tab-count/screenshot evidence — never drives
    the browser itself (that's the backend's job via BrowserWorkspace)."""

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
    """Fresh WS connection per turn — the flight session and browser
    workspace live server-side (BrowserWorkspace/FlightSessionState
    singletons), independent of which WS connection sends the command,
    so this proves cross-connection persistence rather than just
    same-connection continuity, and sidesteps the keepalive fragility of
    holding one WS open across many slow real-Chrome-CDP turns."""
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
    print(">>> Observer CDP connected (read-only)", flush=True)

    # ── Initial search ──────────────────────────────────────────────────
    print("\n>>> INITIAL: Find me a flight from Karachi to Dubai next month", flush=True)
    t0 = time.time()
    turn = await run_turn("Find me a flight from Karachi to Dubai next month", timeout=90.0)
    chunk, log_pos = await collect_since(log_pos, settle_s=3.0)
    deadline = time.time() + 60.0
    while "[FLIGHT_RESULTS_PAGE_OPENED]" not in chunk and time.time() < deadline:
        more, log_pos = await collect_since(log_pos, settle_s=2.0)
        chunk += more

    control_mode_m = re.search(r"\[BROWSER_CONTROL_MODE\] mode=(\S+)", chunk)
    tab_counts = re.findall(r"\[BROWSER_TAB_COUNT\] count=(\d+)", chunk)
    snap = await observer.snapshot("01_initial_search")
    # Workspace may already be alive from a prior run in this same server
    # process — CDP_CONNECTED/CONTROL_MODE only log on first creation, so
    # "reused" is equally valid evidence of the real-Chrome claim as a
    # fresh connect, provided the *page* is genuinely the real Chrome tab
    # (confirmed independently by the observer's own CDP snapshot below).
    results.append({
        "turn": "Find me a flight from Karachi to Dubai next month",
        "final_text": turn["final_text"],
        "windows_chrome_cdp_connected_this_run": "[WINDOWS_CHROME_CDP_CONNECTED]" in chunk,
        "workspace_reused_from_prior_run": "[BROWSER_WORKSPACE_REUSED]" in chunk,
        "control_mode_logged_this_run": control_mode_m.group(1) if control_mode_m else None,
        "flight_session_created_or_updated": ("[FLIGHT_SESSION_CREATED]" in chunk or "[FLIGHT_SESSION_UPDATED]" in chunk),
        "flight_results_page_opened": "[FLIGHT_RESULTS_PAGE_OPENED]" in chunk,
        "tab_counts_seen": [int(x) for x in tab_counts],
        "snapshot": snap,
        "latency_s": round(time.time() - t0, 1),
        "verdict": "PASS" if "[FLIGHT_RESULTS_PAGE_OPENED]" in chunk and snap.get("url", "").startswith(
            "https://www.google.com/travel/flights") else "WARN",
    })
    print(f"    {results[-1]}", flush=True)

    # ── Follow-up turns — fresh connection each time ─────────────────────
    followups = [
        "Check Emirates",
        "Show only direct flights",
        "Morning flights only",
        "Sort by cheapest",
        "Which flights allow 20 kilograms of baggage",
        "Open the first option",
        "Go back",
        "Compare it with FlyDubai",
        "Which one do you recommend",
        "Cancel",
    ]
    for i, phrase in enumerate(followups, start=2):
        print(f"\n>>> TURN {i}: {phrase}", flush=True)
        t0 = time.time()
        try:
            turn = await run_turn(phrase, timeout=60.0)
        except Exception as exc:
            print(f"    CONNECTION ERROR: {exc!r} — retrying once", flush=True)
            await asyncio.sleep(3.0)
            turn = await run_turn(phrase, timeout=60.0)

        chunk, log_pos = await collect_since(log_pos, settle_s=4.0)
        snap = await observer.snapshot(f"{i:02d}_{re.sub(r'[^a-z0-9]+', '_', phrase.lower())[:30]}")
        tab_counts = re.findall(r"\[BROWSER_TAB_COUNT\] count=(\d+)", chunk)
        results.append({
            "turn": phrase,
            "final_text": turn["final_text"],
            "followup_detected": "[FLIGHT_FOLLOWUP_DETECTED]" in chunk,
            "control_result": "success" if "[FLIGHT_CONTROL_SUCCESS]" in chunk else (
                "failed_honestly" if "[FLIGHT_CONTROL_FAILED]" in chunk else "n/a"),
            "workspace_reused": "[BROWSER_WORKSPACE_REUSED]" in chunk,
            "new_tab_approved": "[BROWSER_NEW_TAB_APPROVED]" in chunk,
            "baggage_check_started": "[BAGGAGE_CHECK_START]" in chunk,
            "baggage_unavailable_honest": "[BAGGAGE_INFO_UNAVAILABLE]" in chunk,
            "cancelled_logged": "[VOICE_APPROVAL_CANCELLED]" in chunk,
            "tab_counts_seen": [int(x) for x in tab_counts],
            "snapshot": snap,
            "latency_s": round(time.time() - t0, 1),
            "verdict": "PASS" if "[FLIGHT_FOLLOWUP_DETECTED]" in chunk else "WARN",
        })
        print(f"    {results[-1]}", flush=True)
        await asyncio.sleep(1.0)

    await observer.close()

    print("\n" + "=" * 80)
    print("PHASE 4.9 — REAL WINDOWS CHROME LIVE FLIGHT WORKFLOW")
    print("=" * 80)
    passed = sum(1 for r in results if r.get("verdict") == "PASS")
    for r in results:
        print(f"\n[{r.get('verdict')}] {r['turn']!r}")
        print(f"    final_text: {r['final_text']!r}")
        print(f"    snapshot: {r['snapshot']}")
    print(f"\nSUMMARY: {passed} PASS / {len(results)} total")

    with open(ARTIFACTS_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results saved to {ARTIFACTS_DIR / 'results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
