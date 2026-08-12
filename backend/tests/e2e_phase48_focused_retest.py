"""Focused retest: Test 5 (cancel) + Test 7 (selective cleanup against a
live pending task), with tight polling so we react the moment the
approval gate opens instead of losing the window to a slow, separate
test script."""
from __future__ import annotations

import asyncio
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from ws_audio_harness import WSAudioClient  # noqa: E402

WS_URL = "ws://127.0.0.1:8001/api/v1/voice/ws/session"
LOG_PATH = Path("/tmp/claude-1000/-mnt-e-Xyron/bd6e18a6-64ba-42d8-a546-86f4c4afbd6e/scratchpad/uvicorn2.log")


def tail_pos() -> int:
    return LOG_PATH.stat().st_size if LOG_PATH.exists() else 0


async def wait_for_tag_fast(tag: str, pos: int, timeout: float) -> tuple[bool, int, str]:
    deadline = time.time() + timeout
    acc = ""
    while time.time() < deadline:
        with open(LOG_PATH, "r", errors="ignore") as f:
            f.seek(pos)
            chunk = f.read()
            pos = f.tell()
        acc += chunk
        if tag in chunk:
            return True, pos, acc
        await asyncio.sleep(0.5)
    return False, pos, acc


async def test5_cancel() -> None:
    print(">>> TEST 5 (retest): flight cancel", flush=True)
    client = WSAudioClient(WS_URL)
    await client.connect()
    pos = tail_pos()
    try:
        turn = await client.voice_turn("Find me a flight from Karachi to Dubai next month", timeout=45.0)
        found, pos, _ = await wait_for_tag_fast("[FLIGHT_SESSION_CREATED]", pos, timeout=90.0)
        print(f"    session_created={found}")
        await asyncio.sleep(2.0)
        turn2 = await client.voice_turn("Cancel", timeout=30.0)
        found_cancel, pos, chunk = await wait_for_tag_fast("[VOICE_APPROVAL_CANCELLED]", pos, timeout=20.0)
        print(f"    turn2 final_text={turn2['final_text']!r} cancelled_logged={found_cancel}")
    finally:
        await client.close()


async def test7_selective() -> None:
    print(">>> TEST 7 (retest): selective cleanup against a live pending task", flush=True)
    client = WSAudioClient(WS_URL)
    await client.connect()
    pos = tail_pos()
    try:
        turn = await client.voice_turn("Clean my PC", timeout=30.0)
        print(f"    ack: {turn['final_text']!r}")
        found_approval, pos, _ = await wait_for_tag_fast("[CLEANER_APPROVAL_REQUIRED]", pos, timeout=240.0)
        print(f"    approval_required_seen={found_approval}")
        if not found_approval:
            print("    giving up — scan did not reach approval in time")
            return
        # React immediately — approval gate is open right now.
        turn_a = await client.voice_turn("Don't touch Downloads", timeout=20.0)
        found_excl, pos, chunk_a = await wait_for_tag_fast("[CLEANER_CATEGORY_EXCLUDED]", pos, timeout=10.0)
        print(f"    turn_a final_text={turn_a['final_text']!r} exclude_logged={found_excl}")

        await asyncio.sleep(1.5)
        turn_b = await client.voice_turn("No, don't delete", timeout=20.0)
        found_cancel, pos, chunk_b = await wait_for_tag_fast("[CLEANER_CANCELLED]", pos, timeout=10.0)
        print(f"    turn_b final_text={turn_b['final_text']!r} cancelled_logged={found_cancel}")
    finally:
        await client.close()


async def main() -> None:
    await test5_cancel()
    print()
    await test7_selective()


if __name__ == "__main__":
    asyncio.run(main())
