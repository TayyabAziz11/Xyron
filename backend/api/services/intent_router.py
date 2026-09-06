"""
4-Tier Hybrid Intent Router for Xyron.

Tier 1 — Exact cache        0 ms    previously seen phrases, auto-populated
Tier 2 — Regex patterns     0 ms    known command shapes (highest recall)
Tier 3 — Semantic (local)  ~80 ms   sentence-transformers, handles novel phrasings
Tier 4 — Fall-through       —       returns tool_name=None → caller uses LLM

Usage:
    from api.services.intent_router import intent_router
    result = intent_router.route("please open chrome for me")
    # RouteResult(tool_name='open_application', params={'app':'chrome'}, tier=2, confidence=1.0)

    if result.tool_name and result.confidence >= 0.65:
        # Use result — skip LLM
    else:
        # Fall through to GPT
"""
from __future__ import annotations

import re
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    tool_name: Optional[str]
    params: dict
    tier: int          # 1 = cache, 2 = regex, 3 = semantic, 4 = no match
    confidence: float  # 0.0–1.0


# ── Tool descriptions for semantic embedding ─────────────────────────────────
# Each entry is a bag-of-phrases that captures the tool's intent.
# Richer than registry descriptions — includes colloquial synonyms.
_TOOL_DESCS: dict[str, str] = {
    "open_application":       "launch start run open application program app chrome firefox vscode spotify discord steam notepad",
    "smart_open":             "open find show me a file folder picture photo video document on my system locally",
    "open_directory":         "open folder directory file explorer my documents downloads desktop pictures",
    "open_drive":             "open drive c d e f g h i j k l disk partition explorer storage",
    "brightness_control":     "set increase decrease adjust screen brightness dim bright make screen brighter darker",
    "volume_control":         "set increase decrease adjust volume audio sound louder quieter turn volume up down",
    "mute_unmute":            "mute unmute toggle mute silence audio sound",
    "get_volume":             "current volume level what is volume how loud",
    "media_control":          "play pause resume stop music song track next previous skip go back rewind media spotify youtube vlc",
    "get_battery_status":     "battery percentage charge charging remaining time power level how much battery left",
    "system_info":            "computer specs hardware cpu processor ram memory operating system version info",
    "system_health":          "cpu usage ram memory usage disk usage system performance health status live",
    "system_health_check":    "full system health check battery level storage disk space running apps which app using most cpu memory resource hog complete system status report",
    "get_disk_usage":         "disk space storage how much space free drive usage remaining",
    "get_uptime":             "system uptime how long running since boot last restart",
    "get_running_apps":       "running applications processes apps open currently what is running background",
    "list_processes":         "list running processes memory cpu usage task manager what is using resources",
    "kill_process":           "kill stop terminate close quit end a process forcefully",
    "kill_app":               "close kill quit stop an application window program",
    "take_screenshot":        "screenshot capture screen take picture save screen grab",
    "read_screen":            "read screen what is on screen describe see analyze screenshot ai vision",
    "sleep_system":           "sleep suspend computer put to sleep rest",
    "shutdown_system":        "shut down power off turn off computer",
    "restart_system":         "restart reboot computer start over fresh",
    "lock_system":            "lock screen lock computer workstation secure",
    "hibernate_system":       "hibernate deep sleep low power state",
    "schedule_shutdown":      "schedule shutdown turn off in minutes hours later set timer shutdown",
    "set_power_plan":         "power plan performance battery saver balanced mode",
    "wifi_list":              "scan list available wifi networks wireless connections",
    "open_wifi_panel":        "show open wifi panel networks nearby available connect",
    "wifi_connect":           "connect to wifi network join wireless",
    "wifi_disconnect":        "disconnect from wifi turn off wifi",
    "network_speed_test":     "internet speed test how fast download upload bandwidth",
    "get_ip_info":            "ip address what is my ip local public network address",
    "flush_dns":              "flush dns clear dns cache reset network",
    "get_temp_files_size":    "temp files size how big temporary files disk",
    "clear_temp_files":       "clear temp delete temporary files clean",
    "run_disk_cleanup":       "disk cleanup clean up disk utility junk",
    "empty_recycle_bin":      "empty recycle bin trash delete permanently clear bin",
    "get_startup_apps":       "startup apps programs what starts on boot login",
    "disable_startup_app":    "disable startup app remove from startup boot",
    "set_display_resolution": "change screen resolution display 1080p 1440p 4k",
    "set_refresh_rate":       "refresh rate hz monitor 60hz 144hz change",
    "check_windows_updates":  "check for windows updates pending available",
    "virtual_desktop_create": "create new virtual desktop workspace",
    "virtual_desktop_switch": "switch virtual desktop next previous",
    "switch_window":          "switch to window bring focus foreground application",
    "minimize_window":        "minimize hide window",
    "maximize_window":        "maximize fullscreen enlarge window",
    "close_window":           "close current window alt f4",
    "create_folder":          "create new folder directory make folder",
    "create_subfolders":      "create multiple subfolders inside folder make sub-folders sub-directories in it named",
    "list_directory":         "list files contents folder directory show what is inside",
    "search_files":           "search find file by name pattern in folder",
    "move_file":              "move file folder from one place to another",
    "delete_file":            "delete remove file folder permanently",
    "read_clipboard":         "read clipboard what is in clipboard paste content",
    "write_clipboard":        "copy text to clipboard write save clipboard",
    "clear_clipboard":        "clear empty wipe clipboard",
    "type_text":              "type write input text keyboard window",
    "desktop_hotkey":         "press keyboard shortcut hotkey ctrl alt key combination",
    "desktop_scroll":         "scroll up down page mouse wheel",
    "desktop_focus_app":      "focus bring front foreground window application",
    "search_web":             "search google look up find information online web internet",
    "search_youtube":         "play watch youtube video music search youtube",
    "open_url":               "open website url go to browser visit",
    "wiki_summary":           "what is who is define explain wikipedia facts information",
    "read_inbox":             "read check emails inbox gmail mail unread messages",
    "send_email":             "send compose write email mail to someone",
    "list_events":            "calendar events schedule meetings upcoming appointments agenda today",
    "create_event":           "create add event meeting appointment calendar schedule",
    "get_summary":            "summary activity report what have I done recently history",
    "run_workflow":           "run workflow automation multi-step task sequence",
    "open_system_settings":   (
        "open windows settings display sound bluetooth wifi network privacy apps update power "
        "storage accounts time language accessibility notifications personalization taskbar "
        "screen resolution monitor settings preferences system settings page ms-settings "
        "display wala setting kholo screen setting open karo monitor resolution change"
    ),
}

# ── Tier 0: Local clock — no LLM, no internet, instant ───────────────────────
# Catches all time/date/day queries before anything else.
# Supports English + broken English + Urdu/Hindi colloquial variants.

_TIME_RE = re.compile(
    r'\b(?:'
    r'(?:what(?:\'?s|\s+is)?(?:\s+the)?|tell(?:\s+me)?(?:\s+the)?|give(?:\s+me)?(?:\s+the)?|current|check|bata(?:o)?|batao|kya(?:\s+hai)?)\s+(?:the\s+)?(?:current\s+)?time'
    r'|time\s+(?:hai|kya|batao|abhi|right\s+now|is\s+it|please|abi)'
    r'|what\s+time\s+(?:is\s+it|are\s+we\s+at|do\s+(?:we|i)\s+have)'
    r'|kya\s+(?:time|waqt|baja)\s+(?:hai|hua|ho\s+gaya)'
    r'|(?:time|waqt)\s+(?:kya|batao|bata)'
    r'|bro\s+(?:what|whats|kya)?\s*(?:time|waqt)'
    r'|yo\s+(?:what\s+)?time'
    r')\b',
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r'\b(?:'
    r'(?:what(?:\'?s|\s+is)?(?:\s+the)?|tell(?:\s+me)?(?:\s+the)?|give(?:\s+me)?(?:\s+the)?|current|today(?:\'?s?)?|can\s+you\s+tell\s+me(?:\s+the)?)\s+(?:the\s+)?(?:today(?:\'?s?)?\s+)?date'
    r'|today(?:\'?s?)?\s+(?:date|ki\s+date|kia\s+date)'
    r'|(?:date|tariikh?|tarikh)\s+(?:kya\s+hai|batao|bata)'
    r'|kya\s+(?:date|tarikh)\s+(?:hai|hua)'
    r'|(?:current|aaj(?:\s+ki)?)\s+date'
    r'|bro\s+(?:today|aaj)\s+(?:konsa|kya)?\s*date'
    r')\b',
    re.IGNORECASE,
)

_DAY_RE = re.compile(
    r'\b(?:'
    r'(?:what(?:\'?s|\s+is)?(?:\s+the)?|tell(?:\s+me)?|today(?:\'?s?)?|which)\s+(?:the\s+)?(?:today(?:\'?s?)?\s+)?day'
    r'|what\s+day\s+(?:is\s+(?:it|today)|are\s+we)'
    r'|today\s+(?:is\s+)?(?:konsa|kaunsa|which|what)\s*day'
    r'|(?:aaj|today)\s+(?:konsa|kaunsa|kia)\s+(?:day|din)\s*(?:hai)?'
    r'|(?:konsa|kaunsa)\s+(?:day|din)\s+(?:hai|ho\s+gaya|hua)'
    r'|bro\s+(?:today|aaj)\s+konsa\s+day'
    r'|which\s+day\s+(?:is\s+)?(?:it|today)'
    r')\b',
    re.IGNORECASE,
)


def _local_clock_route(text: str) -> "RouteResult | None":
    """Tier 0 — resolve time/date/day queries instantly using local system clock."""
    import datetime as _dt
    now = _dt.datetime.now()

    if _TIME_RE.search(text):
        t_str = now.strftime("%-I:%M %p") if hasattr(now, 'strftime') else now.strftime("%I:%M %p").lstrip('0')
        response = f"It's {t_str}."
        logger.info("[TIME_ROUTE] [LOCAL_CLOCK_RESPONSE] query=%r response=%r", text[:60], response)
        return RouteResult("local_clock_time", {"response": response}, 0, 1.0)

    if _DATE_RE.search(text):
        d_str = now.strftime("%-d %B %Y") if hasattr(now, 'strftime') else now.strftime("%d %B %Y").lstrip('0')
        response = f"Today is {d_str}."
        logger.info("[DATE_ROUTE] [LOCAL_CLOCK_RESPONSE] query=%r response=%r", text[:60], response)
        return RouteResult("local_clock_date", {"response": response}, 0, 1.0)

    if _DAY_RE.search(text):
        day_str = now.strftime("%A")
        response = f"It's {day_str}."
        logger.info("[DAY_ROUTE] [LOCAL_CLOCK_RESPONSE] query=%r response=%r", text[:60], response)
        return RouteResult("local_clock_day", {"response": response}, 0, 1.0)

    return None


# ── Tier 0.5 — WhatsApp deterministic fast path (Phase 4) ────────────────────
# Bypasses the LLM/semantic tier for confident WhatsApp commands. Never
# resolves a contact over the network itself — only a cheap in-memory
# identity-cache lookup (or the contextual-pronoun check, also in-memory) —
# so this stays true to Tier 0's "no LLM, no internet, instant" contract.
# See api/integrations/whatsapp/wa_intent.py's module docstring for the
# anti-hijack design: an utterance without the literal word "whatsapp"
# only commits to this route when contact_ref is already a known contact
# or a contextual pronoun; otherwise it returns None and falls through to
# Tier 2/3/LLM exactly as it would today.

_WA_TOOL_FOR_ACTION = {
    "send_text": "wa_send_text",
    "send_file": "wa_send_file",
    "show_chat": "wa_show_chat",
    "get_messages": "wa_get_messages",
    "reply": "wa_reply",
}


def _wa_intent_to_route(intent) -> "RouteResult | None":
    from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
    from api.integrations.whatsapp.wa_identity import get_default_identity_store

    ref = intent.contact_ref or ""
    if intent.requires_cache_hit:
        is_ctx = is_contextual_contact_reference(ref)
        ident = None if is_ctx else get_default_identity_store().resolve_cached(ref)
        if not is_ctx and ident is None:
            return None

    tool = _WA_TOOL_FOR_ACTION.get(intent.action)
    if tool is None:
        return None

    if intent.action == "send_text":
        params = {"contact": ref, "message": intent.message, "show_ui": intent.show_ui}
    elif intent.action == "send_file":
        params = {"contact": ref, "file_ref": intent.artifact_ref, "show_ui": intent.show_ui}
    elif intent.action == "reply":
        params = {"contact": ref, "message": intent.message, "show_ui": intent.show_ui}
    elif intent.action == "show_chat":
        params = {"contact": ref}
    else:  # get_messages
        params = {"contact": ref}

    return RouteResult(tool, params, 0, intent.confidence)


def _whatsapp_route(text: str) -> "RouteResult | None":
    """Tier 0.5 — parse + (cheap) commit-check for WhatsApp commands."""
    try:
        from api.integrations.whatsapp.wa_intent import parse_wa_intent
    except Exception:
        return None
    intent = parse_wa_intent(text)
    if intent is None:
        return None
    try:
        result = _wa_intent_to_route(intent)
    except Exception:
        logger.debug("[IntentRouter] WhatsApp tier failed", exc_info=True)
        return None
    if result:
        logger.info("[IntentRouter] Tier0.5 whatsapp: %r -> %s params=%s conf=%.2f",
                    text[:60], result.tool_name, result.params, result.confidence)
    return result


# Tools that should never be routed by the semantic classifier alone
# (require explicit phrasing or confirmation — too dangerous to guess)
_SAFE_GUARD_TOOLS = {
    "shutdown_system", "restart_system", "delete_file",
    "send_email", "clear_temp_files", "empty_recycle_bin",
    "disable_startup_app",
}

# Same Arabic/Urdu/Persian Unicode block api.services.language_detector
# already uses for script detection — mirrored here (not imported) to
# avoid adding a cross-module import to this hot-path router for a single
# regex. See _semantic_route()'s comment for why this gates Tier 3.
_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# Gate for Tier 2.5 (object_resolver) — only worth calling on utterances that
# actually look like an open/navigate request.
_OBJECT_RESOLVER_VERB_RE = re.compile(
    r'\b(open|show|go\s+(?:to|inside|into)|take\s+me\s+to|browse|navigate\s+to|find|locate)\b',
    re.I,
)


def _params_for_object(tool: str, obj) -> dict:
    """Map an ObjectResolution onto the params shape the target tool expects."""
    if tool == "smart_open":
        p: dict = {"query": obj.name, "type": obj.object_type if obj.object_type in ("folder", "file") else "any"}
        if obj.scope.get("drive"):
            p["drive"] = obj.scope["drive"]
        return p
    if tool == "open_drive":
        # The name should already be a bare letter after the shared phonetic
        # layers (whisper _CORRECTIONS + normalizer._correct_drive_phonetics),
        # but resolve() can still hand over residue like "seed" when an
        # intermediate word broke the adjacency those patterns require —
        # map the known homophones here as a last resort instead of silently
        # taking the wrong first character ("seed" → "S").
        _drive_letter = (obj.name or "").strip().upper()
        if _drive_letter:
            _first = _drive_letter.split()[0]
            _drive_letter = {
                "SEE": "C", "SEA": "C", "SI": "C", "CEE": "C", "SEED": "C",
                "DEE": "D", "EE": "E", "EFF": "F",
            }.get(_first, _first)
        return {"drive": _drive_letter[:1]}
    if tool == "open_application":
        return {"app_name": obj.name}
    if tool == "open_url":
        # _exec_open_url reads "site" and resolves known names via its own
        # _URL_MAP (youtube → youtube.com) or builds https://<site>.
        return {"site": obj.name}
    if tool == "open_system_settings":
        return {"page": obj.name}
    return {"query": obj.name}


class IntentRouter:

    def __init__(self) -> None:
        self._cache: dict[str, RouteResult] = {}
        self._cache_lock = threading.Lock()
        self._rules: list[tuple[re.Pattern, str, Callable, Optional[re.Pattern]]] = []
        self._model = None
        self._embeddings: dict[str, object] = {}
        self._np = None
        self._classifier_ready = False
        self._build_rules()
        import os as _os_ir
        if _os_ir.getenv("LOCAL_ONLY_MODE", "").lower() in ("1", "true", "yes"):
            logger.info("[IntentRouter] LOCAL_ONLY_MODE=true — skipping SentenceTransformer load (no HuggingFace)")
        else:
            threading.Thread(target=self._load_classifier, daemon=True, name="intent-classifier").start()

    # ────────────────────────────────────────────────────────────────────────
    # Tier 2 — Regex rules
    # ────────────────────────────────────────────────────────────────────────

    def _build_rules(self) -> None:
        def add(pattern: str, tool: str, fn: Callable = lambda m: {}, reject_if: Optional[str] = None):
            """reject_if: an optional regex (as a string) — if it matches
            ANYWHERE in the full input text, this rule is skipped entirely
            even though `pattern` matched, and matching continues to the
            next rule. Exists for catch-all rules whose capture group can
            legitimately match a SUBSTRING of a sentence whose overall
            structure the rule was never meant to handle (see the
            Urdu/colloquial app-launch rule below for the motivating case:
            re.search retries at every start position, so a plain
            in-pattern negative lookahead only blocks the specific
            substring it's attached to — it can't reject the match on the
            grounds that some OTHER, earlier part of the same sentence
            makes the whole utterance not this rule's business.
            reject_if checks the whole text once, independent of where the
            candidate match starts."""
            self._rules.append((
                re.compile(pattern, re.IGNORECASE), tool, fn,
                re.compile(reject_if, re.IGNORECASE) if reject_if else None,
            ))

        # ── Volume ──────────────────────────────────────────────────────────
        add(r'\b(?:set|put|change)\s+(?:the\s+)?volume\s+(?:to\s+)?(\d+)',
            "volume_control", lambda m: {"action": "set", "steps": int(m.group(1))})
        add(r'\bvolume\s+(?:up|louder|raise|higher|increase)',
            "volume_control", lambda m: {"action": "increase", "steps": 2})
        add(r'\bvolume\s+(?:down|lower|quieter|softer|decrease)',
            "volume_control", lambda m: {"action": "decrease", "steps": 2})
        add(r'\b(?:turn\s+(?:the\s+)?(?:volume|sound)\s+(?:up|down|higher|lower)|turn\s+(?:up|down)\s+(?:the\s+)?(?:volume|sound))',
            "volume_control", lambda m: {
                "action": "increase" if any(w in m.group(0).lower() for w in ("up", "higher")) else "decrease",
                "steps": 2,
            })
        add(r'\b(?:what.?s|get|check|current)\s+(?:the\s+)?volume\b', "get_volume")

        # ── Live system metrics (instant, calls system_monitor.get_snapshot) ──
        add(r'\b(?:what.?s|how.?s|check|show|get)\s+(?:my\s+|the\s+)?(?:cpu|processor)\s*(?:usage|load|utilization|percent|pct|speed)?\b',
            "get_live_system_metrics", lambda m: {"metric": "cpu"})
        add(r'\b(?:what.?s|how.?s|check|show|get)\s+(?:my\s+|the\s+)?(?:ram|memory)\s*(?:usage|load|utilization|percent|pct)?\b',
            "get_live_system_metrics", lambda m: {"metric": "ram"})
        add(r'\b(?:what.?s|how.?s|check|show|get)\s+(?:my\s+|the\s+)?(?:gpu|graphics card|graphics)\s*(?:usage|load|utilization|percent|pct)?\b',
            "get_live_system_metrics", lambda m: {"metric": "gpu"})
        add(r'\b(?:what.?s|how.?s|check|show|get)\s+(?:my\s+|the\s+)?(?:disk|storage|drive)\s*(?:usage|space|free|percent|pct)?\b',
            "get_live_system_metrics", lambda m: {"metric": "disk"})
        add(r'\b(?:what.?s|how.?s|check|show|get)\s+(?:my\s+|the\s+)?(?:network|internet|net)\s*(?:speed|usage|up|down)?\b',
            "get_live_system_metrics", lambda m: {"metric": "network"})
        add(r'\b(?:what.?s|how.?s|check|show|get)\s+(?:my\s+|the\s+)?battery\s*(?:level|charge|percent|pct|life)?\b',
            "get_live_system_metrics", lambda m: {"metric": "battery"})
        add(r'\b(?:system\s+stats?|system\s+metrics?|system\s+(?:monitor|info|status)|how(?:\'?s|\s+is)\s+(?:my\s+)?system(?:\s+doing)?)\b',
            "get_live_system_metrics", lambda m: {"metric": "all"})

        add(r'\b(?:mute|unmute)\b', "mute_unmute",
            lambda m: {"action": m.group(0).lower().strip()})
        add(r'\btoggle\s+(?:the\s+)?mute\b', "mute_unmute", lambda m: {"action": "toggle"})

        # ── YouTube open+play compound (must precede Media controls) ──────
        # "open YouTube and play any famous song" / "youtube ... play X" —
        # previously the generic `play` pattern below matched first and these
        # became media_control play_pause (live regression: nothing opened,
        # reply was "Playing / paused."). Requires "youtube" in the utterance
        # AND a "play" after it, so plain "play music" / "pause" still fall
        # through to media_control; "play X on youtube" (play BEFORE youtube)
        # doesn't match here and is handled by the search_youtube patterns.
        add(r'\b(?:open\s+)?(?:youtube|yt)\b.{0,40}?\bplay\s+(.+?)[.!?\s]*$',
            "search_youtube", lambda m: {"query": m.group(1).strip()})

        # ── Media controls ───────────────────────────────────────────────────
        # "play" — can be standalone; "resume" requires a media noun so that
        # document names like "resume" (CV) are not hijacked as playback commands.
        # Exclude "play X on youtube/spotify/yt" — handled by search_youtube pattern
        add(r'\bplay(?!\s+\S+.*\s+on\s+(?:youtube|yt|spotify))\s*(?:music|song|audio|video|it|that)?\b',
            "media_control", lambda m: {"action": "play_pause"})
        add(r'\b(?:resume|unpause)\s+(?:music|song|audio|video|it|that|playback|the\s+(?:music|audio))\b',
            "media_control", lambda m: {"action": "play_pause"})
        add(r'\bpause\s*(?:music|song|audio|video|it|that)?\b',
            "media_control", lambda m: {"action": "play_pause"})
        add(r'\btoggle\s+(?:play(?:back)?|music|audio)\b',
            "media_control", lambda m: {"action": "play_pause"})
        add(r'\b(?:next|skip)\s*(?:song|track|video|one)?\b',
            "media_control", lambda m: {"action": "next"})
        add(r'\b(?:previous|prev|go\s+back|last\s+(?:song|track))\s*(?:song|track|video|one)?\b',
            "media_control", lambda m: {"action": "prev"})
        add(r'\bgo\s+back\b',
            "media_control", lambda m: {"action": "prev"})
        add(r'\bstop\s+(?:music|song|audio|playback|playing)\b',
            "media_control", lambda m: {"action": "stop"})

        # ── Roman Urdu media controls — noun-gated so bare "rok do" (used
        # all over colloquial Urdu for "stop"/"pull over") never fires this
        # as a media command. Direct regex here as a second line of defense
        # alongside mixed_language_engine's own canonicalization of the
        # same phrases, in case a turn reaches intent_router unnormalized.
        add(r'\b(?:gana|song|music|playback|video)\s+(?:rok\s*do|roko|band\s+kar[oa]?|pause\s+kar[oa]?)\b',
            "media_control", lambda m: {"action": "play_pause"})
        add(r'\b(?:dobara|wapis|vapis)\s+chala[oa]?\b',
            "media_control", lambda m: {"action": "play_pause"})
        add(r'\b(?:agla|agli)\s+(?:gana|song|track|video)\b',
            "media_control", lambda m: {"action": "next"})
        add(r'\b(?:pichla|pichli)\s+(?:gana|song|track|video)\b',
            "media_control", lambda m: {"action": "prev"})

        # ── Brightness ──────────────────────────────────────────────────────
        add(r'\b(?:set|put|change)\s+brightness\s+(?:to\s+)?(\d+)',
            "brightness_control", lambda m: {"action": "set", "level": int(m.group(1))})
        add(r'\bbrightness\s+(?:up|higher|increase|raise|brighter)',
            "brightness_control", lambda m: {"action": "increase", "step": 10})
        add(r'\b(?:increase|raise|boost)\s+(?:the\s+)?brightness\b',
            "brightness_control", lambda m: {"action": "increase", "step": 10})
        add(r'\bbrightness\s+(?:down|lower|decrease|dim|darker)',
            "brightness_control", lambda m: {"action": "decrease", "step": 10})
        add(r'\b(?:decrease|lower|dim|reduce)\s+(?:the\s+)?brightness\b',
            "brightness_control", lambda m: {"action": "decrease", "step": 10})

        # ── Battery ─────────────────────────────────────────────────────────
        add(r'\b(?:battery|how\s+much\s+(?:battery|charge|power))\b', "get_battery_status")
        add(r'\b(?:is\s+(?:it|my\s+(?:laptop|pc))\s+charging|charging\s+status)\b', "get_battery_status")

        # ── System info / health ─────────────────────────────────────────────
        add(r'\b(?:system\s+(?:info|information|specs?|details)|about\s+(?:my\s+)?(?:pc|computer|system))\b', "system_info")
        add(r'\b(?:cpu|processor|ram|memory|os|operating\s+system)\s+(?:info|specs?|details|version)\b', "system_info")
        add(r'\b(?:system\s+health\s+check|full\s+system\s+check|complete\s+system\s+(?:check|report)|'
            r'(?:system|pc)\s+(?:health|status|performance))\b', "system_health_check")
        add(r'\b(?:cpu|ram|memory|disk)\s+usage\b', "system_health")
        add(r'\b(?:how\s+much\s+)?disk\s+(?:space|usage|storage)\b', "get_disk_usage")
        add(r'\bhow\s+much\s+(?:space|storage)\b', "get_disk_usage")
        add(r'\b(?:system\s+)?uptime\b', "get_uptime")
        add(r'\b(?:ip\s+address|what(?:.?s|\s+is)\s+my\s+ip|show\s+(?:my\s+)?ip)\b', "get_ip_info")

        # ── Power ────────────────────────────────────────────────────────────
        add(r'\b(?:sleep|suspend|put\s+(?:it|the\s+computer|my\s+(?:pc|laptop))\s+to\s+sleep)\b', "sleep_system")
        add(r'\bhibernate\b', "hibernate_system")
        add(r'\block\s+(?:the\s+)?(?:screen|computer|pc|workstation)?\b', "lock_system")

        # ── Screenshots ──────────────────────────────────────────────────────
        add(r'\b(?:take\s+(?:a\s+)?)?screenshot\b|\bcapture\s+(?:the\s+)?screen\b', "take_screenshot")
        # "what's on screen" (contraction) AND "what is on screen" (the
        # form local_comprehension._synthesize_canonical's object_type=
        # "screen" branch actually produces) — the old `what.?s` only
        # matched the contracted apostrophe form, so a Qwen-synthesized
        # "what is on my screen" (from Roman/Urdu-script "screen pe kya
        # hai?") silently fell through to unmapped despite being
        # synthesized correctly.
        add(r'\bwhat.?s\s+(?:on|showing\s+on)\s+(?:my\s+)?screen\b|'
            r'\bwhat\s+is\s+(?:on|showing\s+on)\s+(?:my\s+)?screen\b|'
            r'\bread\s+(?:the\s+)?screen\b', "read_screen")

        # ── Drive letters (must be before open_application catch-all) ───────────
        # "open C drive", "open the D drive", "go to E drive", "open C:"
        add(
            r'\b(?:open|go\s+to|show|browse|navigate\s+to)\s+(?:the\s+)?([a-zA-Z])\s+(?:drive|disk|partition)\b',
            "open_drive",
            lambda m: {"drive": m.group(1).upper()},
        )
        add(
            r'\b(?:open|go\s+to|show|browse)\s+(?:the\s+)?([a-zA-Z]):\s*[\\\/]?\b',
            "open_drive",
            lambda m: {"drive": m.group(1).upper()},
        )
        # Bare "Z drive" / "the Z drive" with no leading verb — live-caught
        # failure: this used to fall through Tier2/Tier4 entirely (no verb
        # to match) and land on the general LLM fallback, which answered
        # "what does the Z drive mean" instead of trying to open it and
        # reporting whether it actually exists. Anchored to the WHOLE
        # utterance so it can't swallow a drive letter mentioned inside a
        # longer sentence ("explain what a hard drive is" must not match).
        # Letter range excludes A and I on purpose — "I drive" / "a drive"
        # are ordinary English (pronoun/article), not a drive letter.
        add(
            r'^\s*(?:the\s+)?([b-hj-zB-HJ-Z])\s+(?:drive|disk)\s*[.!?]*\s*$',
            "open_drive",
            lambda m: {"drive": m.group(1).upper()},
        )

        # ── System settings shortcuts ────────────────────────────────────────
        add(
            r'\b(?:open|show|go\s+to|launch)\s+(?:windows?\s+)?settings?\b',
            "open_application",
            lambda m: {"app_name": "settings"},
        )
        add(
            r'\b(?:open|show|launch)\s+(?:the\s+)?control\s+panel\b',
            "open_application",
            lambda m: {"app_name": "control panel"},
        )
        add(
            r'\b(?:open|launch|show)\s+(?:the\s+)?task\s+manager\b',
            "open_application",
            lambda m: {"app_name": "task manager"},
        )
        add(
            r'\b(?:open|launch|show)\s+(?:the\s+)?device\s+manager\b',
            "open_application",
            lambda m: {"app_name": "device manager"},
        )

        # ── Known system folders (must be before open_application catch-all) ───
        _FOLDERS = r'(?:downloads?|documents?|desktop|pictures?|photos?|music|videos?|temp(?:orary)?|home|appdata)'
        # "open downloads folder", "open my downloads", "open the downloads folder"
        add(
            r'\b(?:open|show|go\s+to|browse|navigate\s+to)\s+(?:my\s+|the\s+)?(' + _FOLDERS + r')\s*(?:folder|directory)?\b',
            "open_directory",
            lambda m: {"path": m.group(1).lower()},
        )
        # "open folder downloads", "open the downloads directory"
        add(
            r'\b(?:open|show|go\s+to|browse)\s+(?:the\s+)?(?:folder|directory)\s+(' + _FOLDERS + r')\b',
            "open_directory",
            lambda m: {"path": m.group(1).lower()},
        )

        # ── Subfolder creation ────────────────────────────────────────────────
        _NUM_WORDS = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }

        def _parse_subfolders(m: re.Match) -> dict:
            raw = m.group(0)
            # Extract count from digit or word
            count = 0
            digit_m = re.search(r'\b(\d+)\b', raw)
            if digit_m:
                count = int(digit_m.group(1))
            else:
                for word, n in _NUM_WORDS.items():
                    if re.search(r'\b' + word + r'\b', raw, re.IGNORECASE):
                        count = n
                        break
            # Extract explicit names: "named X, Y, Z" / "name of X" / "called X"
            names: list[str] = []
            named_m = re.search(
                r'(?:named?|called?|with\s+(?:the\s+)?name(?:s)?\s+(?:of)?)\s+([^.?!]+)',
                raw, re.IGNORECASE,
            )
            if named_m:
                raw_names = named_m.group(1)
                # Split on commas, "and", whitespace-only boundaries
                parts = re.split(r'\s*(?:,|and)\s*|\s+', raw_names.strip())
                names = [p.strip().rstrip('.,') for p in parts if p.strip()]
            return {"count": count or len(names) or 1, "names": names}

        add(
            r'\b(?:create|make|add)\s+(?:\d+\s+|(?:' +
            '|'.join(_NUM_WORDS.keys()) +
            r')\s+)?sub[\s-]?(?:folder|directory|folders|directories)\b'
            r'|\bcreate\s+sub[\s-]?folder',
            "create_subfolders",
            _parse_subfolders,
        )

        # ── Create single folder ──────────────────────────────────────────────
        def _parse_create_folder(m: re.Match) -> dict:
            name_raw = m.group(1).strip().rstrip('.,!?')
            loc_raw  = m.group(2).strip().rstrip('.,!?')
            # "C drive" / "C:" / "C disk" → "C:\"
            loc_m = re.match(r'^([a-zA-Z])\s*(?:drive|disk|:)?$', loc_raw, re.IGNORECASE)
            path = loc_m.group(1).upper() + ':\\' if loc_m else loc_raw
            return {'name': name_raw, 'path': path}

        add(
            r'\b(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+'
            r'(?:(?:called|named|with\s+(?:the\s+)?name\s+of)\s+)?(\S+)\s+'
            r'(?:in|on|at|inside|under)\s+(.+)',
            'create_folder',
            _parse_create_folder,
        )
        add(
            r'\b(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+'
            r'(?:(?:called|named|with\s+(?:the\s+)?name\s+of)\s+)?(\S+)',
            'create_folder',
            lambda m: {'name': m.group(1).strip().rstrip('.,!?'), 'path': ''},
        )

        # ── Rename file / folder ─────────────────────────────────────────────
        def _parse_rename(m: re.Match) -> dict:
            try:
                old = (m.group("old_name") or "").strip().rstrip(".,!?")
            except IndexError:
                old = ""
            try:
                new = (m.group("new_name") or "").strip().rstrip(".,!?")
            except IndexError:
                new = ""
            try:
                drv = (m.group("drive") or "").upper()
            except IndexError:
                drv = ""
            path = f"{drv}:\\{old}" if drv and old else old
            return {"path": path, "new_name": new}

        add(
            r'\brename\s+(?:(?:this|that|it|the)\s+)?(?:(?:folder|file|directory)\s+)?'
            r'(?P<old_name>(?!(?:to|into|as)\b)[a-zA-Z0-9_\-\.]{2,40})\s+(?:to|into)\s+'
            r'(?P<new_name>[a-zA-Z0-9_\-\.]+(?:\s+[a-zA-Z0-9_\-\.]+){0,2})',
            'rename_file',
            _parse_rename,
        )
        add(
            r'\bchange\s+(?:the\s+)?(?:folder\s+|file\s+|its\s+)?name\s+'
            r'(?P<old_name>[a-zA-Z0-9_\-\.]+)\s+(?:to|into)\s+'
            r'(?P<new_name>[a-zA-Z0-9_\-\.]+)',
            'rename_file',
            _parse_rename,
        )
        add(
            r'\bchange\s+(?:the\s+)?(?:folder\s+|file\s+)?name\s+(?:(?:to|into)\s+)?'
            r'(?!(?:in|on|at|for|the|a)\b)(?P<new_name>[a-zA-Z0-9_\-\.]+)',
            'rename_file',
            lambda m: {"path": "", "new_name": m.group("new_name").strip().rstrip(".,!?")},
        )
        add(
            r'^(?:call|name)\s+it\s+(?P<new_name>[a-zA-Z0-9_\-\.]+(?:\s+[a-zA-Z0-9_\-\.]+){0,3})',
            'rename_file',
            lambda m: {"path": "", "new_name": m.group("new_name").strip().rstrip(".,!?")},
        )

        # ── Drive-aware open / find (must be before generic smart_open patterns) ──────
        # Supported phrases:
        #   "in E drive open folder named python"  (drive-first)
        #   "open folder named python in E drive"  (drive-last)
        #   "find python in E drive"               (no type word)
        #   "search resume in D drive"             (search verb)
        #   "E drive python folder"                (drive + name + type)

        def _drive_type_str(raw: str) -> str:
            r = (raw or "").lower()
            if r in ("folder", "directory", "dir"):
                return "folder"
            if r in ("file", "document"):
                return "file"
            return "any"

        # Filler/connector words that can appear between a drive-context clause
        # and the actual command verb in a compound utterance — e.g. "Now in
        # E drive, can you also open perfume folder?" has "can you also"
        # between "E drive" and "open". Without excluding these, D1's query
        # capture greedily swallows them as the query itself (confirmed bug:
        # produced smart_open(query="can you also open", drive="E") instead
        # of failing over to the correct "open perfume folder" rule further
        # down the rule list). Excluding them makes D1 correctly NOT match
        # here, so route() falls through to the dedicated
        # "open <name> folder" rule instead.
        _D_FILLER = (r'the|my|a|an|in|on|named|called|can|you|also|please|'
                     r'could|would|just|now|well|kindly|to')

        # D1: drive FIRST → "in E drive open folder named python"
        add(
            r'\b(?:in|on|from)\s+(?P<d1>[a-zA-Z])\s+(?:drive|disk)\b'
            r'(?:\s+(?:open|find|locate|show|search(?:\s+for)?))?\s+'
            r'(?:(?:the|my|a)\s+)?'
            r'(?:(?P<t1>folder|directory|file|document|dir)\s+)?'
            r'(?:(?:named|called)\s+)?'
            r'(?P<q1>(?!(?:' + _D_FILLER + r')\b)\S+(?:\s+\S+){0,3})',
            'smart_open',
            lambda m: {
                "query": m.group("q1").strip().rstrip(".,!?"),
                "type":  _drive_type_str(m.group("t1")),
                "drive": m.group("d1").upper(),
            },
        )
        # D2: open/find/show + [type] + [named] + query + drive LAST
        # "open folder named python in E drive"  /  "find file resume in D drive"
        add(
            r'\b(?:open|find|locate|show|search(?:\s+for)?)\s+'
            r'(?:(?:the|my|a)\s+)?'
            r'(?:(?P<t2>folder|directory|file|document|dir)\s+)?'
            r'(?:(?:named|called)\s+)?'
            r'(?P<q2>(?!(?:' + _D_FILLER + r')\b)\S+(?:\s+\S+){0,2}?)'
            r'\s+(?:in|on|from)\s+(?P<d2>[a-zA-Z])\s+(?:drive|disk)\b',
            'smart_open',
            lambda m: {
                "query": m.group("q2").strip().rstrip(".,!?"),
                "type":  _drive_type_str(m.group("t2")),
                "drive": m.group("d2").upper(),
            },
        )
        # D3: find/search + query (no type word) + in drive
        # "find python in E drive"  /  "search resume in D drive"
        add(
            r'\b(?:find|locate|search(?:\s+for)?|look\s+for)\s+'
            r'(?:(?:the|my|a)\s+)?'
            r'(?P<q3>\S+(?:\s+\S+){0,3}?)'
            r'\s+(?:in|on|from)\s+(?P<d3>[a-zA-Z])\s+(?:drive|disk)\b',
            'smart_open',
            lambda m: {
                "query": m.group("q3").strip().rstrip(".,!?"),
                "type":  "any",
                "drive": m.group("d3").upper(),
            },
        )
        # D4: drive letter + name + type-word at end
        # "E drive python folder"  /  "D drive resume file"
        add(
            r'\b(?P<d4>[a-zA-Z])\s+(?:drive|disk)\s+'
            r'(?P<q4>(?!(?:folder|directory|file|document|dir)\b)\S+(?:\s+\S+){0,3}?)\s+'
            r'(?P<t4>folder|directory|file|document|dir)\b',
            'smart_open',
            lambda m: {
                "query": m.group("q4").strip().rstrip(".,!?"),
                "type":  _drive_type_str(m.group("t4")),
                "drive": m.group("d4").upper(),
            },
        )

        # ── Find / locate file or folder ──────────────────────────────────────
        # Pattern A: type-word at the END — "find the ios folder", "locate my resume file"
        # Named group ftype detects whether the trailing word is file or folder.
        add(
            r'\b(?:find|locate|where\s+is|where\'s|search\s+for)\s+'
            r'(?:(?:the|my|a|an)\s+)?(.+?)\s+(?P<ftype>folder|directory|file|dir)\b',
            'smart_open',
            lambda m: {
                "query": m.group(1).strip().rstrip(".,!?"),
                "type": "folder" if m.group("ftype") in ("folder", "directory") else "file",
            },
        )
        # Pattern B: type-word at the START — "find folder ios", "find file resume"
        # These must come after Pattern A so the more specific end-position wins
        add(
            r'\b(?:find|locate|where\s+is|where\'s)\s+(?:(?:the|my|a)\s+)?'
            r'(?:folder|directory)\s+(?:called\s+|named\s+)?'
            r'(?P<q_folder>\S+(?:\s+\S+){0,4})',
            'smart_open',
            lambda m: {"query": m.group("q_folder").strip().rstrip(".,!?"), "type": "folder"},
        )
        add(
            r'\b(?:find|locate|look\s+for|search\s+for)\s+(?:(?:a|the|my)\s+)?'
            r'file\s+(?:called\s+|named\s+)?'
            r'(?P<q_file>\S+(?:\s+\S+){0,4})',
            'smart_open',
            lambda m: {"query": m.group("q_file").strip().rstrip(".,!?"), "type": "file"},
        )

        # ── Urdu settings shortcuts — MUST precede generic Urdu app catch-all ──
        # "wifi kholo", "bluetooth kholo", "network on karo", etc.
        _URDU_SETTINGS_EARLY: list[tuple[str, str]] = [
            (r'wifi|wi-?fi',                   'wifi'),
            (r'network',                       'network'),
            (r'bluetooth',                     'bluetooth'),
            (r'display|screen|monitor',        'display'),
            (r'sound|audio',                   'sound'),
            (r'updates?|windows?\s+updates?', 'update'),
        ]
        for _ukw2, _upage2 in _URDU_SETTINGS_EARLY:
            _up2 = _upage2
            add(
                r'\b(?:' + _ukw2 + r')\s+(?:kholo|chalao|on\s+karo|dikao|dikhao|open\s+karo|show\s+karo|check\s+karo)\b',
                "open_system_settings",
                lambda m, p=_up2: {"page": p},
            )

        # ── Urdu/colloquial app launch phrases ──────────────────────────────────
        # "chrome kholo", "settings kholo", "vs code chala", etc.
        # Found via real-pipeline validation (2026-08-24): "Is repo ka
        # README kholo." ("open this repo's README") matched this pattern
        # DIRECTLY on the raw, un-canonicalized transcript — re.search
        # tries every start position, so even excluding demonstrative
        # words from the CAPTURED group (an earlier attempt) just let it
        # re-anchor past them and capture "README" alone as the "app
        # name," still wrong (README isn't an application). A demonstrative/
        # relative pronoun ("is/us/ye/wo/jo" = "this/that/which") ANYWHERE
        # in the sentence is a structural signal of a compositional/
        # relative-clause command ("this repo's X", "the one that...") —
        # never present in a real "chrome kholo"-style app launch — so
        # reject_if checks the WHOLE text once, independent of which
        # substring the capture group matched, and defers to Qwen (Tier 4)
        # instead of guessing.
        add(
            r'\b(\w[\w\s]{1,30}?)\s+(?:kholo|khol\s+do|chala(?:o)?|open\s+kar(?:o)?|start\s+kar(?:o)?|launch\s+kar(?:o)?)\b',
            "open_application",
            lambda m: {"app_name": m.group(1).strip()},
            reject_if=r'\b(?:is|us|ye|yeh|wo|woh|jo|iska|uska|jiska|jiski|jinka)\b|اس|یہ|وہ|جو|ان',
        )
        # "wifi on karo", "wifi off karo"
        add(r'\bwifi\s+on\s+(?:karo|kar|kro)\b', "open_wifi_panel")
        add(r'\bwifi\s+off\s+(?:karo|kar|kro)\b', "wifi_disconnect")
        # "volume barhao", "awaaz kam karo"
        add(r'\b(?:volume|awaaz|sound)\s+(?:barhao|barha\s+do|increase\s+kar(?:o)?)\b',
            "volume_control", lambda m: {"action": "increase", "steps": 2})
        add(r'\b(?:volume|awaaz|sound)\s+(?:ghata(?:o)?|kam\s+(?:karo|kar|kro)|decrease\s+kar(?:o)?)\b',
            "volume_control", lambda m: {"action": "decrease", "steps": 2})
        # "brightness barhao", "screen dark kar"
        add(r'\b(?:brightness|screen|display)\s+(?:barhao|barha\s+do|increase\s+kar(?:o)?|zyada\s+kar(?:o)?)\b',
            "brightness_control", lambda m: {"action": "increase", "step": 10})
        add(r'\b(?:brightness|screen)\s+(?:ghata(?:o)?|kam\s+(?:karo|kar|kro)|dark\s+kar(?:o)?)\b',
            "brightness_control", lambda m: {"action": "decrease", "step": 10})

        # ── YouTube play/search ─────────────────────────────────────────────────
        # "play X on YouTube", "play X on youtube"
        add(
            r'\bplay\s+(.+?)\s+on\s+(?:youtube|yt)\b',
            "search_youtube",
            lambda m: {"query": m.group(1).strip()},
        )
        # "search YouTube for X", "watch X on youtube"
        add(
            r'\b(?:search\s+(?:youtube|yt)\s+for|watch\s+.+?\s+on\s+(?:youtube|yt))\s+(.+)',
            "search_youtube",
            lambda m: {"query": m.group(1).strip()},
        )
        # Roman Urdu: "youtube X chalao", "youtube pe X chalao/chlao"
        add(
            r'\byoutube\s+(?:pe\s+)?(.+?)\s+(?:chalao|chlao|chalo|play\s+karo)\b',
            "search_youtube",
            lambda m: {"query": m.group(1).strip()},
        )
        # Roman Urdu: "youtube par/pr X [chalao]" — "par" form + chalao optional
        # Edge-TTS neural voices: Whisper often drops "chalao" at end, outputs "pr" for "par"
        add(
            r'\byoutube\s+(?:par|pr)\s+(.+?)(?:\s+(?:chalao|chlao|chalo))?[\s.,!?]*$',
            "search_youtube",
            lambda m: {"query": m.group(1).strip().rstrip(".,!?").strip()},
        )
        # ── Windows Update (must precede the search_web fallbacks below) ────
        # The generic "X updates/info → search_web" fallback just below
        # otherwise shadows "open windows update(s)" (first-match wins): the
        # dedicated Pattern A/B for the update page are registered ~150 lines
        # later and never get a chance. Same shape as Pattern B.
        add(
            r'\b(?:open|show|check|go\s+to|access|launch)\s+(?:my\s+)?windows?\s+updates?\b',
            "open_system_settings",
            lambda m: {"page": "update"},
        )
        # "search for X" / "search X" / "google X" — found missing entirely
        # during real-pipeline validation: local_comprehension's own
        # canonical synthesis for a web search produces exactly this shape
        # ("search for Pakistan weather"), and it silently depended on
        # Tier 3's semantic classifier (slow to load, not always ready) to
        # ever route anywhere. This is common and unambiguous enough to be
        # deterministic rather than left to a probabilistic fallback tier.
        add(
            r'^search\s+(?:for\s+|the\s+web\s+for\s+)?(.+?)[.,!?\s]*$',
            "search_web",
            lambda m: {"query": m.group(1).strip().rstrip(".,!?")},
        )
        add(
            r'^google\s+(.+?)[.,!?\s]*$',
            "search_web",
            lambda m: {"query": m.group(1).strip().rstrip(".,!?")},
        )
        # "X dikhao" / "X show" — Urdu "show me X" → search (no youtube context = web search)
        add(
            r'^(.+?)\s+(?:dikhao|dikha|show|show\s+karo|search\s+karo)[.,!?\s]*$',
            "search_web",
            lambda m: {"query": m.group(1).strip().rstrip(".,!?")},
        )
        # Fallback: "latest/recent/new X" — Whisper drops Urdu verb but retains noun phrase
        add(
            r'^(?:latest|recent|new|newest)\s+(.+?)\.?\s*$',
            "search_web",
            lambda m: {"query": m.group(0).strip().rstrip(".,!?")},
        )
        # "X research/news/updates/info" without explicit verb
        add(
            r'^(.+?)\s+(?:news|research|updates?|info|information)\.?\s*$',
            "search_web",
            lambda m: {"query": m.group(0).strip().rstrip(".,!?")},
        )

        # ── Microsoft Store install / download ──────────────────────────────────
        # "download/install/get X from microsoft store" → install_store_app
        add(
            r'\b(?:download|install|get)\s+(.+?)\s+from\s+(?:microsoft\s+)?(?:the\s+)?store\b',
            "install_store_app",
            lambda m: {"app_name": m.group(1).strip()},
        )
        # "install/download/get X" (bare, without store context — intent_router provides store routing)
        add(
            r'\b(?:install|get|set\s+up|setup)\s+(?:the\s+)?([A-Za-z][\w\s]{2,30}?)\s+(?:app\b|application\b)',
            "install_store_app",
            lambda m: {"app_name": m.group(1).strip()},
        )
        # Canonical bare + compound store-install detection, shared with
        # voice_ws.py Tier 0g and both follow-up resolvers via store_agent.py.
        # Catches "open microsoft store and install X" — the compound phrasing
        # the two rules above miss (neither has an "open ... and" shape) — and
        # backstops bare "install X" for any caller that reaches intent_router
        # directly without going through voice_ws.py's Tier 0g bypass first.
        from api.services.store_agent import COMPOUND_RE as _sa_compound_re
        from api.services.store_agent import BARE_RE as _sa_bare_re
        from api.services.store_agent import clean_product as _sa_clean_product
        add(
            _sa_compound_re.pattern,
            "install_store_app",
            lambda m: {"app_name": _sa_clean_product(m.group("product"))},
        )
        add(
            _sa_bare_re.pattern,
            "install_store_app",
            lambda m: {"app_name": _sa_clean_product(m.group("product"))},
        )

        # ── Bare known-app name → open_application ──────────────────────────────
        # Handles "chrome." when Whisper drops the Urdu verb "kholo" (accent issue).
        # Only matches if the ENTIRE transcript is one of these exact app names.
        _BARE_APPS = (
            "chrome", "firefox", "edge", "microsoft edge",
            "notepad", "calculator", "spotify", "discord", "steam",
            "settings", "explorer", "file explorer", "vs code", "vscode",
            "whatsapp", "telegram", "teams", "slack", "zoom",
        )
        add(
            r'^\s*(' + '|'.join(re.escape(a) for a in _BARE_APPS) + r')\s*[.!?]?\s*$',
            "open_application",
            lambda m: {"app_name": m.group(1).strip()},
        )

        # ── Work mode / focus mode → run_workflow(name="work_mode") ─────────────
        # Tier2 fast-path so "it's work time" never falls through to LLM
        add(r"\bit['\s]+?s\s+work\s+time\b",
            "run_workflow", lambda m: {"name": "work_mode"})
        add(r'\b(?:it[\'s\s]+work\s+time|work\s+time(?:\s+buddy)?)\b',
            "run_workflow", lambda m: {"name": "work_mode"})
        add(r'\b(?:start|enable|activate|switch\s+to|set\s+up|begin|enter|go\s+into)\s+'
            r'(?:work|coding|developer?|dev|focus|deep\s+work)\s+mode\b',
            "run_workflow", lambda m: {"name": "work_mode"})
        add(r'\b(?:work|coding|developer?|dev|focus)\s+mode\s*(?:on|start|activate|enable|please)?\b',
            "run_workflow", lambda m: {"name": "work_mode"})
        add(r'\b(?:get\s+me\s+ready\s+to\s+(?:work|code|dev)|open\s+my\s+work\s+apps|'
            r'prepare\s+my\s+(?:work|dev(?:eloper)?)\s+(?:setup|environment|env))\b',
            "run_workflow", lambda m: {"name": "work_mode"})

        # ── Organize files → organize_files tool ─────────────────────────────
        # Requires an explicit location noun or "files" right after the verb so
        # generic uses of "clean"/"sort"/"tidy" (e.g. "clean my keyboard") don't
        # misfire — organize_files itself always confirms before moving anything.
        add(
            r'\b(?:organi[sz]e|clean(?:\s*up)?|tidy(?:\s*up)?|sort(?:\s*out)?)\s+'
            r'(?:my\s+|the\s+)?(?P<loc>desktop|downloads?|documents?|pictures?)\b',
            "organize_files",
            lambda m: {"path": m.group("loc")},
        )
        add(
            r'\b(?:organi[sz]e|clean(?:\s*up)?|tidy(?:\s*up)?|sort(?:\s*out)?)\s+'
            r'(?:my\s+|the\s+)?files\b',
            "organize_files",
        )
        add(
            r'\bundo\s+(?:the\s+|last\s+|my\s+)?(?:desktop\s+|file\s+)?organi[sz](?:e|ing|ation|ed)\b',
            "undo_organize_files",
        )
        # Broader natural phrasings for "put it back the way it was" — none
        # of these are destructive (undo_organize_files just no-ops with
        # "nothing to undo" if there's no recent organize run), so it's safe
        # to be generous here. This also matters for safety: a vague phrase
        # like "return them to their original form" must be caught here,
        # at Tier2, before it can ever fall through to the unrelated
        # "delete the folder" memory-pronoun path (see feedback_delete_safety_pattern).
        add(r'\bput\s+(?:everything|them|it|things|my\s+files|the\s+files)\s+back\b', "undo_organize_files")
        add(
            r'\breturn\s+(?:everything|them|it|things)?\s*(?:back\s+)?to\s+(?:their|its|the)?\s*original\b',
            "undo_organize_files",
        )
        add(r'\bback\s+to\s+(?:the\s+)?original\b', "undo_organize_files")
        add(r'\brestore\s+(?:the\s+)?(?:original|desktop|downloads|documents|pictures|files)\b', "undo_organize_files")
        # "return it back on" / "return back all things" — colloquial/STT-garbled
        # variants without "to original", both word orders.
        add(
            r'\breturn\s+(?:back\s+(?:all\s+)?(?:things|everything|them|it)|'
            r'(?:them|it|things|everything)\s+back)\b',
            "undo_organize_files",
        )
        add(r'\b(?:undo|reverse|revert)\s+(?:that|it|this)\b', "undo_organize_files")

        # ── Windows Settings deep-link resolver ──────────────────────────────
        # MUST precede the open_application catch-all.
        # Two patterns per page:
        #   A) keyword + "settings/preferences/panel" (no verb required)
        #   B) explicit verb + optional "windows/system" + keyword
        # Both log [SETTINGS_RESOLVER] and route to open_system_settings.
        #
        # Handles: "display settings", "open display settings",
        #   "open windows display settings please", "monitor settings",
        #   "can you open display settings for me", "screen settings", etc.
        #
        # Order matters: more-specific keyword strings first so "wifi settings"
        # does not accidentally fall inside a broader "network" pattern.

        # ── Urdu / bare-verb settings shortcuts (not covered by Pattern A/B) ──────
        # Pattern B requires an English verb like "open/show"; these use Urdu verbs.
        _URDU_PAGE_KWS: list[tuple[str, str]] = [
            (r'wifi|wi-?fi',                 'wifi'),
            (r'network',                     'network'),
            (r'bluetooth',                   'bluetooth'),
            (r'display|screen|monitor',      'display'),
            (r'sound|audio',                 'sound'),
            (r'updates?|windows?\s+updates?','update'),
        ]
        for _ukw, _upage in _URDU_PAGE_KWS:
            _up = _upage
            add(
                r'\b(?:' + _ukw + r')\s+(?:kholo|chalao|on\s+karo|dikao|dikhao|open\s+karo|show\s+karo|check\s+karo)\b',
                "open_system_settings",
                lambda m, p=_up: {"page": p},
            )

        _WIN_SETTINGS_PAGES: list[tuple[str, str]] = [
            # (keyword_regex_fragment,                         page_name)
            (r'display|screen(?:\s+res(?:olution)?)?|monitor|resolution', 'display'),
            (r'sound\b|audio\s+settings?',                             'sound'),
            (r'bluetooth',                                             'bluetooth'),
            (r'wi-?fi|wireless',                                       'wifi'),
            (r'network',                                               'network'),
            (r'privacy',                                               'privacy'),
            (r'apps?\s+(?:and\s+)?(?:features?|programs?|settings?)',  'apps'),
            (r'(?:windows?\s+)?updates?',                              'update'),
            (r'power(?:\s+(?:and\s+)?sleep)?',                         'power'),
            (r'storage\s+(?:settings?|sense)',                         'storage'),
            (r'accounts?',                                             'accounts'),
            (r'date\s+(?:and\s+)?time|time\s+(?:and\s+date\s+)?',     'time'),
            (r'language|region(?:al)?',                                'language'),
            (r'accessibility|ease\s+of\s+access',                      'accessibility'),
            (r'notifications?',                                        'notifications'),
            (r'personaliz[ae]tion|themes?',                            'personalization'),
            (r'taskbar\s+(?:settings?)?',                              'taskbar'),
            (r'startup\s+(?:apps?|programs?)',                         'startup'),
            (r'mouse\s+(?:settings?)?',                                'mouse'),
            (r'keyboard\s+(?:settings?)?',                             'keyboard'),
            (r'camera\s+(?:settings?)?',                               'camera'),
        ]

        _TRAIL = r'(?:\s+(?:for\s+me|please|buddy|now|right\s+now|mate|bro|yaar))*'
        _OPT_WIN = r'(?:windows?\s+|system\s+|my\s+)?'

        for _kw, _page in _WIN_SETTINGS_PAGES:
            _p = _page   # close over value, not variable

            # Pattern A: keyword immediately followed by settings/preferences/etc.
            # "display settings", "sound settings", "bluetooth settings please"
            add(
                r'\b(?:' + _kw + r')\s+(?:settings?|preferences?|panel|page|options?|config(?:uration)?)' + _TRAIL + r'\b',
                "open_system_settings",
                lambda m, p=_p: (
                    logger.info("[SETTINGS_RESOLVER] page=%s target=ms-settings:%s", p, p) or  # type: ignore[func-returns-value]
                    {"page": p}
                ),
            )

            # Pattern B: explicit intent verb + optional "windows/system" + keyword
            # "open display settings", "open windows sound settings", "show bluetooth"
            add(
                r'\b(?:open|show|change|go\s+to|access|launch|take\s+me\s+to)\s+' + _OPT_WIN +
                r'(?:' + _kw + r')(?:\s+(?:settings?|preferences?|panel|page|options?))?' + _TRAIL + r'\b',
                "open_system_settings",
                lambda m, p=_p: (
                    logger.info("[SETTINGS_RESOLVER] page=%s target=ms-settings:%s", p, p) or  # type: ignore[func-returns-value]
                    {"page": p}
                ),
            )

        # ── Open / close application ──────────────────────────────────────────
        # Strip trailing polite suffixes from app_name so "open chrome for me please"
        # → app_name="chrome" instead of app_name="chrome for me please".
        _POLITE_TAIL_RE = re.compile(
            r'\s+(?:for\s+me|please|buddy|now|right\s+now|mate|bro|yaar|dude|also)'
            r'(?:\s+(?:for\s+me|please|buddy|now|right\s+now|mate|bro|yaar|dude|also))*\s*$',
            re.IGNORECASE,
        )

        def _clean_app_name(m: re.Match) -> dict:
            raw = m.group(1).strip().rstrip(".,!?")
            return {"app_name": _POLITE_TAIL_RE.sub('', raw).strip().rstrip(".,!?")}

        # ── Direct Windows path (from ordinal disambiguation resolution) ────────
        # e.g. "open C:\Users\tayyab\python"  or  "open E:\Projects\Python"
        add(
            r'^open\s+([A-Za-z]:[\\\/](?:[^\\\/\s]+[\\\/]?)+)\s*$',
            'smart_open',
            lambda m: {"query": m.group(1).strip(), "type": "any"},
        )

        # ── Open arbitrary named folder (before open_application catch-all) ──────
        # Catches "open folder alpha", "open folder called My Projects", etc.
        # System-folder names are already handled by the open_directory rules above;
        # this catches everything else so it doesn't fall through to app_finder.

        # Drive-aware version first — "open folder named python in E drive"
        add(
            r'\b(?:open|show|go\s+to|browse|navigate\s+to)\s+(?:(?:the|my|a)\s+)?'
            r'folder\s+(?:called\s+|named\s+|with\s+(?:the\s+)?name\s+)?'
            r'(?P<q_dof>\S+(?:\s+\S+){0,3}?)'
            r'\s+(?:in|on|from)\s+(?P<d_dof>[a-zA-Z])\s+(?:drive|disk)\b',
            'smart_open',
            lambda m: {
                "query": m.group("q_dof").strip().rstrip(".,!?"),
                "type": "folder",
                "drive": m.group("d_dof").upper(),
            },
        )
        # Generic version (no drive)
        add(
            r'\b(?:open|show|go\s+to|browse|navigate\s+to)\s+(?:(?:the|my|a)\s+)?'
            r'folder\s+(?:called\s+|named\s+|with\s+(?:the\s+)?name\s+)?'
            r'(?P<q_opfolder>\S+(?:\s+\S+){0,4})',
            'smart_open',
            lambda m: {"query": m.group("q_opfolder").strip().rstrip(".,!?"), "type": "folder"},
        )

        # ── "open <name> folder/directory" — must be before open_application catch-all ──
        # Handles: "open hackathon folder", "open python directory", "open client folder"
        add(
            r'\b(?:open|show|go\s+to|browse)\s+(?:the\s+|my\s+|a\s+)?'
            r'(?P<q_named_folder>[\w\s\-\.]{2,40}?)'
            r'\s+(?:folder|directory)\s*[.!?]?\s*$',
            'smart_open',
            lambda m: {
                "query": m.group("q_named_folder").strip().rstrip(".,!? "),
                "type": "folder",
            },
        )
        logger.debug("[FOLDER_INTENT_DETECTED] pattern=open_name_folder registered")

        # Pre-existing English gap (not Urdu-specific — found while wiring
        # Roman-Urdu pronoun normalization through this same pipeline):
        # "close it"/"kill it"/"open the first one" used to fall all the way
        # down to the bare catch-alls below, which captured the pronoun
        # itself as the literal app name ("close it" -> kill_app
        # app_name="it") — Tier 2.5's object_resolver/context_stack
        # pronoun resolution never got a chance to run because these two
        # regexes matched first and unconditionally. "open"/"the first one"
        # is excluded here so it falls through to Tier 2.5 (which already
        # resolves pronouns/ordinals via ContextStack — see object_resolver.py).
        _PRONOUN_OR_ORDINAL = (
            r'it|this|that|them|these|those|'
            r'the\s+(?:first|second|third|fourth|next|previous|same|other)\s*(?:one)?'
        )

        def _resolve_close_pronoun(m: re.Match) -> dict:
            """'close it'/'kill it'/'close the first one' — resolve the
            pronoun via ContextStack (same resolver object_resolver.py
            uses for the "open" case) instead of leaving it unresolved.
            object_resolver.py itself can't be reused directly here: it
            hardcodes action="open" (it only ever answers "open X"), so a
            close/kill verb needs its own small resolution using the same
            underlying ContextStack the open path already relies on —
            no new context-tracking system, just the existing one read
            for a verb object_resolver doesn't cover."""
            raw = m.group(1).strip().rstrip(".,!?")
            try:
                from api.services.context_stack import context_stack as _cs
                entity = _cs.resolve(m.group(0))
                if entity is not None and entity.type == "app":
                    return {"app_name": entity.value}
            except Exception:
                pass
            return {"app_name": raw}

        add(r'\b(?:close|quit|exit|kill)\s+(?!window|tab)(' + _PRONOUN_OR_ORDINAL + r')\b',
            "kill_app", _resolve_close_pronoun)

        # ── Open file — MUST precede the generic "open X" catch-all just
        # below: pre-existing English bug (not Urdu-specific — found while
        # tracing why Qwen's own "open file <name>" canonical synthesis
        # misrouted). First-match-wins in this Tier-2 list, and the
        # generic catch-all's `.+` matches ANYTHING after "open ", so
        # "open file report.txt" was captured whole as a literal
        # application name ("file report.txt") and the more specific
        # open_file rule below never got a chance to run at all.
        add(r'\b(?:open|show)\s+(?:the\s+)?(?:file|document|pdf)\s+(.+)',
            "open_file",
            lambda m: {"path": m.group(1).strip().rstrip(".,!?")})

        add(r'\b(?:open|launch|start|run|fire\s+up|pull\s+up)\s+(?!' + _PRONOUN_OR_ORDINAL + r')(.+)',
            "open_application",
            _clean_app_name)
        add(r'\b(?:close|quit|exit|kill)\s+(?!window|tab)(.+)',
            "kill_app",
            lambda m: {"app_name": m.group(1).strip().rstrip(".,!?")})

        # ── Apps / processes ─────────────────────────────────────────────────
        add(r'\bwhat\s+(?:apps?|programs?|applications?)\s+(?:are\s+)?(?:running|open|active)\b', "get_running_apps")
        add(r'\b(?:list|show)\s+(?:running\s+)?(?:apps?|processes?|applications?)\b', "get_running_apps")
        add(r'\b(?:list|show)\s+(?:running\s+)?processes?\b|\btask\s+manager\b', "list_processes")

        # ── Window management ────────────────────────────────────────────────
        add(r'\bminimize\b', "minimize_window")
        add(r'\bmaximize\b', "maximize_window")
        add(r'\bclose\s+(?:this\s+)?(?:window|tab)\b', "close_window")

        # ── WiFi ────────────────────────────────────────────────────────────
        add(r'\b(?:show|find|get|list)\s+me\s+(?:(?:near(?:by|est)?|available|closest)\s+)?(?:wifi|wi-fi|wireless)\b', "open_wifi_panel")
        add(r'\b(?:wifi|wi-fi)\s+(?:near\s+me|nearby|available|networks?|list|scan|panel)\b', "open_wifi_panel")
        add(r'\b(?:show|list|scan)\s+(?:available\s+)?(?:wifi|wi-fi|wireless)\s*(?:networks?|connections?)?\b', "wifi_list")
        add(r'\binternet\s+speed(?:\s+test)?\b|\bspeed\s+test\b|\bhow\s+fast\s+is\s+(?:my\s+)?(?:internet|connection)\b', "network_speed_test")
        add(r'\bflush\s+dns\b|\bclear\s+dns\b|\breset\s+dns\b', "flush_dns")

        # ── Disk / cleanup ───────────────────────────────────────────────────
        add(r'\b(?:how\s+big\s+(?:is|are)\s+)?temp(?:orary)?\s+files?\s*(?:size|space)?\b', "get_temp_files_size")
        add(r'\bclear\s+temp\b|\bdelete\s+temp(?:orary)?\s+files?\b', "clear_temp_files")
        add(r'\bdisk\s+cleanup\b|\brun\s+(?:a\s+)?cleanup\b', "run_disk_cleanup")
        add(r'\bempty\s+(?:the\s+)?(?:recycle\s+bin|trash)\b|\bclear\s+(?:the\s+)?trash\b', "empty_recycle_bin")
        # Roman Urdu: "recycle bin khali karo", "kachra saaf karo", "trash clear karo"
        add(r'\b(?:recycle\s*bin|kachra|kachray|trash)\s+(?:khali\s+kar[oa]?|saaf\s+kar[oa]?|clear\s+kar[oa]?)\b',
            "empty_recycle_bin")
        add(r'\b(?:startup|boot)\s+(?:apps?|programs?|applications?)\b|\bwhat\s+(?:starts?|runs?)\s+on\s+(?:startup|boot)\b', "get_startup_apps")
        add(r'\bcheck\s+(?:for\s+)?(?:windows\s+)?updates?\b', "check_windows_updates")

        # ── Clipboard ────────────────────────────────────────────────────────
        add(r'\bwhat.?s\s+(?:in|on)\s+(?:my\s+)?clipboard\b|\bread\s+(?:the\s+)?clipboard\b', "read_clipboard")
        add(r'\bclear\s+(?:the\s+)?clipboard\b', "clear_clipboard")

        # ── Email / Calendar ─────────────────────────────────────────────────
        add(r'\b(?:read|check|open|show)\s+(?:my\s+)?(?:emails?|inbox|gmail|mail)\b', "read_inbox")
        add(r'\b(?:what.?s\s+on\s+my\s+calendar|upcoming\s+events?|my\s+(?:schedule|agenda)|any\s+meetings?)\b', "list_events")

        # ── Summary ──────────────────────────────────────────────────────────
        add(r'\b(?:what\s+have\s+(?:I|you)\s+done|recent\s+(?:activity|history)|show\s+(?:my\s+)?(?:history|activity))\b', "get_summary")

    # ────────────────────────────────────────────────────────────────────────
    # Tier 3 — Semantic classifier
    # ────────────────────────────────────────────────────────────────────────

    def _load_classifier(self) -> None:
        import os as _os_lc
        if _os_lc.getenv("LOCAL_ONLY_MODE", "").lower() in ("1", "true", "yes"):
            logger.info("[IntentRouter] LOCAL_ONLY_MODE=true — classifier load skipped")
            return

        # Wait for critical voice-path warmup (Kokoro + Whisper) to finish
        # before loading this on CUDA. Confirmed root cause of the
        # keepalive-timeout incident: this constructor's tensor/tokenizer
        # deserialization runs on a plain thread and holds the GIL for
        # extended stretches, starving the asyncio event loop that also
        # has to service uvicorn's own WebSocket ping/pong — that starvation
        # is what produced "code=1011 reason=keepalive ping timeout" during
        # the wake/greeting window. This thread already runs in the
        # background (started at IntentRouter.__init__, i.e. import time),
        # so waiting here costs nothing on any path that matters.
        try:
            from api.services.readiness_service import readiness_service as _rs_wait
            logger.info("[GPU_BACKGROUND_DEFERRED] component=sentence_transformer_classifier waiting_for=core_ready")
            _rs_wait.wait_core_ready(timeout=60.0)
        except Exception:
            pass
        try:
            from api.services.gpu_coordinator import wait_for_voice_idle as _wait_voice_idle
            _wait_voice_idle("sentence_transformer_classifier", timeout=30.0)
        except Exception:
            pass

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            import numpy as np
            import torch as _torch
            _device = "cuda" if _torch.cuda.is_available() else "cpu"
            logger.info("[IntentRouter] Loading sentence-transformers on %s…", _device)
            # local_files_only=True — never contact HuggingFace during runtime.
            # Model must be pre-cached: `python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"`
            try:
                model = SentenceTransformer("all-MiniLM-L6-v2", device=_device, local_files_only=True)
            except Exception:
                from api.services.network_state import apply_offline_env as _apply_offline_env_ir
                if _apply_offline_env_ir(force_recheck=True):
                    logger.warning("[IntentRouter] local cache miss and network unreachable — "
                                    "Tier 3 classifier disabled for this process")
                    raise
                logger.warning("[IntentRouter] local cache miss — attempting download (set LOCAL_ONLY_MODE=1 to suppress)")
                model = SentenceTransformer("all-MiniLM-L6-v2", device=_device)
            names = list(_TOOL_DESCS.keys())
            embs  = model.encode(list(_TOOL_DESCS.values()), show_progress_bar=False, batch_size=64)
            self._model = model
            self._embeddings = dict(zip(names, embs))
            self._np = np
            self._classifier_ready = True
            logger.info("[IntentRouter] Semantic classifier ready (%d tools embedded)", len(names))
        except ImportError:
            logger.exception(
                "[IntentRouter] sentence-transformers import chain failed — Tier 3 disabled permanently "
                "for this process (this is a one-time startup thread, never retried per-request). "
                "The generic 'not installed' guess was misleading: on this environment the real cause "
                "is usually a numpy/pandas/scikit-learn ABI mismatch several imports deep inside "
                "sentence_transformers, not a missing package — see the traceback above for the actual "
                "failing import."
            )
        except Exception:
            logger.exception("[IntentRouter] Failed to load classifier — Tier 3 disabled permanently for this process")

    def _semantic_route(self, text: str) -> RouteResult:
        if not self._classifier_ready:
            return RouteResult(None, {}, 4, 0.0)
        # Found via real-pipeline validation (2026-08-24): raw Urdu-script
        # text ("آواز تھوڑی کم کرو۔" — "reduce the volume a bit") was
        # confidently (but wrongly) routed to brightness_control by this
        # tier. Root cause: the classifier model is all-MiniLM-L6-v2,
        # trained on English only (see the model-load comment a few lines
        # below) — it has no real semantic understanding of Arabic-script
        # text, so its embedding for Urdu script is closer to noise than
        # meaning, yet still produces a confident-looking nearest-neighbor
        # score. Worse, that false-confident match short-circuits BEFORE
        # local_comprehension's Qwen tier ever runs (orchestrator only
        # tries Qwen when intent_router found nothing >= 0.55) — the one
        # tier actually built and tested for Urdu-script semantics. Skip
        # this tier outright for Arabic-script text so it falls straight
        # through to Qwen instead of pre-empting it with a wrong guess.
        # Roman Urdu/mixed text is NOT skipped here — mixed_language_engine
        # (Tier 1) already converts what it can to English before this
        # tier ever sees it, and untranslated Roman Urdu is at least valid
        # Latin-alphabet text the same embedding space partially covers.
        if _ARABIC_SCRIPT_RE.search(text):
            return RouteResult(None, {}, 4, 0.0)
        try:
            np = self._np
            q = self._model.encode(text, show_progress_bar=False, convert_to_numpy=True)  # type: ignore
            best_name, best_score = None, -1.0
            for name, emb in self._embeddings.items():
                score = float(np.dot(q, emb) / (np.linalg.norm(q) * np.linalg.norm(emb) + 1e-9))
                if score > best_score:
                    best_score, best_name = score, name
            confidence = best_score
            # Don't route dangerous tools via semantics alone
            if best_name in _SAFE_GUARD_TOOLS:
                return RouteResult(None, {}, 4, 0.0)
            if confidence >= 0.65:
                return RouteResult(best_name, {}, 3, confidence)
        except Exception:
            logger.debug("[IntentRouter] Semantic inference error", exc_info=True)
        return RouteResult(None, {}, 4, 0.0)

    # ────────────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────────────

    def route(self, text: str) -> RouteResult:
        """
        Route a user utterance to a tool.
        Returns RouteResult with tool_name=None if no confident match (Tier 4).
        """
        # Phonetic drive-letter guard (real-mic Urdu test Issue 2A): route()
        # may receive text that bypasses the voice normalizer (direct callers,
        # canonical rewrites), so apply the shared context-scoped correction
        # here too — idempotent when the normalizer already ran. "open see
        # drive" → "open c drive" so the drive rules below match it instead
        # of the open_application catch-all.
        try:
            from api.services.normalizer import _correct_drive_phonetics
            text = _correct_drive_phonetics(text)
        except Exception:
            pass
        key = text.lower().strip()

        # Tier 0 — local clock (no LLM, no internet, instant)
        _local = _local_clock_route(key)
        if _local:
            return _local

        # Tier 0.5 — WhatsApp deterministic fast path (Phase 4)
        _wa = _whatsapp_route(text)
        if _wa:
            return _wa

        # Tier 1 — exact cache
        with self._cache_lock:
            if key in self._cache:
                c = self._cache[key]
                logger.debug("[IntentRouter] Tier1 cache hit: %r → %s", text[:60], c.tool_name)
                return RouteResult(c.tool_name, c.params, 1, 1.0)

        # Tier 2 — regex
        for pattern, tool, params_fn, reject_if in self._rules:
            m = pattern.search(text)
            if m and reject_if and reject_if.search(text):
                continue
            if m:
                try:
                    params = params_fn(m)
                except Exception:
                    params = {}
                # ── Object-type-aware re-route (real-mic Urdu test 2B/6) ────
                # The generic "open X" catch-all must not swallow objects
                # whose TYPE object_resolver can prove — live failure: "Seed
                # drive kholo" → "open Seed drive" hit the catch-all and
                # executed open_application even though object_resolver
                # reported type=drive conf=0.95. Object evidence outranks
                # the catch-all here, the same invariant
                # _exec_open_application already enforces for folder/file
                # types. settings_page/control_panel deliberately stay on
                # open_application — that IS the ms-settings:/known-map
                # launch path the verifier understands.
                if tool == "open_application":
                    try:
                        from api.services.object_resolver import (
                            resolve as _t2_obj_resolve,
                            tool_for as _t2_obj_tool_for,
                        )
                        _t2_obj = _t2_obj_resolve(text)
                        _t2_type = _t2_obj.object_type
                        if (
                            _t2_obj.confidence >= 0.6
                            and _t2_obj.name
                            and _t2_type in (
                                "drive", "folder", "file", "document",
                                "project", "workspace", "repository",
                                "website", "browser_tab",
                            )
                        ):
                            if _t2_type in ("website", "browser_tab"):
                                # Known browser-shortcut sites keep the
                                # open_application path — it routes through
                                # _APP_MAP into browser_workspace, the shared
                                # CDP tab every follow-up command reuses.
                                # Only sites with no app-map entry go to the
                                # generic open_url tool.
                                _t2_keep_app = False
                                try:
                                    from api.tools.system_tools import (
                                        _APP_MAP as _t2_app_map,
                                        _normalise_app as _t2_norm,
                                    )
                                    _t2_keep_app = _t2_norm(_t2_obj.name) in _t2_app_map
                                except Exception:
                                    pass
                                if _t2_keep_app:
                                    params = {"app_name": _t2_obj.name}
                                    logger.info(
                                        "[IntentRouter] Tier2 website keeps open_application "
                                        "(browser_workspace path): %r", text[:60],
                                    )
                                else:
                                    tool = "open_url"
                                    params = _params_for_object(tool, _t2_obj)
                                    logger.info(
                                        "[IntentRouter] Tier2 object re-route: %r → open_url (type=%s conf=%.2f)",
                                        text[:60], _t2_type, _t2_obj.confidence,
                                    )
                            else:
                                tool = _t2_obj_tool_for(_t2_type)
                                params = _params_for_object(tool, _t2_obj)
                                logger.info(
                                    "[IntentRouter] Tier2 object re-route: %r → %s (type=%s conf=%.2f)",
                                    text[:60], tool, _t2_type, _t2_obj.confidence,
                                )
                    except Exception:
                        logger.debug("[IntentRouter] Tier2 object re-route check failed",
                                     exc_info=True)
                result = RouteResult(tool, params, 2, 1.0)
                with self._cache_lock:
                    self._cache[key] = result
                logger.info("[IntentRouter] Tier2 regex: %r → %s params=%s",
                            text[:60], tool, params)
                return result

        # Tier 2.5 — Object Resolver (Phase 3.5): catches compositional
        # phrasings the fixed regex shapes above don't cover — "go inside
        # perfume", "take me to perfume", "open it" — using explicit-noun/
        # pronoun/scope evidence instead of yet another regex. Deterministic
        # and offline (Part 12: no LLM on the critical path for obvious
        # filesystem commands). Only fires on utterances that actually look
        # like an open/navigate request, and only acts on a confident result.
        if _OBJECT_RESOLVER_VERB_RE.search(text):
            try:
                from api.services.object_resolver import resolve as _obj_resolve, tool_for as _obj_tool_for
                obj = _obj_resolve(text)
                # 0.6 cleanly separates every confident resolution path
                # (explicit noun 0.95, pronoun/context-stack 0.9, scoped
                # filesystem match up to 0.85, known app name 0.75) from the
                # "no evidence at all" unknown fallback (0.3) — see
                # object_resolver.resolve().
                if obj.object_type != "unknown" and obj.confidence >= 0.6 and obj.name:
                    tool = _obj_tool_for(obj.object_type)
                    params = _params_for_object(tool, obj)
                    result = RouteResult(tool, params, 2, obj.confidence)
                    with self._cache_lock:
                        self._cache[key] = result
                    logger.info("[IntentRouter] Tier2.5 object_resolver: %r → %s params=%s conf=%.2f",
                                text[:60], tool, params, obj.confidence)
                    return result
            except Exception:
                logger.debug("[IntentRouter] object_resolver tier failed", exc_info=True)

        # Tier 3 — semantic classifier
        result = self._semantic_route(text)
        if result.tool_name:
            logger.info("[IntentRouter] Tier3 semantic: %r → %s conf=%.2f",
                        text[:60], result.tool_name, result.confidence)
            return result

        # Tier 4 — no match; log top candidates so engineers can diagnose
        candidates = self.top_candidates(text, n=3)
        if candidates:
            top_str = ", ".join(f"{n}({c:.2f})" for n, c in candidates)
            logger.info("[IntentRouter] Tier4 no match for %r — top candidates: %s "
                        "(none exceeded 0.65 threshold) → falling back to LLM",
                        text[:60], top_str)
        else:
            logger.info("[IntentRouter] Tier4 no match for %r — semantic classifier "
                        "not ready or no candidates → falling back to LLM", text[:60])
        return RouteResult(None, {}, 4, 0.0)

    def top_candidates(self, text: str, n: int = 3) -> list[tuple[str, float]]:
        """
        Return the top-N (tool_name, confidence) pairs from the semantic classifier.
        Used by the clarification flow when route() returns Tier 4.
        Returns [] if classifier not ready.
        """
        if not self._classifier_ready:
            return []
        try:
            import numpy as np
            q = self._model.encode(text, show_progress_bar=False)
            scores = []
            for name, emb in self._embeddings.items():
                if name in _SAFE_GUARD_TOOLS:
                    continue
                score = float(np.dot(q, emb) / (np.linalg.norm(q) * np.linalg.norm(emb) + 1e-9))
                scores.append((name, score))
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:n]
        except Exception:
            return []

    def confirm(self, text: str, tool_name: str, params: dict) -> None:
        """
        Called after LLM confirms a route — teaches the cache so the same
        phrasing skips LLM next time.
        """
        key = text.lower().strip()
        with self._cache_lock:
            self._cache[key] = RouteResult(tool_name, params, 1, 1.0)

    @property
    def classifier_ready(self) -> bool:
        return self._classifier_ready


intent_router = IntentRouter()
