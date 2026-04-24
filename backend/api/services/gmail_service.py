"""
Gmail Service — read inbox and send emails via Gmail API (OAuth2).

Setup (one-time):
  1. Go to https://console.cloud.google.com → APIs & Services → Credentials
  2. Create an OAuth 2.0 Client ID (Desktop app)
  3. Download credentials.json → save to ~/.ai-operator/gmail_credentials.json
  4. First call will open browser for OAuth consent → token saved automatically

Required scopes: gmail.readonly + gmail.send
"""
from __future__ import annotations

import base64
import email as _email_lib
import logging
import re
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_AI_DIR      = Path.home() / ".ai-operator"
_CREDS_FILE  = _AI_DIR / "gmail_credentials.json"
_TOKEN_FILE  = _AI_DIR / "gmail_token.json"

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _get_service():
    """Return an authenticated Gmail API service object."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds: Optional[Credentials] = None
    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif _CREDS_FILE.exists():
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_FILE), _SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            raise RuntimeError(
                f"Gmail credentials not found at {_CREDS_FILE}. "
                "Download OAuth credentials.json from Google Cloud Console and save it there."
            )
        _AI_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def is_configured() -> bool:
    """Return True if Gmail credentials or token are present."""
    return _CREDS_FILE.exists() or _TOKEN_FILE.exists()


def read_inbox(max_results: int = 5) -> list[dict]:
    """Return the latest unread emails as dicts with from/subject/snippet."""
    svc = _get_service()
    results = svc.users().messages().list(
        userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results,
    ).execute()

    messages = results.get("messages", [])
    emails: list[dict] = []

    for msg in messages:
        full = svc.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                          metadataHeaders=["From", "Subject", "Date"]).execute()
        headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        snippet = full.get("snippet", "")
        # Clean snippet
        snippet = re.sub(r'\s+', ' ', snippet).strip()[:200]

        sender = headers.get("From", "Unknown")
        # Strip email address from "Name <email>" format for cleaner speech
        name_only = re.sub(r'\s*<[^>]+>', '', sender).strip() or sender

        emails.append({
            "id":      msg["id"],
            "from":    name_only,
            "subject": headers.get("Subject", "(no subject)"),
            "date":    headers.get("Date", ""),
            "snippet": snippet,
        })

    return emails


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success."""
    svc = _get_service()
    msg = MIMEText(body)
    msg["to"]      = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return True


def build_inbox_spoken(emails: list[dict]) -> str:
    """Format inbox list as a natural spoken summary."""
    if not emails:
        return "Your inbox is clear — no unread emails right now."
    count = len(emails)
    intro = f"You have {count} unread email{'s' if count > 1 else ''}. "
    parts: list[str] = []
    for i, e in enumerate(emails):
        lead = ["First", "Second", "Third", "Fourth", "Fifth"][i] if i < 5 else "Next"
        parts.append(f"{lead}, from {e['from']}: {e['subject']}.")
    return intro + " ".join(parts)
