"""
Local Qwen semantic canonicalization — structured meaning extraction for
Urdu / Roman Urdu / mixed-language commands that the deterministic fast path
(ml_normalizer + mixed_language_engine + intent_router tiers 1-3) did not
confidently match.

This is Phase 2 of the routing pipeline, not a replacement for it:

    transcript
      -> intent_router (regex/semantic, <100ms, no LLM)  [unchanged]
      -> confident match (>=0.55)? execute directly        [unchanged]
      -> NO match / low confidence
           -> comprehend() (this module) via local Ollama qwen2.5:1.5b
              -> structured slots: action/object_type/name/scope/
                 time_reference/context_reference/confidence
           -> validate_and_map():
                1. confidence gate
                2. resolve context_reference via ContextStack (never guesses
                   a name the model wasn't given)
                3. synthesize a plain ENGLISH canonical sentence from the
                   resolved slots (e.g. "open folder Perfume in E drive")
                4. feed that sentence into the SAME intent_router.route()
                   the deterministic tier and English users already go
                   through
           -> intent_router found a confident tool? -> caller executes it
           -> otherwise -> caller falls through to general_query

Why synthesize back to English instead of mapping action->tool directly:
this is the entire point of the design. A direct action->tool map (the
previous version of this module) tops out at whatever tools someone
remembered to add an entry for — today that was 4 tools out of the whole
registry. Synthesizing a canonical English sentence and handing it to
intent_router.route() means ANY tool intent_router already understands in
English is automatically reachable from Urdu/Roman Urdu/mixed, including
tools added after this module was written, with zero extra mapping code.
mixed_language_engine.py already proves this pattern works for the
deterministic tier; this module applies the same pattern one tier up.

Qwen NEVER executes a tool and is never trusted blindly: comprehend()
returns a proposal, validate_and_map() checks confidence, resolves context
references through the real ContextStack (not the model's guess), and only
ever executes what intent_router.route() — which only ever returns tools
that actually exist in the registry — confidently resolves to. This module
cannot invent a tool name because it never assigns one directly; the only
tool names that can come out of validate_and_map() are ones intent_router
itself already trusts for English text.

Model choice: qwen2.5:1.5b, selected 2026-08-19 after benchmarking
qwen2.5:1.5b / qwen3:4b / llama3.2:3b on 18 Roman Urdu / Urdu-script / mixed
test sentences. qwen3:4b was excluded outright — its installed build ignores
the `think:false` request param and burns ~11s of hidden reasoning tokens
even for a trivial 2-field JSON reply. qwen2.5:1.5b and llama3.2:3b tied on
comprehension (17/18 and 18/18 intent-allowlisted/JSON-valid respectively,
one questionable entity per model); qwen2.5:1.5b won on the tiebreak
criteria (1.4GB vs 2.8GB resident VRAM, ~1.0s vs ~1.5s avg latency) given
this runs on a 4GB T1200 already shared with Whisper/Kokoro/XTTS. See
api.services.openai_client.LOCAL_OLLAMA_MODEL to override.

Timeout / warm-keep (2026-08-24 investigation — see the trailing
[LOCAL_COMPREHEND_ERROR] "timed out" reports from real Urdu sentences):
measured with Ollama's own load_duration/prompt_eval_duration/eval_duration
breakdown, on an idle GPU (0% util, ruled out contention), two separate
cold calls with the REAL system prompt below (859 tokens) cost
11.6s-15.7s in load_duration alone — model weights loading into VRAM.
Prompt eval (the full 859-token system prompt) cost only ~2.0-2.2s, and
generation ~0.5-0.6s. Once warm, total latency drops to ~0.8-0.9s. So the
old _TIMEOUT_S=8.0 was shorter than a cold load BY ITSELF, before the
model had processed a single token of the actual request — this was never
a "Qwen is slow at understanding Urdu" problem, it was "the model wasn't
resident and nothing kept it that way," identical to the exact failure
mode XTTS's and Whisper's own preload/warm-keep code already exists to
prevent. Two fixes, not one blind timeout bump:
  1. _TIMEOUT_S raised to comfortably clear the measured cold-load range
     (still bounded — a truly hung Ollama fails in 20s, not indefinitely).
  2. keep_alive is now passed on every call (OLLAMA_QWEN_KEEP_ALIVE,
     default "10m") so the model survives gaps between turns in the same
     conversation instead of Ollama's default eviction re-triggering a
     cold load on every Urdu turn. voice_ws.py also fires a background,
     gpu_coordinator-gated pre-warm the first time a session's language
     goes non-English (see ensure_warm() below and its call site next to
     the existing XTTS preload trigger) — the same "predict need, warm in
     the background, never block voice-critical work" pattern XTTS uses,
     not a new mechanism.

Known limitation (honest, not silently papered over): time_reference values
like "yesterday" are extracted but NOT actually filtered by calendar day —
ContextStack has no timestamp-scoped lookup today, only "most recent of this
type". A context_reference resolves to the single most recent matching
entity regardless of time_reference. Building real time-scoped resolution
is future work, not something this module fakes.

Logs:
  [LOCAL_COMPREHEND_START]    transcript + detected_lang
  [LOCAL_COMPREHEND_RAW]      raw model output
  [LOCAL_COMPREHEND_INVALID]  reason validation failed
  [ML_CANONICALIZATION]       original= lang= method= canonical= confidence= context_refs= latency_ms=
  [LOCAL_COMPREHEND_MAPPED]   canonical -> tool_name/tool_params (via intent_router)
  [LOCAL_COMPREHEND_UNMAPPED] understood but no confident tool match
  [LOCAL_COMPREHEND_ERROR]    Ollama call failed/timed out
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Strict schema the model must fill in ─────────────────────────────────────
# `action` reuses the SAME verb vocabulary mixed_language_engine.py's
# _VERB_MAP already normalizes Roman/Urdu-script verbs to — keeping one
# shared vocabulary between the deterministic and Qwen tiers means the
# synthesis step below (_synthesize_canonical) only has to speak one
# "dialect" of English regardless of which tier produced it.
ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "open", "close", "install", "download", "play", "increase", "decrease",
    "take_screenshot", "search", "show", "lock", "sleep", "shutdown",
    "restart", "mute", "unmute", "delete", "create", "unknown",
})

# object_type values this module knows how to synthesize a canonical
# sentence for today (Stage 1 of the Urdu-parity rollout). Anything else
# comes back as object_type=<value> but stays unmapped — never guessed at.
_STAGE1_OBJECT_TYPES: frozenset[str] = frozenset({
    "application", "folder", "file", "drive", "browser", "website", "screen",
})

MIN_CONFIDENCE = 0.6

# Stricter bar for irreversible-ish, no-object system actions. Found via
# real-pipeline validation (2026-08-24): "Wapis jao." ("go back" — a
# capability Xyron doesn't have; see the "jao" vs "so jao" prompt note
# above) was inconsistently guessed by qwen2.5:1.5b as action=sleep or
# action=lock across separate runs of the SAME input, both times with a
# self-reported confidence of 0.8 — comfortably clearing MIN_CONFIDENCE.
# These four actions have no object to verify a guess against (unlike
# "open Chrome", where a wrong name at least fails a real app lookup) and
# their effect (locking/sleeping/restarting/shutting down the user's
# machine) is the most disruptive class of action in the whole registry —
# exactly the situation instruction #16/#17 warn about: never execute a
# risky interpretation just because Qwen produced confident-looking JSON.
# Raising the bar specifically for this class costs nothing for the
# common case (an unambiguous "lock kar do"/"sleep karo" is caught by the
# FAST deterministic tier — mixed_language_engine/intent_router — long
# before Qwen ever runs; Qwen only sees these four actions when the
# deterministic tier already found no confident match, i.e. already an
# unusual phrasing).
_HIGH_STAKES_NO_OBJECT_ACTIONS = frozenset({"lock", "sleep", "shutdown", "restart"})
_HIGH_STAKES_MIN_CONFIDENCE = 0.9

_SYSTEM_PROMPT = (
    "You are Xyron's local semantic canonicalization engine, running fully "
    "offline on a small device. The user speaks English, Roman Urdu (Urdu "
    "written in Latin script), Urdu Nastaliq script, or mixed Urdu-English. "
    "Given the transcript, extract STRUCTURED MEANING, not a literal "
    "translation. Return ONLY a single strict JSON object (no markdown, no "
    "explanation, no extra text) with this exact shape:\n"
    '{"action": "<one of: open, close, install, download, play, increase, '
    'decrease, take_screenshot, search, show, lock, sleep, shutdown, '
    'restart, mute, unmute, delete, create, unknown>", '
    '"object_type": "<application|folder|file|drive|browser|website|screen|'
    'repository|other|null>", '
    '"name": "<specific target name if the user said one, else null>", '
    '"scope": "<a location qualifier like \'E drive\' or \'Downloads '
    'folder\', else null>", '
    '"time_reference": "<e.g. \'yesterday\', \'today\', else null>", '
    '"context_reference": "<e.g. \'current_app\', \'previously_active_'
    'folder\', \'last_opened_file\' if the user refers to something without '
    'naming it (it/that/wapis/dubara/pehle wala), else null>", '
    '"confidence": <0.0-1.0>}\n'
    "CRITICAL RULES:\n"
    "- Never invent or guess a specific name the user did not say. If they "
    "refer to something vaguely (\"open it again\", \"wo wala folder\", "
    "\"jis pe kaam kar raha tha\"), set name to null and context_reference "
    "to a short description instead — a separate system will resolve it "
    "from real session history, not your guess.\n"
    "- action must be exactly one of the listed verbs. If unsure, use "
    "\"unknown\" with a low confidence rather than guessing.\n"
    "- Extract MEANING, not literal words. Common Roman Urdu → meaning:\n"
    "  * \"Chrome kholo\" → action=open, object_type=application, name=Chrome\n"
    "  * \"Settings band karo\" → action=close, object_type=application, name=Settings\n"
    "  * \"E drive kholo\" → action=open, object_type=drive, name=E\n"
    "  * \"volume barhao\" → action=increase, object_type=null, name=volume\n"
    "  * \"awaz kam karo\" → action=decrease, object_type=null, name=awaz\n"
    "  * \"screenshot lo\" → action=take_screenshot (an explicit request to "
    "SAVE an image of the screen)\n"
    "  * \"screen pe kya hai?\" / \"is page ko explain karo\" → action=show, "
    "object_type=screen (a QUESTION about what's currently visible — the "
    "user wants a spoken description, NOT a saved screenshot file; use "
    "take_screenshot only when they explicitly ask to capture/save/take one)\n"
    "  * \"laptop lock karo\" → action=lock\n"
    "  * \"sleep karo\" / \"so jao\" → action=sleep (NOTE: \"so jao\" means "
    "sleep — \"jao\" ALONE, without \"so\", just means \"go\"/\"go back\" and "
    "is NOT a sleep command; there is no dedicated action for it, so use "
    "action=\"unknown\" with low confidence rather than guessing sleep)\n"
    "  * \"shutdown karo\" → action=shutdown\n"
    "  * \"mute karo\" / \"khamosh karo\" → action=mute\n"
    "  * \"ye file delete karo\" → action=delete, context_reference=current_file\n"
    "  * \"naya folder banao\" → action=create, object_type=folder\n"
    "  * \"YouTube pe gaana chalao\" → action=play, name=gaana, scope=youtube\n"
    "  * \"Spotify pe music bajao\" → action=play, name=music, scope=spotify\n"
    "  * \"Google pe Pakistan weather search karo\" → action=search, "
    "object_type=browser, name=Pakistan weather (the QUERY, never the "
    "engine name — \"Google\"/\"YouTube\"/\"Bing\" here is WHERE to search, "
    "not WHAT to search for)\n"
    "  * \"Google pe search karo\" (no query given) → action=search, "
    "object_type=browser, name=null\n"
    "- Filler words like \"aree\", \"areee\", \"suno\", \"dekho\", \"abey\" "
    "carry no command meaning — ignore them.\n"
    "- Negation words (\"nahi\", \"nahin\", \"mat\", \"na\") mean the user "
    "is cancelling or refusing — set action=\"unknown\" and confidence=0.0.\n"
    "- Mixed language is normal: \"Chrome kholo\" is a valid command, not "
    "an error. Extract the English app name and the Urdu verb meaning.\n"
    "- If the user says \"wo wala folder\" or \"jis pe kaam kar raha tha\", "
    "set name=null and context_reference to a short English description of "
    "what they\'re referring to.\n"
    "- ORDINAL references (\"pehla wala\"/\"the first one\", \"doosra wala\"/"
    "\"the second one\") mean the user is picking from a list of options a "
    "PREVIOUS turn already showed them. You do NOT know what that list "
    "contained — NEVER invent or guess a specific name (do not say "
    "\"Chrome\", do not reuse a name from these instructions' own examples "
    "above) just because a command verb like \"kholo\" is also present. "
    "For \"pehla wala kholo\"/\"دوسرا والا کھولو\" style commands, always "
    "set name=null, object_type=null, and context_reference=\"ordinal:first\" "
    "(or \"ordinal:second\", etc.) — a separate system resolves ordinals "
    "against the real pending list, never your guess."
)

# ── Compound-intent variant of the same schema ──────────────────────────────
# Used by comprehend_multi() (see below) — the deterministic tier
# (mixed_language_engine.split_compound) is the FIRST attempt at compound
# Urdu commands and never calls this; comprehend_multi() is the Tier 4
# fallback for compounds the deterministic splitter couldn't confidently
# resolve (e.g. connectors other than "aur"/"phir", or a clause that
# doesn't independently canonicalize). Same per-intent fields as
# _SYSTEM_PROMPT above, wrapped in a top-level "intents" array so a
# multi-action utterance doesn't have to be squeezed into one action's
# slots. Live bug this closes (2026-09-04 real backend log): "YouTube کو "
# "کھولو اور کوئی گانا چلا دو" against the OLD single-action schema had "
# nowhere to put "play a song" except context_reference, which
# validate_and_map() only ever treats as a pronoun/referent lookup — the
# second action was silently discarded. The explicit two-intent worked
# example below is the fix, not a new capability bolted on top: it tells
# the model the schema HAS room for a second action, which the old
# single-object shape structurally did not.
_SYSTEM_PROMPT_COMPOUND = (
    "You are Xyron's local semantic canonicalization engine, running fully "
    "offline on a small device. The user speaks English, Roman Urdu (Urdu "
    "written in Latin script), Urdu Nastaliq script, or mixed Urdu-English. "
    "The user may ask for ONE thing or SEVERAL things in a single sentence "
    "(e.g. \"open YouTube and play a song\", \"YouTube کو کھولو اور کوئی "
    "گانا چلا دو\"). Extract STRUCTURED MEANING for EACH separate action — "
    "not a literal translation. Return ONLY a single strict JSON object "
    "(no markdown, no explanation, no extra text) with this exact shape:\n"
    '{"intents": [{"action": "<one of: open, close, install, download, '
    'play, increase, decrease, take_screenshot, search, show, lock, sleep, '
    'shutdown, restart, mute, unmute, delete, create, unknown>", '
    '"object_type": "<application|folder|file|drive|browser|website|screen|'
    'repository|other|null>", '
    '"name": "<specific target name if the user said one, else null>", '
    '"scope": "<a location qualifier like \'E drive\' or \'youtube\', else '
    'null>", '
    '"time_reference": "<e.g. \'yesterday\', \'today\', else null>", '
    '"context_reference": "<e.g. \'current_app\', \'previously_active_'
    'folder\', \'last_opened_file\' if the user refers to something without '
    'naming it, else null>", '
    '"confidence": <0.0-1.0>}, ...]}\n'
    "CRITICAL RULES:\n"
    "- If the user asked for only ONE thing, return \"intents\" as a "
    "single-element array — never merge an unrelated second action into "
    "the first intent's fields.\n"
    "- If the user asked for SEVERAL things (connected by \"and\"/\"aur\"/"
    "\"اور\"/\"then\"/\"phir\"/\"پھر\" or simply said back to back), return "
    "ONE intent object per distinct action, in the order the user said "
    "them. Each intent's own action/object_type/name/scope/confidence must "
    "stand entirely on its own — do NOT put a second action's meaning "
    "(e.g. \"play a song\") inside the first intent's context_reference or "
    "scope field. Worked example: \"YouTube کو کھولو اور کوئی گانا چلا "
    "دو\" (\"open YouTube and play some song\") -> "
    '"intents": [{"action": "open", "object_type": "application", '
    '"name": "YouTube", "confidence": 0.95}, {"action": "play", '
    '"object_type": null, "name": null, "scope": "youtube", '
    '"confidence": 0.9}] — NOT a single intent with name="YouTube" and '
    "context_reference=\"play a song\".\n"
    "- Never invent or guess a specific name the user did not say. If they "
    "refer to something vaguely (\"open it again\", \"wo wala folder\"), "
    "set name to null and context_reference to a short description "
    "instead — a separate system resolves it from real session history, "
    "never your guess.\n"
    "- action must be exactly one of the listed verbs. If unsure, use "
    "\"unknown\" with a low confidence rather than guessing.\n"
    "- Extract MEANING, not literal words. Common Roman Urdu → meaning:\n"
    "  * \"Chrome kholo\" → action=open, object_type=application, name=Chrome\n"
    "  * \"Settings band karo\" → action=close, object_type=application, name=Settings\n"
    "  * \"E drive kholo\" → action=open, object_type=drive, name=E\n"
    "  * \"volume barhao\" → action=increase, object_type=null, name=volume\n"
    "  * \"awaz kam karo\" → action=decrease, object_type=null, name=awaz\n"
    "  * \"screenshot lo\" → action=take_screenshot\n"
    "  * \"laptop lock karo\" → action=lock\n"
    "  * \"sleep karo\" / \"so jao\" → action=sleep (bare \"jao\" alone is "
    "NOT sleep — use action=\"unknown\" with low confidence)\n"
    "  * \"shutdown karo\" → action=shutdown\n"
    "  * \"mute karo\" / \"khamosh karo\" → action=mute\n"
    "  * \"ye file delete karo\" → action=delete, context_reference=current_file\n"
    "  * \"naya folder banao\" → action=create, object_type=folder\n"
    "  * \"YouTube pe gaana chalao\" → action=play, name=gaana, scope=youtube\n"
    "  * \"Spotify pe music bajao\" → action=play, name=music, scope=spotify\n"
    "  * \"Google pe X search karo\" → action=search, object_type=browser, "
    "name=X (the QUERY, never the engine name)\n"
    "- Filler words (\"aree\", \"suno\", \"dekho\") carry no command "
    "meaning — ignore them.\n"
    "- Negation words (\"nahi\", \"nahin\", \"mat\", \"na\") mean that "
    "specific intent is being cancelled — set its action=\"unknown\" and "
    "confidence=0.0.\n"
    "- ORDINAL references (\"pehla wala\", \"دوسرا والا\") refer to a list "
    "a PREVIOUS turn showed. NEVER invent a name — set name=null, "
    "object_type=null, context_reference=\"ordinal:first\" (or "
    "\"ordinal:second\", etc.)."
)

# See the module docstring's "Timeout / warm-keep" section for the
# measurements behind these two numbers — both are evidence-based, not
# arbitrary. _TIMEOUT_S must clear a genuine cold load (11.6-15.7s
# measured); it is NOT meant to make a slow-but-working call feel fast —
# that's keep_alive's job.
_TIMEOUT_S = float(os.getenv("OLLAMA_QWEN_TIMEOUT_S", "20.0"))
# 5m (was 10m): on this 4GB T1200 Qwen residency is the dominant VRAM consumer
# (3.6/4GB observed in the real-mic trace). 5m still comfortably covers the
# gap between consecutive Urdu turns in one conversation — its actual job —
# but frees the VRAM sooner for Whisper/XTTS once the session goes quiet.
_KEEP_ALIVE = os.getenv("OLLAMA_QWEN_KEEP_ALIVE", "5m")

_warm_lock = threading.Lock()
_warm_triggered = False
_warm_ready = False


def is_warm() -> bool:
    """Best-effort: has a warm-up (or a real comprehend() call) already
    loaded the model in this process? Not authoritative — Ollama may have
    evicted it since (VRAM pressure, idle timeout) — just avoids firing
    redundant background warm-up threads every turn."""
    return _warm_ready


def ensure_warm(reason: str = "") -> None:
    """
    Kick off a background, gpu_coordinator-gated warm-up of the Qwen model,
    mirroring xtts_service._ensure_bg_load()'s exact pattern: idempotent
    (safe to call every turn), never blocks the caller, waits for any
    active voice-session STT/TTS to go idle before doing the actual GPU
    work, and does nothing if a warm-up already ran this process.

    This does NOT eagerly load on every turn or on English turns — call
    site is voice_ws.py's existing non-English-language-detected branch
    (the same one that already triggers XTTS's own preload), so the cost
    is paid once, in the background, the first time a session looks like
    it might need Tier-4 comprehension — not on the critical path of the
    turn that actually needs it.
    """
    global _warm_triggered
    if _warm_ready:
        return
    with _warm_lock:
        if _warm_triggered:
            return
        _warm_triggered = True

    def _do_warm() -> None:
        global _warm_ready
        try:
            from api.services.gpu_coordinator import defer_background_job
            defer_background_job("qwen_comprehension_preload", timeout=30.0)
        except Exception:
            pass
        # A minimal real request — not a bare ping — so Ollama actually
        # finishes loading the model into VRAM before this returns, and
        # keep_alive is honored the same way a real comprehend() call
        # would set it.
        content, ms = _ollama_chat("kholo", keep_alive=_KEEP_ALIVE)
        _warm_ready = content is not None
        logger.info("[LOCAL_COMPREHEND_WARM] reason=%s success=%s ms=%.0f",
                     reason, _warm_ready, ms)

    threading.Thread(
        target=_do_warm, daemon=True, name="qwen-comprehension-preload",
    ).start()


@dataclass
class ComprehensionResult:
    """What comprehend() returns — a structured PROPOSAL, not an executed
    action. validate_and_map() resolves context/time references and
    synthesizes a canonical English sentence before anything gets routed."""
    original_transcript: str
    detected_language:   str
    action:               str
    object_type:           Optional[str]
    name:                    Optional[str] = None
    scope:                    Optional[str] = None
    time_reference:             Optional[str] = None
    context_reference:            Optional[str] = None
    model_confidence:               float = 0.0
    latency_ms:                       float = 0.0
    # Filled in by validate_and_map() — None until mapped to a real tool
    canonical_text: Optional[str] = None
    tool_name:      Optional[str] = None
    tool_params:    Optional[dict] = None
    route_confidence:                float = 0.0  # intent_router's confidence on canonical_text
    mapped:           bool = False
    trace_id:         str = ""


def _ollama_chat(user_text: str, keep_alive: str = _KEEP_ALIVE,
                  system_prompt: str = _SYSTEM_PROMPT) -> tuple[Optional[str], float]:
    """Raw call to the local Ollama model. Returns (content, latency_ms) or (None, 0).

    keep_alive tells Ollama how long to keep the model resident in VRAM
    after THIS call — passed on every call (not just warm-up) so a real
    comprehend() call also extends the window, keeping a multi-turn Urdu
    conversation warm without any separate keepalive thread pinging it.

    system_prompt: defaults to the single-intent _SYSTEM_PROMPT; callers
    that need compound-intent extraction pass _SYSTEM_PROMPT_COMPOUND
    instead (see comprehend_multi()) — same underlying call, different
    instructions/schema.
    """
    global _warm_ready
    from api.services.openai_client import LOCAL_OLLAMA_MODEL
    base_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434")

    t0 = time.monotonic()
    try:
        import ollama as _ollama
        client = _ollama.Client(host=base_url, timeout=_TIMEOUT_S)
        resp = client.chat(
            model=LOCAL_OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            options={"temperature": 0.0},
            keep_alive=keep_alive,
        )
        ms = (time.monotonic() - t0) * 1000
        _warm_ready = True
        logger.info("[LOCAL_COMPREHEND_TIMING] load_ms=%.0f prompt_eval_ms=%.0f eval_ms=%.0f total_ms=%.0f",
                     resp.get("load_duration", 0) / 1e6,
                     resp.get("prompt_eval_duration", 0) / 1e6,
                     resp.get("eval_duration", 0) / 1e6, ms)
        return resp["message"]["content"], ms
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        logger.warning("[LOCAL_COMPREHEND_ERROR] model=%s error=%s ms=%.0f",
                        LOCAL_OLLAMA_MODEL, exc, ms)
        return None, ms


# Urdu-family languages get OpenAI (gpt-4o-mini) as the PRIMARY
# comprehension engine as of 2026-09-04 — live-caught quality gap: on a
# compound Roman/Urdu-script utterance ("chalo, YouTube kholo aur koi bhi
# famous gaana chala do") the deterministic tier's entity extraction
# grabbed the whole noisy phrase as the app name and got blocked by the
# LOW_CONF_ACTION_BLOCKED safety gate, and the local qwen2.5:1.5b model
# (1.5B params) is simply too small to reliably extract structured meaning
# from noisy multilingual Urdu speech the way GPT-4o-mini can. Same
# SYSTEM_PROMPT/JSON schema as the local path so validate_and_map()
# downstream is completely engine-agnostic. Falls back to local Qwen if
# OpenAI is unavailable/quota-exhausted/errors (openai_client.generate()
# already does this fallback internally — see openai_client.py's
# _ollama_fallback), so this can never make Urdu comprehension WORSE than
# the pre-existing local-only path, only better when OpenAI is reachable.
_OPENAI_COMPREHEND_LANGS = frozenset({"ur", "ur_roman", "mixed"})


def _openai_chat(user_text: str, system_prompt: str = _SYSTEM_PROMPT,
                  max_tokens: int = 200) -> tuple[Optional[str], float]:
    """Same contract as _ollama_chat: returns (content, latency_ms) or
    (None, 0). Never raises — openai_client.generate() already swallows
    its own exceptions and returns None on failure.

    system_prompt/max_tokens: compound-intent calls (comprehend_multi())
    pass _SYSTEM_PROMPT_COMPOUND and a larger max_tokens (a multi-intent
    JSON array is longer than a single-intent object) — same underlying
    OpenAI call, different instructions/response budget."""
    from api.services.openai_client import openai_client
    t0 = time.monotonic()
    content = openai_client.generate(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        temperature=0.0,
    )
    ms = (time.monotonic() - t0) * 1000
    if content is None:
        logger.warning("[OPENAI_COMPREHEND_ERROR] no response ms=%.0f", ms)
        return None, ms
    logger.info("[OPENAI_COMPREHEND_TIMING] ms=%.0f", ms)
    return content, ms


def _parse_json(content: str) -> Optional[dict]:
    s = content.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    s = s.strip()
    # Model occasionally wraps the object in surrounding prose despite instructions —
    # extract the first {...} block as a last resort.
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r'\{.*\}', s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


def _norm_str(v) -> Optional[str]:
    if v is None:
        return None
    v = str(v).strip()
    return v if v and v.lower() not in ("null", "none", "n/a") else None


def _result_from_intent_dict(
    transcript: str, detected_language: str, parsed: dict,
    latency_ms: float, trace_id: str,
) -> Optional[ComprehensionResult]:
    """Shared field-extraction/validation logic for ONE intent object —
    used by both comprehend() (single-shape response) and comprehend_multi()
    (one call per element of the "intents" array). Same allowlist/
    confidence-defaulting rules either way, so a compound and a single-shot
    comprehension never diverge in how strictly they validate the model's
    output."""
    if "action" not in parsed:
        logger.warning("[TRACE %s] [LOCAL_COMPREHEND_INVALID] reason=missing_action raw=%r",
                        trace_id or "?", str(parsed)[:120])
        return None

    action = parsed.get("action")
    if action not in ALLOWED_ACTIONS:
        logger.warning("[TRACE %s] [LOCAL_COMPREHEND_INVALID] reason=action_not_allowlisted action=%r",
                        trace_id or "?", action)
        return None

    # qwen2.5:1.5b sometimes omits "confidence" from an otherwise well-formed,
    # correctly-understood response despite the system prompt requiring it and
    # temperature=0.0. Defaulting a MISSING field to 0.0 silently discarded
    # otherwise-correct comprehension by failing MIN_CONFIDENCE downstream.
    # Distinguish the two failure modes: a missing field is a self-assessment
    # the model just skipped (the action itself is still allowlisted and
    # JSON-valid, so trust it moderately); an INVALID value (non-numeric) is
    # a real malformed-output signal and stays untrusted at 0.0.
    _raw_confidence = parsed.get("confidence")
    if _raw_confidence is None:
        confidence = 0.75
    else:
        try:
            confidence = float(_raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0

    return ComprehensionResult(
        original_transcript=transcript,
        detected_language=detected_language,
        action=action,
        object_type=_norm_str(parsed.get("object_type")),
        name=_norm_str(parsed.get("name")),
        scope=_norm_str(parsed.get("scope")),
        time_reference=_norm_str(parsed.get("time_reference")),
        context_reference=_norm_str(parsed.get("context_reference")),
        model_confidence=confidence,
        latency_ms=latency_ms,
        trace_id=trace_id,
    )


def comprehend(transcript: str, detected_language: str, trace_id: str = "") -> Optional[ComprehensionResult]:
    """
    Ask the local Qwen fallback to extract structured meaning from an
    unmatched command.

    Returns None if the model is unavailable, times out, or returns
    unparseable/invalid JSON — caller must fall back to general_query.
    Never raises.

    trace_id: the SAME per-turn trace ID voice_ws.py already generates
    (api.services.tracer) — stamped on every log line here so a turn's
    STT/language/canonicalization/Qwen/routing/TTS logs can all be
    filtered by one ID instead of manually correlated by timestamp.
    """
    logger.info("[TRACE %s] [LOCAL_COMPREHEND_START] transcript=%r lang=%s",
                trace_id or "?", transcript[:80], detected_language)

    if detected_language in _OPENAI_COMPREHEND_LANGS:
        content, ms = _openai_chat(transcript)
    else:
        content, ms = _ollama_chat(transcript)
    if content is None:
        return None

    logger.info("[TRACE %s] [LOCAL_COMPREHEND_RAW] ms=%.0f content=%r", trace_id or "?", ms, content[:200])

    parsed = _parse_json(content)
    if not parsed:
        logger.warning("[TRACE %s] [LOCAL_COMPREHEND_INVALID] reason=unparseable_json raw=%r",
                        trace_id or "?", content[:120])
        return None

    return _result_from_intent_dict(transcript, detected_language, parsed, ms, trace_id)


def comprehend_multi(
    transcript: str, detected_language: str, trace_id: str = "",
) -> Optional[list[ComprehensionResult]]:
    """
    Tier-4 COMPOUND fallback — asks OpenAI/Qwen for a top-level "intents"
    array instead of one action, using _SYSTEM_PROMPT_COMPOUND. Only meant
    to be called when the deterministic compound splitter
    (mixed_language_engine.split_compound) could NOT confidently resolve
    every clause on its own (a connector other than "aur"/"phir", or a
    clause the deterministic per-clause analyze() didn't recognize).

    Returns a list of 1+ ComprehensionResult (unvalidated — caller still
    runs each through validate_and_map(), exactly like the single-intent
    comprehend() path; this function NEVER executes a tool itself, it only
    proposes structured intents, same trust boundary as comprehend()).
    Returns None on any failure (unreachable model, unparseable JSON,
    empty/invalid intents list) — caller falls back to general_query,
    never silently drops a clause.
    """
    logger.info("[TRACE %s] [LOCAL_COMPREHEND_MULTI_START] transcript=%r lang=%s",
                trace_id or "?", transcript[:80], detected_language)

    if detected_language in _OPENAI_COMPREHEND_LANGS:
        content, ms = _openai_chat(transcript, system_prompt=_SYSTEM_PROMPT_COMPOUND, max_tokens=500)
    else:
        content, ms = _ollama_chat(transcript, system_prompt=_SYSTEM_PROMPT_COMPOUND)
    if content is None:
        return None

    logger.info("[TRACE %s] [LOCAL_COMPREHEND_MULTI_RAW] ms=%.0f content=%r", trace_id or "?", ms, content[:400])

    parsed = _parse_json(content)
    if not parsed:
        logger.warning("[TRACE %s] [LOCAL_COMPREHEND_MULTI_INVALID] reason=unparseable_json raw=%r",
                        trace_id or "?", content[:200])
        return None

    # Tolerate a model that ignores the "intents" wrapper instruction and
    # answers with the old bare single-object shape ("action": "open", ...)
    # — treat that as a one-element intents list rather than failing
    # outright. This can't silently regress comprehend()'s own behavior:
    # comprehend() never calls this function, it always uses the plain
    # _SYSTEM_PROMPT/_openai_chat/_ollama_chat path unchanged.
    raw_intents = parsed.get("intents")
    if raw_intents is None and "action" in parsed:
        raw_intents = [parsed]
    if not isinstance(raw_intents, list) or not raw_intents:
        logger.warning("[TRACE %s] [LOCAL_COMPREHEND_MULTI_INVALID] reason=no_intents_array raw=%r",
                        trace_id or "?", content[:200])
        return None

    results: list[ComprehensionResult] = []
    for raw_intent in raw_intents:
        if not isinstance(raw_intent, dict):
            continue
        r = _result_from_intent_dict(transcript, detected_language, raw_intent, ms, trace_id)
        if r:
            results.append(r)

    if not results:
        logger.warning("[TRACE %s] [LOCAL_COMPREHEND_MULTI_INVALID] reason=no_valid_intents raw=%r",
                        trace_id or "?", content[:200])
        return None

    logger.info("[TRACE %s] [LOCAL_COMPREHEND_MULTI_PARSED] count=%d actions=%s",
                trace_id or "?", len(results), [r.action for r in results])
    return results


# ── Context-reference resolution — real session state, never a model guess ──
# Maps a free-text context_reference the model produced to a ContextStack
# entity type, then asks ContextStack for the actual most-recent entity of
# that type. This never trusts the model's own idea of "what" the referent
# is, only "roughly which category" — the real answer comes from ContextStack.
_CONTEXT_REF_ENTITY_MAP: dict[str, str] = {
    "current_app": "app", "previously_active_app": "app", "last_app": "app",
    "last_opened_app": "app", "current_application": "app",
    "current_folder": "folder", "previously_active_folder": "folder",
    "last_folder": "folder", "last_opened_folder": "folder",
    "current_file": "file", "last_file": "file", "last_opened_file": "file",
    "current_drive": "drive", "last_drive": "drive",
    "current_repository": "repository", "previously_active_repository": "repository",
    "last_repository": "repository", "current_repo": "repository",
    "previously_active_repo": "repository", "last_repo": "repository",
    "current_url": "url", "last_url": "url", "previously_active_url": "url",
}


def _resolve_context_reference(context_reference: str) -> Optional[str]:
    """Best-effort: map a context_reference to a ContextStack entity type and
    return that entity's display name, or None if unresolvable. Does NOT
    honor time_reference (e.g. "yesterday") — see module docstring."""
    entity_type = _CONTEXT_REF_ENTITY_MAP.get(context_reference.lower().strip().replace(" ", "_"))
    if not entity_type:
        return None
    try:
        from api.services.context_stack import context_stack as _cstack
        entity = _cstack.get_last(entity_type)
        return entity.display if entity else None
    except Exception as exc:
        logger.debug("[LOCAL_COMPREHEND_CONTEXT_RESOLVE_FAILED] ref=%s error=%s", context_reference, exc)
        return None


# ── Canonicalization — structured slots -> plain English sentence ───────────
# The synthesized sentence is deliberately phrased to match patterns
# intent_router.py's Tier 1/2 regex already recognizes for English speakers
# (e.g. "open folder named X in E drive" — see intent_router.py's D1/D2
# drive-aware rules) where possible, and falls back to natural English that
# Tier 3's semantic embedding matching can still resolve otherwise.

_DRIVE_LETTER_RE = re.compile(r'^\s*([a-zA-Z])\s*(?:drive|disk)?\s*$', re.IGNORECASE)


_VOLUME_HINTS     = ("vol", "awaz", "awaaz", "sound", "audio")
_BRIGHTNESS_HINTS = ("bright", "roshni", "screen", "display")


def _infer_volume_or_brightness(name: Optional[str], context_reference: Optional[str]) -> str:
    """Which category an increase/decrease action refers to. Checks BOTH
    name and context_reference (Qwen may legitimately put the hint in
    either — the schema allows it) against Roman-Urdu-aware keyword sets,
    not just an English "vol" substring in name alone. Live bug this
    closes: "آواز تھوڑی کم کرو۔" (reduce the volume a bit) had the model
    correctly identify context_reference="volume" with name=null — the old
    check (`name and 'vol' in name.lower()`) never looks at
    context_reference at all, so name=null defaulted straight to
    "brightness" regardless of what the user actually said. Defaults to
    volume when there's no signal either way — a voice assistant's
    increase/decrease commands are more often about volume than screen
    brightness."""
    for hint in (name, context_reference):
        if not hint:
            continue
        h = hint.lower()
        if any(k in h for k in _VOLUME_HINTS):
            return "volume"
        if any(k in h for k in _BRIGHTNESS_HINTS):
            return "brightness"
    return "volume"


def _synthesize_canonical(action: str, object_type: Optional[str],
                           name: Optional[str], scope: Optional[str],
                           context_reference: Optional[str] = None) -> Optional[str]:
    """Build a plain English command string from resolved slots, or None if
    this action/object_type combination isn't handled yet (Stage 1 only)."""
    # ── System actions with no object needed ────────────────────────────────
    # These fire FIRST, before the object_type gate, because they don't
    # require a specific object_type — the model may return object_type=null
    # or object_type="other" for these, and that's fine.
    if action == "take_screenshot":
        return "take screenshot"
    if action == "lock":
        return "lock screen"
    if action == "sleep":
        return "sleep"
    if action == "shutdown":
        return "shutdown"
    if action == "restart":
        return "restart"
    if action in ("mute", "unmute"):
        return action

    if object_type and object_type not in _STAGE1_OBJECT_TYPES:
        return None  # not yet in scope — e.g. repository/other (later stage)

    if object_type == "screen":
        return "what is on my screen"

    # Create with no specific name — generic "create folder" / "create file"
    if action == "create" and object_type in ("folder", "file") and not name:
        return f"create {object_type}"

    # increase/decrease need a CATEGORY (volume vs brightness), not a name —
    # check this before the "no name -> give up" gate below, since Qwen
    # correctly has nowhere else to put "which one" when there's no object
    # to name (see _infer_volume_or_brightness's comment for the live bug
    # this closes: "آواز تھوڑی کم کرو۔" put the hint in context_reference,
    # which this branch used to never look at).
    if action in ("increase", "decrease"):
        category = _infer_volume_or_brightness(name, context_reference)
        return f"{category} {'up' if action == 'increase' else 'down'}"

    # "play <song/media>" — checked BEFORE the per-object_type branches
    # below (drive/folder/file/application/browser), since the model may
    # (correctly) tag a song request as object_type="file" (see this
    # module's own system-prompt example:
    # "YouTube pe gaana chalao" -> action=play, name=gaana, scope=youtube)
    # and object_type=="file" would otherwise fall into the file-search
    # branch below, producing "find file <song> in youtube" instead of
    # actually playing it. Live-caught bug (2026-09-03 real backend log):
    # "چلو پر کام کرو، YouTube کو کھولو اور کوئی بھی فیمز گانا چلا دو"
    # ("let's get to it, open YouTube and play any famous song") correctly
    # comprehended as action=play/name="famous song"/scope=youtube, then
    # silently mis-synthesized into a filesystem search that could never
    # find a "file" called a song title. intent_router.py's search_youtube
    # tier only has a reliably-wired pattern for "play X on youtube" (see
    # its "YouTube play/search" rules) — no equivalent for Spotify exists,
    # so a play request is always routed there regardless of the model's
    # reported scope; a nameless play request has nothing to search for,
    # so it falls back to the same generic "play music" -> media_control
    # resume/pause path ml_normalizer.py's own bare-song rules already use.
    if action == "play":
        if name:
            return f"play {name} on youtube"
        # No specific title given. If a platform scope was reported
        # (youtube/spotify — the worked compound example in
        # _SYSTEM_PROMPT_COMPOUND is exactly this shape: "...aur koi gana
        # chala do" -> action=play, name=null, scope=youtube), route
        # through search_youtube with the SAME "trending songs" fallback
        # query mixed_language_engine._media_noun_query already uses for
        # the identical deterministic-tier case ("koi gana chalao" — see
        # test_urdu_language_parity.py's test_chalao_disambiguation) —
        # this keeps the two tiers' behavior consistent instead of the
        # Qwen/OpenAI tier silently dropping the platform info a bare
        # "play music" -> media_control fallback would lose. Only fall
        # back to the platform-agnostic media_control resume/pause path
        # when there's truly no platform signal at all.
        _scope_l = (scope or "").lower()
        if "youtube" in _scope_l or "spotify" in _scope_l:
            platform = "spotify" if "spotify" in _scope_l else "youtube"
            return f"play trending songs on {platform}"
        return "play music"

    if not name:
        return None  # nothing left to act on and no name was resolved

    if object_type == "drive":
        m = _DRIVE_LETTER_RE.match(name)
        letter = m.group(1).upper() if m else name.strip()[:1].upper()
        return f"open {letter} drive" if action == "open" else f"{action} {letter} drive"

    if object_type == "folder":
        _verb = action if action in ("open", "close", "create", "delete") else "open"
        drive_m = _DRIVE_LETTER_RE.match(scope or "")
        if drive_m:
            return f"{_verb} folder named {name} in {drive_m.group(1).upper()} drive"
        if scope:
            return f"{_verb} folder named {name} in {scope}"
        return f"{_verb} folder named {name}"

    if object_type == "file":
        # Delete has its own canonical form for files
        if action == "delete":
            drive_m = _DRIVE_LETTER_RE.match(scope or "")
            if drive_m:
                return f"delete file {name} in {drive_m.group(1).upper()} drive"
            if scope:
                return f"delete file {name} in {scope}"
            return f"delete file {name}"
        drive_m = _DRIVE_LETTER_RE.match(scope or "")
        if drive_m:
            return f"find file {name} in {drive_m.group(1).upper()} drive"
        if scope:
            return f"find file {name} in {scope}"
        return f"open file {name}"

    if object_type == "application":
        if action in ("open", "close", "install", "download"):
            return f"{action} {name}"
        # For other actions on applications, default to open
        return f"open {name}"

    if object_type in ("browser", "website"):
        if action == "search":
            return f"search for {name}"
        return f"open {name}"

    # No object_type given but we have a name and a known action — let
    # Tier 3 semantic matching take a shot rather than refusing outright.
    # (increase/decrease are handled earlier, before the "no name" gate —
    # they never reach here.)
    if action in ("open", "close", "install", "download", "play", "search", "show", "delete", "create"):
        return f"{action} {name}"

    return None


def validate_and_map(result: ComprehensionResult, registry) -> ComprehensionResult:
    """
    Mutates and returns `result` with canonical_text/tool_name/tool_params/
    mapped set.

    `registry` is the real api.tools registry — passed in so this module
    never needs its own copy of what tools exist. This function never
    fabricates a tool name: the only tool names that can come out are ones
    intent_router.route() already trusts for the synthesized English text,
    the exact same trust boundary English users go through.

    mapped=False means: understood, but no confident tool match — caller
    falls through to general_query.
    """
    _t0 = time.monotonic()
    context_refs_used = []

    if result.model_confidence < MIN_CONFIDENCE:
        logger.info("[LOCAL_COMPREHEND_UNMAPPED] reason=low_confidence conf=%.2f",
                     result.model_confidence)
        _log_canonicalization(result, None, "local_qwen", context_refs_used, _t0)
        return result

    if (result.action in _HIGH_STAKES_NO_OBJECT_ACTIONS
            and result.model_confidence < _HIGH_STAKES_MIN_CONFIDENCE):
        logger.info("[LOCAL_COMPREHEND_UNMAPPED] reason=high_stakes_low_confidence "
                     "action=%s conf=%.2f required=%.2f",
                     result.action, result.model_confidence, _HIGH_STAKES_MIN_CONFIDENCE)
        _log_canonicalization(result, None, "local_qwen", context_refs_used, _t0)
        return result

    name = result.name

    # Try synthesis with whatever name Qwen actually gave FIRST (possibly
    # None) — several actions need no name at all (take_screenshot, lock,
    # sleep, shutdown, restart, mute/unmute, object_type="screen",
    # increase/decrease). Live bug this closes: "Screen pe kya hai?"
    # ("what's on screen?") had Qwen correctly return object_type="screen"
    # with context_reference="current_screen" — but the OLD code tried to
    # resolve context_reference via ContextStack BEFORE ever attempting
    # synthesis, and "current_screen" isn't a real entity type
    # (ContextStack doesn't track "the screen" as a pushable entity, there's
    # nothing to look up) — so it gave up right there, even though
    # _synthesize_canonical never needed a name for object_type="screen" in
    # the first place. Only fall back to context_reference/ContextStack
    # resolution as a SECOND attempt, for actions that actually turned out
    # to need a name synthesis couldn't produce without one.
    canonical = _synthesize_canonical(result.action, result.object_type, name, result.scope,
                                       result.context_reference)

    if not canonical and not name and result.context_reference:
        resolved = _resolve_context_reference(result.context_reference)
        if resolved:
            name = resolved
            context_refs_used.append(f"{result.context_reference}->{resolved}")
            canonical = _synthesize_canonical(result.action, result.object_type, name, result.scope,
                                               result.context_reference)
        else:
            logger.info("[LOCAL_COMPREHEND_UNMAPPED] reason=context_reference_unresolved ref=%s",
                        result.context_reference)

    result.canonical_text = canonical

    if not canonical:
        logger.info("[LOCAL_COMPREHEND_UNMAPPED] reason=no_synthesis action=%s object_type=%s",
                     result.action, result.object_type)
        _log_canonicalization(result, None, "local_qwen", context_refs_used, _t0)
        return result

    try:
        from api.services.intent_router import intent_router as _ir
        route = _ir.route(canonical)
    except Exception as exc:
        logger.warning("[LOCAL_COMPREHEND_ERROR] intent_router call failed: %s", exc)
        _log_canonicalization(result, canonical, "local_qwen", context_refs_used, _t0)
        return result

    if route.tool_name and route.tool_name in registry and route.confidence >= 0.55:
        result.tool_name        = route.tool_name
        result.tool_params      = route.params
        result.route_confidence = route.confidence
        result.mapped           = True
        logger.info("[LOCAL_COMPREHEND_MAPPED] canonical=%r -> tool=%s params=%s conf=%.2f",
                     canonical, route.tool_name, route.params, route.confidence)
    else:
        logger.info("[LOCAL_COMPREHEND_UNMAPPED] canonical=%r reason=%s",
                     canonical,
                     "no_tool_match" if not route.tool_name else "tool_not_in_registry_or_low_conf")

    _log_canonicalization(result, canonical, "local_qwen", context_refs_used, _t0)
    return result


def _log_canonicalization(result: ComprehensionResult, canonical: Optional[str],
                           method: str, context_refs: list[str], t0: float) -> None:
    logger.info(
        "[ML_CANONICALIZATION] original=%r lang=%s method=%s canonical=%r "
        "confidence=%.2f context_refs=%s latency_ms=%.0f",
        result.original_transcript[:80], result.detected_language, method,
        canonical, result.model_confidence, context_refs or "none",
        (time.monotonic() - t0) * 1000,
    )
    # One-line ORIGINAL -> CANONICAL summary per turn, everything a human
    # debugging a multilingual command needs in a single grep-able line —
    # see local_comprehension's module docstring / this session's parity
    # report §5 for why this exists as one line instead of scattered ones.
    logger.info(
        "[TRACE %s] [TURN_SUMMARY] original=%r language=%s qwen_used=true "
        "action=%s object_type=%s name=%s scope=%s canonical=%r "
        "tool=%s params=%s route_confidence=%.2f mapped=%s",
        result.trace_id or "?", result.original_transcript[:80], result.detected_language,
        result.action, result.object_type, result.name, result.scope, canonical,
        result.tool_name, result.tool_params, result.route_confidence, result.mapped,
    )
