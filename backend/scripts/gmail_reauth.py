#!/usr/bin/env python3
"""
gmail_reauth.py — Re-authenticate Gmail OAuth2 and save a long-lived refresh token.

Usage:
    python3 scripts/gmail_reauth.py

Works in WSL2: opens the auth URL in your Windows browser automatically.
The resulting token will never expire as long as you keep using it.
"""

import os
import sys
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SECRETS_DIR = REPO_ROOT / ".secrets"
CREDENTIALS_FILE = SECRETS_DIR / "gmail_credentials.json"
TOKEN_FILE = SECRETS_DIR / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def open_in_windows_browser(url: str):
    """Open URL in Windows browser from WSL2."""
    # Try PowerShell first (most reliable in WSL2)
    try:
        subprocess.run(
            ["powershell.exe", "-Command", f"Start-Process '{url}'"],
            check=True, capture_output=True
        )
        print("  ✓ Opened in Windows browser via PowerShell")
        return
    except Exception:
        pass

    # Fallback: cmd.exe
    try:
        subprocess.run(
            ["cmd.exe", "/c", "start", url],
            check=True, capture_output=True
        )
        print("  ✓ Opened in Windows browser via cmd.exe")
        return
    except Exception:
        pass

    # Fallback: xdg-open (native Linux)
    try:
        subprocess.Popen(["xdg-open", url])
        print("  ✓ Opened via xdg-open")
        return
    except Exception:
        pass

    print(f"\n  Could not auto-open browser. Please open this URL manually:\n\n  {url}\n")


def main():
    print()
    print("=" * 70)
    print("  GMAIL RE-AUTHENTICATION")
    print("=" * 70)

    # Check google libraries
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("\n  ERROR: Gmail API libraries not installed.")
        print("  Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    if not CREDENTIALS_FILE.exists():
        print(f"\n  ERROR: credentials file not found at:\n  {CREDENTIALS_FILE}")
        print("\n  Steps to fix:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. APIs & Services → Credentials → Create OAuth 2.0 Client ID (Desktop)")
        print("  3. Download JSON → save as .secrets/gmail_credentials.json")
        sys.exit(1)

    # Remove old invalid token
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print("\n  Removed old expired token.")

    print("\n  Starting OAuth2 flow...")
    print("  Your Windows browser will open automatically.")
    print("  Sign in with your Google account and grant all permissions.")
    print()

    # Use InstalledAppFlow with offline access for long-lived refresh token
    # prompt=consent forces Google to issue a new refresh token every time
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
    )

    # Override the browser opener to use Windows browser in WSL2
    import webbrowser
    import threading

    original_open = webbrowser.open

    def wsl_open(url, new=0, autoraise=True):
        open_in_windows_browser(url)
        return True

    webbrowser.open = wsl_open

    # Run local server — listens for the OAuth callback
    creds = flow.run_local_server(
        port=0,                    # OS picks a free port
        access_type="offline",     # mandatory for refresh token
        prompt="consent",          # forces Google to issue a new refresh token
        open_browser=True,
    )

    # Save token
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())

    print()
    print("=" * 70)
    print("  ✓ GMAIL AUTHENTICATION SUCCESSFUL")
    print("=" * 70)

    # Verify by getting profile
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "unknown")
        print(f"  Authenticated as: {email}")
    except Exception as e:
        print(f"  (Could not fetch profile: {e})")

    print(f"  Token saved to: {TOKEN_FILE}")
    print()
    print("  This token uses an offline refresh token and will auto-renew")
    print("  indefinitely as long as your Google account is active.")
    print()
    print("  IMPORTANT: If Google revokes it again, it means your OAuth app")
    print("  is in 'Testing' mode. Fix it permanently:")
    print("  Google Cloud Console → OAuth consent screen → Publishing status")
    print("  → 'Publish App' (or add your email as a Test User)")
    print("=" * 70)


if __name__ == "__main__":
    main()
