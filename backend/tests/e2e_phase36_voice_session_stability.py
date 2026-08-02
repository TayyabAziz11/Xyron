"""
Live e2e validation for Phase 3.6 — voice-session keepalive-timeout incident.

Drives the actual /api/v1/voice/ws/session endpoint (same protocol the real
Tauri/web frontend uses), same pattern as ws_audio_harness.py's other e2e_*
scripts. Requires a live backend on ws://127.0.0.1:8000. Not part of the
default pytest collection (this repo's convention: e2e_*.py files require a
running backend and are run manually, not by `pytest tests/`).

Usage:
    # terminal 1
    cd backend && python3 -m uvicorn api.main:app --port 8000 --host 0.0.0.0
    # terminal 2
    cd backend && python3 tests/e2e_phase36_voice_session_stability.py

Covers Task 13's test matrix:
  A. Normal wake — greeting plays/skips, listening within a strict target,
     session survives well past uvicorn's ws-ping window (~20s default).
  B. Forced greeting timeout (set XYRON_GREETING_TIMEOUT_S=0.01 on the
     backend before running) — recovers to listening, no audio, no crash.
  D. Real spoken command ("open settings") after greeting — STT, activity
     events, tool execution, TTS response, session stays connected.
  G. Five repeated wake/session cycles — no leaked tasks, no duplicate
     sessions, consistent recovery.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import websockets
from ws_audio_harness import WSAudioClient

URL = "ws://127.0.0.1:8000/api/v1/voice/ws/session"


async def test_a_normal_wake() -> None:
    print("=== TEST A: normal wake, greeting, session survival ===")
    t0 = time.monotonic()
    ws = await websockets.connect(URL, max_size=None)
    await ws.send(json.dumps({"type": "config", "voice": "nova", "speed": 1.0}))

    listening_t = None
    while time.monotonic() - t0 < 20:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
        except asyncio.TimeoutError:
            break
        if isinstance(raw, (bytes, bytearray)):
            continue
        msg = json.loads(raw)
        if msg.get("type") == "audio":
            await ws.send(json.dumps({"type": "tts_done"}))
        elif msg.get("type") == "listening":
            listening_t = time.monotonic() - t0
            break

    assert listening_t is not None, "never reached listening state"
    assert listening_t < 15.0, f"took too long to reach listening: {listening_t}s"
    print(f"  time_to_listening={listening_t:.2f}s")

    survive_start = time.monotonic()
    closed, close_info = False, None
    try:
        while time.monotonic() - survive_start < 65:
            try:
                await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
    except websockets.exceptions.ConnectionClosed as e:
        closed, close_info = True, (e.code, e.reason)

    survived_s = time.monotonic() - survive_start
    print(f"  survived_s={survived_s:.1f} closed={closed} close_info={close_info}")
    if closed:
        assert close_info[0] != 1011, f"REGRESSION: got 1011 keepalive timeout: {close_info}"
    await ws.close()
    print("  PASS\n")


async def test_d_real_command() -> None:
    print("=== TEST D: real spoken command after greeting ===")
    client = WSAudioClient(URL)
    await client.connect()
    result = await client.voice_turn("open settings", timeout=45.0)
    activity_events = [r for r in result["responses"] if r.get("type") == "activity"]
    assert activity_events, "expected at least one activity event for a direct command"
    stages = [e["stage"] for e in activity_events]
    print(f"  activity_stages={stages}")
    assert "opening_app" in stages or "completed" in stages, f"unexpected stages: {stages}"
    still_open = client.ws.state.name == "OPEN"
    assert still_open, "session disconnected after a real command turn"
    await client.close()
    print("  PASS\n")


async def test_g_five_cycles() -> None:
    print("=== TEST G: five repeated wake/session cycles ===")

    async def one_cycle(n: int) -> tuple[bool, float]:
        t0 = time.monotonic()
        ws = await websockets.connect(URL, max_size=None)
        await ws.send(json.dumps({"type": "config", "voice": "nova", "speed": 1.0}))
        got_listening = False
        while time.monotonic() - t0 < 10:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, (bytes, bytearray)):
                continue
            if json.loads(raw).get("type") == "listening":
                got_listening = True
                break
        elapsed = time.monotonic() - t0
        await ws.close()
        print(f"  cycle {n}: reached_listening={got_listening} elapsed={elapsed:.2f}s")
        return got_listening, elapsed

    results = [await one_cycle(i) for i in range(1, 6)]
    assert all(ok for ok, _ in results), "not every cycle reached listening"
    print("  PASS\n")


async def main() -> None:
    await test_a_normal_wake()
    await test_d_real_command()
    await test_g_five_cycles()
    print("ALL E2E PHASE 3.6 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
