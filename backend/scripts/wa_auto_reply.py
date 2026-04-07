#!/usr/bin/env python3
"""
wa_auto_reply.py — WhatsApp Auto-Reply Daemon (real-time, MutationObserver)

HOW IT WORKS:
  A JavaScript MutationObserver is injected once into WhatsApp Web.
  It watches the chat list panel and pushes only genuinely NEW incoming
  messages into a queue.  Python drains that queue every 1 second.

  Safeguards:
  - Groups and channels are skipped (DOM icon + preview-pattern detection)
  - 2-second JS grace period before queueing a message: if Tayyab replies
    within that window, the candidate is cancelled before it ever reaches Python
  - "You: " preview → instantly cancel the candidate + record cooldown
  - Python pre-send live-preview check: abort if Tayyab already replied
  - 10-minute cooldown per contact after Tayyab sends anything there

Usage:
    python3 scripts/wa_auto_reply.py          # foreground (Ctrl+C to stop)
    python3 scripts/wa_auto_reply.py --once   # single pass
    python3 scripts/wa_auto_reply.py --dry-run
    python3 scripts/wa_auto_reply.py --reset  # clear checkpoint
"""

import sys
import os
import json
import time
import signal
import logging
import atexit
import urllib.request
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT  = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_dotenv():
    for env_file in [
        REPO_ROOT / "apps" / "web" / ".env",
        REPO_ROOT / "apps" / "web" / ".env.local",
        REPO_ROOT / ".env",
    ]:
        if not env_file.exists():
            continue
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wa_auto_reply] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
CHECKPOINT_PATH   = REPO_ROOT / "Logs" / "wa_auto_reply_checkpoint.json"
SIGNATURE         = "— Tayyab's AI Employee"
CHECK_INTERVAL    = 1      # seconds — how often Python reads the JS queue
COOLDOWN_MINUTES  = 10     # minutes to pause after Tayyab sends in a chat
QUEUE_DELAY_MS    = 2000   # ms grace period in JS before queueing a candidate
MAX_HISTORY       = 20     # max messages in per-contact conversation history
READY_FILE        = Path("/tmp/wa_auto_reply.ready")
atexit.register(lambda: READY_FILE.unlink(missing_ok=True))

FALLBACK_REPLY = (
    "Hi! I'm Tayyab's AI assistant. He's stepped away for a moment but I've "
    "passed your message along and he'll get back to you soon.\n\n"
    f"{SIGNATURE}"
)

SYSTEM_PROMPT = f"""You are Tayyab's personal AI assistant managing his WhatsApp.
You are having an ongoing conversation — reply naturally, like a real person chatting.

Guidelines:
- LANGUAGE: Detect the language of the incoming message and reply in that EXACT same language.
  If they write in Urdu, reply in Urdu. If in English, reply in English.
  If they mix Urdu and English (Roman Urdu), match that mix naturally.
  Never switch languages unless the person switches first.
- Be warm, friendly, and professional — conversational, not templated
- Match their energy: relaxed if they're casual, more formal if they're formal
- Keep replies concise (2–4 sentences) — this is WhatsApp, not email
- If they ask about scheduling or a meeting: tell them you'll check with Tayyab and let them know
- If they ask where Tayyab is or his availability: honestly say you're his AI managing messages
- Answer straightforward factual questions briefly
- Never commit to things you can't guarantee (dates, decisions, Tayyab's plans)
- Vary your phrasing — do NOT start with the same word every time
- Plain text only — no markdown, no asterisks, no bullet points (WhatsApp doesn't render them)
- End every reply with exactly: {SIGNATURE}
"""

# ── JavaScript MutationObserver ───────────────────────────────────────────────
# Key behaviours:
#   1. Groups/channels skipped via DOM icon check + preview pattern
#   2. Sent-message detection uses DELIVERY TICK ICONS (msg-check / msg-dblcheck)
#      NOT the "You: " text prefix — ticks are reliable across all locales and
#      WhatsApp Web versions; the "You: " label can live in a separate DOM node
#      outside the span we read, so textContent alone is not enough.
#   3. readChats() returns { name: {preview, youSent} } — youSent=true when ticks found
#   4. Incoming message → 2-second grace-period timer (scheduleCandidate).
#      If youSent becomes true during those 2s, timer is cancelled.

_OBSERVER_JS = r"""
(function(signature, queueDelayMs) {
    if (window.__waAutoReplyActive) return 'already_active';
    window.__waAutoReplyActive = true;
    window.__waSendingTo       = null;   // name of contact being replied to right now
    window.__waPendingReplies  = [];
    window.__waUserSentEvents  = [];     // Tayyab's outgoing messages → cooldown
    window.__waSnapshots       = {};     // { chatName: lastSeenPreview }
    window.__waCandidates      = {};     // { chatName: timerId } — grace-period timers

    const SKIP_PREVIEW  = /^(typing\.\.\.|recording audio\.\.\.|recording\.\.\.)$/i;
    const GROUP_PREVIEW = /^(?!You[\s:])(?:[^:]{1,35}): \S/;  // "Sender: text" — group fallback (excludes "You: ")

    function isGroupOrChannel(item) {
        // 1. Icon-based: covers all group/broadcast/community/channel variants
        const spans = item.querySelectorAll('span[data-icon]');
        for (const sp of spans) {
            const icon = (sp.getAttribute('data-icon') || '').toLowerCase();
            if (icon.includes('group') || icon.includes('newsletter') ||
                icon.includes('channel') || icon.includes('community') ||
                icon.includes('broadcast') || icon.includes('linked-device') ||
                icon === 'status' || icon === 'default-group') return true;
        }
        // 2. data-testid based
        if (item.querySelector('[data-testid*="group-icon"]')    ||
            item.querySelector('[data-testid*="newsletter"]')     ||
            item.querySelector('[data-testid*="channel-icon"]')   ||
            item.querySelector('[data-testid*="community"]')) return true;
        // 3. Preview-pattern fallback: "SenderName: message" → group.
        //    Applied here (not just in scheduleCandidate) so groups are fully
        //    excluded from readChats output even if icons are missing/updated.
        const ltrEl = item.querySelector('span[dir="ltr"]');
        const ltrText = ltrEl ? ltrEl.textContent.trim() : '';
        if (ltrText && /^(?!You[\s:])(?:[^:]{1,40}): \S/.test(ltrText)) return true;
        return false;
    }

    // Delivery tick icons scoped to the preview container only.
    // Uses getComputedStyle (not offsetWidth/offsetHeight) — headless-safe.
    // Checks display, visibility AND opacity so all CSS-hiding strategies are covered.
    function hasSentTick(secEl) {
        if (!secEl) return false;
        const tickIcons = ['msg-check', 'msg-dblcheck', 'msg-dblcheck-ack', 'msg-time'];
        for (const icon of tickIcons) {
            const el = secEl.querySelector('[data-icon="' + icon + '"]');
            if (!el) continue;
            try {
                const cs = window.getComputedStyle(el);
                if (cs.display     !== 'none'    &&
                    cs.visibility  !== 'hidden'   &&
                    cs.opacity     !== '0') return true;
            } catch(e) { return true; }  // if CSS query fails, assume visible
        }
        return false;
    }

    // Returns { name: { preview, youSent } } — only DM contacts (no groups/channels)
    //
    // preview = ltrEl.textContent (message text ONLY — no timestamps/counters).
    //   Using the narrow ltrEl makes snapshot comparison stable: WhatsApp Web
    //   updates the timestamp string inside secEl every minute, which triggers
    //   spurious "preview changed" events and resets the scheduleCandidate timer.
    //   With ltrEl we only react to actual message text changes.
    //
    // youSent uses THREE independent signals so any one is sufficient:
    //   1. "You: " text prefix in the full secEl container text
    //   2. Visible delivery tick icon via getComputedStyle (headless-safe)
    //   3. item aria-label containing "you:" or "you sent" (locale-stable)
    function readChats() {
        const out = {};
        const items = document.querySelectorAll([
            'div[aria-label="Chat list"] div[role="gridcell"][tabindex="0"]',
            'div[role="gridcell"][tabindex="0"]',
            '[data-testid="cell-frame-container"]'
        ].join(','));
        items.forEach(item => {
            if (isGroupOrChannel(item)) return;
            const nameEl = item.querySelector('span[dir="auto"][title], [data-testid="cell-frame-title"]');
            if (!nameEl) return;
            const name = (nameEl.getAttribute('title') || nameEl.textContent || '').trim();

            const secEl    = item.querySelector('[data-testid="cell-frame-secondary"]');
            const ltrEl    = item.querySelector('span[dir="ltr"]');
            // Stable preview: message text only (timestamps live in secEl siblings)
            const preview  = ltrEl ? ltrEl.textContent.trim()
                                   : (secEl ? secEl.textContent.trim() : '');
            // Full container text for "You:" prefix detection
            const fullText = secEl ? secEl.textContent.trim() : preview;
            // Row-level aria-label (WA sets "You: msg" or "You sent" here)
            const ariaLbl  = (item.getAttribute('aria-label') || '').toLowerCase();

            if (!name || !preview) return;

            // Three independent youSent signals — any one is sufficient
            const youSent = /^You[\s:]/.test(fullText)          // text prefix
                         || hasSentTick(secEl)                   // tick icon
                         || /\byou[\s:]/i.test(ariaLbl);        // aria-label
            out[name] = { preview, youSent };
        });
        return out;
    }

    function cancelCandidate(name) {
        if (window.__waCandidates[name] !== undefined) {
            clearTimeout(window.__waCandidates[name]);
            delete window.__waCandidates[name];
        }
    }

    function scheduleCandidate(name, preview) {
        cancelCandidate(name);
        window.__waCandidates[name] = setTimeout(function() {
            delete window.__waCandidates[name];
            if (window.__waSendingTo === name) return;
            const current = readChats();
            const info = current[name];
            if (!info)                           return;  // contact gone from list
            if (info.youSent)                    return;  // Tayyab replied in time
            if (info.preview !== preview)        return;  // preview changed
            if (info.preview.includes(signature)) return;  // our own daemon reply
            if (SKIP_PREVIEW.test(info.preview)) return;
            if (GROUP_PREVIEW.test(info.preview)) return;
            window.__waPendingReplies.push({ name, preview });
        }, queueDelayMs);
    }

    function checkChanges() {
        const current = readChats();
        for (const [name, info] of Object.entries(current)) {
            if (name === window.__waSendingTo) continue;  // mid-send — skip only this contact
            const { preview, youSent } = info;
            if (!preview || SKIP_PREVIEW.test(preview)) continue;
            if (preview.includes(signature))             continue;
            if (GROUP_PREVIEW.test(preview))             continue;

            // Tayyab sent this message (tick icon detected OR "You:" prefix)
            if (youSent) {
                cancelCandidate(name);                        // kill any pending timer
                window.__waUserSentEvents.push({ name });     // trigger Python cooldown
                window.__waSnapshots[name] = preview;
                continue;
            }

            if (!(name in window.__waSnapshots)) {
                window.__waSnapshots[name] = preview;         // first sight — baseline
                continue;
            }
            if (window.__waSnapshots[name] !== preview) {
                window.__waSnapshots[name] = preview;
                scheduleCandidate(name, preview);
            }
        }
    }

    window.__waResetSnapshot = function() {
        const current = readChats();
        for (const [name, info] of Object.entries(current)) {
            window.__waSnapshots[name] = info.preview;
        }
    };

    const pane = document.querySelector('#pane-side')
               || document.querySelector('div[aria-label="Chat list"]');
    if (!pane) return 'no_pane';

    window.__waObserver = new MutationObserver(checkChanges);
    window.__waObserver.observe(pane, { childList: true, subtree: true, characterData: true });

    return 'ok';
})
"""


# ── Checkpoint helpers ─────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text())
        except Exception:
            pass
    return {}


def save_checkpoint(data: dict):
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(data, indent=2))


# ── AI reply ──────────────────────────────────────────────────────────────────

def generate_reply(sender_name: str, history: list) -> str:
    """
    Generate a contextual, conversational reply using per-contact message history.
    history: list of {"role": "user"|"assistant", "content": "..."}
             The latest incoming message is already the last entry.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — using fallback reply")
        return FALLBACK_REPLY

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[-MAX_HISTORY:])

    payload = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "max_tokens": 200,
        "temperature": 0.75,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())
            reply  = result["choices"][0]["message"]["content"].strip()
            if SIGNATURE not in reply:
                reply = f"{reply}\n\n{SIGNATURE}"
            return reply
    except Exception as e:
        logger.warning(f"OpenAI failed ({e}) — fallback reply")
        return FALLBACK_REPLY


# ── Daemon class ──────────────────────────────────────────────────────────────

class AutoReplyDaemon:

    def __init__(self, dry_run: bool = False):
        self.dry_run              = dry_run
        self.client               = None
        self.page                 = None
        self.checkpoint           = load_checkpoint()
        self.conversation_history = {}  # { name: [{"role": ..., "content": ...}] }
        self.user_active_chats    = {}  # { name: timestamp } — cooldown tracking
        self.running              = False

    # ── Cooldown helpers ──────────────────────────────────────────────────────

    def _is_user_active(self, name: str) -> bool:
        """True if Tayyab sent a message in this chat within COOLDOWN_MINUTES."""
        last_active = self.user_active_chats.get(name)
        if last_active is None:
            return False
        return (time.time() - last_active) / 60 < COOLDOWN_MINUTES

    def _mark_user_active(self, name: str):
        self.user_active_chats[name] = time.time()
        logger.info(
            f"👤 Tayyab active in '{name}' — auto-reply paused for {COOLDOWN_MINUTES} min"
        )

    # ── Browser connection ────────────────────────────────────────────────────

    def _connect(self):
        from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient, SEL

        if self.client:
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None

        logger.info("Connecting to WhatsApp Web…")
        self.client = WhatsAppWebClient(headless=True, slow_mo_ms=50)
        self.client.start()

        self.page = self.client.ensure_page()
        self.page.goto(
            "https://web.whatsapp.com",
            wait_until="domcontentloaded",
            timeout=60_000,
        )

        LOGGED_IN  = SEL["logged_in_check"]
        NOT_LOGGED = f'{SEL["qr_canvas"]}, {SEL["landing_title"]}'
        try:
            self.page.wait_for_selector(f"{LOGGED_IN}, {NOT_LOGGED}", timeout=60_000)
        except Exception:
            raise RuntimeError("WhatsApp Web timed out — run: python3 scripts/wa_setup.py")

        if not self.page.query_selector(LOGGED_IN):
            raise RuntimeError("Not logged in — run: python3 scripts/wa_setup.py")

        logger.info("WhatsApp Web connected ✓")

    def _inject_observer(self):
        result = self.page.evaluate(
            f"{_OBSERVER_JS}({json.dumps(SIGNATURE)}, {QUEUE_DELAY_MS})"
        )
        if result == "ok":
            logger.info("MutationObserver injected — watching for new messages in real-time")
        elif result == "already_active":
            logger.debug("Observer already active")
        else:
            logger.warning(f"Observer injection returned: {result!r}")
        return result

    def _observer_healthy(self) -> bool:
        try:
            return bool(self.page.evaluate("() => !!window.__waAutoReplyActive"))
        except Exception:
            return False

    def _get_pending(self) -> list:
        """Drain the JS pending-replies queue (confirmed incoming messages)."""
        try:
            return self.page.evaluate(
                "() => { const m = [...(window.__waPendingReplies||[])];"
                " window.__waPendingReplies = []; return m; }"
            )
        except Exception:
            return []

    def _get_user_sent_events(self) -> list:
        """Drain the JS user-sent-events queue (Tayyab's outgoing messages)."""
        try:
            return self.page.evaluate(
                "() => { const m = [...(window.__waUserSentEvents||[])];"
                " window.__waUserSentEvents = []; return m; }"
            )
        except Exception:
            return []

    def _get_live_preview(self, name: str):
        """
        Read the current chat-list preview for a contact directly from the DOM.
        Returns {'preview': str, 'youSent': bool} or None if contact not visible.
        Uses getComputedStyle for tick detection (headless-safe — offsetWidth is
        always 0 in headless Chromium and cannot be used).
        """
        try:
            return self.page.evaluate(
                """(contactName) => {
                    const items = document.querySelectorAll([
                        'div[aria-label="Chat list"] div[role="gridcell"][tabindex="0"]',
                        'div[role="gridcell"][tabindex="0"]',
                        '[data-testid="cell-frame-container"]'
                    ].join(','));
                    for (const item of items) {
                        const nameEl = item.querySelector(
                            'span[dir="auto"][title], [data-testid="cell-frame-title"]');
                        if (!nameEl) continue;
                        const n = (nameEl.getAttribute('title') || nameEl.textContent || '').trim();
                        if (n !== contactName) continue;
                        const secEl  = item.querySelector('[data-testid="cell-frame-secondary"]');
                        const ltrEl  = item.querySelector('span[dir="ltr"]');
                        const preview  = ltrEl ? ltrEl.textContent.trim()
                                               : (secEl ? secEl.textContent.trim() : '');
                        const fullText = secEl ? secEl.textContent.trim() : preview;
                        const tickIcons = ['msg-check','msg-dblcheck','msg-dblcheck-ack','msg-time'];
                        const hasTick = secEl && tickIcons.some(ic => {
                            const el = secEl.querySelector('[data-icon="' + ic + '"]');
                            if (!el) return false;
                            try {
                                const cs = window.getComputedStyle(el);
                                return cs.display    !== 'none'   &&
                                       cs.visibility !== 'hidden' &&
                                       cs.opacity    !== '0';
                            } catch(e) { return true; }
                        });
                        const ariaLbl = (item.getAttribute('aria-label') || '').toLowerCase();
                        const youSent = /^You[\\s:]/.test(fullText)
                                     || !!hasTick
                                     || /\\byou[\\s:]/i.test(ariaLbl);
                        return { preview, youSent };
                    }
                    return null;
                }""",
                name,
            )
        except Exception:
            return None

    def _last_msg_in_chat_is_ours(self, name: str):
        """
        Navigate into `name`'s chat, read the last message bubble's data-id.
        Returns:
            True  — Tayyab sent the last message (data-id starts with 'true_')
            False — the other person sent it (starts with 'false_')
            None  — couldn't determine (navigation failed / no messages)
        Always returns to the chat-list view before returning.

        This is the definitive sent/received signal — it reads from the actual
        message sync state, not the chat-list preview, so it works regardless of
        WhatsApp Web version, locale, or DOM structure changes.
        """
        try:
            # Click the contact's row in the chat list panel
            opened = self.page.evaluate(
                """(contactName) => {
                    const items = document.querySelectorAll([
                        'div[aria-label="Chat list"] div[role="gridcell"][tabindex="0"]',
                        'div[role="gridcell"][tabindex="0"]',
                        '[data-testid="cell-frame-container"]'
                    ].join(','));
                    for (const item of items) {
                        const nameEl = item.querySelector(
                            'span[dir="auto"][title], [data-testid="cell-frame-title"]');
                        if (!nameEl) continue;
                        const n = (nameEl.getAttribute('title') || nameEl.textContent || '').trim();
                        if (n !== contactName) continue;
                        item.click();
                        return true;
                    }
                    return false;
                }""",
                name,
            )
            if not opened:
                logger.debug(f"_last_msg: '{name}' not found in chat list")
                return None

            # Wait for at least one message bubble with a WA message ID to be present.
            # WA message data-id values always start with "true_" (we sent) or "false_"
            # (they sent).  Using attribute prefix selectors is more specific than the
            # broad div[data-id] which can match non-message elements.
            try:
                self.page.wait_for_selector(
                    '[data-id^="true_"], [data-id^="false_"]',
                    timeout=5_000,
                )
            except Exception:
                return None  # empty chat or timeout

            # Read the very last WA message element's data-id
            data_id: str | None = self.page.evaluate(
                """() => {
                    const msgs = document.querySelectorAll(
                        '[data-id^="true_"], [data-id^="false_"]');
                    if (!msgs.length) return null;
                    const last = msgs[msgs.length - 1];
                    return last.getAttribute('data-id');
                }"""
            )

            if not data_id:
                return None

            is_ours = data_id.startswith("true_")
            logger.debug(f"_last_msg '{name}': data-id={data_id[:20]}… → ours={is_ours}")
            return is_ours

        except Exception as e:
            logger.debug(f"_last_msg_in_chat_is_ours error for '{name}': {e}")
            return None
        finally:
            # Always escape back to the chat list before returning
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(400)
            except Exception:
                pass

    def _before_send(self, name: str):
        """
        Mark this specific contact as mid-send.
        The observer keeps running for all OTHER contacts during this time —
        their messages are still queued and will be processed next tick.
        Only the target contact is suppressed (to avoid false re-trigger from
        our own send navigating into their chat).
        """
        try:
            self.page.evaluate(
                "(n) => {"
                "  window.__waSendingTo = n;"
                # Remove any pending entries for this contact only (not others)
                "  window.__waPendingReplies = (window.__waPendingReplies||[])"
                "    .filter(m => m.name !== n);"
                "}",
                name,
            )
        except Exception:
            pass

    def _after_send(self):
        try:
            self.page.evaluate(
                "() => {"
                "  window.__waSendingTo = null;"
                "  if(window.__waResetSnapshot) window.__waResetSnapshot();"
                "}"
            )
        except Exception:
            pass

    def _return_to_chat_list(self):
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(400)
        except Exception:
            pass

    # ── Main daemon loop ──────────────────────────────────────────────────────

    def run(self):
        self.running = True

        def _stop(sig, frame):
            logger.info("Shutdown signal — stopping…")
            self.running = False

        signal.signal(signal.SIGINT,  _stop)
        signal.signal(signal.SIGTERM, _stop)

        retries = 0
        while self.running:
            try:
                self._connect()
                break
            except Exception as e:
                retries += 1
                wait = min(30 * retries, 120)
                logger.error(f"Connection failed: {e} — retry in {wait}s")
                time.sleep(wait)

        if not self.running:
            return

        self._inject_observer()

        logger.info(
            f"\n{'═'*57}\n"
            f"  WhatsApp Auto-Reply is LIVE  🟢\n"
            f"  Groups/channels: skipped\n"
            f"  JS grace period: {QUEUE_DELAY_MS // 1000}s before queuing\n"
            f"  User cooldown: {COOLDOWN_MINUTES} min after Tayyab sends\n"
            f"  Replies as: Tayyab's AI Employee\n"
            f"  Press Ctrl+C to stop\n"
            f"{'═'*57}"
        )

        print("SERVICE_READY")
        try:
            READY_FILE.write_text("ready")
        except Exception:
            pass

        last_health_check  = time.time()
        consecutive_errors = 0

        while self.running:
            try:
                # ── Observer health check every 60 s ──────────────────────────
                if time.time() - last_health_check > 60:
                    if not self._observer_healthy():
                        logger.info("Observer lost — re-injecting…")
                        self._inject_observer()
                    last_health_check = time.time()

                # ── Drain Tayyab's outgoing events (cooldown) ─────────────────
                for evt in self._get_user_sent_events():
                    name = (evt.get("name") or "").strip()
                    if name:
                        self._mark_user_active(name)

                # ── Drain confirmed incoming messages ─────────────────────────
                pending = self._get_pending()
                for msg in pending:
                    name    = (msg.get("name") or "").strip()
                    preview = (msg.get("preview") or "").strip()

                    if not name or not preview:
                        continue

                    # Cooldown: Tayyab is actively in this chat
                    if self._is_user_active(name):
                        logger.info(
                            f"⏸  Skipping '{name}' — Tayyab active "
                            f"(cooldown {COOLDOWN_MINUTES} min)"
                        )
                        continue

                    # Dedup: same preview already replied to
                    if self.checkpoint.get(name) == preview:
                        logger.debug(f"Already replied to '{name}' — skip")
                        continue

                    # ── Pre-send live check ───────────────────────────────────
                    # Re-read the DOM right now. If Tayyab has replied since the
                    # 2-second JS delay fired, abort before we even generate text.
                    # youSent is detected via tick icons — reliable across locales.
                    live = self._get_live_preview(name)
                    if live is not None:
                        if live.get("youSent"):
                            logger.info(
                                f"⚡ Abort '{name}' — Tayyab already replied "
                                f"(tick detected, preview: '{live.get('preview','')[:50]}')"
                            )
                            self._mark_user_active(name)
                            # Roll back the user message we added to history
                            if self.conversation_history.get(name):
                                self.conversation_history[name].pop()
                            continue
                        if SIGNATURE in live.get("preview", ""):
                            logger.debug(f"⚡ Abort '{name}' — our own reply is newest")
                            if self.conversation_history.get(name):
                                self.conversation_history[name].pop()
                            continue

                    logger.info(f"🔔 New message from '{name}': {preview[:80]}")

                    # Build / extend conversation history
                    if name not in self.conversation_history:
                        self.conversation_history[name] = []
                    self.conversation_history[name].append(
                        {"role": "user", "content": preview}
                    )

                    reply = generate_reply(name, self.conversation_history[name])

                    if self.dry_run:
                        print(f"\n[DRY-RUN] From: {name}\nMsg: {preview}\nReply: {reply}\n")
                        self.conversation_history[name].append(
                            {"role": "assistant", "content": reply}
                        )
                        self.checkpoint[name] = preview
                        save_checkpoint(self.checkpoint)
                        continue

                    # ── Definitive pre-send check (chat-view data-id) ─────────
                    # Navigate into the chat and read the last message bubble's
                    # data-id attribute.  true_* = Tayyab sent it → abort.
                    # This is authoritative (actual sync state, not preview text)
                    # and works regardless of WA Web version or locale.
                    self._before_send(name)  # pause observer for this contact first

                    last_is_ours = self._last_msg_in_chat_is_ours(name)
                    if last_is_ours is True:
                        logger.info(
                            f"⚡ Abort '{name}' — last message is Tayyab's "
                            f"(data-id: true_*)"
                        )
                        self._mark_user_active(name)
                        self._after_send()
                        if self.conversation_history.get(name):
                            self.conversation_history[name].pop()
                        continue

                    success = self.client.send_message(to=name, text=reply)

                    self._return_to_chat_list()
                    self.page.wait_for_timeout(1_500)
                    self._after_send()

                    if success:
                        logger.info(f"✓ Replied to '{name}'")
                        self.conversation_history[name].append(
                            {"role": "assistant", "content": reply}
                        )
                        self.checkpoint[name] = preview
                        save_checkpoint(self.checkpoint)
                    else:
                        logger.warning(f"✗ send_message failed for '{name}'")
                        # Roll back the user message we added (send didn't go through)
                        if self.conversation_history.get(name):
                            self.conversation_history[name].pop()

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Loop error ({consecutive_errors}): {e}")
                if consecutive_errors >= 5:
                    logger.warning("5 consecutive errors — reconnecting…")
                    try:
                        self._connect()
                        self._inject_observer()
                        consecutive_errors = 0
                    except Exception as re_err:
                        logger.error(f"Reconnect failed: {re_err} — waiting 60 s")
                        time.sleep(60)
                        continue

            if not self.running:
                break

            time.sleep(CHECK_INTERVAL)

        if self.client:
            try:
                self.client.stop()
            except Exception:
                pass
        READY_FILE.unlink(missing_ok=True)
        logger.info("Auto-reply daemon stopped.")

    def run_once(self):
        self._connect()
        self._inject_observer()
        time.sleep(3)
        pending = self._get_pending()
        logger.info(f"Pending replies: {len(pending)}")
        for msg in pending:
            name, preview = msg.get("name", ""), msg.get("preview", "")
            logger.info(f"  {name}: {preview[:60]}")
        self.client.stop()
        return len(pending)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="WhatsApp Auto-Reply Daemon — real-time, MutationObserver-based")
    parser.add_argument("--once",    action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset",   action="store_true",
                        help="Clear checkpoint (replied-message memory)")
    args = parser.parse_args()

    if args.reset:
        if CHECKPOINT_PATH.exists():
            CHECKPOINT_PATH.unlink()
        print("✓ Checkpoint cleared.")
        return

    daemon = AutoReplyDaemon(dry_run=args.dry_run)

    if args.once:
        daemon.run_once()
        return

    daemon.run()


if __name__ == "__main__":
    main()
