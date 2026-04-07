#!/usr/bin/env python3
"""
wa_live_chats.py — Fetch live WhatsApp chat list as JSON.

Outputs a single JSON object to stdout:
  { "error": null, "chats": [...] }
  { "error": "not_logged_in", "reason": "qr_required|session_timeout", "chats": [] }

Waits for either the chat list (logged in) or QR/landing page (not logged in),
whichever appears first.  Debug info is always written to stderr.
"""

import sys
import json
from pathlib import Path

_REPO = Path(__file__).parent.parent
_SRC  = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient, SEL

    # slow_mo=0: no artificial delays needed for a read-only fetch
    client = WhatsAppWebClient(headless=True, slow_mo_ms=0)
    client.start()

    page = client.ensure_page()

    # domcontentloaded is enough — WA is an SPA; waiting for 'load' can hang
    # on slow networks because WA keeps firing background XHRs indefinitely.
    page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60_000)

    # Build a combined selector so we react to whichever element appears first:
    #   • logged_in_check  → chat list visible, session is active
    #   • qr_canvas / landing_title → WA is asking for re-auth
    LOGGED_IN  = SEL["logged_in_check"]
    NOT_LOGGED = f'{SEL["qr_canvas"]}, {SEL["landing_title"]}'
    COMBINED   = f"{LOGGED_IN}, {NOT_LOGGED}"

    detected = "timeout"
    try:
        page.wait_for_selector(COMBINED, timeout=60_000)
        if page.query_selector(LOGGED_IN):
            detected = "logged_in"
        elif page.query_selector(NOT_LOGGED):
            detected = "qr"
    except Exception:
        detected = "timeout"

    # Always emit debug info so the caller (or human) can see what WA showed
    try:
        _url   = page.url
        _title = page.title()
    except Exception:
        _url, _title = "unknown", "unknown"
    print(f"[wa_live_chats] url={_url!r} title={_title!r} detected={detected}", file=sys.stderr)

    if detected != "logged_in":
        reason = "qr_required" if detected == "qr" else "session_timeout"
        if detected == "qr":
            print("[wa_live_chats] QR code visible — run: python3 scripts/wa_setup.py", file=sys.stderr)
        print(json.dumps({"error": "not_logged_in", "reason": reason, "chats": []}))
        client.stop()
        sys.exit(0)

    # list_chats() handles its own waits + 2 s virtual-scroll settle internally
    chats = client.list_chats(limit=30)
    client.stop()

    print(json.dumps({"error": None, "chats": chats}))

except ImportError as e:
    print(json.dumps({"error": f"import_error: {e}", "chats": []}))
    sys.exit(1)
except Exception as e:
    print(json.dumps({"error": str(e), "chats": []}))
    sys.exit(1)
