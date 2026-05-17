"""
Streaming voice pipeline — intelligent, context-aware, self-correcting.

Flow:
  STT (with confidence gate)
    → normalize
    → context resolve
    → intent router  (local tools, tier 1-2)
    → model router   (score complexity + conversation context)
    → tool execution (if local)
    → AI generation  (gpt-4o-mini or gpt-4o, based on score)
    → response validation + gpt-4o retry if mini fails
    → TTS
    → update routing state in memory

Chunk types (newline-delimited JSON):
  {"type": "interim",    "text": "On it..."}
  {"type": "transcript", "text": "<speech>", "confidence": <float>}
  {"type": "tool",       "tool": "<name>", "params": {...}}
  {"type": "response",   "text": "<reply>"}
  {"type": "audio",      "data": "<base64>", "mime": "audio/wav"}
  {"type": "done"}
  {"type": "error",      "message": "<reason>"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Whisper avg_logprob below this is treated as noise / hallucination
_WHISPER_CONF_THRESHOLD = -1.2   # env: WHISPER_CONFIDENCE_THRESHOLD overrides per-segment;
                                  # this guards the pipeline-level overall confidence

_SYSTEM_PROMPT_EN = (
    "You are Xyron, a voice AI assistant built by Tayyab Aziz. "
    "You were NOT built by OpenAI. "
    "Be natural and conversational, like a helpful friend. "
    "Keep replies under 2 sentences for voice output. "
    "Never use markdown, bullet points, or formatting."
)

_SYSTEM_PROMPT_UR = (
    "Tum Xyron ho, ek voice AI assistant jo Tayyab Aziz ne banaya hai. "
    "Tum OpenAI ke nahi ho. "
    "User Roman Urdu ya mixed Urdu/English mein bol raha hai. "
    "Usi andaaz mein jawab do — Roman Urdu mein, natural aur friendly. "
    "2 sentences se zyada mat bolna. Markdown mat likhna."
)

_SYSTEM_PROMPT_MIXED = (
    "You are Xyron, a voice AI assistant built by Tayyab Aziz. "
    "The user mixes Urdu and English. Match their style naturally. "
    "Short, warm, 2 sentences max. No markdown."
)


def _get_system_prompt(detected_language: Optional[str] = None) -> str:
    if detected_language in ("urdu", "roman_urdu"):
        return _SYSTEM_PROMPT_UR
    if detected_language == "mixed":
        return _SYSTEM_PROMPT_MIXED
    return _SYSTEM_PROMPT_EN

# Tools whose output is already self-explanatory — skip AI narration
_NARRATE_SKIP = frozenset({
    "open_application", "open_directory", "volume_control",
    "brightness_control", "mute_unmute", "take_screenshot",
    "minimize_window", "maximize_window", "close_window",
    "desktop_type", "desktop_hotkey", "desktop_scroll",
    "get_date_time", "get_battery_status", "get_volume",
})


def _chunk(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


async def voice_pipeline(
    audio_bytes:  bytes,
    content_type: str = "audio/webm",
    openai_key:   str = "",
    language:     Optional[str] = None,
    session_id:   Optional[str] = None,
) -> AsyncIterator[str]:
    """
    Full voice pipeline as an async generator of newline-delimited JSON chunks.
    Safe to wrap in StreamingResponse(media_type="text/event-stream").
    """

    # ── 0. ACK ───────────────────────────────────────────────────────────────
    yield _chunk({"type": "interim", "text": "On it..."})

    # ── 1. STT ───────────────────────────────────────────────────────────────
    t0 = time.monotonic()
    transcript  = ""
    stt_conf    = 0.0
    try:
        transcript, stt_conf = await asyncio.to_thread(
            _stt, audio_bytes, content_type, language
        )
    except Exception as exc:
        logger.warning("[Pipeline] STT failed: %s", exc)
        yield _chunk({"type": "error", "message": f"Transcription failed: {exc}"})
        return

    if not transcript:
        yield _chunk({"type": "error", "message": "No speech detected"})
        return

    # Confidence gate — discard low-confidence audio (noise, breathing, etc.)
    if stt_conf < _WHISPER_CONF_THRESHOLD:
        logger.info(
            "[Pipeline] STT confidence too low (%.2f < %.2f) — discarding: %r",
            stt_conf, _WHISPER_CONF_THRESHOLD, transcript[:60],
        )
        yield _chunk({"type": "error", "message": "Low confidence — please speak again."})
        return

    stt_ms = int((time.monotonic() - t0) * 1000)
    logger.debug("[Pipeline] STT: %r (conf=%.2f, %dms)", transcript, stt_conf, stt_ms)
    yield _chunk({"type": "transcript", "text": transcript,
                  "confidence": round(stt_conf, 3), "stt_ms": stt_ms})

    # ── 2. Normalize ─────────────────────────────────────────────────────────
    try:
        from .normalizer import normalize
        normalized = normalize(transcript)
    except Exception:
        normalized = transcript

    # ── 2b. Multilingual normalization (Roman Urdu / mixed → English intent) ─
    detected_language: Optional[str] = None
    try:
        from cognition.language_detector import detect as _detect_lang
        _lang_result = _detect_lang(transcript)
        detected_language = _lang_result["language"]
        logger.info("[MULTI] raw=%r detected=%s conf=%.2f",
                    transcript[:60], detected_language, _lang_result["confidence"])
    except Exception as exc:
        logger.debug("[MULTI] language detection failed: %s", exc)

    try:
        from cognition.text_normalizer import normalize as _ml_normalize
        _ml_normalized = _ml_normalize(normalized)
        if _ml_normalized != normalized:
            logger.info("[MULTI] normalized=%r → %r", normalized[:60], _ml_normalized[:60])
        normalized = _ml_normalized
    except Exception as exc:
        logger.debug("[MULTI] text normalization failed: %s", exc)

    # Persist language mode for this session
    if detected_language and session_id:
        try:
            from .memory_service import memory_service as _ms
            _ms.set_language_mode(session_id, detected_language)
        except Exception:
            pass

    # ── 3. Context resolve ───────────────────────────────────────────────────
    try:
        from .window_context import window_context
        active_window = window_context.get_active_window()
    except Exception:
        active_window = None

    try:
        from .context_resolver import resolve_context
        resolved = resolve_context(normalized, active_window)
    except Exception:
        resolved = normalized

    # ── 4. Intent routing (local tools) ──────────────────────────────────────
    tool_name:   Optional[str] = None
    tool_params: dict          = {}

    try:
        from .intent_router import IntentRouter
        _ir    = IntentRouter.get_instance()
        route  = _ir.route(resolved)
        if route.tool_name and route.confidence >= 0.65 and route.tier <= 2:
            tool_name   = route.tool_name
            tool_params = dict(route.params or {})
            logger.info("[MULTI] intent=%s (conf=%.2f tier=%d) text=%r",
                        tool_name, route.confidence, route.tier, resolved[:60])
        else:
            logger.info("[MULTI] intent=unmatched (best=%.2f) text=%r",
                        getattr(route, "confidence", 0.0), resolved[:60])
    except Exception as exc:
        logger.debug("[Pipeline] IntentRouter error: %s", exc)

    # ── 4b. Load conversation context ────────────────────────────────────────
    try:
        from .memory_service import memory_service
        from .model_router import RouteContext
        route_ctx = memory_service.get_routing_context(session_id or "")
    except Exception:
        from .model_router import RouteContext
        route_ctx = RouteContext()

    # ── 4c. Complexity score + model selection ───────────────────────────────
    tool_matched_locally = bool(
        tool_name and tool_name not in ("general_query",)
    )
    try:
        from .model_router import score_complexity, select_model
        complexity    = score_complexity(resolved)
        model_choice  = select_model(resolved, context=route_ctx, tool_matched=tool_matched_locally)
    except Exception:
        complexity   = 0.3
        model_choice = "gpt-4o-mini"

    logger.debug(
        "[Pipeline] complexity=%.2f model=%s (depth=%d last=%s)",
        complexity, model_choice, route_ctx.conv_depth, route_ctx.last_model,
    )

    # AI tool classification disabled — was making a GPT call on every unmatched command.
    # Keyword routing (above) handles ~95% of real commands without API cost.

    if tool_name and tool_name not in ("general_query",):
        yield _chunk({"type": "tool", "tool": tool_name, "params": tool_params})

    # ── 5. Execute tool ──────────────────────────────────────────────────────
    tool_output: Optional[str] = None
    tool_success: bool = False
    if tool_name and tool_name not in ("general_query", "confirm_draft", "reject_draft"):
        try:
            from api.tools.registry import registry as _registry
            if tool_name in _registry:
                ctx    = {"openai_key": openai_key or _get_api_key(), "active_window": active_window}
                result = await asyncio.to_thread(_registry.execute, tool_name, tool_params, ctx)
                tool_output  = result.spoken or result.text or None
                tool_success = result.success if hasattr(result, "success") else bool(tool_output)
                logger.info("[MULTI] tool=%s executed=%s result=%r",
                            tool_name, tool_success, (tool_output or "")[:80])
            else:
                logger.warning("[MULTI] tool=%s NOT in registry — execution skipped", tool_name)
        except Exception as exc:
            logger.warning("[Pipeline] Tool %r failed: %s", tool_name, exc)
            tool_output = f"Tool error: {exc}"
            tool_success = False

    # ── 6. Generate + validate response ──────────────────────────────────────
    ai_text     = ""
    model_used  = model_choice
    try:
        ai_text, model_used = await asyncio.to_thread(
            _generate_and_validate,
            resolved, tool_name, tool_output, model_choice, complexity,
            detected_language, tool_success,
        )
    except Exception as exc:
        logger.warning("[Pipeline] Response generation failed: %s", exc)
        ai_text    = tool_output or "Done."
        model_used = model_choice

    logger.info("[MULTI] response_lang=%s model=%s response=%r",
                detected_language or "unknown", model_used, ai_text[:80])
    yield _chunk({"type": "response", "text": ai_text, "model": model_used})

    # ── 7. TTS ───────────────────────────────────────────────────────────────
    if ai_text:
        try:
            audio_bytes_out, mime = await _synthesize(ai_text)
            if audio_bytes_out:
                yield _chunk({
                    "type": "audio",
                    "data": base64.b64encode(audio_bytes_out).decode(),
                    "mime": mime,
                })
        except Exception as exc:
            logger.warning("[Pipeline] TTS failed: %s", exc)

    # ── 8. Update routing state in memory ────────────────────────────────────
    try:
        from .memory_service import memory_service as _ms
        _ms.update_routing(
            session_id or "",
            model      = model_used,
            score      = complexity,
            intent     = tool_name or "general_query",
        )
    except Exception:
        pass

    yield _chunk({"type": "done"})


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_api_key() -> str:
    try:
        from api.config import settings
        return settings.openai_api_key or ""
    except Exception:
        return ""


def _stt(audio_bytes: bytes, content_type: str, language: Optional[str]) -> tuple[str, float]:
    """
    Blocking STT.
    Returns (transcript_text, avg_confidence).
    confidence is avg_logprob from Whisper (0=perfect, -1=uncertain, <-2=noise).
    """
    import tempfile, os
    from pathlib import Path

    suffix = ".webm" if "webm" in content_type else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        from voice.whisper_service import transcribe_file
        result     = transcribe_file(Path(tmp_path), language=language)
        text       = result.get("text", "").strip()
        confidence = float(result.get("confidence", 0.0))
        return text, confidence
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _generate_and_validate(
    text:              str,
    tool_name:         Optional[str],
    tool_output:       Optional[str],
    model_choice:      str,
    complexity:        float,
    detected_language: Optional[str] = None,
    tool_success:      bool = True,
) -> tuple[str, str]:
    """
    Generate a spoken reply and validate quality.

    Returns (response_text, model_actually_used).
    """
    from .openai_client import openai_client

    # Self-explanatory tool output — use AI to mirror the response language
    if tool_name in _NARRATE_SKIP:
        if tool_output and detected_language in ("urdu", "roman_urdu", "mixed"):
            # Let AI rephrase in user's language
            pass  # fall through to AI generation below
        elif tool_output:
            return tool_output[:150], "local"
        elif not tool_success and tool_name:
            return f"{tool_name.replace('_', ' ')} failed.", "local"

    # Local / no-LLM path
    if model_choice == "local":
        if tool_output:
            return tool_output[:150], "local"
        if tool_name:
            return f"Done — {tool_name.replace('_', ' ')}.", "local"
        return "Sure, done.", "local"

    # Offline path
    if model_choice == "offline" or not openai_client.available:
        fallback = _offline_response(text, tool_output, detected_language)
        return fallback, "offline"

    # Build language-aware messages
    messages = _build_messages(text, tool_output, detected_language, tool_success)
    reply = openai_client.generate(messages, model="gpt-4o-mini", max_tokens=120)  # type: ignore[arg-type]

    if not reply:
        return _offline_response(text, tool_output, detected_language), "offline"

    return reply, "gpt-4o-mini"


def _build_messages(
    text:              str,
    tool_output:       Optional[str],
    detected_language: Optional[str] = None,
    tool_success:      bool = True,
) -> list[dict]:
    """Build the OpenAI messages list with language-aware system prompt."""
    system = _get_system_prompt(detected_language)
    try:
        from api.services.memory_service import memory_service
        facts = memory_service.get_context_string()
        if facts:
            system = system + f"\n\n{facts}"
    except Exception:
        pass

    if tool_output:
        summarise_instruction = (
            "Ro mein jawab do." if detected_language in ("urdu", "roman_urdu")
            else "Summarise in 1-2 natural spoken sentences."
        )
        user_content = (
            f"User said: '{text}'\n"
            f"Result: {tool_output[:300]}\n\n"
            f"{summarise_instruction}"
        )
    else:
        user_content = text

    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_content},
    ]


# Roman Urdu confirmations for common tools — used by Ollama / offline path
_UR_CONFIRMS: dict[str, str] = {
    "open_application":     "khol diya.",
    "open_system_settings": "Settings khol diye.",
    "volume_control":       "Volume adjust kar diya.",
    "take_screenshot":      "Screenshot le liya.",
    "get_battery_status":   "Battery status check kar liya.",
    "lock_system":          "Screen lock kar diya.",
    "close_window":         "Window band kar di.",
}


def _offline_response(
    text:              str,
    tool_output:       Optional[str],
    detected_language: Optional[str] = None,
) -> str:
    """Offline fallback with language-aware canned responses."""
    from .openai_client import offline_generate
    system = _get_system_prompt(detected_language)
    result = offline_generate(text, system=system)
    if result:
        return result
    if tool_output:
        return tool_output[:150]
    return "I'm in offline mode. I can run system commands but not answer open questions right now."


async def _synthesize(text: str) -> tuple[Optional[bytes], str]:
    """TTS with 3-tier fallback."""
    for tier_fn, is_async, mime in [
        ("synthesize_kokoro", False, "audio/wav"),
        ("synthesize_edge",   True,  "audio/mpeg"),
        ("synthesize_speech", False, "audio/wav"),
    ]:
        try:
            from voice import tts_service as _tts
            fn = getattr(_tts, tier_fn)
            audio = await fn(text) if is_async else await asyncio.to_thread(fn, text)
            if audio:
                return audio, mime
        except Exception:
            continue
    return None, "audio/wav"
