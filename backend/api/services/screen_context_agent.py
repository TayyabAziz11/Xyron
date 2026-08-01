"""
ScreenContextAgent — system-API-based screen awareness.

Captures active application context using:
  - window_context.py  (foreground window, proc_name, title)  — reused, not duplicated
  - PowerShell Shell.Application COM  (Explorer folder path)
  - Window title parsing  (browser title, Store page, VS Code project)
  - Chrome DevTools Protocol  (browser URL) — optional, requires --remote-debugging-port

Performance:
  <100ms when window_context cache is warm (30s TTL)
  <500ms cold (first call or cache expired)

Answers:
  "What am I looking at?"  → describe()
  "What app is open?"      → active_app
  "What project is open?"  → project_name

Log prefixes: [SCREEN_AGENT_QUERY] [SCREEN_AGENT_CACHED] [SCREEN_AGENT_DESCRIBE] [SCREEN_AGENT_MS]
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Hot cache: same snapshot is returned for calls within this window.
# window_context already caches for 30s; this avoids repeated title parsing.
_HOT_TTL = 0.10   # 100ms

# Explorer PS command — runs via persistent PS session
_PS_EXPLORER_CMD = (
    "$s=(New-Object -ComObject Shell.Application).Windows();"
    "$w=$s | Where-Object { $_.Name -eq 'File Explorer' } | Select-Object -First 1;"
    "if($w){ $w.Document.Folder.Self.Path }else{ '' }"
)

# Regexes for title parsing
_CHROME_RE      = re.compile(r'^(.+?)\s+-\s+Google Chrome\s*$', re.IGNORECASE)
_EDGE_RE        = re.compile(r'^(.+?)\s+-\s+Microsoft Edge\s*$', re.IGNORECASE)
_FIREFOX_RE     = re.compile(r'^(.+?)\s+-\s+Mozilla Firefox\s*$', re.IGNORECASE)
_STORE_RE       = re.compile(r'^(.+?)\s+-\s+Microsoft Store\s*$', re.IGNORECASE)
_VSCODE_RE      = re.compile(
    r'^(?P<file>[^-·]+?)\s+[-·]\s+(?P<project>[^-·]+?)\s+[-·]\s+Visual Studio Code',
    re.IGNORECASE,
)
_EXPLORER_RE    = re.compile(r'^(.+?)\s*$')   # title IS the folder name in Explorer

# Process names for well-known apps (lowercase, .exe stripped)
_PROC_TO_APP: dict[str, str] = {
    "chrome":           "Google Chrome",
    "msedge":           "Microsoft Edge",
    "firefox":          "Mozilla Firefox",
    "code":             "Visual Studio Code",
    "winstore.app":     "Microsoft Store",
    "microsoft.windowsstore": "Microsoft Store",
    "explorer":         "File Explorer",
    "notepad":          "Notepad",
    "calc":             "Calculator",
    "calculatorapp":    "Calculator",
    "mspaint":          "Microsoft Paint",
    "wordpad":          "WordPad",
    "spotify":          "Spotify",
    "discord":          "Discord",
    "slack":            "Slack",
    "teams":            "Microsoft Teams",
    "winword":          "Microsoft Word",
    "excel":            "Microsoft Excel",
    "powerpnt":         "Microsoft PowerPoint",
    "outlook":          "Microsoft Outlook",
    "onenote":          "Microsoft OneNote",
    "powershell":       "PowerShell",
    "windowsterminal":  "Windows Terminal",
    "cmd":              "Command Prompt",
    "pwsh":             "PowerShell",
}

# CDP port to try for browser URL (optional, requires chrome --remote-debugging-port=9222)
_CDP_PORT = 9222


@dataclass
class ScreenSnapshot:
    active_app:    str   = ""   # human-readable app name
    window_title:  str   = ""   # full foreground window title
    proc_name:     str   = ""   # process name (lowercase, no .exe)

    # Browser
    browser_url:   str   = ""   # URL if browser active (CDP only)
    browser_title: str   = ""   # page title from window title
    is_browser:    bool  = False

    # Explorer
    explorer_path: str   = ""   # current folder path
    is_explorer:   bool  = False

    # Microsoft Store
    store_page:    str   = ""   # app name being viewed
    is_store:      bool  = False

    # VS Code
    project_name:  str   = ""   # project name from title
    open_file:     str   = ""   # file name from title
    is_vscode:     bool  = False

    # GitHub / structured page perception (Part 8/9) — populated from World
    # State's browser_perception output when available, richer than title
    # parsing alone (owner/name/branch/current_path/page_type/description).
    page_type:     str            = ""
    repository:    Optional[dict] = None

    captured_at:   float = field(default_factory=time.time)

    # ── Describe ──────────────────────────────────────────────────────────────

    def describe(self) -> str:
        """
        Return a human-readable, TTS-safe, natural-sounding description of
        what's on screen — built from structured perception (World State's
        GitHub/browser extraction when available), never from a list of raw
        window-title/OCR tokens. Uses hedged phrasing ("It looks like...",
        "You appear to be...") for anything inferred rather than directly
        confirmed by structured data (Part 9).
        """
        import logging as _lg
        _dl = _lg.getLogger(__name__)
        if not self.active_app:
            _dl.warning("[SCREEN_CONTEXT_DESCRIPTION] result='no_active_app' proc=%r title=%r",
                        self.proc_name, self.window_title[:60])
            return "I can't tell what's on your screen right now."

        if self.is_browser and self.repository:
            desc = _describe_repository(self.repository)
            _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r repository=%s desc=%r",
                     self.active_app, self.repository.get("name"), desc[:120])
            return desc

        if self.is_store:
            if self.store_page:
                return f"You're viewing {self.store_page} on the Microsoft Store."
            return "The Microsoft Store is open."

        if self.is_vscode:
            parts = []
            if self.project_name:
                parts.append(f"You have {self.project_name} open in Visual Studio Code")
            else:
                parts.append("Visual Studio Code is open")
            if self.open_file:
                parts[0] += f", editing {self.open_file}"
            return parts[0] + "."

        if self.is_browser:
            if self.browser_title:
                return f"You're currently viewing {self.browser_title!r} in {self.active_app}."
            return f"{self.active_app} is open."

        if self.is_explorer:
            if self.explorer_path:
                folder = self.explorer_path.rstrip("\\/").split("\\")[-1].split("/")[-1]
                return f"You're in File Explorer, currently in the {folder} folder."
            return "File Explorer is open."

        if self.window_title and self.window_title != self.active_app:
            desc = f"You have {self.active_app} open — {self.window_title}."
            _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r title=%r desc=%r",
                     self.active_app, self.window_title[:60], desc[:80])
            return desc

        desc = f"{self.active_app} is currently active."
        _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r desc=%r", self.active_app, desc[:80])
        return desc

    def short_answer(self) -> str:
        """Single-phrase answer for 'what app is open?' """
        if self.is_browser and self.repository:
            return f"GitHub, on the {self.repository['name']} repository"
        if self.is_store:
            return f"Microsoft Store" + (f", showing {self.store_page}" if self.store_page else "")
        if self.is_vscode:
            return f"Visual Studio Code" + (f" on {self.project_name}" if self.project_name else "")
        if self.is_browser:
            return self.active_app + (f" on {self.browser_title}" if self.browser_title else "")
        if self.is_explorer:
            folder = (self.explorer_path.rstrip("\\/").split("\\")[-1].split("/")[-1]
                      if self.explorer_path else "")
            return "File Explorer" + (f" in {folder}" if folder else "")
        return self.active_app or "Unknown"


# ── Screen description composer (Part 9) ─────────────────────────────────────
# GitHub structured page (Part 8/9) — one function, reused by describe() and
# by the "review it"/follow-up handling in voice_ws.py so the wording stays
# consistent everywhere a repository gets described.

_GITHUB_PAGE_TYPE_PHRASE: dict[str, str] = {
    "repository_home": " It looks like the main project repo — you're viewing its code and project files.",
    "issue":            " You appear to be looking at issues.",
    "pull_request":     " You appear to be reviewing a pull request.",
    "commit":           " You're looking at recent commits.",
    "actions":          " You're checking GitHub Actions.",
    "settings":         " You're in the repository settings.",
    "other":            "",
}


def _describe_repository(repo: dict) -> str:
    """Compose a natural sentence from a browser_perception GitHub extraction
    — never a raw list of window-title/OCR tokens (Part 9)."""
    name = repo.get("name") or "a"
    page_type = repo.get("page_type") or "repository_home"
    base = f"You're looking at your {name} repository on GitHub."

    if page_type == "file_view":
        path = repo.get("current_path")
        detail = f" You're viewing {path} in the codebase." if path else " You're browsing the repository files."
    else:
        detail = _GITHUB_PAGE_TYPE_PHRASE.get(page_type, "")

    desc = repo.get("description")
    if desc and page_type == "repository_home":
        detail += f" It's described as: {desc}."

    return (base + detail).strip()


class ScreenContextAgent:
    """
    Singleton that returns a ScreenSnapshot of the current Windows desktop context.

    Architecture:
      1. Hot cache (100ms): same object returned within _HOT_TTL seconds.
      2. window_context.get_active_window(): foreground window query with 30s cache.
      3. Title parsing: browser, store, vscode — pure Python, <1ms.
      4. Explorer path: PowerShell Shell.Application via ps_session — ~150ms uncached.
      5. CDP: Chrome URL — optional, skipped if port not open.

    All blocking I/O runs in calling thread. Use asyncio.to_thread() in async contexts.
    """

    def __init__(self) -> None:
        self._lock       = threading.Lock()
        self._last_snap: Optional[ScreenSnapshot] = None
        self._last_at:   float                    = 0.0

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self) -> ScreenSnapshot:
        """
        Return current screen snapshot.
        Hot-cached for _HOT_TTL seconds to serve repeated calls within a voice turn.
        Delegates window query to window_context (30s cache) — no redundant PS calls.
        """
        now = time.monotonic()
        with self._lock:
            if self._last_at > 0 and now - self._last_at < _HOT_TTL:
                logger.debug("[SCREEN_AGENT_CACHED] age_ms=%.0f", (now - self._last_at) * 1000)
                return self._last_snap  # type: ignore[return-value]

        t0 = time.monotonic()
        snap = self._query()
        ms = (time.monotonic() - t0) * 1000

        with self._lock:
            self._last_snap = snap
            self._last_at   = now

        logger.info(
            "[SCREEN_AGENT_QUERY] app=%r store=%s vscode=%s browser=%s explorer=%s ms=%.0f",
            snap.active_app, snap.is_store, snap.is_vscode, snap.is_browser, snap.is_explorer, ms,
        )
        return snap

    def get_fresh(self) -> ScreenSnapshot:
        """Force a fresh query, bypassing all caches (including window_context's 30s cache)."""
        try:
            from api.services.window_context import window_context as _wctx
            _wctx.invalidate()
        except Exception:
            pass
        with self._lock:
            self._last_at = 0.0
        return self.get()

    def invalidate(self) -> None:
        """Discard hot cache so next get() re-queries."""
        with self._lock:
            self._last_at = 0.0

    # ── Internal ──────────────────────────────────────────────────────────────

    def _query(self) -> ScreenSnapshot:
        t0 = time.monotonic()
        snap = ScreenSnapshot(captured_at=time.time())

        # Step 1: foreground window via window_context (PS bridge, 2s cache)
        # No invalidate here — get_fresh() already invalidated before calling _query().
        win: Optional[dict] = None
        try:
            from api.services.window_context import window_context as _wctx
            win = _wctx.get_active_window()
        except Exception as exc:
            logger.debug("[SCREEN_AGENT] window_context error: %s", exc)

        if win:
            snap.window_title = win.get("title", "") or ""
            proc_raw          = (win.get("proc_name") or "").lower().replace(".exe", "").strip()
            snap.proc_name    = proc_raw
            snap.active_app   = _PROC_TO_APP.get(proc_raw, proc_raw.capitalize()) if proc_raw else ""

            logger.info("[SCREEN_WINDOWS_ACTIVE_WINDOW] title=%r proc=%r pid=%s",
                        snap.window_title[:80], proc_raw, win.get("pid", "?"))
            logger.info("[SCREEN_ACTIVE_PROCESS] proc=%r app=%r", proc_raw, snap.active_app)
            logger.info("[SCREEN_ACTIVE_TITLE] title=%r", snap.window_title[:80])
        else:
            logger.warning("[SCREEN_WINDOWS_ACTIVE_WINDOW] no result from window_context (PS bridge may be cold)")

        # Step 2: classify from title + proc
        if snap.window_title:
            self._classify(snap)

        # Step 3: Explorer path (only if Explorer is in foreground — costs ~150ms PS call)
        if snap.is_explorer and not snap.explorer_path:
            snap.explorer_path = self._get_explorer_path()

        # Step 4: Browser URL via CDP (optional, non-blocking if port closed)
        if snap.is_browser and not snap.browser_url:
            snap.browser_url = self._get_browser_url_cdp()

        # Step 5 (Part 8/9): prefer Perception Engine's structured browser/
        # GitHub extraction over this module's own bare window-title parsing
        # — it already has the real URL via CDP/Playwright (no title-regex
        # guessing) and, for GitHub, owner/name/branch/current_path/
        # page_type. This is what stops "what's on my screen" from just
        # echoing the window title as the entire answer.
        if snap.is_browser:
            self._enrich_from_world_state(snap)

        logger.debug(
            "[SCREEN_AGENT_MS] total=%.0fms app=%r store_page=%r project=%r",
            (time.monotonic() - t0) * 1000, snap.active_app, snap.store_page, snap.project_name,
        )
        return snap

    def _enrich_from_world_state(self, snap: ScreenSnapshot) -> None:
        """Pull browser_perception's structured URL/page_type/repository data
        out of World State — read-only, never triggers a Chrome connection
        (World State's browser sensor is a no-op if Chrome isn't already
        connected, same safety guarantee as browser_perception itself)."""
        try:
            from api.services.world_state import world_state
            ctx = world_state.get_context(refresh=False)
            cb = ctx.get("current_browser")
            if cb and cb.get("url"):
                snap.browser_url = cb["url"] or snap.browser_url
                snap.page_type = cb.get("page_type") or snap.page_type
            repo = ctx.get("current_repository")
            if repo:
                snap.repository = repo
        except Exception as exc:
            logger.debug("[SCREEN_AGENT] world_state enrichment failed: %s", exc)

    def _classify(self, snap: ScreenSnapshot) -> None:
        """Parse window title to detect app type and extract structured info."""
        title = snap.window_title
        proc  = snap.proc_name

        # Microsoft Store
        if "microsoft.windowsstore" in proc or "winstore" in proc or "windowsstore" in proc:
            snap.is_store   = True
            snap.active_app = "Microsoft Store"
            m = _STORE_RE.match(title)
            if m:
                snap.store_page = m.group(1).strip()
            return

        # Microsoft Store by title (proc name varies)
        m = _STORE_RE.match(title)
        if m:
            snap.is_store   = True
            snap.active_app = "Microsoft Store"
            snap.store_page = m.group(1).strip()
            return

        # VS Code
        if proc == "code" or "visual studio code" in title.lower():
            snap.is_vscode  = True
            snap.active_app = "Visual Studio Code"
            m = _VSCODE_RE.match(title)
            if m:
                snap.open_file    = m.group("file").strip().lstrip("•").strip()
                snap.project_name = m.group("project").strip()
            return

        # Google Chrome
        m = _CHROME_RE.match(title)
        if m:
            snap.is_browser    = True
            snap.active_app    = "Google Chrome"
            snap.browser_title = m.group(1).strip()
            return

        # Microsoft Edge
        m = _EDGE_RE.match(title)
        if m:
            snap.is_browser    = True
            snap.active_app    = "Microsoft Edge"
            snap.browser_title = m.group(1).strip()
            return

        # Firefox
        m = _FIREFOX_RE.match(title)
        if m:
            snap.is_browser    = True
            snap.active_app    = "Mozilla Firefox"
            snap.browser_title = m.group(1).strip()
            return

        # File Explorer (proc = "explorer", title is folder name or path)
        if proc == "explorer":
            snap.is_explorer = True
            snap.active_app  = "File Explorer"
            return

    def _get_explorer_path(self) -> str:
        """
        Query Explorer's current folder path via PowerShell Shell.Application COM.
        Only called when Explorer is the foreground window.
        Returns path string or "" on failure.
        """
        try:
            from api.services.ps_session import ps_session as _pss
            if _pss.is_warm:
                ok, out = _pss.run(_PS_EXPLORER_CMD, timeout=6)
                if ok and out.strip():
                    path = out.strip()
                    logger.info("[SCREEN_AGENT_EXPLORER] path=%r", path)
                    return path
        except Exception as exc:
            logger.debug("[SCREEN_AGENT] explorer path error: %s", exc)
        return ""

    def _get_browser_url_cdp(self) -> str:
        """
        Try to get the active tab URL from Chrome DevTools Protocol.
        Requires Chrome to be running with --remote-debugging-port=9222.
        Silently returns "" if unavailable (<50ms timeout).
        """
        try:
            import urllib.request
            import json as _json
            with urllib.request.urlopen(
                f"http://127.0.0.1:{_CDP_PORT}/json", timeout=0.3
            ) as resp:
                tabs = _json.loads(resp.read())
                for tab in tabs:
                    if tab.get("type") == "page":
                        url = tab.get("url", "")
                        if url and not url.startswith("chrome://"):
                            logger.debug("[SCREEN_AGENT_CDP] url=%r", url[:80])
                            return url
        except Exception:
            pass
        return ""


# Module-level singleton
screen_context_agent = ScreenContextAgent()


# ── Repository follow-ups (Part 10) ───────────────────────────────────────────
# Reuses the repository entity ContextStack already tracks from a prior
# screen query (update_from_screen, above) — no separate GitHub memory
# system. Navigation actions reuse the existing browser tab via
# browser_workspace when Chrome is already connected (never launches it —
# same safety guarantee as browser_perception.py).

_REPO_FOLLOWUP_ACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\breview\s+(?:it|this|the\s+repo(?:sitory)?)\b', re.I), "review"),
    (re.compile(r'\bgive\s+me\s+a\s+review\b', re.I), "review"),
    (re.compile(r'\bopen\s+(?:the\s+)?read\s?me\b', re.I), "readme"),
    (re.compile(r'\b(?:show|check)\s+(?:me\s+)?(?:the\s+)?issues\b', re.I), "issues"),
    (re.compile(r'\bcheck\s+(?:the\s+)?latest\s+commit', re.I), "commits"),
    (re.compile(r'\bwhat\s+does\s+this\s+file\s+do\b', re.I), "explain_file"),
]


def match_repository_followup(text: str) -> Optional[str]:
    """Return the follow-up action this utterance requests, or None."""
    for pattern, action in _REPO_FOLLOWUP_ACTIONS:
        if pattern.search(text):
            return action
    return None


async def _navigate_existing_tab(url: str) -> bool:
    """Best-effort: navigate the already-open Chrome tab to *url*. Returns
    False (never raises) if Chrome isn't already connected — caller falls
    back to telling the user rather than claiming an action that didn't
    happen."""
    try:
        from api.agents.browser_agent.browser_workspace import browser_workspace
        if not browser_workspace.is_healthy:
            return False
        page = await browser_workspace.get_or_create_page()
        await page.goto(url)
        return True
    except Exception as exc:
        logger.debug("[SCREEN_AGENT_REPO_FOLLOWUP] navigate failed: %s", exc)
        return False


async def handle_repository_followup(action: str) -> str:
    """
    Execute a repository follow-up using the repo entity ContextStack
    already tracked from the last screen query. review/explain_file are
    answered only from data Perception Engine actually extracted (Part 8) —
    never a fabricated summary of content nobody read.
    """
    try:
        from api.services.context_stack import context_stack
        entity = context_stack.get_last("repository")
    except Exception:
        entity = None

    if entity is None:
        return "I don't have a repository in context — open one on GitHub first."

    repo = entity.metadata or {}
    owner, name, branch = repo.get("owner"), repo.get("name"), (repo.get("branch") or "main")

    if action == "review":
        parts = [f"Here's what I can tell you about {name or 'this repository'}."]
        if repo.get("description"):
            parts.append(f"It's described as: {repo['description']}.")
        if repo.get("page_type") == "file_view" and repo.get("current_path"):
            parts.append(f"You're currently looking at {repo['current_path']}.")
        parts.append("I can open the README, show issues, or check the latest commit for more.")
        return " ".join(parts)

    if action == "explain_file":
        path = repo.get("current_path")
        if not path:
            return "I don't see a specific file open right now — open one and ask again."
        return (f"You have {path} open, but I'd need to read its contents to explain "
                f"what it does — want me to open it?")

    if not owner or not name:
        return "I don't have enough information about that repository to do that."

    target_url = {
        "readme":  f"https://github.com/{owner}/{name}/blob/{branch}/README.md",
        "issues":  f"https://github.com/{owner}/{name}/issues",
        "commits": f"https://github.com/{owner}/{name}/commits/{branch}",
    }.get(action)
    if not target_url:
        return "I'm not sure how to do that yet."

    label = {"readme": "the README", "issues": "the issues page", "commits": "the latest commits"}[action]
    navigated = await _navigate_existing_tab(target_url)
    return f"Opened {label}." if navigated else f"I'll open {label} — {target_url}"
