"""
Streaming response pipeline: OpenAI token stream → sentence chunks → Kokoro TTS.

Key optimization: Kokoro synthesis for sentence N starts the moment its boundary
is detected — while OpenAI is still generating sentence N+1.

Latency comparison:
  Old path: full LLM (2-4s) → full TTS (1-2s) = 3-6s to first audio
  New path: first sentence (0.5-1s) → TTS (0.3-0.5s) = ~0.8-1.5s to first audio
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Sentence boundary after .  !  ?  followed by whitespace or end-of-string
_SENT_RE = re.compile(r'(?<=[.!?])(?:\s+|$)')
# Strip markdown artifacts before TTS
_MD_RE   = re.compile(r'\*{1,2}([^*]+)\*{1,2}|`([^`]+)`|#{1,6}\s|>\s?|~~([^~]+)~~|\[([^\]]+)\]\([^)]+\)')

VOICE_SYSTEM_PROMPT = (
    "You are Xyron, a voice-first AI assistant built by Tayyab Aziz. "
    "Reply in plain English only — no markdown, no lists, no bullet points. "
    "Keep responses concise (1-2 sentences) unless detail is genuinely needed. "
    "The user may use Urdu/Hindi phrasing — understand intent, always reply in English."
)


def _strip_md(text: str) -> str:
    def _replace(m: re.Match) -> str:
        return next(g for g in m.groups() if g is not None)
    return _MD_RE.sub(_replace, text).strip()


async def _kokoro_async(text: str, voice: str, speed: float) -> Optional[bytes]:
    """Synthesize text with Kokoro; retry once on None or exception."""
    for attempt in range(2):
        try:
            from api.routers.voice import _kokoro_to_wav
            wav = await asyncio.to_thread(_kokoro_to_wav, text, voice, speed)
            if wav:
                return wav
            if attempt == 0:
                logger.warning("[Pipeline] Kokoro returned None for %r, retrying...", text[:40])
                await asyncio.sleep(0.15)
        except Exception as exc:
            if attempt == 0:
                logger.warning("[Pipeline] Kokoro attempt 1 failed for %r, retrying: %s", text[:40], exc)
                await asyncio.sleep(0.15)
            else:
                logger.error("[Pipeline] Kokoro failed after retry for %r: %s", text[:40], exc)
    return None


async def stream_response_with_tts(
    transcript: str,
    history: list[dict],
    voice: str = "af_nova",
    speed: float = 1.0,
) -> AsyncIterator[tuple[str, Optional[bytes], int, bool]]:
    """
    Async generator — yields (sentence_text, wav_bytes, chunk_index, is_final).

    Synthesis is pipelined: as soon as a sentence boundary is found, Kokoro
    starts synthesizing it while OpenAI continues generating the next sentence.
    The consumer receives the audio chunk as soon as synthesis completes.
    """
    from api.config import settings as _cfg
    from api.services.model_router import select_model

    if not _cfg.openai_api_key:
        logger.warning("[Pipeline] No OpenAI key — returning empty stream")
        return

    # Pick model based on complexity
    model = select_model(transcript)
    if model in ("local", "offline"):
        model = "gpt-4o-mini"

    messages: list[dict] = [{"role": "system", "content": VOICE_SYSTEM_PROMPT}]
    for h in history[-10:]:
        role = h.get("role", "user")
        text = h.get("text", h.get("content", ""))
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": transcript})

    # asyncio.Queue bridges the producer (LLM stream) and consumer (this generator)
    queue: asyncio.Queue[Optional[tuple[str, asyncio.Task[Optional[bytes]], int, bool]]] = \
        asyncio.Queue(maxsize=4)

    async def _producer() -> None:
        """Stream OpenAI, detect sentence boundaries, fire TTS tasks into queue."""
        pending   = ""
        chunk_idx = 0

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=_cfg.openai_api_key)

            async with await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=200,
                temperature=0.7,
                stream=True,
            ) as stream:
                async for chunk in stream:
                    delta = ""
                    if chunk.choices and chunk.choices[0].delta:
                        delta = chunk.choices[0].delta.content or ""
                    if not delta:
                        continue

                    pending += delta

                    # Flush complete sentences
                    parts = _SENT_RE.split(pending)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            sentence = _strip_md(sentence)
                            if sentence:
                                chunk_idx += 1
                                tts_task = asyncio.create_task(
                                    _kokoro_async(sentence, voice, speed)
                                )
                                await queue.put((sentence, tts_task, chunk_idx, False))
                        pending = parts[-1]

        except Exception as exc:
            logger.warning("[Pipeline] LLM stream error: %s", exc)
        finally:
            # Flush remaining fragment as final
            remaining = _strip_md(pending)
            if remaining:
                chunk_idx += 1
                tts_task = asyncio.create_task(_kokoro_async(remaining, voice, speed))
                await queue.put((remaining, tts_task, chunk_idx, True))
            elif chunk_idx > 0:
                # Mark the last queued item as final — handled below
                pass
            await queue.put(None)  # sentinel

    producer_task = asyncio.create_task(_producer())
    total_chunks  = 0
    last_item: Optional[tuple] = None

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            sentence, tts_task, idx, is_final = item
            total_chunks = idx

            # If there's a buffered previous item, yield it (not yet marked final)
            if last_item is not None:
                prev_s, prev_task, prev_idx, _ = last_item
                prev_wav = await prev_task
                yield prev_s, prev_wav, prev_idx, False

            last_item = (sentence, tts_task, idx, is_final)

        # Yield the last item as final
        if last_item is not None:
            s, t, i, _ = last_item
            wav = await t
            yield s, wav, i, True

    finally:
        producer_task.cancel()

    logger.info("[Pipeline] done — %d chunks, model=%s", total_chunks, model)


async def quick_response(
    transcript: str,
    history: list[dict] | None = None,
    model: str = "gpt-4o-mini",
) -> str:
    """
    Non-streaming LLM call. Returns the full response text.
    Used for tool narration, clarification prompts, and low-latency paths.
    """
    from api.config import settings as _cfg
    if not _cfg.openai_api_key:
        return "I'm not sure how to help with that."

    messages: list[dict] = [{"role": "system", "content": VOICE_SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:
        role = h.get("role", "user")
        text = h.get("text", h.get("content", ""))
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": transcript})

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_cfg.openai_api_key)
        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=150, temperature=0.7,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("[Pipeline] quick_response error: %s", exc)
        return "I ran into an issue. Please try again."
