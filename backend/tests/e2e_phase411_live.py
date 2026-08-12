"""
Phase 4.11 live verification — real voice turn through the real
WebSocket pipeline, with a concurrent /monitor/ws metrics stream running
alongside it (to genuinely exercise the QueueFull fix under load), and a
CDP observer watching the real Chrome window for blank-page/tab-count
evidence.

Run with: cd backend && python3 -u tests/e2e_phase411_live.py
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
import websockets  # noqa: E402

WS_URL = "ws://127.0.0.1:8001/api/v1/voice/ws/session"
MONITOR_WS_URL = "ws://127.0.0.1:8001/api/v1/monitor/ws"
LOG_PATH = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/uvicorn_p411.log")
ARTIFACTS_DIR = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/phase411_artifacts")
CDP_ENDPOINT = "http://172.25.224.1:9222"


def tail_pos() -> int:
    return LOG_PATH.stat().st_size if LOG_PATH.exists() else 0


async def collect_since(pos: int, settle_s: float = 3.0) -> tuple[str, int]:
    await asyncio.sleep(settle_s)
    if not LOG_PATH.exists():
        return "", pos
    with open(LOG_PATH, "r", errors="ignore") as f:
        f.seek(pos)
        chunk = f.read()
        new_pos = f.tell()
    return chunk, new_pos


async def monitor_stress(duration_s: float, results: dict) -> None:
    """Connects to /monitor/ws and just keeps receiving — a slow-ish
    consumer (we don't read super fast) to genuinely put backpressure on
    the metrics queue while the flight search runs."""
    frames = 0
    try:
        async with websockets.connect(MONITOR_WS_URL, max_size=None) as ws:
            deadline = time.time() + duration_s
            while time.time() < deadline:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
                    frames += 1
                    await asyncio.sleep(0.3)  # deliberately slower than the ~1s producer cadence
                except asyncio.TimeoutError:
                    continue
    except Exception as exc:
        results["monitor_error"] = repr(exc)
    results["monitor_frames_received"] = frames


class ChromeObserver:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def connect(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15000)

    async def snapshot(self, label: str) -> dict:
        ctx = self._browser.contexts[0] if self._browser.contexts else None
        if ctx is None or not ctx.pages:
            return {"label": label, "url": None, "title": None, "tab_count": 0}
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
        except Exception:
            pass
        return {"label": label, "url": url, "title": title, "tab_count": tab_count}

    async def close(self) -> None:
        if self._pw:
            await self._pw.stop()


async def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    log_pos = tail_pos()
    results: dict = {}

    # Start metrics stress in the background for the duration of the test.
    stress_task = asyncio.create_task(monitor_stress(60.0, results))

    # Fresh environment (post-reboot) — Chrome may not be running yet. The
    # backend's own BrowserWorkspace auto-launches it on first use, so the
    # voice command must go FIRST; only then does the observer have
    # anything real to connect to.
    print("\n>>> Voice: Find me a flight from Karachi to London next month", flush=True)
    t0 = time.time()
    client = WSAudioClient(WS_URL)
    await client.connect()
    turn = await client.voice_turn("Find me a flight from Karachi to London next month", timeout=90.0)
    await client.close()
    print(f"    ack: {turn['final_text']!r}  (turn_latency={time.time()-t0:.1f}s)", flush=True)

    observer = ChromeObserver()
    for attempt in range(10):
        try:
            await observer.connect()
            break
        except Exception as exc:
            print(f"    observer connect attempt {attempt+1} failed: {exc!r} — retrying", flush=True)
            await asyncio.sleep(1.5)
    else:
        print("    observer could not connect to Chrome CDP at all", flush=True)
        results["observer_connect_failed"] = True

    before = {"label": "00_before", "url": None, "title": None, "tab_count": None}
    quick = await observer.snapshot("01_quick_after_ack") if observer._browser else before
    print(f"    quick snapshot: {quick}", flush=True)

    chunk, log_pos = await collect_since(log_pos, settle_s=3.0)
    deadline = time.time() + 60.0
    while "[UX_LATENCY]" not in chunk and time.time() < deadline:
        more, log_pos = await collect_since(log_pos, settle_s=2.0)
        chunk += more

    final = await observer.snapshot("02_after_search") if observer._browser else before
    print(f"    final snapshot: {final}", flush=True)

    ux_latency = re.search(r"\[UX_LATENCY\][^\n]*", chunk)
    bottleneck = re.search(r"\[UX_LATENCY_BOTTLENECK\][^\n]*", chunk)
    browser_visible = re.search(r"\[BROWSER_VISIBLE_MS\][^\n]*", chunk)
    page_ready = re.search(r"\[BROWSER_PAGE_READY_MS\][^\n]*", chunk)
    launch_parallel = "[BROWSER_LAUNCH_PARALLEL_START]" in chunk
    blank_blocked = "[BLANK_PAGE_BLOCKED]" in chunk
    empty_query_blocked = "[EMPTY_QUERY_BLOCKED]" in chunk
    delegation_single = "[DELEGATION_SINGLE_AGENT]" in chunk
    delegation_multi = "[DELEGATION_MULTI_AGENT]" in chunk
    queuefull_unhandled = bool(re.search(r"QueueFull(?!.*PREVENTED)", chunk))

    results.update({
        "before_tab_count": before["tab_count"],
        "quick_snapshot": quick,
        "final_snapshot": final,
        "ux_latency": ux_latency.group(0) if ux_latency else None,
        "bottleneck": bottleneck.group(0) if bottleneck else None,
        "browser_visible_ms": browser_visible.group(0) if browser_visible else None,
        "page_ready_ms": page_ready.group(0) if page_ready else None,
        "launch_parallel_start": launch_parallel,
        "blank_page_blocked_triggered": blank_blocked,
        "empty_query_blocked_triggered": empty_query_blocked,
        "delegation_path": "single_agent (fast)" if delegation_single else ("multi_agent (slow)" if delegation_multi else "unknown"),
        "unhandled_queuefull_in_chunk": queuefull_unhandled,
    })

    stress_task.cancel()
    try:
        await stress_task
    except asyncio.CancelledError:
        pass

    await observer.close()

    # Final full-log QueueFull check (whole file, not just this turn's window)
    full_log = LOG_PATH.read_text(errors="ignore") if LOG_PATH.exists() else ""
    all_queuefull = re.findall(r".*QueueFull.*", full_log)
    unhandled = [l for l in all_queuefull if "QUEUEFULL_PREVENTED" not in l and "EVENT_DROPPED_NONCRITICAL" not in l
                 and "_safe_put_metrics" not in l and "safe put" not in l.lower()]
    results["total_queuefull_mentions_in_full_log"] = len(all_queuefull)
    results["unhandled_queuefull_lines"] = unhandled[:5]

    print("\n" + "=" * 80)
    print("PHASE 4.11 LIVE VERIFICATION RESULTS")
    print("=" * 80)
    for k, v in results.items():
        print(f"{k}: {v}")

    with open(ARTIFACTS_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
