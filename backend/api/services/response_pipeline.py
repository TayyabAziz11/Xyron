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
import time
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Circuit-breaker: if OpenAI times out, route to Ollama for 60s
_OPENAI_TIMEOUT_S  = 8.0          # give up on OpenAI after 8s (streaming first-token)
_FALLBACK_DURATION = 60.0         # use Ollama for this many seconds after one failure
_OLLAMA_BASE       = "http://localhost:11434/v1"
_OLLAMA_MODEL      = "qwen2.5:1.5b"  # override with OLLAMA_MODEL env var at startup
# qwen3:4b was tried and reverted: its default "thinking" mode burns ~9.5s of
# real generation time on internal reasoning before any spoken answer, and
# neither the OpenAI-compat nor native /api/chat think:false flag suppressed
# it on Ollama 0.20.7 — unusable for voice latency. qwen2.5:1.5b generates in
# ~170ms once warm and is what api/main.py already preloads at startup.
_openai_failed_until: float = 0.0 # epoch time; 0 = circuit closed (use OpenAI)

# response_lang codes that currently justify spending an OpenAI call — see
# Settings.openai_urdu_only in api/config.py. Everything else routes to
# Ollama directly instead of eating a guaranteed-429 round trip first.
_URDU_LANGS = frozenset({"ur", "ur_roman", "mixed"})

# A fresh AsyncOpenAI() rebuilds the whole httpx connection pool (new TCP+TLS
# handshake) on every single voice turn. Combined with the SDK's default
# max_retries=2, one slow/failed connect attempt turns an 8s timeout into a
# ~16-24s wall-clock stall that starves the WS keepalive — live-measured via
# asyncio debug mode as a 9.96s single-step block in _producer(). We already
# have our own circuit-breaker + Ollama fallback above, so SDK-level retries
# only add uncontrolled latency; disable them and reuse one pooled client.
_openai_client: "AsyncOpenAI | None" = None


def _get_openai_client() -> "AsyncOpenAI":
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        from api.config import settings as _cfg
        _openai_client = AsyncOpenAI(
            api_key=_cfg.openai_api_key, timeout=_OPENAI_TIMEOUT_S, max_retries=0,
        )
    return _openai_client

# Sentence boundary after .  !  ?  (or Urdu ۔ ؟) followed by whitespace or
# end-of-string. Urdu ۔ (U+06D4 Arabic full stop) and ؟ (U+061F Arabic
# question mark) are what the model actually emits for Urdu-script replies
# (response_lang="ur", per _LANG_REPLY_INSTRUCTIONS below) — without them
# here, a whole Urdu response never split into sentences until the stream
# ended, silently defeating the pipelined-synthesis latency win this module
# exists for, and denying the response any inter-sentence pause at all.
_SENT_RE = re.compile(r'(?<=[.!?۔؟])(?:\s+|$)')
# Strip markdown artifacts before TTS
_MD_RE   = re.compile(r'\*{1,2}([^*]+)\*{1,2}|`([^`]+)`|#{1,6}\s|>\s?|~~([^~]+)~~|\[([^\]]+)\]\([^)]+\)')

VOICE_SYSTEM_PROMPT = (
    "You are Xyron, a voice-first AI assistant built by Tayyab Aziz. "
    "No markdown, no lists, no bullet points. "
    "Keep responses concise (1-2 sentences) unless detail is genuinely needed. "
    "Reply in plain English only."
)

# response_lang -> reply-language instruction. Codes match
# api.services.language_detector / api.services.response_language output
# (en | ur | ur_roman | hi | ar | mixed) — this module does not detect
# language itself, it only speaks whatever the caller already decided via
# that existing pipeline (voice_ws.py's session_state["ml_resp_lang"]).
_LANG_REPLY_INSTRUCTIONS: dict[str, str] = {
    "en": "Reply in plain English only.",
    "ur": (
        "Reply in natural conversational Urdu script (Nastaliq), the way Pakistanis "
        "actually speak — not overly formal or literary. Keep common technical terms "
        "(Chrome, WhatsApp, GitHub, Excel, file, folder, order, invoice, download, "
        "delivery) in English/Latin script inside the Urdu sentence when that sounds "
        "more natural, exactly as Pakistani speakers do."
    ),
    "ur_roman": (
        "Reply in natural conversational Roman Urdu (Urdu written in Latin letters), "
        "the way Pakistanis actually speak — not overly formal. Keep common technical "
        "terms (Chrome, WhatsApp, GitHub, Excel, file, folder, order, invoice, "
        "download, delivery) in English when that sounds more natural."
    ),
    "hi": "Reply in natural conversational Hindi.",
    "ar": "Reply in natural conversational Arabic.",
    "mixed": (
        "Reply in natural mixed Roman Urdu-English code-switching, the way Pakistanis "
        "actually speak day to day — keep English nouns/technical terms in English, "
        "Urdu grammar and verbs in Roman Urdu. Not overly formal."
    ),
}


def _build_voice_system_prompt(response_lang: str = "en") -> str:
    """Language-aware system prompt — same base persona, swaps only the
    reply-language instruction based on the caller's already-detected
    session language (never re-detects language itself)."""
    instr = _LANG_REPLY_INSTRUCTIONS.get(response_lang, _LANG_REPLY_INSTRUCTIONS["en"])
    return (
        "You are Xyron, a voice-first AI assistant built by Tayyab Aziz. "
        "No markdown, no lists, no bullet points. "
        "Keep responses concise (1-2 sentences) unless detail is genuinely needed. "
        f"{instr}"
    )


def _strip_md(text: str) -> str:
    def _replace(m: re.Match) -> str:
        return next(g for g in m.groups() if g is not None)
    return _MD_RE.sub(_replace, text).strip()


async def _kokoro_async(text: str, voice: str, speed: float) -> Optional[bytes]:
    """Synthesize text with Kokoro; retry once on None or exception."""
    for attempt in range(2):
        try:
            from api.routers.voice import _kokoro_to_wav, _kokoro_executor
            wav = await asyncio.get_running_loop().run_in_executor(
                _kokoro_executor, _kokoro_to_wav, text, voice, speed
            )
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


async def _synthesize_for_lang(text: str, voice: str, speed: float, response_lang: str) -> Optional[bytes]:
    """Route this pipeline's per-sentence synthesis by language, same as
    voice_ws.py's _tts_ml does for tool-completion responses.

    Bug this closes: every synthesis call in this file (streaming
    conversational replies — the general-LLM-fallback path, which is what
    answers anything that doesn't match a deterministic tool) called
    _kokoro_async() directly and unconditionally, regardless of
    response_lang. tts_router.py's Edge-TTS routing (voice/tts_router.py)
    was wired in for tool-completion responses only — a free-form Urdu
    conversational answer never reached it at all, so Kokoro's English
    phonemizer was asked to pronounce Urdu/Arabic script text and either
    produced garbage or returned None outright (live-observed:
    "[Pipeline] Kokoro returned None for 'حسناً، ...'").
    """
    if response_lang == "en":
        return await _kokoro_async(text, voice, speed)
    try:
        from voice.tts_router import synthesize as _route_synth
        return await asyncio.wait_for(
            asyncio.to_thread(_route_synth, text, response_lang, voice),
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning("[Pipeline] tts_router synth failed for lang=%s: %s — falling back to Kokoro",
                        response_lang, exc)
        return await _kokoro_async(text, voice, speed)


async def _ollama_stream(
    transcript: str,
    history: list[dict],
    voice: str,
    speed: float,
    response_lang: str = "en",
) -> AsyncIterator[tuple[str, Optional[bytes], int, bool]]:
    """Fallback: non-streaming Ollama call wrapped as a single-chunk stream."""
    import os as _os
    model = _os.getenv("OLLAMA_MODEL", _OLLAMA_MODEL)
    messages: list[dict] = [{"role": "system", "content": _build_voice_system_prompt(response_lang)}]
    for h in history[-6:]:
        role = h.get("role", "user")
        text = h.get("text", h.get("content", ""))
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": transcript})

    # main.py's boot warmup already confirmed whether `model` is actually
    # pulled and fits in available RAM (mark_service("ollama", ...)) — on a
    # RAM-constrained machine with the model missing/oversized, every one of
    # these calls was previously a guaranteed 404/500 that still cost a full
    # network round-trip (plus retry backoff) on top of the OpenAI failure
    # that got us here in the first place. Skip straight to the fallback
    # text instead of repeating a check we already know will fail.
    from api.services.readiness_service import readiness_service as _rs_ollama
    from api.services.failure_messages import offline_fallback as _offline_fallback
    if not _rs_ollama.is_service_ready("ollama"):
        logger.warning("[MODEL_FALLBACK] ollama skipped — marked unavailable at boot (model=%s)", model)
        text_out = _offline_fallback()
    else:
        try:
            import httpx
            async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=15.0) as client:
                resp = await client.post(
                    "/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": 150, "stream": False},
                )
                resp.raise_for_status()
                text_out = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            logger.warning("[MODEL_FALLBACK] ollama failed: %s", exc)
            text_out = _offline_fallback()

    text_out = _strip_md(text_out)
    if text_out:
        wav = await _synthesize_for_lang(text_out, voice, speed, response_lang)
        yield text_out, wav, 1, True


async def stream_response_with_tts(
    transcript: str,
    history: list[dict],
    voice: str = "af_nova",
    speed: float = 1.0,
    response_lang: str = "en",
) -> AsyncIterator[tuple[str, Optional[bytes], int, bool]]:
    """
    Async generator — yields (sentence_text, wav_bytes, chunk_index, is_final).

    Synthesis is pipelined: as soon as a sentence boundary is found, Kokoro
    starts synthesizing it while OpenAI continues generating the next sentence.
    The consumer receives the audio chunk as soon as synthesis completes.

    response_lang: en | ur | ur_roman | hi | ar | mixed — from the caller's
    already-detected session language (api.services.response_language).
    Controls only which language the reply is generated in, never re-detects.
    """
    global _openai_failed_until
    from api.config import settings as _cfg
    from api.services.model_router import select_model

    if _cfg.openai_urdu_only and response_lang not in _URDU_LANGS:
        # Local-first for non-Urdu: skip the OpenAI attempt entirely instead
        # of paying its full timeout/429 latency before falling back.
        logger.info("[MODEL_ROUTE] lang=%s not urdu — using=ollama reason=openai_urdu_only", response_lang)
        async for item in _ollama_stream(transcript, history, voice, speed, response_lang):
            yield item
        return

    if not _cfg.openai_api_key:
        # No key at all — not a transient failure, so this used to just
        # return an empty stream (dead air for the user). Complex/free-form
        # Urdu requests must still work without OpenAI configured at all
        # (local-first requirement) — go straight to Ollama instead.
        logger.info("[MODEL_ROUTE] no OpenAI key configured — using Ollama directly")
        async for item in _ollama_stream(transcript, history, voice, speed, response_lang):
            yield item
        return

    # Circuit-breaker: OpenAI failed recently → route to Ollama immediately
    use_ollama = time.time() < _openai_failed_until
    if use_ollama:
        logger.info("[MODEL_SWITCH_LOCAL] [MODEL_ROUTE] using=ollama reason=circuit_open remaining=%.0fs",
                    _openai_failed_until - time.time())
        async for item in _ollama_stream(transcript, history, voice, speed, response_lang):
            yield item
        return

    # Pick model based on complexity
    model = select_model(transcript)
    if model in ("local", "offline"):
        model = "gpt-4o-mini"
    logger.info("[MODEL_ROUTE] using=openai model=%s", model)

    messages: list[dict] = [{"role": "system", "content": _build_voice_system_prompt(response_lang)}]
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
        global _openai_failed_until
        pending   = ""
        chunk_idx = 0

        try:
            client = _get_openai_client()

            # Hard backstop: even with max_retries=0, a stalled connect/TLS
            # handshake could still hang past _OPENAI_TIMEOUT_S in rare cases.
            # wait_for guarantees this step can never starve the event loop
            # (and the WS keepalive) beyond one bounded window.
            stream_cm = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=200,
                    temperature=0.7,
                    stream=True,
                ),
                timeout=_OPENAI_TIMEOUT_S,
            )
            async with stream_cm as stream:
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
                                    _synthesize_for_lang(sentence, voice, speed, response_lang)
                                )
                                await queue.put((sentence, tts_task, chunk_idx, False))
                        pending = parts[-1]

        except Exception as exc:
            # A hard billing/quota failure (e.g. "credit_balance_exhausted",
            # "insufficient_quota") will not clear up on its own in 60s the
            # way a transient timeout might — keep hammering OpenAI every
            # minute and every retry just burns _OPENAI_TIMEOUT_S for nothing.
            # Open the circuit much longer for that case specifically.
            _exc_s = str(exc)
            _is_quota_error = any(
                s in _exc_s for s in (
                    "insufficient_quota", "credit_balance_exhausted",
                    "no credits remaining", "invalid_api_key",
                )
            )
            _fallback_s = 1800.0 if _is_quota_error else _FALLBACK_DURATION
            _openai_failed_until = time.time() + _fallback_s
            logger.warning(
                "[MODEL_SWITCH_LOCAL] [MODEL_FALLBACK] openai stream failed: %s — using ollama for %.0fs%s",
                exc, _fallback_s, " (billing/quota — long backoff)" if _is_quota_error else "",
            )
            # This turn is the one that *discovered* the failure — if nothing
            # was already streamed to the user (chunk_idx == 0), don't leave
            # them with dead air. Previously this path fell straight through
            # to the empty sentinel below and the user got total silence
            # (live-observed: "[Pipeline] done — 0 chunks" + TTS_NO_AUDIO_SENT).
            # Answer THIS turn via Ollama too, not just subsequent ones.
            if chunk_idx == 0:
                try:
                    async def _passthrough(_w: Optional[bytes]) -> Optional[bytes]:
                        return _w
                    async for _sent, _wav, _idx, _fin in _ollama_stream(transcript, history, voice, speed, response_lang):
                        chunk_idx += 1
                        tts_task = asyncio.create_task(_passthrough(_wav))
                        await queue.put((_sent, tts_task, chunk_idx, _fin))
                except Exception as _fallback_exc:
                    logger.error("[MODEL_FALLBACK] same-turn ollama rescue also failed: %s", _fallback_exc)
                    if chunk_idx == 0:
                        # Both OpenAI and the Ollama rescue failed with nothing
                        # queued — without this, the turn ends in total silence
                        # (no text, no audio, no exception the caller can catch).
                        from api.services.failure_messages import offline_fallback as _offline_fallback_p
                        _msg = _offline_fallback_p()
                        chunk_idx += 1
                        tts_task = asyncio.create_task(
                            _synthesize_for_lang(_msg, voice, speed, response_lang)
                        )
                        await queue.put((_msg, tts_task, chunk_idx, True))
        finally:
            # Flush remaining fragment as final
            remaining = _strip_md(pending)
            if remaining:
                chunk_idx += 1
                tts_task = asyncio.create_task(_synthesize_for_lang(remaining, voice, speed, response_lang))
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
    system_override: Optional[str] = None,
    response_lang: str = "en",
) -> str:
    """
    Non-streaming LLM call. Returns the full response text.
    Used for tool narration, clarification prompts, and low-latency paths.

    response_lang: en | ur | ur_roman | hi | ar | mixed — see stream_response_with_tts.
    Ignored when system_override is given (caller owns the full prompt then).
    """
    global _openai_failed_until
    from api.config import settings as _cfg

    async def _ollama_quick(sys_prompt: str) -> str:
        import os as _os
        import httpx
        ol_model = _os.getenv("OLLAMA_MODEL", _OLLAMA_MODEL)
        logger.info("[MODEL_ROUTE] quick_response using=ollama model=%s", ol_model)
        msgs = [{"role": "system", "content": sys_prompt}]
        for h in (history or [])[-6:]:
            r = h.get("role", "user")
            t = h.get("text", h.get("content", ""))
            if r in ("user", "assistant") and t:
                msgs.append({"role": r, "content": t})
        msgs.append({"role": "user", "content": transcript})
        try:
            async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=15.0) as client:
                resp = await client.post(
                    "/chat/completions",
                    json={"model": ol_model, "messages": msgs, "max_tokens": 150, "stream": False},
                )
                resp.raise_for_status()
                return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            logger.warning("[MODEL_FALLBACK] ollama quick_response failed: %s", exc)
            from api.services.failure_messages import offline_fallback as _offline_fallback_qr
            return _offline_fallback_qr()

    if _cfg.openai_urdu_only and response_lang not in _URDU_LANGS:
        # Local-first for non-Urdu — see Settings.openai_urdu_only.
        logger.info("[MODEL_ROUTE] quick_response lang=%s not urdu — using=ollama reason=openai_urdu_only", response_lang)
        return await _ollama_quick(system_override or _build_voice_system_prompt(response_lang))

    if not _cfg.openai_api_key:
        # No key at all — go straight to Ollama instead of a canned English
        # non-answer (local-first requirement; must not silently degrade).
        return await _ollama_quick(system_override or _build_voice_system_prompt(response_lang))

    # Circuit-breaker: OpenAI failed recently → call Ollama instead
    if time.time() < _openai_failed_until:
        return await _ollama_quick(system_override or _build_voice_system_prompt(response_lang))

    sys_prompt = system_override or _build_voice_system_prompt(response_lang)
    messages: list[dict] = [{"role": "system", "content": sys_prompt}]
    for h in (history or [])[-6:]:
        role = h.get("role", "user")
        txt = h.get("text", h.get("content", ""))
        if role in ("user", "assistant") and txt:
            messages.append({"role": role, "content": txt})
    messages.append({"role": "user", "content": transcript})

    try:
        client = _get_openai_client()
        logger.info("[MODEL_ROUTE] quick_response using=openai model=%s", model)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model, messages=messages, max_tokens=150, temperature=0.7,
            ),
            timeout=_OPENAI_TIMEOUT_S,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        _openai_failed_until = time.time() + _FALLBACK_DURATION
        logger.warning("[MODEL_FALLBACK] openai quick_response failed: %s — ollama for %.0fs",
                       exc, _FALLBACK_DURATION)
        # This turn discovered the failure — answer it via Ollama too instead
        # of a canned English non-answer (mirrors stream_response_with_tts's
        # same-turn rescue).
        return await _ollama_quick(sys_prompt)
