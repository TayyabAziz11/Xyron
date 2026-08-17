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

import asyncio
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

# Chrome address-bar (omnibox) URL read via UI Automation — the working
# fallback for when CDP is unreachable (see _get_browser_url_cdp's docstring
# and _get_browser_url_uia below: CDP requires Chrome to have been launched
# with --remote-debugging-port, which a normal everyday Chrome session never
# is, and there is no way to retroactively attach). Chrome's address bar
# AutomationId is an unstable per-build numeric value (e.g. "view_1012"),
# but its ClassName is the stable "OmniboxViewViews" across Chrome versions
# — verified live. Runs against the first Chrome process with a real
# top-level window, independent of which window currently has focus, since
# GetForegroundWindow() from window_context already establishes that Chrome
# IS the foreground app before this is ever called.
_PS_CHROME_OMNIBOX_CMD = (
    "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes; "
    "$procs = Get-Process -Name chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }; "
    "if (-not $procs) { Write-Output 'URL:' } else { "
    "$h = $procs[0].MainWindowHandle; "
    "$root=[System.Windows.Automation.AutomationElement]::FromHandle($h); "
    "$cond=New-Object System.Windows.Automation.PropertyCondition("
    "[System.Windows.Automation.AutomationElement]::ClassNameProperty,'OmniboxViewViews'); "
    "$addr=$root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$cond); "
    "if ($addr) { "
    "$vp=$addr.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); "
    "Write-Output ('URL:' + $vp.Current.Value) "
    "} else { Write-Output 'URL:' } "
    "}"
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

# GitHub repo title pattern ("owner/repo: description") — GitHub sets this
# as the literal <title> on repo pages. Used as a fallback when the CDP tab
# URL is unavailable (e.g. the bridge — see cdp_config.py — isn't reachable,
# which happens whenever Chrome wasn't launched with the debug port), since
# without a URL classify_github_page() has nothing to work with and
# describe() would otherwise fall all the way to a bare title echo. Less
# certain than the URL-confirmed path, so phrasing stays hedged ("It looks
# like...") rather than the flat "You're looking at your X repository."
_TITLE_GITHUB_REPO_RE = re.compile(
    r'^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/'
    r'(?P<repo>[A-Za-z0-9._-]+):\s*(?P<desc>.+)$'
)

# Trailing " - <something>" / " | <something>" boilerplate — Windows and
# browser titles routinely tack their own app/site name onto the end
# ("readme.txt - Notepad", "Some Item - Google Chrome"). Once we're already
# saying "in <app>" separately, repeating it verbatim reads redundant.
_TITLE_TRAILING_SEGMENT_RE = re.compile(r"\s*[-|—]\s*([^-|—]{1,50})$")


def _clean_spoken_title(title: str, app_name: str = "", max_len: int = 70) -> str:
    """Turn a raw window/tab title into something worth reading aloud.

    Two problems with reading a raw title verbatim (Part 9 follow-up — the
    generic fallback branches were still doing this even after GitHub/
    YouTube/etc. got dedicated composers): (1) it usually repeats the app
    or site name a second time right after we've already said "in <app>",
    and (2) real-world titles run long ("TayyabAziz11/Xyron: Voice-driven
    AI OS operator - Whisper STT, Brain V2, operator mode, Electron +
    FastAPI" is 87 characters) — reading the whole thing aloud is exactly
    the "just reads what's on screen" experience this function exists to
    avoid. Strip a trailing segment that's just the app/site name repeated,
    then cap length at a clean word boundary.
    """
    t = (title or "").strip()
    if not t:
        return t
    m = _TITLE_TRAILING_SEGMENT_RE.search(t)
    if m:
        tail = m.group(1).strip()
        if app_name and (tail.lower() in app_name.lower() or app_name.lower() in tail.lower()):
            t = t[:m.start()].strip()
    if len(t) > max_len:
        head = t[:max_len].rsplit(" ", 1)[0]
        t = (head or t[:max_len]).strip() + "…"
    return t

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

    # Product / shopping page perception — populated from World State's
    # browser_perception schema.org extraction (name/brand/price/currency/
    # rating/review_count/availability/seller). Only trusted when
    # page_type == "shopping" (see describe()) since world_state doesn't
    # clear current_product on navigation away from a product page.
    product:       Optional[dict] = None

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

        if self.is_browser and self.page_type == "shopping":
            desc = _describe_shopping(self)
            _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r page_type=shopping desc=%r",
                     self.active_app, desc[:120])
            return desc

        if self.is_browser and self.page_type and self.page_type != "unknown":
            desc = _describe_by_page_type(self.page_type, self.browser_title, self.browser_url)
            if desc:
                _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r page_type=%s desc=%r",
                         self.active_app, self.page_type, desc[:120])
                return desc

        if self.is_browser and not self.repository:
            m = _TITLE_GITHUB_REPO_RE.match(self.browser_title or "")
            if m:
                owner, repo, desc = m.group("owner"), m.group("repo"), m.group("desc").strip()
                sentence = f"It looks like you're viewing the {repo} repository by {owner} on GitHub."
                if desc:
                    sentence += f" It's described as: {desc}."
                _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r title_github_fallback=%s/%s desc=%r",
                         self.active_app, owner, repo, sentence[:120])
                return sentence

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
                clean = _clean_spoken_title(self.browser_title, self.active_app)
                site = _site_label(self.browser_url, "")
                desc = (f"You're looking at {clean} on {site}." if site
                        else f"You're looking at {clean} in {self.active_app}.")
                _dl.info("[SCREEN_CONTEXT_DESCRIPTION] app=%r title=%r desc=%r",
                         self.active_app, self.browser_title[:60], desc[:120])
                return desc
            return f"{self.active_app} is open."

        if self.is_explorer:
            if self.explorer_path:
                folder = self.explorer_path.rstrip("\\/").split("\\")[-1].split("/")[-1]
                return f"You're in File Explorer, currently in the {folder} folder."
            return "File Explorer is open."

        if self.window_title and self.window_title != self.active_app:
            clean = _clean_spoken_title(self.window_title, self.active_app)
            desc = f"You're using {self.active_app}, on {clean}."
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
        if self.is_browser and self.page_type == "shopping" and self.product:
            return f"{self.active_app}, viewing {self.product.get('name') or 'a product'}"
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


# ── Product / page-type description composers ─────────────────────────────
# Same "structured data, never a raw window-title echo" approach as
# _describe_repository above, extended to the other page types Perception
# Engine's browser_perception.classify_page_type() already classifies but
# describe() used to ignore entirely.

_YOUTUBE_TITLE_SUFFIX_RE = re.compile(r'\s*-\s*YouTube\s*$', re.IGNORECASE)
_GOOGLE_SEARCH_TITLE_SUFFIX_RE = re.compile(r'\s*-\s*Google\s+Search\s*$', re.IGNORECASE)

_PAGE_TYPE_PHRASES: dict[str, str] = {
    "chatgpt": "You have ChatGPT open.",
    # Deliberately no page content/title read out for email or banking —
    # privacy-sensitive page types, unlike the others here.
    "email":   "You're in your email inbox.",
    "banking": "You're on a banking site.",
}


def _extract_search_query(url: str, title: str) -> str:
    """Prefer the real ?q= query param (exact); fall back to stripping the
    ' - Google Search' suffix off the window title."""
    if url:
        try:
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(url).query).get("q")
            if q and q[0]:
                return q[0]
        except Exception:
            pass
    m = _GOOGLE_SEARCH_TITLE_SUFFIX_RE.search(title or "")
    return title[:m.start()].strip() if m else ""


def _site_label(url: str, fallback: str) -> str:
    """Bare hostname (no scheme/www) for a fallback sentence when no richer
    structured data is available — never leak the full URL/path into TTS."""
    if url:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower()
            if host:
                return host[4:] if host.startswith("www.") else host
        except Exception:
            pass
    return fallback


def _describe_product(product: dict) -> str:
    """Compose a natural sentence from a browser_perception schema.org
    Product extraction. Always ends with the compare/cheaper offer so a
    later bare 'yes' has something concrete to confirm (see
    context_stack.update_from_screen's 'offer' metadata)."""
    name = product.get("name") or "this product"
    bits = []
    if product.get("brand"):
        bits.append(str(product["brand"]))
    price = product.get("price")
    currency = (product.get("currency") or "").strip()
    if price:
        bits.append(f"{currency} {price}".strip())
    sentence = f"You're looking at {name}"
    if bits:
        sentence += " — " + ", ".join(bits)
    rating = product.get("rating")
    if rating:
        sentence += f", rated {rating}"
        review_count = product.get("review_count")
        if review_count:
            sentence += f" ({review_count} reviews)"
    sentence += ". Want me to compare it or find something cheaper?"
    return sentence


def _describe_shopping(snap: "ScreenSnapshot") -> str:
    """product is only trusted when page_type == 'shopping' (see caller) —
    world_state doesn't clear current_product on navigating away from a
    product page, so this guards against describing a stale product."""
    if snap.product:
        return _describe_product(snap.product)
    site = _site_label(snap.browser_url, snap.active_app)
    return (f"You appear to be shopping for something on {site}."
            if site else "You appear to be on a shopping site.")


def _describe_by_page_type(page_type: str, title: str, url: str) -> str:
    """Returns "" for page types with no dedicated phrasing yet — caller
    falls back to the generic browser branch in that case."""
    if page_type == "youtube":
        video = _YOUTUBE_TITLE_SUFFIX_RE.sub("", title or "").strip()
        return f"You're watching {video!r} on YouTube." if video else "You have YouTube open."
    if page_type == "google_search":
        query = _extract_search_query(url, title)
        return f"You're searching for {query!r}." if query else "You're on a search results page."
    if page_type == "documentation":
        return f"You're reading documentation: {title!r}." if title else "You have a documentation page open."
    if page_type == "news":
        return f"You're reading an article: {title!r}." if title else "You have a news article open."
    if page_type == "developer_tools":
        return (f"You're viewing a local development page: {title!r}." if title
                else "You're viewing a local development server.")
    return _PAGE_TYPE_PHRASES.get(page_type, "")


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

        # Step 4: Browser URL. UIA (address-bar read) tried first — fast
        # (~30ms warm) and works against a normal, already-running Chrome
        # window. CDP is the fallback for environments where the bridge in
        # cdp_config.py actually is reachable (it never attaches to a
        # normal everyday Chrome session, which is never launched with
        # --remote-debugging-port — see _get_browser_url_cdp's docstring).
        if snap.is_browser and not snap.browser_url:
            snap.browser_url = self._get_browser_url_uia()
        if snap.is_browser and not snap.browser_url:
            snap.browser_url = self._get_browser_url_cdp()

        # Step 4b: classify the URL we just got directly — classify_github_page
        # and classify_page_type are pure URL/title parsing (no Playwright, no
        # page.evaluate needed), so this works for ANY tab CDP can see, not
        # only ones Xyron's own agent opened. Previously the only source of
        # page_type/repository was world_state's browser_perception, which is
        # gated behind Xyron's own agent-managed browser connection — so a tab
        # you opened yourself always fell through to the bare title echo even
        # once the URL was reachable. This is the fallback that actually uses
        # it: real repo owner/branch/path for GitHub with zero extra I/O, and
        # at least a page_type guess (shopping/docs/video/search/etc.) for
        # everything else, before world_state gets a chance to enrich further.
        if snap.is_browser and snap.browser_url:
            try:
                from api.services.perception.browser_perception import (
                    classify_github_page as _classify_gh,
                    classify_page_type as _classify_pt,
                )
                repo = _classify_gh(snap.browser_url)
                if repo:
                    snap.repository = repo
                    snap.page_type = "repository"
                else:
                    pt = _classify_pt(snap.browser_url, snap.browser_title)
                    if pt and pt != "unknown":
                        snap.page_type = pt
            except Exception as exc:
                logger.debug("[SCREEN_AGENT] local URL classification failed: %s", exc)

        # Step 4c: on a shopping page with no schema.org data yet (the
        # common case — full product name/price/rating needs a live page
        # scrape via browser_workspace, a *different* Chrome instance than
        # the one being read here), record at least the tab title as a
        # name guess. Without this, describe() falls back to the generic
        # "you appear to be shopping" sentence AND — more importantly —
        # context_stack never gets a "product" entity, so a follow-up
        # ("find me something cheaper") has nothing to search for and
        # replies "I don't have a product in context." Step 5 below
        # overrides this with real schema.org data when world_state has it.
        if snap.is_browser and snap.page_type == "shopping" and not snap.product and snap.browser_title:
            snap.product = {"name": snap.browser_title}

        # Step 5 (Part 8/9): let Perception Engine's world_state data (richer —
        # has Playwright schema.org product extraction, etc.) override/augment
        # the local classification above when Xyron's own agent-managed
        # browser is the one connected.
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
            product = ctx.get("current_product")
            if product:
                snap.product = product
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
            ok, out = _pss.run(_PS_EXPLORER_CMD, timeout=6)
            if ok and out.strip():
                path = out.strip()
                logger.info("[SCREEN_AGENT_EXPLORER] path=%r", path)
                return path
        except Exception as exc:
            logger.debug("[SCREEN_AGENT] explorer path error: %s", exc)
        return ""

    def _get_browser_url_uia(self) -> str:
        """
        Read the current tab URL directly out of Chrome's address bar via
        Windows UI Automation, over the same persistent ps_session used
        everywhere else in this file. Works against the user's own,
        normally-launched Chrome — unlike CDP, which requires Chrome to
        have been started with --remote-debugging-port (see
        _get_browser_url_cdp's docstring for why that's unreachable for a
        normal Chrome session). Verified live: ~330ms cold (UIA assembly
        load), ~30ms warm.

        Chrome's omnibox shows a display-simplified URL (scheme and
        sometimes "www." stripped) — normalized here with a scheme prefix
        so downstream urlparse()-based classification (classify_github_page
        / classify_page_type) gets a real netloc instead of parsing the
        whole string as a path.
        """
        try:
            from api.services.ps_session import ps_session as _pss
            ok, out = _pss.run(_PS_CHROME_OMNIBOX_CMD, timeout=6)
            if not ok:
                return ""
            out = (out or "").strip()
            if not out.startswith("URL:"):
                return ""
            url = out[len("URL:"):].strip()
            if not url:
                return ""
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
                url = "https://" + url
            logger.debug("[SCREEN_AGENT_UIA] url=%r", url[:80])
            return url
        except Exception as exc:
            logger.debug("[SCREEN_AGENT_UIA] unavailable: %s", exc)
        return ""

    def _get_browser_url_cdp(self) -> str:
        """
        Try to get the active tab URL from Chrome DevTools Protocol.

        This backend runs inside WSL2; Chrome runs on the Windows host. A
        plain 127.0.0.1:9222 lookup was silently failing on every call —
        127.0.0.1 from inside WSL2 is WSL2's own loopback, never Windows'.
        cdp_config.py exists specifically to resolve the real WSL2->Windows
        bridge address (gateway IP + forwarded bridge port, since Chrome's
        own port and the bridge port can't share a number — see that
        module's docstring); browser_workspace.py already uses it correctly
        for agent-driven browsing, this just needed to do the same instead
        of a stale hardcoded 127.0.0.1:9222 that could never have worked
        cross-VM. This is why every screen-query answer degraded to a bare
        window-title echo instead of the real URL-based description.
        """
        try:
            from api.services.cdp_config import get_config as _get_cdp_config
            import urllib.request
            import json as _json
            _endpoint = _get_cdp_config().endpoint
            with urllib.request.urlopen(f"{_endpoint}/json", timeout=0.5) as resp:
                tabs = _json.loads(resp.read())
                for tab in tabs:
                    if tab.get("type") == "page":
                        url = tab.get("url", "")
                        if url and not url.startswith("chrome://"):
                            logger.debug("[SCREEN_AGENT_CDP] url=%r via=%s", url[:80], _endpoint)
                            return url
        except Exception as exc:
            logger.debug("[SCREEN_AGENT_CDP] unavailable: %s", exc)
        return ""


# Module-level singleton
screen_context_agent = ScreenContextAgent()


# ── Vision-enriched description (real screen understanding) ──────────────────
# Merges the fast, structured title/URL-based description above with what a
# vision model (perception/vision_perception.py, reused not reinvented)
# actually reports seeing on screen — the part describe() alone can never
# provide since it never looks at pixels.

_MAX_VISION_COMPOSED_LEN = 320  # keep the merged answer TTS-reasonable


def compose_with_vision(structured_desc: str, vision_text: str) -> str:
    """Append real visual detail to the structured description. Structured
    description stays first (reliable, always available); vision_text is
    never fabricated — it's exactly what the vision model reported, or this
    returns structured_desc unchanged if vision_text is empty."""
    vision_text = (vision_text or "").strip()
    if not vision_text:
        return structured_desc
    combined = f"{structured_desc.rstrip()} {vision_text}"
    if len(combined) > _MAX_VISION_COMPOSED_LEN:
        head = combined[:_MAX_VISION_COMPOSED_LEN].rsplit(" ", 1)[0]
        combined = (head or combined[:_MAX_VISION_COMPOSED_LEN]).strip() + "…"
    return combined


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


# ── Product follow-ups ────────────────────────────────────────────────────────
# Mirrors the repository follow-ups above exactly: reuses the "product"
# entity ContextStack already tracks from a prior screen query
# (update_from_screen), and — for "cheaper"/"compare" — actually searches
# and reads real prices back via BrowserReader rather than just opening a
# tab and saying nothing about what's on it. Runs through the normal async
# tool-call path (like handle_repository_followup), not the <200ms fast
# tier, since a live search + a few page loads genuinely takes a few
# seconds.

_PRODUCT_FOLLOWUP_ACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bcompare\s+(?:it|this|prices?)\b', re.I), "compare"),
    (re.compile(r'\bfind\s+(?:me\s+)?(?:something\s+)?cheaper\b', re.I), "cheaper"),
    (re.compile(r'\b(?:is\s+there|find\s+me?)\s+a?\s*cheaper\s+(?:one|option|alternative)\b', re.I), "cheaper"),
    (re.compile(r'\bis\s+this\s+the\s+best\s+price\b', re.I), "cheaper"),
    (re.compile(r'\bcheaper\s+(?:option|alternative|price)\b', re.I), "cheaper"),
]

# How many search-result candidates to open concurrently, and how many
# priced results are "enough" to report back (checked all at once via
# asyncio.gather in _search_prices, not sequentially — see its docstring).
_PRODUCT_SEARCH_MAX_CANDIDATES = 4
_PRODUCT_SEARCH_ENOUGH_PRICES  = 2


def match_product_followup(text: str) -> Optional[str]:
    """Return the follow-up action ('compare'/'cheaper') this utterance
    requests, or None."""
    for pattern, action in _PRODUCT_FOLLOWUP_ACTIONS:
        if pattern.search(text):
            return action
    return None


async def _check_price_candidate(url: str, title: str) -> Optional[dict]:
    """Open one candidate result in its own tab and read its price — a
    single unit of work run concurrently (not sequentially) across
    candidates by _search_prices below. Each call goes through
    browser_workspace.new_tab_if_approved, the same approval choke point
    the single-candidate version always used; it's the sanctioned way to
    open additional tabs (documented in browser_workspace.py) and is safe
    to call multiple times concurrently — each call gets its own Page."""
    try:
        from api.agents.browser_agent.browser_workspace import browser_workspace
        page = await browser_workspace.new_tab_if_approved(
            url, approved=True, reason="product_price_comparison_candidate",
        )
        if page is None:
            return None
        try:
            from api.agents.browser_agent.browser_reader import BrowserReader
            price = await BrowserReader().extract_price(page)
            if price:
                return {"title": (title or url)[:60], "price": price}
            return None
        finally:
            try:
                await page.close()
            except Exception:
                pass
    except Exception:
        return None


async def _search_prices(product_name: str) -> list[dict]:
    """Open a NEW tab (never navigates the user's current product tab away
    — unlike repository follow-ups, which intentionally redirect the same
    tab) via browser_workspace's approval choke point, search for the
    product, and read real prices off the top results. The user's own
    follow-up command ("find me something cheaper") is the approval this
    gate exists for. Returns [] (never raises) if the browser isn't
    connected or nothing priced was found.

    Candidates are checked concurrently (asyncio.gather), not one at a
    time — the previous sequential version could take up to
    MAX_CANDIDATES x NAV_TIMEOUT (4 x 8s = 32s) in the worst case; running
    them at once bounds the wait to roughly the single slowest candidate
    instead, and in practice most complete in a few seconds."""
    try:
        from api.agents.browser_agent.browser_workspace import browser_workspace
        if not browser_workspace.is_healthy:
            return []

        import urllib.parse
        search_url = f"https://www.google.com/search?q={urllib.parse.quote(product_name + ' price')}"
        search_page = await browser_workspace.new_tab_if_approved(
            search_url, approved=True, reason="product_price_comparison",
        )
        if search_page is None:
            return []

        try:
            from api.agents.browser_agent.browser_reader import BrowserReader
            results = await BrowserReader().extract_search_results(search_page)
        finally:
            try:
                await search_page.close()
            except Exception:
                pass

        candidate_urls = [
            (r.get("url", ""), r.get("title", ""))
            for r in results[:_PRODUCT_SEARCH_MAX_CANDIDATES]
            if r.get("url", "").startswith("http")
        ]
        if not candidate_urls:
            return []

        results_raw = await asyncio.gather(
            *(_check_price_candidate(url, title) for url, title in candidate_urls),
            return_exceptions=True,
        )
        candidates = [r for r in results_raw if isinstance(r, dict)]
        return candidates[:_PRODUCT_SEARCH_ENOUGH_PRICES]
    except Exception as exc:
        logger.debug("[PRODUCT_FOLLOWUP_SEARCH] failed: %s", exc)
        return []


async def handle_product_followup(action: str) -> str:
    """
    Execute a product follow-up using the product entity ContextStack
    already tracked from the last screen query — a real search with real
    extracted prices, never a fabricated "sure, I'll look into it".
    """
    try:
        from api.services.context_stack import context_stack
        entity = context_stack.get_last("product")
    except Exception:
        entity = None

    if entity is None:
        return "I don't have a product in context — open one first."

    product = entity.metadata or {}
    name = product.get("name") or entity.display or ""
    if not name:
        return "I don't have enough information about that product to look into it."

    candidates = await _search_prices(name)
    if not candidates:
        return f"I searched but couldn't find pricing for {name} elsewhere — you may want to check yourself."

    lines = [f"{c['title']} for {c['price']}" for c in candidates]
    spoken = "Here's what I found: " + "; ".join(lines) + "."

    current_price = product.get("price")
    if current_price:
        currency = (product.get("currency") or "").strip()
        price_str = f"{currency} {current_price}".strip()
        spoken += f" You're currently looking at it for {price_str}."

    return spoken


# ── Bare "yes" confirming a screen-agent offer ────────────────────────────────
# Generalizes CONTINUE_INSTALL_WORDS-style bare confirmation (already used
# for Microsoft Store installs in follow_up_resolver.py) to whatever the
# last screen query actually offered — "want me to compare it or find
# something cheaper?" / GitHub's implicit "I can open the README..." — so a
# plain "yes"/"sure" resolves correctly instead of only working for store
# installs.

_SCREEN_OFFER_MAX_AGE_S = 60.0


async def handle_screen_offer_confirmation() -> Optional[str]:
    """Returns a spoken response if the most recent ContextStack entity is
    a fresh screen-agent product/repository with a pending offer, else None
    (caller falls through to normal routing — a bare "yes" with nothing
    pending should never be swallowed here)."""
    try:
        from api.services.context_stack import context_stack
        entity = context_stack.peek()
    except Exception:
        return None

    if not entity or entity.source != "screen_agent":
        return None
    offer = (entity.metadata or {}).get("offer")
    if not offer:
        return None
    if time.time() - entity.pushed_at > _SCREEN_OFFER_MAX_AGE_S:
        return None

    if entity.type == "product":
        return await handle_product_followup(offer[0])
    if entity.type == "repository":
        return await handle_repository_followup(offer[0])
    return None


# ── Generic descriptive follow-ups ("what is this", "tell me more") ─────────
# Distinct from the repository/product action-verb follow-ups above ("review
# it", "compare it") — this catches plain descriptive questions asked right
# after a screen query, for ANY entity type screen_agent can push (window/
# store_app/app/folder, not just repository/product). Without this, a
# generic "what is this? tell me more about it" fell through to the normal
# intent router, which had no ContextStack entity to resolve it against and
# misrouted it to a browser research agent that searched the literal
# follow-up text ("this? can you tell me more about it?" — 0 results).

_GENERIC_FOLLOWUP_RE = re.compile(
    r'\b(?:'
    r'what(?:\'s|\s+is)\s+(?:this|that)\b'
    r'|tell\s+me\s+more(?:\s+about\s+(?:it|this|that))?\b'
    r'|explain\s+(?:this|that|it)\b'
    r'|(?:can\s+you\s+)?elaborate(?:\s+on\s+(?:it|this|that))?\b'
    r')',
    re.IGNORECASE,
)


def match_generic_followup(text: str) -> bool:
    """True if *text* is a generic descriptive follow-up ("what is this",
    "tell me more about it", "explain this") that should resolve against
    the last screen-agent ContextStack entity rather than being routed as
    a fresh command/search."""
    return bool(_GENERIC_FOLLOWUP_RE.search(text))


async def handle_generic_followup() -> Optional[str]:
    """Answer a generic descriptive follow-up using the last screen-agent
    ContextStack entity. Returns None (caller falls through to normal
    routing) if there's nothing fresh enough to expand on."""
    try:
        from api.services.context_stack import context_stack
        entity = context_stack.peek()
    except Exception:
        entity = None

    if not entity or entity.source != "screen_agent":
        return None
    if time.time() - entity.pushed_at > _SCREEN_OFFER_MAX_AGE_S:
        return None

    if entity.type == "repository":
        return await handle_repository_followup("review")
    if entity.type == "product" and entity.metadata:
        return _describe_product(entity.metadata)

    # window / store_app / app / folder — no structured data to expand on;
    # the only way to say anything more is to actually look again, this
    # time vision-enriched (mirrors the Tier 0x screen-query path).
    try:
        snap = await asyncio.to_thread(screen_context_agent.get_fresh)
        desc = snap.describe()
    except Exception as exc:
        logger.debug("[GENERIC_FOLLOWUP] failed: %s", exc)
        return None

    # Vision enrichment is best-effort on top of the description above,
    # which is already valid and worth returning on its own. A timeout or
    # any other vision failure must only skip enrichment, not blank out
    # the whole answer — that's what previously turned a good "you're
    # looking at X" into a silent None, falling through to the intent
    # router and misrouting the turn to a browser research agent.
    try:
        openai_key = ""
        from api.config import settings as _gf_cfg
        if _gf_cfg.openai_api_key and _gf_cfg.openai_api_key.startswith("sk-"):
            openai_key = _gf_cfg.openai_api_key

        if openai_key:
            from api.services.perception import vision_perception as _vp
            vision_result = await asyncio.wait_for(
                asyncio.to_thread(_vp.maybe_capture, "generic_followup", openai_key),
                timeout=2.5,
            )
            if vision_result and vision_result.get("description"):
                desc = compose_with_vision(desc, vision_result["description"])
    except Exception as exc:
        logger.debug("[GENERIC_FOLLOWUP_VISION] skipped: %s", exc)

    return desc
