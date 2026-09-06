"""
conversational_replies — human-sounding, emotion-aware reply pools for the
voice fast path.

The tool fast path must stay instant (no LLM — OpenAI quota may be exhausted
and the Ollama fallback costs ~1.4s), so "not sounding scripted" is achieved
with:

  1. Larger, naturally-phrased variant pools per tool event (ack = spoken
     before execution, completion = spoken after success).
  2. Tone selection driven by the emotion detected in the user's voice turn:
     an excited user gets upbeat replies, a stressed/frustrated user gets
     short calm-reassuring ones, curiosity gets warmth, neutral commands get
     neutral replies.
  3. Anti-repeat: the exact same variant is never spoken twice in a row for
     the same slot.

English fast path only — Urdu / Roman-Urdu / mixed replies are produced by
the multilingual pipeline and never touch this module.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TONES = ("neutral", "warm", "upbeat", "reassuring")

# ── Emotion → tone mapping ────────────────────────────────────────────────────
# Emotion labels come from cognition.emotion_engine.EMOTIONS.
_EMOTION_TONE: dict[str, str] = {
    "excitement":  "upbeat",
    "hype":        "upbeat",
    "pride":       "upbeat",
    "humor":       "upbeat",
    "stress":      "reassuring",
    "frustration": "reassuring",
    "curiosity":   "warm",
    "seriousness": "neutral",
    "calmness":    "neutral",
    "sarcasm":     "neutral",
}


def emotion_to_tone(emotion: Optional[str]) -> str:
    """Map a detected user emotion to a reply tone. Unknown/None → neutral."""
    if not emotion:
        return "neutral"
    return _EMOTION_TONE.get(str(emotion).strip().lower(), "neutral")


# ── Human-readable settings page names (moved from voice_ws) ─────────────────
SETTINGS_PAGE_NAMES: dict[str, str] = {
    "wifi":            "Wi-Fi",
    "network":         "Network",
    "bluetooth":       "Bluetooth",
    "display":         "Display",
    "sound":           "Sound",
    "privacy":         "Privacy",
    "apps":            "Apps",
    "update":          "Windows Update",
    "power":           "Power",
    "storage":         "Storage",
    "accounts":        "Accounts",
    "time":            "Date and Time",
    "language":        "Language",
    "accessibility":   "Accessibility",
    "notifications":   "Notifications",
    "personalization": "Personalization",
    "themes":          "Themes",
    "taskbar":         "Taskbar",
    "startup":         "Startup Apps",
    "mouse":           "Mouse",
    "keyboard":        "Keyboard",
    "camera":          "Camera",
    "home":            "Settings",
}


# ── Entity-name extraction (same rules the old voice_ws builders used) ───────

def _app_name(params: dict) -> str:
    app = (params.get("app") or params.get("app_name") or params.get("name") or "").strip()
    return app.title() if app else "it"


def _settings_label(params: dict) -> str:
    page = (params.get("page") or "").strip().lower()
    nice = SETTINGS_PAGE_NAMES.get(page, page.replace("-", " ").replace("_", " ").title())
    if not nice or page == "home":
        return "Settings"
    return nice


def _drive_name(params: dict) -> str:
    drive = (params.get("drive") or "").upper().replace("DRIVE", "").strip()
    return f"{drive} drive" if drive else "drive"


def _dir_name(params: dict) -> str:
    raw = (params.get("query") or params.get("path") or "").strip()
    name = Path(raw).name if ("/" in raw or "\\" in raw) else raw
    return name.title() if name else "it"


def _query_name(params: dict, limit: int = 35) -> Optional[str]:
    q = (params.get("query") or "").strip()
    return q[:limit].title() if q else None


# ── Anti-repeat picker ────────────────────────────────────────────────────────

_last_reply: dict[str, str] = {}


def _pick(slot: str, variants: list[str]) -> str:
    """Pick a random variant, never the exact one spoken last for this slot."""
    if not variants:
        return ""
    last = _last_reply.get(slot)
    choices = [v for v in variants if v != last] or variants
    text = random.choice(choices)
    _last_reply[slot] = text
    return text


def _from_pool(pool: dict[str, list[str]], tone: str, slot: str, **fmt) -> str:
    """Choose from a tone-tagged pool, falling back to neutral, then any tone."""
    variants = pool.get(tone) or pool.get("neutral") or []
    if not variants:  # defensive: pool exists but tone keys missing
        for v in pool.values():
            variants = v
            break
    text = _pick(slot, variants)
    try:
        return text.format(**fmt) if fmt else text
    except (KeyError, IndexError):
        return text


# ── ACK pools — spoken while/just before the tool runs ───────────────────────

_ACK_POOLS: dict[str, dict[str, list[str]]] = {
    "open_application": {
        "neutral":    ["Opening {name}.", "Opening {name} now.", "Launching {name}."],
        "warm":       ["Sure, opening {name} for you.", "Let me open {name} for you.",
                       "Getting {name} ready for you."],
        "upbeat":     ["On it, {name} coming right up!", "Let's go, opening {name}.",
                       "{name}? Say no more, opening it."],
        "reassuring": ["Okay, opening {name}.", "Right away, {name}.",
                       "I'm on it. Opening {name}."],
    },
    "open_system_settings": {
        "neutral":    ["Opening {page} Settings.", "Opening your {page} settings.",
                       "Pulling up {page} settings."],
        "warm":       ["Sure, taking you to {page} settings.", "Let me pull up {page} for you."],
        "upbeat":     ["On it, {page} settings coming up!", "Easy, opening {page} settings."],
        "reassuring": ["Okay, opening {page} settings.", "Right away, pulling up {page}."],
    },
    "open_drive": {
        "neutral":    ["Opening your {name}.", "Opening the {name}.", "Opening {name} now."],
        "warm":       ["Sure, opening your {name}.", "Let me bring up the {name} for you."],
        "upbeat":     ["On it, {name} coming right up!", "Easy, opening the {name}."],
        "reassuring": ["Okay, opening your {name}.", "Right away, the {name} it is."],
    },
    "open_directory": {
        "neutral":    ["Opening {name}.", "Opening {name} now.", "Pulling up {name}."],
        "warm":       ["Sure, opening {name} for you.", "Let me pull up {name}."],
        "upbeat":     ["On it, {name} coming right up!", "Easy, pulling up {name}."],
        "reassuring": ["Okay, opening {name}.", "Right away, opening {name}."],
    },
    "search_youtube": {
        "neutral":    ["Playing {name}.", "Finding {name} on YouTube."],
        "warm":       ["Sure, let me find {name} on YouTube.", "Finding {name} for you."],
        "upbeat":     ["Great choice, playing {name}!", "On it, {name} coming up!"],
        "reassuring": ["Okay, finding {name} on YouTube.", "Right away, playing {name}."],
    },
    "search_web": {
        "neutral":    ["Searching the web.", "Let me search that.", "Searching now."],
        "warm":       ["Sure, let me look that up.", "Let me search that for you."],
        "upbeat":     ["On it, searching now!", "Great question, let me dig that up."],
        "reassuring": ["Okay, searching now.", "Let me check that for you."],
    },
    "open_url": {
        "neutral":    ["Opening it.", "Opening that now.", "Pulling it up."],
        "warm":       ["Sure, opening that for you.", "Let me pull that up for you."],
        "upbeat":     ["On it, opening it now!", "Easy, pulling it up."],
        "reassuring": ["Okay, opening it.", "Right away, opening that."],
    },
    "play_media_file": {
        "neutral":    ["Playing {name}.", "Playing {name} now."],
        "warm":       ["Sure, playing {name} for you.", "Let me play {name}."],
        "upbeat":     ["Great pick, playing {name}!", "On it, {name} coming up!"],
        "reassuring": ["Okay, playing {name}.", "Right away, playing {name}."],
    },
    "install_store_app": {
        "neutral":    ["Searching for {name} in the Store.", "Looking up {name} in the Store."],
        "warm":       ["Sure, let me find {name} in the Store.", "Checking the Store for {name}."],
        "upbeat":     ["On it, finding {name} in the Store!", "Nice pick, looking up {name}."],
        "reassuring": ["Okay, checking the Store for {name}.", "I'm on it, looking up {name}."],
    },
    "_generic": {
        "neutral":    ["On it.", "Right away.", "Working on it now."],
        "warm":       ["Sure, on it.", "Happy to, one moment."],
        "upbeat":     ["On it, let's go!", "Easy, right on it!"],
        "reassuring": ["Okay, I'm on it.", "Don't worry, handling it now."],
    },
}

# ── COMPLETION pools — spoken after the tool succeeded (past tense) ──────────

_COMPLETION_POOLS: dict[str, dict[str, list[str]]] = {
    "open_application": {
        "neutral":    ["{name} is open.", "{name} opened.", "There's {name}."],
        "warm":       ["There you go, {name} is open.", "I've opened {name} for you.",
                       "{name}'s ready for you."],
        "upbeat":     ["{name}'s up, you're all set!", "Done! {name} is right there.",
                       "Boom, {name} is open."],
        "reassuring": ["All set, {name} is open.", "Done, {name} is open for you.",
                       "{name}'s open. Take your time."],
    },
    "open_system_settings": {
        "neutral":    ["{page} settings is open.", "You're in {page} settings."],
        "warm":       ["There you go, you're in {page} settings.",
                       "I've pulled up {page} settings for you."],
        "upbeat":     ["And there's your {page} settings!", "Done, you're in {page} settings."],
        "reassuring": ["All set, {page} settings is open.", "Okay, you're in {page} settings."],
    },
    "open_drive": {
        "neutral":    ["{name} is open.", "There's your {name}."],
        "warm":       ["There you go, your {name} is open.", "I've opened the {name} for you."],
        "upbeat":     ["And there's the {name}!", "Done, {name} is right there."],
        "reassuring": ["All set, {name} is open.", "Okay, there's your {name}."],
    },
    "open_directory": {
        "neutral":    ["{name} opened.", "There's {name}."],
        "warm":       ["There you go, {name} is open.", "I've pulled up {name} for you."],
        "upbeat":     ["And there's {name}!", "Done, {name} is right there."],
        "reassuring": ["All set, {name} is open.", "Okay, {name} opened."],
    },
    "search_youtube": {
        "neutral":    ["Playing {name}.", "{name} is playing."],
        "warm":       ["There you go, {name} is playing.", "Enjoy, {name} is on."],
        "upbeat":     ["Great choice, {name} is playing!", "And {name} is on, enjoy!"],
        "reassuring": ["All set, {name} is playing.", "Okay, {name} is on now."],
    },
    "search_web": {
        "neutral":    ["Here's what I found.", "Search results are up.", "Done searching."],
        "warm":       ["I found some results for you.", "Here's what I dug up."],
        "upbeat":     ["Got some great results for you!", "And there's what I found!"],
        "reassuring": ["All set, the results are up.", "Okay, here's what I found."],
    },
    "open_url": {
        "neutral":    ["It's open.", "Page is up.", "Done."],
        "warm":       ["There you go, it's open.", "The page is up for you."],
        "upbeat":     ["And it's up!", "Done, page is right there."],
        "reassuring": ["All set, it's open.", "Okay, the page is up."],
    },
    "play_media_file": {
        "neutral":    ["Playing {name}.", "{name} is playing."],
        "warm":       ["There you go, {name} is playing.", "Enjoy, {name} is on."],
        "upbeat":     ["And {name} is on, enjoy!", "Great pick, {name} is playing."],
        "reassuring": ["All set, {name} is playing.", "Okay, {name} is on now."],
    },
    "install_store_app": {
        "neutral":    ["Found {name} in the Store.", "Found it in the Store."],
        "warm":       ["Good news, I found {name} in the Store.",
                       "There it is, {name} in the Store."],
        "upbeat":     ["Got it! {name} is right there in the Store.",
                       "And there's {name} in the Store!"],
        "reassuring": ["All good, I found {name} in the Store.",
                       "Okay, {name} is up in the Store."],
    },
    "_generic": {
        "neutral":    ["Done.", "All set."],
        "warm":       ["All done for you.", "There you go, all set."],
        "upbeat":     ["There we go, done!", "And that's a wrap, done!"],
        "reassuring": ["Done. We're good.", "All handled, don't worry about it."],
    },
}

# Context-aware follow-up questions — ONLY for the whitelisted cases that
# already asked one before this module existed (YouTube open). No new
# chattiness anywhere else.
_YOUTUBE_OPEN_DONE = [
    "YouTube's up, what are you in the mood to watch?",
    "There's YouTube. Want a recommendation, or looking for something specific?",
]


# ── Public API ────────────────────────────────────────────────────────────────

def pick_ack(tool_name: str, tool_params: Optional[dict] = None,
             emotion: Optional[str] = None) -> str:
    """Human-sounding acknowledgement spoken while the tool starts."""
    params = tool_params or {}
    tone = emotion_to_tone(emotion)

    if tool_name == "open_application":
        return _from_pool(_ACK_POOLS["open_application"], tone,
                          f"ack:{tool_name}", name=_app_name(params))
    if tool_name == "open_system_settings":
        return _from_pool(_ACK_POOLS["open_system_settings"], tone,
                          f"ack:{tool_name}", page=_settings_label(params))
    if tool_name == "open_drive":
        return _from_pool(_ACK_POOLS["open_drive"], tone,
                          f"ack:{tool_name}", name=_drive_name(params))
    if tool_name in ("open_directory", "smart_open"):
        return _from_pool(_ACK_POOLS["open_directory"], tone,
                          f"ack:{tool_name}", name=_dir_name(params))
    if tool_name == "search_youtube":
        name = _query_name(params)
        if not name:
            return _pick("ack:search_youtube:noquery",
                         ["Opening YouTube.", "Pulling up YouTube."])
        return _from_pool(_ACK_POOLS["search_youtube"], tone,
                          "ack:search_youtube", name=name)
    if tool_name == "search_web":
        return _from_pool(_ACK_POOLS["search_web"], tone, "ack:search_web")
    if tool_name == "open_url":
        return _from_pool(_ACK_POOLS["open_url"], tone, "ack:open_url")
    if tool_name == "play_media_file":
        return _from_pool(_ACK_POOLS["play_media_file"], tone,
                          "ack:play_media_file", name=_query_name(params) or "it")
    if tool_name == "install_store_app":
        app = (params.get("app_name") or "").strip()
        name = app.title() if app else "the app"
        return _from_pool(_ACK_POOLS["install_store_app"], tone,
                          "ack:install_store_app", name=name)
    return _from_pool(_ACK_POOLS["_generic"], tone, "ack:_generic")


def pick_completion(tool_name: str, tool_params: Optional[dict] = None,
                    emotion: Optional[str] = None) -> str:
    """Human-sounding narration of what just succeeded (past tense)."""
    params = tool_params or {}
    tone = emotion_to_tone(emotion)

    if tool_name == "open_application":
        app = (params.get("app") or params.get("app_name") or
               params.get("name") or "").strip()
        # Bare "open youtube" lands here (intent_router always maps it to
        # open_application) — the one case where a mood question adds value.
        if app.lower() == "youtube":
            return _pick("done:youtube_open", _YOUTUBE_OPEN_DONE)
        return _from_pool(_COMPLETION_POOLS["open_application"], tone,
                          f"done:{tool_name}", name=_app_name(params))
    if tool_name == "open_system_settings":
        return _from_pool(_COMPLETION_POOLS["open_system_settings"], tone,
                          f"done:{tool_name}", page=_settings_label(params))
    if tool_name == "open_drive":
        return _from_pool(_COMPLETION_POOLS["open_drive"], tone,
                          f"done:{tool_name}", name=_drive_name(params))
    if tool_name in ("open_directory", "smart_open"):
        return _from_pool(_COMPLETION_POOLS["open_directory"], tone,
                          f"done:{tool_name}", name=_dir_name(params))
    if tool_name == "search_youtube":
        name = _query_name(params)
        if not name:
            return _pick("done:search_youtube:noquery",
                         ["Playing it now.", "It's playing."])
        return _from_pool(_COMPLETION_POOLS["search_youtube"], tone,
                          "done:search_youtube", name=name)
    if tool_name == "search_web":
        return _from_pool(_COMPLETION_POOLS["search_web"], tone, "done:search_web")
    if tool_name == "open_url":
        site = (params.get("site") or "").strip().lower()
        if site == "youtube" or "youtube.com" in site:
            return _pick("done:youtube_open", _YOUTUBE_OPEN_DONE)
        return _from_pool(_COMPLETION_POOLS["open_url"], tone, "done:open_url")
    if tool_name == "play_media_file":
        return _from_pool(_COMPLETION_POOLS["play_media_file"], tone,
                          "done:play_media_file", name=_query_name(params) or "it")
    if tool_name == "install_store_app":
        app = (params.get("app_name") or "").strip()
        name = app.title() if app else "it"
        return _from_pool(_COMPLETION_POOLS["install_store_app"], tone,
                          "done:install_store_app", name=name)
    return _from_pool(_COMPLETION_POOLS["_generic"], tone, "done:_generic")


def pick_reply(kind: str, tool_name: str, tool_params: Optional[dict] = None,
               emotion: Optional[str] = None) -> str:
    """Unified entry point. kind = 'ack' | 'completion'."""
    if kind == "ack":
        return pick_ack(tool_name, tool_params, emotion)
    return pick_completion(tool_name, tool_params, emotion)


# ── Media control replies (used by api.tools.system_tools) ───────────────────

_MEDIA_REPLIES: dict[str, list[str]] = {
    "play_pause": [
        "There you go, playback toggled.",
        "Done, switched the playback over.",
        "Okay, toggled it for you.",
    ],
    "next": [
        "Skipped ahead, next one's up.",
        "Moving on to the next track.",
        "There you go, next track.",
    ],
    "prev": [
        "Back one track, there you go.",
        "Took it back to the previous one.",
        "Rewound to the last track for you.",
    ],
    "stop": [
        "Stopped the playback.",
        "All quiet, playback stopped.",
        "Okay, stopped it for you.",
    ],
}


def media_reply(action: str) -> str:
    """Human-sounding completion for a media_control action."""
    variants = _MEDIA_REPLIES.get(action) or _MEDIA_REPLIES["play_pause"]
    return _pick(f"media:{action}", variants)
