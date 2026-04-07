#!/usr/bin/env python3
"""
wa_setup.py — WhatsApp Web pairing & session setup.

Run this ONCE to link your WhatsApp account. Session is then saved and reused
by the watcher and MCP server automatically.

Usage:
    python3 scripts/wa_setup.py                   # headed mode (QR scan)
    python3 scripts/wa_setup.py --headless         # requires Xvfb / DISPLAY
    python3 scripts/wa_setup.py --phone +923001234567  # phone number pairing
    python3 scripts/wa_setup.py --status           # check existing session
    python3 scripts/wa_setup.py --reset            # delete session and re-pair

Session saved to:
    .secrets/whatsapp_session/   (if .secrets/ exists — gitignored)
    or ~/.ai_operator/whatsapp_session/   (default)

WSL quick-start:
    # Install deps first (one-time):
    pip install playwright
    playwright install chromium
    playwright install-deps chromium   # install system libraries

    # If headed mode in WSL:
    Xvfb :0 -screen 0 1280x720x24 &
    export DISPLAY=:0

    # Run pairing:
    python3 scripts/wa_setup.py
"""

import sys
import os
import json
import argparse
import time
from pathlib import Path
from datetime import datetime, timezone

# Ensure src/ is on path
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))


def _banner(msg: str):
    print(f"\n{'─'*60}")
    print(f"  {msg}")
    print('─'*60)


def _check_playwright():
    """Verify Playwright is installed and chromium browser is available."""
    try:
        from playwright.sync_api import sync_playwright  # noqa
        print("✓ Playwright Python package installed")
    except ImportError:
        print("✗ Playwright not installed")
        print("  Run: pip install playwright")
        sys.exit(1)

    import subprocess
    result = subprocess.run(
        ["playwright", "install", "--dry-run", "chromium"],
        capture_output=True, text=True
    )
    if "chromium" in result.stdout.lower() or result.returncode == 0:
        print("✓ Chromium browser available")
    else:
        print("⚠  Chromium may not be installed")
        print("  Run: playwright install chromium")
        print("  Run: playwright install-deps chromium   # for system libs (WSL)")


def _get_session_dir() -> Path:
    from ai_operator.core.whatsapp_web_helper import _default_session_dir
    return _default_session_dir()


def _write_meta(session_dir: Path, status: str, extra: dict = None):
    """Write .secrets/whatsapp_session_meta.json with connection status."""
    meta_path = REPO_ROOT / ".secrets" / "whatsapp_session_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "status": status,
        "session_dir": str(session_dir),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {})
    }
    meta_path.write_text(json.dumps(data, indent=2))
    print(f"  Meta written: {meta_path}")


def cmd_status(args):
    """Check current WhatsApp session status."""
    _banner("WhatsApp Session Status")

    # Check meta file
    meta_path = REPO_ROOT / ".secrets" / "whatsapp_session_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        print(f"  Last status  : {meta.get('status', 'unknown')}")
        print(f"  Session dir  : {meta.get('session_dir')}")
        print(f"  Updated at   : {meta.get('updated_at')}")
    else:
        print("  No session meta found")

    # Quick live check
    session_dir = _get_session_dir()
    if not session_dir.exists():
        print(f"  Session dir  : {session_dir} (not found)")
        print("\n  → Run: python3 scripts/wa_setup.py  to pair your device")
        return

    print(f"\n  Checking live session (headless) …")
    try:
        from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient
        client = WhatsAppWebClient(session_dir=session_dir, headless=True)
        client.start()
        logged_in = client.is_logged_in()
        client.stop()

        if logged_in:
            print("  ✓ Session active — WhatsApp Web is logged in")
            _write_meta(session_dir, "connected")
        else:
            print("  ✗ Session exists but not logged in — re-run pairing")
            _write_meta(session_dir, "disconnected")
    except Exception as e:
        print(f"  ✗ Live check failed: {e}")
        print("  Is Playwright installed? Run: pip install playwright && playwright install chromium")


def cmd_pair(args):
    """Run the pairing flow (QR scan or phone number)."""
    _banner("WhatsApp Web Pairing")
    _check_playwright()

    session_dir = _get_session_dir()
    headless = args.headless
    phone = args.phone

    if headless and not os.environ.get("DISPLAY") and not phone:
        print("⚠  Headless mode requested but DISPLAY not set.")
        print("   For headless pairing in WSL you need either:")
        print("   1) Xvfb: run  Xvfb :0 -screen 0 1280x720x24 &  export DISPLAY=:0")
        print("   2) Phone number pairing: --phone +923001234567 --headless")

    print(f"\n  Session dir : {session_dir}")
    print(f"  Headless    : {headless}")
    print(f"  Phone mode  : {bool(phone)}")

    if phone:
        print(f"\n  PHONE PAIRING FLOW")
        print(f"  1. Browser will open WhatsApp Web")
        print(f"  2. We'll click 'Link with phone number'")
        print(f"  3. Enter phone: {phone}")
        print(f"  4. A pairing code will appear on screen")
        print(f"  5. Open WhatsApp on your phone → Linked Devices → Link a Device → enter code")
    else:
        print(f"\n  QR PAIRING FLOW")
        print(f"  1. Browser will open WhatsApp Web")
        print(f"  2. A QR code appears — scan it with WhatsApp on your phone")
        print(f"  3. WhatsApp → Linked Devices → Link a Device → scan QR")

    print(f"\n  Starting browser …\n")

    try:
        from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient

        if phone:
            os.environ["WHATSAPP_PHONE"] = phone

        client = WhatsAppWebClient(
            session_dir=session_dir,
            headless=headless,
            slow_mo_ms=300,
        )
        client.start()

        # Navigate to WhatsApp Web
        client.open_whatsapp()
        time.sleep(2)

        # Check if already logged in
        if client.is_logged_in():
            print("✓ Already logged in! Session is valid.")
            _write_meta(session_dir, "connected")
            client.stop()
            return

        # Phone number pairing
        if phone:
            print("  Initiating phone number pairing …")
            code = client.get_pairing_code()
            if code:
                _banner(f"PAIRING CODE:  {code}")
                print("  Open WhatsApp on your phone:")
                print("  → Settings → Linked Devices → Link a Device → enter code above")
                print(f"\n  Waiting for login (up to 120 s) …")
            else:
                print("  Could not get pairing code. Check if 'Link with phone number' is visible.")
                print("  Falling back to QR mode — please scan the QR code on screen.")
        else:
            print("  Please scan the QR code in the browser window …")

        # Wait for login
        success = client.wait_for_login(timeout_s=120)

        if success:
            print("\n✓ WhatsApp Web paired successfully!")
            print(f"  Session saved: {session_dir}")
            _write_meta(session_dir, "connected", {"paired_at": datetime.now(timezone.utc).isoformat()})

            # Quick test
            print("\n  Testing session — listing chats …")
            time.sleep(2)
            try:
                chats = client.list_chats(limit=3)
                print(f"  ✓ Found {len(chats)} chats — session is working")
                for c in chats[:3]:
                    print(f"    · {c['name']} (unread: {c['unread_count']})")
            except Exception as e:
                print(f"  ⚠  Chat listing test failed: {e}")
        else:
            print("\n✗ Pairing timed out. Please try again.")
            _write_meta(session_dir, "pairing_failed")

        client.stop()

    except Exception as e:
        print(f"\n✗ Pairing failed: {e}")
        import traceback
        traceback.print_exc()
        _write_meta(session_dir, "error")
        sys.exit(1)

    print("\nNext steps:")
    print("  1. Run watcher (real mode): python3 scripts/whatsapp_watcher_skill.py --mode real")
    print("  2. Start MCP server:       python3 mcp_servers/whatsapp_mcp/server.py")
    print("  3. Check status:           python3 scripts/wa_setup.py --status")


def cmd_reset(args):
    """Delete existing session and re-pair."""
    session_dir = _get_session_dir()
    _banner("Reset WhatsApp Session")

    if not session_dir.exists():
        print(f"  No session found at {session_dir}")
        return

    import shutil
    print(f"  Deleting session: {session_dir}")
    shutil.rmtree(session_dir, ignore_errors=True)

    meta_path = REPO_ROOT / ".secrets" / "whatsapp_session_meta.json"
    if meta_path.exists():
        meta_path.unlink()

    print("  ✓ Session deleted")
    print("\n  Re-run pairing: python3 scripts/wa_setup.py")


def main():
    parser = argparse.ArgumentParser(
        description="WhatsApp Web pairing & session setup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--status", action="store_true", help="Check existing session status")
    parser.add_argument("--reset", action="store_true", help="Delete session and re-pair")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser headless (needs DISPLAY or Xvfb for QR mode)")
    parser.add_argument("--phone", type=str, default="",
                        help="Phone number for pairing code flow (e.g. +923001234567)")

    args = parser.parse_args()

    if args.status:
        cmd_status(args)
    elif args.reset:
        cmd_reset(args)
    else:
        cmd_pair(args)


if __name__ == "__main__":
    main()
