"""
Natural Urdu Acknowledgment Generator — uses the local qwen2.5:1.5b model
to generate natural, varied Urdu acknowledgment phrases instead of fixed
templates.

The qwen model CAN generate Urdu text — it just can't UNDERSTAND or CORRECT
Urdu. So we use it ONLY for response phrasing, not for comprehension:
  - Input: English ack text + tool info ("Opening Chrome.")
  - Output: Natural Urdu ack ("Chrome کھول رہا ہوں۔" or "چھوٹی، کھلتا ہوں۔")

Falls back to template-based localize_response if the model is unavailable,
times out, or produces invalid output.

Log markers:
  [URDU_ACK_GEN] input= output= ms=
  [URDU_ACK_GEN_FALLBACK] reason=
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Timeout for the ack generation call. qwen2.5:1.5b (non-Urdu langs, local
# path) responds in ~0.8-1.5s warm (measured 2026-08-24) — kept short there
# since this sits on the critical path of every non-English TTS response
# (voice_ws.py awaits it before speaking) and a cold-load stall must
# degrade to the template localizer (near-instant) rather than block audio
# for ~15s. Urdu-family langs now call OpenAI first (2026-09-04) — a
# network round-trip typically takes 1-3s but can occasionally run longer
# (live-observed up to ~5s for a short completion on a cold connection),
# so this timeout is wider than the original 2s/3s local-only value to
# avoid falsely discarding a genuinely-in-flight OpenAI response and
# falling back to the (worse-quality) template ack for no real reason.
_ACK_GEN_TIMEOUT_S = float(os.getenv("URDU_ACK_GEN_TIMEOUT_S", "6.0"))

# Cache: avoid calling Ollama for the same ack text twice in a session.
# Keyed by (english_ack, lang). Bounded to last 50 entries.
_ACK_CACHE: dict[tuple[str, str], str] = {}
_ACK_CACHE_MAX = 50


def _build_prompt(english_ack: str, lang: str, tool_name: str = "") -> str:
    """Build a prompt that asks qwen for a natural Urdu acknowledgment."""
    _lang_name = {
        "ur": "Urdu Nastaliq script",
        "ur_roman": "Roman Urdu (Latin script, Urdu grammar)",
        "mixed": "Roman Urdu (Latin script, Urdu grammar)",
    }.get(lang, "Roman Urdu")

    _script_hint = ""
    if lang == "ur":
        _script_hint = " Write in Urdu Nastaliq script (اردو رسم الخط)."

    return (
        f"You are Xyron, a voice assistant. Translate this English acknowledgment "
        f"to natural {_lang_name}.{_script_hint} "
        f"Keep it SHORT (max 6 words), natural, and conversational — like a "
        f"real person talking, not a robot. Reply with ONLY the translated "
        f"phrase, no quotes, no explanation.\n"
        f"English: {english_ack}"
    )


def _generate_sync(english_ack: str, lang: str, tool_name: str = "") -> Optional[str]:
    """Returns the generated Urdu text or None.

    2026-09-04: OpenAI (gpt-4o-mini) is now tried FIRST for Urdu-family
    langs (ur/ur_roman/mixed — this module's whole scope, see its module
    docstring) — live-caught quality bug: the local qwen2.5:1.5b model
    produced visibly wrong output for conversational fallback text (e.g.
    "عذرًا، دلایل نمی‌باشند – برو مجددا؟" for "Sorry, that command wasn't
    clear — try again", mixing in Farsi/Arabic script artifacts, not
    coherent Urdu). openai_client.generate() already falls back to local
    Ollama internally on failure/quota (see openai_client.py's
    _ollama_fallback), so this can only ever match-or-beat the previous
    quality, never fall below it.
    """
    try:
        prompt = _build_prompt(english_ack, lang, tool_name)
        if lang in ("ur", "ur_roman", "mixed"):
            from api.services.openai_client import openai_client
            result = openai_client.generate(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-4o-mini",
                max_tokens=40,
                temperature=0.7,
            )
        else:
            # complex=True -> LOCAL_OLLAMA_MODEL (qwen2.5:1.5b), NOT
            # complex=False's llama3.2:3b. Found during real-pipeline
            # validation (2026-08-24): llama3.2:3b measured a 30s cold load
            # on this machine (vs qwen2.5:1.5b's 12-16s) against this
            # module's 2.0s timeout — meaning this call had essentially
            # never actually succeeded from cold, silently falling back to
            # the template layer every time. It also produced visibly
            # broken Roman Urdu in a sample check ("Khulay Google Chrome
            # Kharāy ja raha hai" for "Opening Chrome.") — llama3.2:3b was
            # never evaluated for Urdu quality the way qwen2.5:1.5b was
            # (see local_comprehension.py's model-choice docstring).
            from api.services.openai_client import offline_generate
            result = offline_generate(prompt, complex=True)
        if not result:
            return None
        # Clean up: strip quotes, whitespace, markdown
        result = result.strip().strip('"`').strip()
        # Reject if it's empty or too long (> 200 chars = model went off-rails)
        if not result or len(result) > 200:
            return None
        # Reject if it looks like English (no Urdu/Roman Urdu markers)
        _urdu_chars = sum(1 for c in result if 0x0600 <= ord(c) <= 0x06FF)
        _urdu_words = {"ho", "raha", "rahi", "khol", "band", "karo", "gay",
                       "hua", "hogaya", "liya", "diya", "abhi", "theek"}
        _words = result.lower().split()
        _urdu_word_hits = sum(1 for w in _words if w.rstrip(".,!?") in _urdu_words)
        if _urdu_chars == 0 and _urdu_word_hits == 0:
            # Model returned English — reject
            return None
        # ── Script-agreement guard (real-mic Urdu test Issue 3C) ─────────────
        # Live failure: lang=ur_roman was requested but qwen emitted Arabic-
        # script nonsense ("تحاكي الافتتاحية." for "Opening Settings.") and
        # the check above PASSED it because Arabic lives in the same 0x0600–
        # 0x06FF block the Urdu-char test counts. Roman Urdu must be Latin
        # script by definition — any Arabic-block character is a script
        # disagreement and the output is rejected outright.
        if lang in ("ur_roman", "mixed"):
            if _urdu_chars > 0:
                logger.info("[URDU_ACK_GEN_REJECTED] reason=script_mismatch lang=%s out=%r",
                            lang, result[:40])
                return None
        elif lang == "ur":
            # Urdu Nastaliq requested: the Arabic block is shared, so reject
            # plain-Arabic output by requiring at least one letter that only
            # exists in the Urdu alphabet (ے ھ ڈ ں ٹ پ آ گ چ ژ ...).
            _URDU_SPECIFIC_LETTERS = set("\u06D2\u06BE\u0688\u06BA\u0679\u067E\u0622\u06AF\u0686\u0698\u06A9\u06CC")
            if _urdu_chars > 0 and not any(c in _URDU_SPECIFIC_LETTERS for c in result):
                logger.info("[URDU_ACK_GEN_REJECTED] reason=arabic_not_urdu out=%r", result[:40])
                return None
        return result
    except Exception as exc:
        logger.debug("[URDU_ACK_GEN_ERROR] %s", exc)
        return None


async def generate_natural_ack(
    english_ack: str,
    lang: str,
    tool_name: str = "",
) -> Optional[str]:
    """
    Generate a natural Urdu acknowledgment using qwen2.5:1.5b.

    Returns the generated Urdu text, or None if the model is unavailable,
    times out, or produces invalid output (caller should use template fallback).
    """
    # Check cache first
    _cache_key = (english_ack, lang)
    if _cache_key in _ACK_CACHE:
        logger.debug("[URDU_ACK_GEN_CACHE_HIT] %r", english_ack[:40])
        return _ACK_CACHE[_cache_key]

    try:
        _t0 = time.time()
        result = await asyncio.wait_for(
            asyncio.to_thread(_generate_sync, english_ack, lang, tool_name),
            timeout=_ACK_GEN_TIMEOUT_S,
        )
        _ms = (time.time() - _t0) * 1000

        if result:
            logger.info("[URDU_ACK_GEN] input=%r output=%r ms=%.0f",
                        english_ack[:40], result[:40], _ms)
            # Cache the result
            if len(_ACK_CACHE) >= _ACK_CACHE_MAX:
                # Evict oldest entry (dict preserves insertion order in 3.7+)
                _oldest = next(iter(_ACK_CACHE))
                del _ACK_CACHE[_oldest]
            _ACK_CACHE[_cache_key] = result
            return result

        logger.info("[URDU_ACK_GEN_FALLBACK] reason=no_result ms=%.0f", _ms)
        return None
    except asyncio.TimeoutError:
        logger.info("[URDU_ACK_GEN_FALLBACK] reason=timeout(%ss)", _ACK_GEN_TIMEOUT_S)
        return None
    except Exception as exc:
        logger.info("[URDU_ACK_GEN_FALLBACK] reason=error:%s", exc)
        return None


async def localize_with_fallback(
    english_ack: str,
    lang: str,
    tool_name: str = "",
) -> str:
    """
    Localize a response: deterministic template layer FIRST, qwen generation
    only for genuinely conversational text.

    Real-mic Urdu test Issue 3: this used to call qwen FIRST for every
    non-English ack, including deterministic tool acks ("Opening Settings.").
    On a 4GB-VRAM GPU shared with Whisper the model was frequently cold or
    starved, and when it did answer it occasionally emitted wrong-script
    nonsense ("تحاكي الافتتاحية."). Deterministic tool acks need no
    generation at all — response_localizer's templates already produce
    natural Pakistani phrasing ("{app} khol raha hoon.") instantly and
    can never drift in script or content. qwen is now reserved for the
    conversational remainder (no template match AND no tool attached).

    Always returns a string (never None) — if both generation and template
    fail, returns the original English text.
    """
    # 1. Template layer first — instant, deterministic, script-safe. Covers
    #    every deterministic tool ack shape ("Opening X.", volume, done, ...).
    try:
        from api.services.response_localizer import localize_response
        templated = localize_response(english_ack, lang)
        if templated:
            return templated
    except Exception:
        pass

    # 2. A deterministic tool command whose text no template matched still
    #    must NOT go through qwen — a wrong-script guess on a tool ack is
    #    worse than the English original. Keep qwen for conversational text
    #    only (tool_name empty == nothing routed/executed).
    if tool_name:
        logger.info("[URDU_ACK_GEN_SKIPPED] reason=deterministic_tool_no_template tool=%s text=%r",
                    tool_name, english_ack[:40])
        return english_ack

    # 3. Conversational text — try natural generation, then fall back to the
    #    generic template ack.
    natural = await generate_natural_ack(english_ack, lang, tool_name)
    if natural:
        return natural

    try:
        from api.services.response_localizer import _GENERIC_ACK
        generic = _GENERIC_ACK.get(lang)
        if generic:
            return generic
    except Exception:
        pass

    # Last resort: return the original English text
    return english_ack
