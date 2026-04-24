"""
Meeting Assistant Service — Feature #6
Buffers real-time transcription chunks from a side-channel mic stream.
On command ("summarize what was said"), returns a bullet-point recap.
Uses the existing Whisper pipeline.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_BUFFER   = 500    # transcript entries before oldest are dropped
_SUMMARY_MINS = 30     # max minutes of content summarized


class MeetingService:

    def __init__(self) -> None:
        self._lock       = threading.Lock()
        self._buffer:    deque[dict] = deque(maxlen=_MAX_BUFFER)
        self._active     = False
        self._start_ts   = 0.0

    # ── Session management ────────────────────────────────────────────────────

    def start_session(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._active   = True
            self._start_ts = time.time()
        logger.info("Meeting session started")

    def stop_session(self) -> None:
        with self._lock:
            self._active = False
        logger.info("Meeting session stopped — %d transcript entries", len(self._buffer))

    @property
    def is_active(self) -> bool:
        return self._active

    # ── Transcript ingestion ──────────────────────────────────────────────────

    def add_chunk(self, text: str, speaker: str = "participant") -> None:
        """Add a transcription chunk to the buffer."""
        if not text.strip():
            return
        with self._lock:
            self._buffer.append({
                "ts":      time.time(),
                "speaker": speaker,
                "text":    text.strip(),
            })

    # ── Summarization ─────────────────────────────────────────────────────────

    def get_transcript(self, last_minutes: int = _SUMMARY_MINS) -> str:
        cutoff = time.time() - last_minutes * 60
        with self._lock:
            recent = [e for e in self._buffer if e["ts"] >= cutoff]
        if not recent:
            return ""
        return "\n".join(f"[{e['speaker']}]: {e['text']}" for e in recent)

    async def summarize(self, openai_key: str, last_minutes: int = 10) -> str:
        """Return a GPT bullet-point recap of recent meeting content."""
        transcript = self.get_transcript(last_minutes)
        if not transcript:
            return "There's no recorded meeting content to summarize yet."
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_key)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=300,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a meeting assistant. Summarize the provided transcript "
                            "as 3-5 concise bullet points. Focus on decisions, action items, "
                            "and key topics. No markdown headers. Just bullet points starting with '•'."
                        ),
                    },
                    {"role": "user", "content": f"Transcript:\n{transcript}"},
                ],
            )
            return resp.choices[0].message.content or "Summary unavailable."
        except Exception as exc:
            logger.warning("Meeting summarize failed: %s", exc)
            return "I wasn't able to generate a summary right now."

    def word_count(self) -> int:
        with self._lock:
            return sum(len(e["text"].split()) for e in self._buffer)


meeting_service = MeetingService()
