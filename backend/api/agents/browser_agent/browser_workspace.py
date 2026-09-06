from __future__ import annotations

"""
BrowserWorkspace — one persistent, controllable browser for the whole
travel-consultant conversation (Phase 4.8 / 4.9 / 4.11.1).

Architectural history (documented, not assumed):
  1. `cmd.exe /c start chrome <url>` mirrored automation into the user's
     real Windows Chrome while a separate headless Playwright driver did
     the actual work — two divergent browsers, the visible one was never
     actually controllable.
  2. `connect_over_cdp("http://localhost:9222")` with a plain
     `--remote-debugging-port` failed reproducibly: `ECONNREFUSED` on
     localhost, `TimeoutError` on the WSL2 gateway IP even with
     `--remote-debugging-address=0.0.0.0`.
  3. Root cause found on retest (Phase 4.9): Chrome silently restricts
     the DevTools port to 127.0.0.1 regardless of
     `--remote-debugging-address` — a Chrome security hardening, not a
     WSL/firewall bug. WSL2 has no built-in loopback-forwarding for
     arbitrary Windows-side services in this environment. Fix: a
     Windows-side `netsh interface portproxy` bridge.
  4. Phase 4.11.1 root cause (confirmed live via Chrome's own stderr):
     the portproxy bridge and Chrome's DevTools listener were both
     configured on port 9222. Windows only allows one owner per port;
     portproxy (a persistent OS service) wins, and Chrome's own bind()
     fails and falls back to `[::1]:9222` (IPv6 loopback only) — a
     socket the IPv4 bridge can never reach. Every CDP connect attempt
     then failed with "socket hang up", and the code fell back to an
     uncontrolled, un-navigated Chromium/about:blank window — exactly
     the "blank Chrome" symptom this phase exists to eliminate.
     Fix: Chrome always binds its own local port (`cdp_config.CDP_CHROME_LOCAL_PORT`,
     127.0.0.1 only, no `--remote-debugging-address` override); the
     bridge listens externally on a *different*, centrally-configured
     port (`cdp_config.CDP_BRIDGE_PORT_PREFERRED`, normally 9223) and
     forwards to Chrome's local port. See `cdp_config.py` for every
     port/path value and `cdp_environment_doctor.py` for the diagnose +
     one-time-elevated-repair flow that detects and fixes a collision
     automatically instead of requiring a manual netsh command.

Control mode preference order, every time a page is requested:
  1. Reuse the existing, still-connected CDP session (`Browser.is_connected()`
     — cheaper and more accurate than trusting a possibly-stale Page object).
  2. Reconnect to an already-running, debug-enabled Windows Chrome (the
     dedicated Xyron profile, not the user's normal profile) via the bridge.
  3. If not running, launch it, then poll for the debug port to come up.
  4. If still unreachable, clear a stale Xyron-profile process and retry
     once (no elevation required for this step).
  5. If still unreachable: raise `CDPUnavailableError` — Phase 4.11.1
     removed the silent WSLg-Chromium/about:blank fallback for controlled
     browser tasks. The caller (browser_agent.py) is responsible for
     running the CDP Environment Doctor's repair flow (one Windows
     elevation prompt) and retrying, or honestly telling the user Chrome
     control could not be established.

Tab policy: one task = one workspace = one active tab, by default.
On a fresh connection, `_discover_and_reuse_tab()` prefers (in order): the
exact URL from the active `FlightSessionState`, an already-open Google
Flights tab, then the first available tab, then a new tab only if none
exist. A second tab beyond that requires either (a) the current site
cannot supply the needed information and the user explicitly approves
checking another source, or (b) it's a direct official-source lookup
already gated behind approval — enforced by requiring callers to pass
approved=True to open a second tab.

Required log tags: [WINDOWS_CHROME_LAUNCH] [WINDOWS_CHROME_DEBUG_ENDPOINT]
[WINDOWS_CHROME_CDP_CONNECTED] [WINDOWS_CHROME_CONTROL_READY]
[BROWSER_CONTROL_MODE] [BROWSER_WORKSPACE_CREATED] [BROWSER_WORKSPACE_REUSED]
[BROWSER_ACTIVE_PAGE] [BROWSER_TAB_COUNT] [BROWSER_STATE_SAVED]
[BROWSER_NEW_TAB_BLOCKED] [BROWSER_NEW_TAB_APPROVED] [BROWSER_TAB_REUSED]
[CDP_HEALTH_CHECK] [CDP_CONNECTING] [CDP_CONNECTED] [CDP_DISCONNECTED]
[CDP_RECONNECT_START] [CDP_RECONNECT_SUCCESS] [CDP_RECONNECT_FAILED]
[CDP_SELF_HEAL_START] [CDP_SELF_HEAL_SUCCESS] [CDP_SELF_HEAL_FAILED]
[CDP_TAB_DISCOVERY] [CDP_REUSED_TAB] [CDP_REUSED_FLIGHT_SESSION]
[CDP_NEW_TAB_CREATED] [BROWSER_FALLBACK_BLOCKED]
"""

import asyncio
import logging
import time
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from api.services import cdp_config

logger = logging.getLogger("api.agents.browser_agent.workspace")

CONTROL_MODE_CDP = "windows_chrome_cdp"
CONTROL_MODE_WSLG = "playwright_visible_chromium"  # retained for close()'s branch; no longer auto-selected


class CDPUnavailableError(RuntimeError):
    """Raised instead of silently degrading to an uncontrolled browser
    window (Phase 4.11.1, Part 7). Carries enough context for the caller
    to decide whether to run the Environment Doctor's repair flow."""

    def __init__(self, message: str, repair_recommended: bool = True) -> None:
        super().__init__(message)
        self.repair_recommended = repair_recommended


class BrowserWorkspace:
    """Singleton owner of the one persistent, controllable browser.

    Not closed when an individual agent task finishes — it outlives task
    boundaries so voice follow-ups in later turns keep operating on the
    same page. Only closed explicitly (idle timeout, session end); in
    windows_chrome_cdp mode, close() only drops the CDP connection and
    never terminates the user's actual Chrome process.
    """

    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._created_at: Optional[float] = None
        self._last_used_at: Optional[float] = None
        self._control_mode: str = CONTROL_MODE_CDP
        self._cdp_endpoint: str = ""
        self.last_tab_match: Optional[str] = None
        self._keepalive_task: Optional[asyncio.Task] = None

    @property
    def is_open(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    @property
    def is_healthy(self) -> bool:
        """CDP-transport-aware health check — a `Page` object can look
        open while its underlying CDP connection has already dropped;
        `Browser.is_connected()` reflects the actual transport state."""
        return (
            self.is_open
            and self._browser is not None
            and self._browser.is_connected()
        )

    @property
    def control_mode(self) -> str:
        return self._control_mode

    # ── Windows Chrome CDP path ──────────────────────────────────────────

    async def _try_connect_windows_chrome(self) -> bool:
        cfg = cdp_config.get_config()
        endpoint = cfg.endpoint
        self._cdp_endpoint = endpoint
        logger.info("[WINDOWS_CHROME_DEBUG_ENDPOINT] endpoint=%s", endpoint)
        logger.info("[CDP_CONNECTING] endpoint=%s", endpoint)
        try:
            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(endpoint, timeout=cfg.connect_timeout_ms)
            logger.info("[WINDOWS_CHROME_CDP_CONNECTED] endpoint=%s", endpoint)
            logger.info("[CDP_CONNECTED] endpoint=%s", endpoint)
            self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            self._page = await self._discover_and_reuse_tab()
            logger.info("[WINDOWS_CHROME_CONTROL_READY] endpoint=%s", endpoint)
            return True
        except Exception as exc:
            logger.warning("[WINDOWS_CHROME_CDP_CONNECT_FAILED] endpoint=%s error=%r", endpoint, str(exc)[:200])
            return False

    async def _discover_and_reuse_tab(self) -> Page:
        """Phase 4.11.1 Part 6 — on a fresh connection, find the tab worth
        continuing on instead of blindly grabbing pages[0] or opening a
        new one. Priority: exact FlightSessionState URL match > an
        already-open Google Flights tab > first available tab > new tab."""
        assert self._context is not None
        pages = self._context.pages
        logger.info("[CDP_TAB_DISCOVERY] count=%d", len(pages))

        if not pages:
            page = await self._context.new_page()
            logger.info("[CDP_NEW_TAB_CREATED] reason=no_existing_pages")
            self.last_tab_match = "new_tab"
            return page

        try:
            from api.agents.browser_agent.flight_session_state import get_active as _get_active_flight_session
            session = _get_active_flight_session()
        except Exception:
            session = None

        if session and session.current_page_url:
            for p in pages:
                if not p.is_closed() and p.url == session.current_page_url:
                    logger.info("[CDP_REUSED_FLIGHT_SESSION] url=%s", p.url)
                    logger.info("[CDP_REUSED_TAB] match=exact_session_url")
                    self.last_tab_match = "exact_session_url"
                    return p

        for p in pages:
            if not p.is_closed() and p.url.startswith("https://www.google.com/travel/flights"):
                logger.info("[CDP_REUSED_TAB] match=google_flights_page url=%s", p.url)
                self.last_tab_match = "google_flights_page"
                return p

        for p in pages:
            if not p.is_closed():
                logger.info("[CDP_REUSED_TAB] match=active_tab_fallback url=%s", p.url)
                self.last_tab_match = "active_tab_fallback"
                return p

        page = await self._context.new_page()
        logger.info("[CDP_NEW_TAB_CREATED] reason=all_existing_pages_closed")
        self.last_tab_match = "new_tab"
        return page

    async def _kill_stale_xyron_chrome(self) -> None:
        """Non-elevated recovery step: clears a leftover Xyron-profile
        Chrome process that may be holding the profile lock / old bridge
        port in a bad state. Only processes whose command line references
        the dedicated Xyron profile dir are targeted — the user's own
        personal Chrome windows are never touched. Requires no UAC prompt
        (Stop-Process on a process owned by the same user needs no
        elevation), unlike the portproxy/firewall repair in
        `cdp_environment_doctor.py`.
        """
        cfg = cdp_config.get_config()
        ps = cdp_config.powershell_exe()
        if not ps:
            return
        profile_escaped = cfg.profile_dir.replace("\\", "\\\\")
        script = (
            "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{profile_escaped}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "/init", ps, "-NoProfile", "-Command", script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10.0)
            logger.info("[WINDOWS_CHROME_STALE_PROCESS_KILLED] profile=%s", cfg.profile_dir)
        except Exception as exc:
            logger.warning("[WINDOWS_CHROME_KILL_FAILED] error=%r", str(exc)[:200])

    async def _launch_windows_chrome(self) -> bool:
        cfg = cdp_config.get_config()
        chrome_path = cdp_config.find_windows_chrome_exe()
        ps = cdp_config.powershell_exe()
        if not chrome_path or not ps:
            logger.warning(
                "[WINDOWS_CHROME_LAUNCH_FAILED] reason=%s",
                "chrome_not_found" if not chrome_path else "powershell_not_found",
            )
            return False

        logger.info(
            "[XYRON_CHROME_PROFILE] profile=%s", cfg.profile_dir,
        )
        logger.info(
            "[WINDOWS_CHROME_LAUNCH] path=%s profile=%s port=%d",
            chrome_path, cfg.profile_dir, cfg.chrome_local_port,
        )
        logger.info("[WINDOWS_CHROME_NEW_STARTED] port=%d", cfg.chrome_local_port)
        # No `--remote-debugging-address` override: Chrome's own hardening
        # already restricts the listener to 127.0.0.1, which is exactly
        # what the bridge's `connectaddress=127.0.0.1` expects. Explicitly
        # requesting 0.0.0.0 was the root cause of the Phase 4.11.1 port
        # collision (see module docstring) — never re-add it.
        script = (
            f"New-Item -ItemType Directory -Force -Path '{cfg.profile_dir}' | Out-Null; "
            f"Start-Process -FilePath '{chrome_path}' -ArgumentList "
            f"'--remote-debugging-port={cfg.chrome_local_port}',"
            f"'--user-data-dir={cfg.profile_dir}','--no-first-run','--no-default-browser-check',"
            f"'--new-window','about:blank'"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "/init", ps, "-NoProfile", "-Command", script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15.0)
            return True
        except Exception as exc:
            logger.warning("[WINDOWS_CHROME_LAUNCH_FAILED] error=%r", str(exc)[:200])
            return False

    async def _connect_with_retry(self, attempts: int = 6, delay_s: float = 1.0) -> bool:
        """Poll for CDP readiness instead of a single fixed sleep+attempt.

        A genuinely cold `chrome.exe` start (as opposed to reconnecting to
        an instance that's been running since an earlier session) can take
        longer than a single short delay to bind its debug listener under
        WSL2/Windows process-launch overhead.

        Latency tuning: Chrome frequently binds its debug listener within
        1–2s of launch, so short polling (1.0s) catches it much sooner
        than the old 2.0s-first-sleep cadence without losing total
        coverage (6 × 1.0s ≈ the old 4 × 2.0s window).
        """
        for attempt in range(1, attempts + 1):
            await asyncio.sleep(delay_s)
            if await self._try_connect_windows_chrome():
                if attempt > 1:
                    logger.info("[WINDOWS_CHROME_CDP_READY_AFTER_RETRY] attempt=%d", attempt)
                return True
        return False

    # ── Public API ────────────────────────────────────────────────────────

    async def get_or_create_page(self) -> Page:
        """Return the single active page, connecting/self-healing the
        workspace as needed. Every subsequent call reuses the same page —
        this is the entire fix for "follow-ups don't continue on the same
        page". Raises `CDPUnavailableError` instead of silently falling
        back to an uncontrolled browser (Phase 4.11.1, Part 7) — callers
        that need a repair-and-retry loop should catch it and drive
        `cdp_environment_doctor.doctor`.
        """
        async with self._lock:
            logger.info("[CDP_HEALTH_CHECK] status=%s", "healthy" if self.is_healthy else "unhealthy")
            if self.is_healthy:
                logger.info("[BROWSER_WORKSPACE_REUSED] age_s=%.1f mode=%s",
                            time.time() - (self._created_at or time.time()), self._control_mode)
                logger.info("[BROWSER_ACTIVE_PAGE] url=%s", self._page.url)
                logger.info("[WINDOWS_CHROME_EXISTING_REUSED]")
                self._last_used_at = time.time()
                return self._page

            if self._page is not None:
                logger.info("[CDP_DISCONNECTED]")
            self._page = None
            self._context = None
            self._browser = None

            # 1. Reconnect to an already-running, debug-enabled Chrome.
            logger.info("[CDP_RECONNECT_START]")
            if await self._try_connect_windows_chrome():
                logger.info("[CDP_RECONNECT_SUCCESS]")
                self._control_mode = CONTROL_MODE_CDP
            else:
                logger.info("[CDP_RECONNECT_FAILED]")
                # 2. Not running — launch it, then poll for the debug port
                # to come up (a cold start can take much longer than a
                # single fixed delay — see `_connect_with_retry`).
                if await self._launch_windows_chrome():
                    if await self._connect_with_retry(attempts=6, delay_s=1.0):
                        self._control_mode = CONTROL_MODE_CDP

                if self._page is None:
                    # 3. Still unreachable — a stale Xyron-profile Chrome
                    # process may be holding the port/profile in a bad
                    # state. This is a non-elevated recovery attempt; if it
                    # also fails, the caller must run the elevated
                    # Environment Doctor repair (Part 3) rather than us
                    # silently degrading to an uncontrolled browser.
                    #
                    # Bounded failure policy (2026-09-04): this self-heal
                    # tier's retry budget was attempts=6/delay_s=1.5 (up to
                    # 9s poll + a fresh 15s launch wait + 1.5s presleep —
                    # ~25.5s on TOP of tier 2's own ~21s worst case, ~46.5s
                    # total before raising CDPUnavailableError). Now that
                    # simple website opens never reach this function at all
                    # (system_tools.py's _launch_app no longer routes
                    # through browser_workspace for a plain "open X" — see
                    # its own comment), this tier is reached ONLY by genuine
                    # browser-automation commands, where an unbounded retry
                    # storm is still bad UX. Tightened to attempts=3/
                    # delay_s=1.0 (3s poll) + 1.0s presleep — a real second
                    # chance at recovering a stale/broken profile, not a
                    # second full 9s+ poll cycle stacked on the first.
                    logger.info("[CDP_SELF_HEAL_START] action=kill_stale_and_retry")
                    await self._kill_stale_xyron_chrome()
                    await asyncio.sleep(1.0)
                    if await self._launch_windows_chrome():
                        if await self._connect_with_retry(attempts=3, delay_s=1.0):
                            self._control_mode = CONTROL_MODE_CDP
                            logger.info("[CDP_SELF_HEAL_SUCCESS] recovered_via=kill_stale_and_relaunch")

            if self._page is None:
                logger.warning("[CDP_SELF_HEAL_FAILED]")
                logger.warning("[BROWSER_FALLBACK_BLOCKED] reason=windows_chrome_cdp_unreachable")
                raise CDPUnavailableError(
                    "Could not establish CDP control of Windows Chrome.",
                    repair_recommended=True,
                )

            self._created_at = time.time()
            self._last_used_at = self._created_at
            logger.info("[BROWSER_CONTROL_MODE] mode=%s", self._control_mode)
            logger.info("[BROWSER_WORKSPACE_CREATED] control_mode=%s", self._control_mode)
            logger.info("[BROWSER_ACTIVE_PAGE] url=%s", self._page.url)
            self.log_tab_count()
            self._start_keepalive()
            return self._page

    def _start_keepalive(self) -> None:
        """Phase 4.14: the CDP connection established here (including the
        startup pre-warm) was observed to die from a WSL2<->Windows bridge
        idle timeout in well under a minute of inactivity — measured live:
        `_browser.is_connected()` went False after only ~36s idle, forcing
        a full ~10-15s reconnect on the user's first real command despite
        having pre-warmed the browser at boot. A cheap periodic CDP
        round-trip keeps the connection active so the pre-warm actually
        pays off instead of being silently defeated by idle disconnects."""
        if getattr(self, "_keepalive_task", None) is not None and not self._keepalive_task.done():
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(15.0)
                try:
                    if self._page is not None and not self._page.is_closed():
                        await self._page.evaluate("1")
                except Exception as exc:
                    logger.debug("[CDP_KEEPALIVE_STOPPED] error=%r", str(exc)[:150])
                    return
                if not self.is_healthy:
                    return

        self._keepalive_task = asyncio.create_task(_loop())

    def log_tab_count(self) -> int:
        count = len(self._context.pages) if self._context else 0
        logger.info("[BROWSER_TAB_COUNT] count=%d", count)
        return count

    async def new_tab_if_approved(self, url: str, approved: bool, reason: str = "") -> Optional[Page]:
        """Open a second tab — but only if *approved* is True. This is the
        single choke point for the "ask before opening another site" rule;
        every caller (alt-site fallback, official-airline baggage check)
        must go through this instead of calling context.new_page() itself."""
        if not self._context:
            return None
        if not approved:
            logger.info("[BROWSER_NEW_TAB_BLOCKED] url=%s reason=%r", url, reason or "not_approved")
            return None

        logger.info("[BROWSER_NEW_TAB_APPROVED] url=%s reason=%r", url, reason)
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        except Exception as exc:
            logger.warning("[BROWSER_NAV_ERROR] url=%s error=%r", url, str(exc))
        self.log_tab_count()
        return page

    async def close_extra_tabs(self, keep: Page) -> None:
        """Close every tab except *keep* — used after a second-tab check
        completes, to restore the one-tab default."""
        if not self._context:
            return
        for p in list(self._context.pages):
            if p is not keep and not p.is_closed():
                try:
                    await p.close()
                except Exception:
                    pass
        logger.info("[BROWSER_TAB_REUSED] active_url=%s", keep.url if not keep.is_closed() else "closed")
        self.log_tab_count()

    def save_state_snapshot(self, extra: Optional[dict] = None) -> dict:
        """Lightweight state snapshot for logging/debugging."""
        snapshot = {
            "url": self._page.url if self.is_open else None,
            "tab_count": self.log_tab_count(),
            "control_mode": self._control_mode,
        }
        if extra:
            snapshot.update(extra)
        logger.info("[BROWSER_STATE_SAVED] url=%s tab_count=%d", snapshot["url"], snapshot["tab_count"])
        return snapshot

    async def close(self) -> None:
        """Explicit teardown — session end or idle timeout only. Never
        called from a per-task `finally` block; that was the previous
        bug that made persistence impossible.

        In windows_chrome_cdp mode this ONLY drops the CDP connection —
        it must never terminate the user's real Chrome process or tabs.
        """
        async with self._lock:
            try:
                if self._control_mode == CONTROL_MODE_CDP:
                    if self._pw:
                        await self._pw.stop()
                else:
                    if self._context:
                        await self._context.close()
                    if self._browser:
                        await self._browser.close()
                    if self._pw:
                        await self._pw.stop()
            except Exception as exc:
                logger.debug("[BROWSER_WORKSPACE] close error (ignored): %r", exc)
            finally:
                self._page = None
                self._context = None
                self._browser = None
                self._pw = None


# Module-level singleton — the one persistent workspace for this process.
browser_workspace = BrowserWorkspace()
