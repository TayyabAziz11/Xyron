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
    "open_drive":             "open drive c d e f disk explorer",
    "brightness_control":     "set increase decrease adjust screen brightness dim bright make screen brighter darker",
    "volume_control":         "set increase decrease adjust volume audio sound louder quieter turn volume up down",
    "mute_unmute":            "mute unmute toggle mute silence audio sound",
    "get_volume":             "current volume level what is volume how loud",
    "media_control":          "play pause resume stop music song track next previous skip go back rewind media spotify youtube vlc",
    "get_battery_status":     "battery percentage charge charging remaining time power level how much battery left",
    "system_info":            "computer specs hardware cpu processor ram memory operating system version info",
    "system_health":          "cpu usage ram memory usage disk usage system performance health status live",
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
}

# Tools that should never be routed by the semantic classifier alone
# (require explicit phrasing or confirmation — too dangerous to guess)
_SAFE_GUARD_TOOLS = {
    "shutdown_system", "restart_system", "delete_file",
    "send_email", "clear_temp_files", "empty_recycle_bin",
    "disable_startup_app",
}


class IntentRouter:

    def __init__(self) -> None:
        self._cache: dict[str, RouteResult] = {}
        self._cache_lock = threading.Lock()
        self._rules: list[tuple[re.Pattern, str, Callable]] = []
        self._model = None
        self._embeddings: dict[str, object] = {}
        self._np = None
        self._classifier_ready = False
        self._build_rules()
        threading.Thread(target=self._load_classifier, daemon=True, name="intent-classifier").start()

    # ────────────────────────────────────────────────────────────────────────
    # Tier 2 — Regex rules
    # ────────────────────────────────────────────────────────────────────────

    def _build_rules(self) -> None:
        def add(pattern: str, tool: str, fn: Callable = lambda m: {}):
            self._rules.append((re.compile(pattern, re.IGNORECASE), tool, fn))

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
        add(r'\b(?:mute|unmute)\b', "mute_unmute",
            lambda m: {"action": m.group(0).lower().strip()})
        add(r'\btoggle\s+(?:the\s+)?mute\b', "mute_unmute", lambda m: {"action": "toggle"})

        # ── Media controls ───────────────────────────────────────────────────
        add(r'\b(?:play|resume)\s*(?:music|song|audio|video|it|that)?\b',
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

        # ── Brightness ──────────────────────────────────────────────────────
        add(r'\b(?:set|put|change)\s+brightness\s+(?:to\s+)?(\d+)',
            "brightness_control", lambda m: {"action": "set", "level": int(m.group(1))})
        add(r'\bbrightness\s+(?:up|higher|increase|raise|brighter)',
            "brightness_control", lambda m: {"action": "increase", "step": 10})
        add(r'\bbrightness\s+(?:down|lower|decrease|dim|darker)',
            "brightness_control", lambda m: {"action": "decrease", "step": 10})

        # ── Battery ─────────────────────────────────────────────────────────
        add(r'\b(?:battery|how\s+much\s+(?:battery|charge|power))\b', "get_battery_status")
        add(r'\b(?:is\s+(?:it|my\s+(?:laptop|pc))\s+charging|charging\s+status)\b', "get_battery_status")

        # ── System info / health ─────────────────────────────────────────────
        add(r'\b(?:system\s+(?:info|information|specs?|details)|about\s+(?:my\s+)?(?:pc|computer|system))\b', "system_info")
        add(r'\b(?:cpu|processor|ram|memory|os|operating\s+system)\s+(?:info|specs?|details|version)\b', "system_info")
        add(r'\b(?:system|pc)\s+(?:health|status|performance)\b', "system_health")
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
        add(r'\bwhat.?s\s+(?:on|showing\s+on)\s+(?:my\s+)?screen\b|\bread\s+(?:the\s+)?screen\b', "read_screen")

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

        # ── Open / close application ──────────────────────────────────────────
        add(r'\b(?:open|launch|start|run|fire\s+up|pull\s+up)\s+(.+)',
            "open_application",
            lambda m: {"app_name": m.group(1).strip().rstrip(".,!?")})
        add(r'\b(?:close|quit|exit|kill)\s+(?!window|tab)(.+)',
            "kill_app",
            lambda m: {"app_name": m.group(1).strip().rstrip(".,!?")})

        # ── Open file ────────────────────────────────────────────────────────
        add(r'\b(?:open|show)\s+(?:the\s+)?(?:file|document|pdf)\s+(.+)',
            "open_file",
            lambda m: {"path": m.group(1).strip().rstrip(".,!?")})

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
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            import numpy as np
            import torch as _torch
            _device = "cuda" if _torch.cuda.is_available() else "cpu"
            logger.info("[IntentRouter] Loading sentence-transformers on %s…", _device)
            model = SentenceTransformer("all-MiniLM-L6-v2", device=_device)
            names = list(_TOOL_DESCS.keys())
            embs  = model.encode(list(_TOOL_DESCS.values()), show_progress_bar=False, batch_size=64)
            self._model = model
            self._embeddings = dict(zip(names, embs))
            self._np = np
            self._classifier_ready = True
            logger.info("[IntentRouter] Semantic classifier ready (%d tools embedded)", len(names))
        except ImportError:
            logger.warning("[IntentRouter] sentence-transformers not installed — Tier 3 disabled. "
                           "Run: pip install sentence-transformers")
        except Exception:
            logger.exception("[IntentRouter] Failed to load classifier")

    def _semantic_route(self, text: str) -> RouteResult:
        if not self._classifier_ready:
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
        key = text.lower().strip()

        # Tier 1 — exact cache
        with self._cache_lock:
            if key in self._cache:
                c = self._cache[key]
                return RouteResult(c.tool_name, c.params, 1, 1.0)

        # Tier 2 — regex
        for pattern, tool, params_fn in self._rules:
            m = pattern.search(text)
            if m:
                try:
                    params = params_fn(m)
                except Exception:
                    params = {}
                result = RouteResult(tool, params, 2, 1.0)
                with self._cache_lock:
                    self._cache[key] = result
                return result

        # Tier 3 — semantic classifier
        result = self._semantic_route(text)
        if result.tool_name:
            return result

        # Tier 4 — no match
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
