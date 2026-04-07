#!/usr/bin/env python3
"""
whatsapp_web_helper.py — WhatsApp Web Playwright automation client.

Gold Tier — G-M4-T01

Architecture:
  - "persistent" mode: browser stays alive; multiple callers reuse one instance via lock.
  - "oneshot" mode: opens browser, does task, closes. Used for CLI/tests.

WSL notes:
  - headed mode requires DISPLAY=:0 or Xvfb.
  - Set WHATSAPP_HEADLESS=1 for fully headless mode.
  - Run: Xvfb :0 -screen 0 1280x720x24 &  export DISPLAY=:0

Session path (default):
  ~/.ai_operator/whatsapp_session/
  Override: WHATSAPP_SESSION_DIR env var
  Alternatively: .secrets/whatsapp_session/ (gitignored)

SAFETY: This helper NEVER initiates sending unless send_message() is called explicitly
from an approved executor. The watcher only calls list_chats() / get_unread_messages().
"""

import os
import sys
import json
import time
import fcntl
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Selector catalogue (WhatsApp Web, circa 2026) ──────────────────────────────
# Selectors are organised with primary + fallback to survive DOM updates.
SEL = {
    # ── Chat list panel ────────────────────────────────────────────────────────
    # WhatsApp Web 2025+ uses role="grid" inside #pane-side.
    # The div[aria-label="Chat list"] is the grid container.
    "chat_list":       '#pane-side, div[aria-label="Chat list"]',

    # ── Individual chat rows ───────────────────────────────────────────────────
    # New UI: role="grid" > role="row" > role="gridcell"[tabindex="0"]
    # The gridcell with tabindex="0" is the clickable chat row.
    # Legacy fallback kept for older versions.
    "chat_item":       (
        'div[aria-label="Chat list"] div[role="gridcell"][tabindex="0"], '
        'div[role="gridcell"][tabindex="0"], '
        '[data-testid="cell-frame-container"]'
    ),

    # ── Title inside a chat row ────────────────────────────────────────────────
    # New UI: <span dir="auto" title="Chat Name">…</span>
    "chat_title":      'span[dir="auto"][title], [data-testid="cell-frame-title"]',

    # ── Last message preview ───────────────────────────────────────────────────
    "chat_subtitle":   'span[dir="ltr"], [data-testid="cell-frame-secondary"]',

    # ── Timestamp ─────────────────────────────────────────────────────────────
    "chat_timestamp":  'div[class*="_ak8i"] span, [data-testid="cell-frame-date"]',

    # ── Unread badge ──────────────────────────────────────────────────────────
    # New UI: a span containing a number in the lower-right of the cell.
    # class names change; target by aria/role heuristics.
    "unread_badge":    (
        '[data-testid="icon-unread-count"], '
        'span[aria-label*="unread"], '
        'span[data-testid="unread-count"]'
    ),

    # ── Compose box ───────────────────────────────────────────────────────────
    # IMPORTANT: must be scoped to #main so we never match the search input
    # in the left panel (which is also a contenteditable div).
    "msg_input":       (
        '#main [data-testid="conversation-compose-box-input"], '
        '#main div[contenteditable="true"][data-tab], '
        '#main div[role="textbox"][data-lexical-editor], '
        '#main div[contenteditable="true"], '
        '[data-testid="conversation-compose-box-input"], '
        'div[role="textbox"][data-lexical-editor]'
    ),
    "send_btn":        '[data-testid="compose-btn-send"], [aria-label="Send"], button[data-testid="send"]',

    # ── Search ────────────────────────────────────────────────────────────────
    "search_input":    (
        '[data-testid="chat-list-search"], '
        'div[contenteditable="true"][data-tab="3"], '
        '[aria-label="Search input textbox"], '
        '[aria-label="Search or start new chat"]'
    ),

    # ── Auth / pairing ────────────────────────────────────────────────────────
    "qr_canvas":       'canvas[aria-label="Scan me!"], div[data-testid="qrcode"] canvas',
    "landing_title":   'div[data-testid="intro-title"]',
    "link_phone":      (
        'span:has-text("Link with phone number"), '
        'a:has-text("Link with phone number")'
    ),
    "phone_input":     'input[placeholder*="phone"], input[type="tel"]',
    "pairing_code":    '[data-testid="pairing-code"], div.linking-device-pairing-code',

    # ── Message panel ─────────────────────────────────────────────────────────
    "messages_list":   '[data-testid="conversation-panel-messages"], #main',
    "msg_in":          '[data-testid="msg-container"][data-id], div[data-id]',
    "msg_text":        'span.selectable-text, span[data-testid="msg-text"]',

    # ── Login detection ───────────────────────────────────────────────────────
    # Any of these means authenticated and chat list loaded.
    "logged_in_check": (
        '#pane-side, '
        'div[aria-label="Chat list"], '
        '[data-testid="chat-list"], '
        'header[data-testid="chatlist-header"]'
    ),
}

# ── Chromium launch args optimised for WSL2 ───────────────────────────────────
WSL_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--disable-translate",
    "--mute-audio",
    "--safebrowsing-disable-auto-update",
    "--no-first-run",
    # Prevent WhatsApp Web from detecting automation (headless detection bypass)
    "--disable-blink-features=AutomationControlled",
    "--exclude-switches=enable-automation",
    "--disable-infobars",
]

# Real Chrome user-agent — prevents WhatsApp Web "browser not supported" block
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def _default_session_dir() -> Path:
    """Return the WhatsApp session directory (persistent across restarts)."""
    env_override = os.environ.get("WHATSAPP_SESSION_DIR")
    if env_override:
        return Path(env_override)
    # Prefer .secrets/ if it exists (gitignored)
    secrets = Path(__file__).parent.parent.parent.parent / ".secrets" / "whatsapp_session"
    if secrets.parent.exists():
        return secrets
    # Fallback: ~/.ai_operator/
    return Path.home() / ".ai_operator" / "whatsapp_session"


def redact_phone(text: str) -> str:
    """Lightly redact phone numbers for safe logging."""
    return re.sub(r'\+?[\d\s\-().]{7,}', '[PHONE]', text)


class WhatsAppWebClient:
    """
    Playwright-based WhatsApp Web client.

    Usage (one-shot):
        client = WhatsAppWebClient()
        client.start()
        msgs = client.get_unread_messages(limit=10)
        client.stop()

    Usage (context manager):
        with WhatsAppWebClient() as client:
            client.send_message("+923001234567", "Hello!")

    Session is stored in self.session_dir and reused on next start.
    """

    def __init__(
        self,
        session_dir: Optional[Path] = None,
        headless: bool = None,
        slow_mo_ms: int = 200,
        timeout_ms: int = 30_000,
        extra_args: Optional[List[str]] = None,
    ):
        self.session_dir = Path(session_dir) if session_dir else _default_session_dir()
        self.headless = headless if headless is not None else (
            os.environ.get("WHATSAPP_HEADLESS", "0") == "1"
        )
        self.slow_mo_ms = slow_mo_ms
        self.timeout_ms = timeout_ms
        self.extra_args = extra_args or []

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

        # Performance: record startup time
        self._t_start: Optional[float] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Start Playwright + browser + restore session."""
        self._t_start = time.perf_counter()
        self.session_dir.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            )

        logger.info(f"Starting WhatsApp Web client (headless={self.headless})")

        self._playwright = sync_playwright().start()

        args = WSL_ARGS + self.extra_args

        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.session_dir),
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
            args=args,
            viewport={"width": 1280, "height": 900},
            user_agent=_UA,
        )

        # Mask navigator.webdriver so WhatsApp Web treats us as a real browser
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        self._page = self._context.new_page() if not self._context.pages else self._context.pages[0]
        elapsed = time.perf_counter() - self._t_start
        logger.info(f"Browser started in {elapsed:.1f}s")

    def stop(self):
        """Close browser and clean up."""
        try:
            if self._context:
                self._context.close()
        except Exception as e:
            logger.warning(f"Error closing context: {e}")
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.warning(f"Error stopping Playwright: {e}")
        self._playwright = self._browser = self._context = self._page = None
        logger.info("WhatsApp Web client stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def ensure_page(self):
        """Make sure we have a live page, reconnecting if needed."""
        if self._page is None or self._page.is_closed():
            if self._context:
                pages = self._context.pages
                self._page = pages[0] if pages else self._context.new_page()
        return self._page

    # ── Navigation ────────────────────────────────────────────────────────────

    def open_whatsapp(self, wait_for_logged_in: bool = False, timeout_s: int = 60):
        """Navigate to WhatsApp Web. Optionally wait for login."""
        page = self.ensure_page()
        current = page.url
        if "web.whatsapp.com" not in current:
            logger.info("Navigating to web.whatsapp.com …")
            page.goto("https://web.whatsapp.com", timeout=self.timeout_ms)

        if wait_for_logged_in:
            logger.info("Waiting for login …")
            page.wait_for_selector(SEL["logged_in_check"], timeout=timeout_s * 1000)
            logger.info("Logged in ✓")

    # ── Auth checks ───────────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Return True if the chat list is visible (session active)."""
        page = self.ensure_page()
        try:
            if "web.whatsapp.com" not in page.url:
                page.goto("https://web.whatsapp.com", timeout=self.timeout_ms)

            # Wait for the JS app to settle — especially important in headless mode
            # where WhatsApp Web takes longer to initialise from a saved session.
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                # networkidle may never fire; domcontentloaded is enough
                page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)

            # Fast check after page load
            el = page.query_selector(SEL["logged_in_check"])
            if el:
                return True

            # In headless mode WhatsApp Web may take up to ~20s to render the chat list
            # after restoring a session.  Give it enough time before declaring "not logged in".
            wait_ms = 20_000 if self.headless else 8_000
            try:
                page.wait_for_selector(
                    f'{SEL["logged_in_check"]}, {SEL["qr_canvas"]}, {SEL["landing_title"]}',
                    timeout=wait_ms,
                )
            except Exception:
                pass

            el = page.query_selector(SEL["logged_in_check"])
            return el is not None
        except Exception as e:
            logger.warning(f"is_logged_in check failed: {e}")
            return False

    def wait_for_login(self, timeout_s: int = 120) -> bool:
        """Block until user scans QR or enters pairing code. Returns True on success."""
        page = self.ensure_page()
        logger.info(f"Waiting up to {timeout_s}s for WhatsApp login …")
        try:
            page.wait_for_selector(SEL["logged_in_check"], timeout=timeout_s * 1000)
            logger.info("Login successful ✓")
            return True
        except Exception:
            logger.error("Login timeout — user did not authenticate in time")
            return False

    def get_pairing_code(self) -> Optional[str]:
        """
        Click 'Link with phone number' and return the 8-digit pairing code
        shown on screen (for phone number pairing flow).
        Returns None if element not found.
        """
        page = self.ensure_page()
        try:
            link_btn = page.query_selector(SEL["link_phone"])
            if not link_btn:
                logger.warning("'Link with phone number' button not found")
                return None
            link_btn.click()
            page.wait_for_timeout(1000)

            # Enter phone number if prompted
            phone_input = page.query_selector(SEL["phone_input"])
            if phone_input:
                phone = os.environ.get("WHATSAPP_PHONE", "")
                if phone:
                    phone_input.fill(phone)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(2000)

            # Read pairing code
            code_el = page.wait_for_selector(SEL["pairing_code"], timeout=15_000)
            if code_el:
                code_text = code_el.inner_text().strip()
                return code_text
        except Exception as e:
            logger.warning(f"get_pairing_code failed: {e}")
        return None

    # ── Chat listing ──────────────────────────────────────────────────────────

    def _check_logged_in_fast(self):
        """
        Lightweight login guard — checks page URL + DOM element without
        triggering another networkidle wait.  Call this instead of
        is_logged_in() from methods that already know the session is live.
        Raises RuntimeError if not logged in.
        """
        page = self.ensure_page()
        if "web.whatsapp.com" not in page.url:
            raise RuntimeError("Not logged in to WhatsApp Web (wrong URL)")
        el = page.query_selector(SEL["logged_in_check"])
        if not el:
            raise RuntimeError("Not logged in to WhatsApp Web (chat list not visible)")

    def list_chats(self, limit: int = 20) -> List[Dict]:
        """
        Return the first `limit` chats from the chat list panel.

        Each chat dict:
          name (str), chat_id (str), unread_count (int),
          last_msg_preview (str), timestamp (str)
        """
        page = self.ensure_page()
        t0 = time.perf_counter()

        # Fast guard — avoids the 20 s networkidle wait inside is_logged_in()
        # which resets the virtual-scroll viewport and hides unread badges.
        self._check_logged_in_fast()

        chats = []
        try:
            # First: wait for the side panel (fast indicator that WA is loaded)
            try:
                page.wait_for_selector(SEL["chat_list"], timeout=self.timeout_ms)
            except Exception:
                logger.warning("Chat list panel not found — WhatsApp Web may still be loading")
                return chats

            # Then wait for individual chat rows (these load after the panel)
            item_timeout = max(self.timeout_ms, 45_000)
            try:
                page.wait_for_selector(SEL["chat_item"], timeout=item_timeout)
            except Exception:
                logger.warning("No chat rows found within timeout")
                return chats

            # Let the virtual-scroll list finish rendering so unread badges appear
            page.wait_for_timeout(2_000)

            items = page.query_selector_all(SEL["chat_item"])[:limit]

            for item in items:
                try:
                    # Title: prefer the `title` attribute (new WA UI has it on the span)
                    name = "Unknown"
                    title_el = item.query_selector(SEL["chat_title"])
                    if title_el:
                        t = (title_el.get_attribute("title") or title_el.inner_text()).strip()
                        if t:
                            name = t

                    sub_el = item.query_selector(SEL["chat_subtitle"])
                    preview = sub_el.inner_text().strip() if sub_el else ""

                    ts_el = item.query_selector(SEL["chat_timestamp"])
                    timestamp = ts_el.inner_text().strip() if ts_el else ""

                    badge_el = item.query_selector(SEL["unread_badge"])
                    unread_str = badge_el.inner_text().strip() if badge_el else "0"
                    try:
                        unread_count = int(unread_str)
                    except ValueError:
                        unread_count = 1 if unread_str else 0

                    # Derive a stable chat_id from name
                    chat_id = re.sub(r'\W+', '_', name.lower())[:40]

                    chats.append({
                        "name": name,
                        "chat_id": chat_id,
                        "unread_count": unread_count,
                        "last_msg_preview": preview[:200],
                        "timestamp": timestamp,
                    })
                except Exception as e:
                    logger.debug(f"Failed parsing chat item: {e}")

        except Exception as e:
            logger.error(f"list_chats failed: {e}")

        elapsed = time.perf_counter() - t0
        logger.info(f"list_chats: {len(chats)} chats in {elapsed:.2f}s")
        return chats

    def get_unread_messages(self, limit: int = 20) -> List[Dict]:
        """
        Open each chat with unread badge and extract messages.
        PERCEPTION-ONLY — does not mark messages as read.

        Returns list of message dicts:
          id, from_name, chat_id, received_at, text, is_unread
        """
        page = self.ensure_page()
        t0 = time.perf_counter()

        # Fast guard — avoids the 20 s networkidle wait inside is_logged_in()
        # which resets the virtual-scroll viewport and hides unread badges.
        self._check_logged_in_fast()

        messages = []
        try:
            # Wait for chat list panel first, then for rows
            try:
                page.wait_for_selector(SEL["chat_list"], timeout=self.timeout_ms)
            except Exception:
                logger.warning("Chat list panel not found")
                return messages

            item_timeout = max(self.timeout_ms, 45_000)
            try:
                page.wait_for_selector(SEL["chat_item"], timeout=item_timeout)
            except Exception:
                logger.warning("No chat rows found")
                return messages

            # Let the virtual-scroll list finish rendering so unread badges appear
            page.wait_for_timeout(2_000)

            items = page.query_selector_all(SEL["chat_item"])

            for item in items:
                if len(messages) >= limit:
                    break

                badge_el = item.query_selector(SEL["unread_badge"])
                if not badge_el:
                    continue  # no unread badge — skip (fast path)

                # Get chat name
                title_el = item.query_selector(SEL["chat_title"])
                chat_name = title_el.inner_text().strip() if title_el else "Unknown"
                chat_id = re.sub(r'\W+', '_', chat_name.lower())[:40]

                # Open the chat
                try:
                    item.click()
                    page.wait_for_selector(SEL["messages_list"], timeout=8_000)
                    page.wait_for_timeout(500)
                except Exception as e:
                    logger.warning(f"Could not open chat '{chat_name}': {e}")
                    continue

                # Extract messages
                try:
                    msg_els = page.query_selector_all(SEL["msg_in"])
                    for msg_el in msg_els[-10:]:  # last 10 msgs per chat
                        try:
                            text_el = msg_el.query_selector(SEL["msg_text"])
                            text = text_el.inner_text().strip() if text_el else ""
                            if not text:
                                continue

                            # Build a stable pseudo-id from content hash
                            msg_hash = abs(hash(chat_id + text)) % 10_000_000
                            msg_id = f"wa_{chat_id}_{msg_hash}"

                            messages.append({
                                "id": msg_id,
                                "from_name": chat_name,
                                "sender": chat_name,
                                "sender_name": chat_name,
                                "chat_id": chat_id,
                                "body": text,
                                "text": text,
                                "received_at": datetime.now(timezone.utc).isoformat(),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "thread_id": f"thread_{chat_id}",
                                "type": "incoming",
                                "is_unread": True,
                                "urgent": False,
                            })

                            if len(messages) >= limit:
                                break

                        except Exception as e:
                            logger.debug(f"Failed parsing message: {e}")
                except Exception as e:
                    logger.warning(f"Failed reading messages from '{chat_name}': {e}")

        except Exception as e:
            logger.error(f"get_unread_messages failed: {e}")

        elapsed = time.perf_counter() - t0
        logger.info(f"get_unread_messages: {len(messages)} in {elapsed:.2f}s")
        return messages

    # ── Send message ──────────────────────────────────────────────────────────

    def send_message(self, to: str, text: str) -> bool:
        """
        Send a WhatsApp message.

        `to` can be:
          - phone number: "+923001234567"
          - chat_id from list_chats()

        Returns True on success.

        SAFETY: Only call from approved executor — NEVER from watcher.
        """
        page = self.ensure_page()

        # Use _check_logged_in_fast() — avoids the 20 s networkidle wait of
        # is_logged_in() which is unnecessary in a persistent-browser context.
        self._check_logged_in_fast()

        # Validate input
        if not text or not text.strip():
            raise ValueError("Message text cannot be empty")

        logger.info(f"Sending message to {redact_phone(to)}")

        # Use deep link for phone numbers (most reliable)
        if to.startswith("+") or to.lstrip("+").isdigit():
            phone = re.sub(r'\D', '', to)
            url = f"https://web.whatsapp.com/send?phone={phone}&text="
            page.goto(url, timeout=self.timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)

            # Wait for compose box
            try:
                page.wait_for_selector(SEL["msg_input"], timeout=20_000)
            except Exception:
                logger.error(f"Could not open chat for {redact_phone(to)}: input box not found")
                return False
        else:
            # Contact name: open via search
            if not self._open_chat_by_search(to):
                logger.error(f"Could not find chat: {to}")
                return False

        # ── Focus the compose box and send ────────────────────────────────────
        # Use JavaScript to find the footer-level compose box and focus it.
        # This is more reliable than Playwright selectors because:
        #  • The search box (left panel) is never inside <footer>
        #  • JS can check element visibility before clicking
        #  • No dependency on data-testid / data-tab values that WA changes
        try:
            focused = page.evaluate("""
                () => {
                    // Prefer the footer compose area — it is never the search box
                    const candidates = [
                        'footer [contenteditable="true"]',
                        '#main footer [contenteditable]',
                        '#main footer div',
                        '[data-testid="conversation-compose-box-input"]',
                        '#main [contenteditable="true"]',
                    ];
                    for (const sel of candidates) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) {
                            el.click();
                            el.focus();
                            return sel;
                        }
                    }
                    return null;
                }
            """)

            if not focused:
                logger.error("Could not find compose box via JS")
                return False

            logger.debug(f"Compose box focused via: {focused}")
            page.wait_for_timeout(400)

            # Type into whatever element has focus (keyboard events follow focus)
            page.keyboard.type(text, delay=25)
            page.wait_for_timeout(500)

            # Enter sends the message
            page.keyboard.press("Enter")
            page.wait_for_timeout(2_000)

            logger.info(f"Message sent to {redact_phone(to)} ✓")
            return True

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def _open_chat_by_search(self, query: str) -> bool:
        """Search for a chat by name and click to open it. Returns True on success."""
        page = self.ensure_page()
        try:
            # Dismiss any open panels/dialogs first
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

            # ── Strategy 1: find the search input directly ──────────────────────
            search = None
            search_selectors = [
                '[data-testid="chat-list-search"]',
                '[aria-label="Search input textbox"]',
                '[aria-label="Search or start new chat"]',
                'div[contenteditable="true"][data-tab="3"]',
                '[role="searchbox"]',
                'div[title="Search input textbox"]',
            ]
            for sel in search_selectors:
                try:
                    el = page.wait_for_selector(sel, timeout=1_500)
                    if el and el.is_visible():
                        search = el
                        break
                except Exception:
                    continue

            # ── Strategy 2: click a search icon to reveal the input ─────────────
            if not search:
                for btn_sel in [
                    '[data-testid="search"]',
                    'button[aria-label*="earch"]',
                    'span[data-testid="search"]',
                    'div[title="Search"]',
                    '#side header button',
                ]:
                    try:
                        btn = page.wait_for_selector(btn_sel, timeout=1_000)
                        if btn:
                            btn.click()
                            page.wait_for_timeout(600)
                            for sel in search_selectors:
                                try:
                                    el = page.wait_for_selector(sel, timeout=2_000)
                                    if el and el.is_visible():
                                        search = el
                                        break
                                except Exception:
                                    continue
                            if search:
                                break
                    except Exception:
                        continue

            if not search:
                logger.warning(f"Could not find search input for '{query}'")
                return False

            # Click to focus, clear any previous query, then type
            search.click()
            page.wait_for_timeout(300)
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            page.wait_for_timeout(200)
            page.keyboard.type(query, delay=80)

            # Wait for results — 3 s covers slow connections
            page.wait_for_timeout(3_000)

            # Grab first result row (try multiple selectors)
            first_result = None
            for result_sel in [
                SEL["chat_item"],
                'div[role="gridcell"][tabindex="0"]',
                '[data-testid="cell-frame-container"]',
                '#pane-side div[role="row"]',
            ]:
                first_result = page.query_selector(result_sel)
                if first_result:
                    break

            if not first_result:
                logger.warning(f"No search results for '{query}'")
                page.keyboard.press("Escape")
                return False

            first_result.click()

            # Wait for the conversation panel to appear
            try:
                page.wait_for_selector(SEL["messages_list"], timeout=10_000)
            except Exception:
                pass  # chat may have opened even if selector is stale

            page.wait_for_timeout(1_000)
            return True

        except Exception as e:
            logger.warning(f"Chat search failed for '{query}': {e}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        return False

    def mark_chat_read(self, chat_id: str) -> bool:
        """Right-click chat and select 'Mark as read' (if available)."""
        page = self.ensure_page()
        try:
            items = page.query_selector_all(SEL["chat_item"])
            for item in items:
                title_el = item.query_selector(SEL["chat_title"])
                if title_el:
                    cid = re.sub(r'\W+', '_', title_el.inner_text().strip().lower())[:40]
                    if cid == chat_id:
                        item.click(button="right")
                        page.wait_for_timeout(500)
                        # Look for "Mark as read" in context menu
                        mark_el = page.query_selector('li[data-testid="read"], li:has-text("Mark as read")')
                        if mark_el:
                            mark_el.click()
                            return True
            return False
        except Exception as e:
            logger.warning(f"mark_chat_read failed: {e}")
            return False

    def healthcheck(self) -> Dict:
        """Return status dict for MCP health endpoint."""
        try:
            logged_in = self.is_logged_in()
            return {
                "status": "ok" if logged_in else "not_logged_in",
                "logged_in": logged_in,
                "session_dir": str(self.session_dir),
                "headless": self.headless,
                "url": self._page.url if self._page else None,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
