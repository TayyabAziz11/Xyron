"""
Real WebSocket + synthesized-audio test harness for Phase 4.8 Part 10.

Drives actual voice turns through the live /api/v1/voice/ws/session
endpoint: synthesizes each phrase with espeak-ng, resamples to the exact
16kHz mono float32 PCM format the server expects, streams it as binary
frames (the same shape a real microphone client would send), sends
end_of_speech, and reads back the server's real STT -> routing -> TTS
response. This is NOT task.metadata injection — it exercises the actual
WebSocket protocol and whisper transcription.

Usage: from ws_audio_harness import voice_turn, WSAudioClient
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import websockets

FRAME_SAMPLES = 1280  # 80ms @ 16kHz, matches FRAME_BYTES = 1280*4 in voice_ws.py
SAMPLE_RATE = 16000


def synthesize_16k_mono_f32(text: str) -> np.ndarray:
    """espeak-ng -> WAV -> ffmpeg resample to 16kHz mono -> float32 array."""
    with tempfile.TemporaryDirectory() as td:
        raw_wav = str(Path(td) / "raw.wav")
        out_wav = str(Path(td) / "out.wav")
        subprocess.run(
            ["espeak-ng", "-v", "en", "-s", "155", "-w", raw_wav, text],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_wav, "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "wav", out_wav],
            check=True, capture_output=True,
        )
        data, sr = sf.read(out_wav, dtype="float32")
        assert sr == SAMPLE_RATE, f"unexpected sample rate {sr}"
        return data


def to_frames(data: np.ndarray) -> list[bytes]:
    frames = []
    n = len(data)
    for i in range(0, n, FRAME_SAMPLES):
        chunk = data[i:i + FRAME_SAMPLES]
        if len(chunk) < FRAME_SAMPLES:
            chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
        frames.append(chunk.astype(np.float32).tobytes())
    return frames


class WSAudioClient:
    """One persistent WS connection representing one continuous voice
    session — multiple voice_turn() calls on the same client reuse the
    same connection, exactly like a real multi-turn conversation."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> None:
        # Real Windows Chrome CDP round-trips are slower than the local
        # WSLg driver — a strict ping_interval/ping_timeout closed the
        # connection mid-turn. Use a longer interval and timeout so a
        # single slow (real network + CDP) turn can't trip keepalive.
        self.ws = await websockets.connect(self.url, max_size=None, ping_interval=45, ping_timeout=30)
        await self.ws.send(json.dumps({"type": "config", "voice": "alloy", "speed": 1.0}))
        # The server auto-plays a greeting on connect and sets is_speaking=True
        # until it gets a tts_done ack — while true, it silently drops every
        # PCM frame. Drain the greeting, then send tts_done ourselves (a real
        # client sends this once its own audio playback finishes) so the
        # server is actually listening before the first real voice_turn().
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "done":
                break
        await self.ws.send(json.dumps({"type": "tts_done"}))
        await asyncio.sleep(0.3)

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()

    async def voice_turn(self, text: str, timeout: float = 60.0) -> dict:
        """Speak *text* (synthesized) into the live session, wait for the
        real STT transcript + routed response, and return
        {"transcript_used": text, "responses": [...], "final_text": str}."""
        assert self.ws is not None
        data = synthesize_16k_mono_f32(text)
        # Lead-in silence frames: real mic streams always have a little
        # pre-roll; starting mid-word on frame 0 was truncating the VAD's
        # view of the utterance.
        lead_in = np.zeros(FRAME_SAMPLES * 3, dtype=np.float32)
        frames = to_frames(np.concatenate([lead_in, data]))
        for f in frames:
            await self.ws.send(f)
            await asyncio.sleep(0.08)  # real-time pacing — matches 80ms/frame
        await self.ws.send(json.dumps({"type": "end_of_speech"}))

        responses: list[dict] = []
        final_text = ""
        deadline = asyncio.get_event_loop().time() + timeout
        got_done = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                if got_done:
                    break
                continue
            if isinstance(raw, (bytes, bytearray)):
                continue  # TTS audio bytes — not needed for this harness
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            responses.append(msg)
            if msg.get("type") == "response" and msg.get("text"):
                final_text = msg["text"]
            if msg.get("type") == "done":
                got_done = True
                # Ack tts_done so the server resets is_speaking and is
                # ready for the next real turn (a real client would send
                # this once its own audio playback finishes).
                try:
                    await self.ws.send(json.dumps({"type": "tts_done"}))
                except Exception:
                    pass
                break
            # NOTE: do NOT break on "listening" — the server can emit an
            # early/interim listening ping while STT + routing are still
            # in flight; breaking there disconnects before the real
            # response arrives (confirmed via live server logs: STT alone
            # took ~2.7s, the full intelligence pipeline longer).

        if not got_done:
            # We hit the timeout without ever seeing "done" — the server
            # may still think is_speaking=True, which would silently drop
            # every PCM frame of the NEXT turn. Force tts_done regardless
            # so a slow/missed response on this turn can't cascade into a
            # false failure on the next one.
            try:
                await self.ws.send(json.dumps({"type": "tts_done"}))
            except Exception:
                pass

        return {"transcript_used": text, "responses": responses, "final_text": final_text}


async def single_turn(url: str, text: str, timeout: float = 60.0) -> dict:
    """One-shot helper: connect, speak once, close."""
    client = WSAudioClient(url)
    await client.connect()
    try:
        return await client.voice_turn(text, timeout=timeout)
    finally:
        await client.close()
