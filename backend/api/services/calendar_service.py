"""
Google Calendar Service — list upcoming events and create events.

Setup (one-time, shares the same Google Cloud project as Gmail):
  1. Enable Google Calendar API in your Google Cloud project
  2. Use the same OAuth credentials file at ~/.ai-operator/gmail_credentials.json
     (Calendar scope is added automatically; you may need to re-authenticate)

If you already went through Gmail OAuth, run this once to add the Calendar scope:
  Delete ~/.ai-operator/gmail_token.json and re-authenticate via Gmail.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_AI_DIR     = Path.home() / ".ai-operator"
_CREDS_FILE = _AI_DIR / "gmail_credentials.json"
_TOKEN_FILE = _AI_DIR / "calendar_token.json"

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_service():
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
                f"Calendar credentials not found at {_CREDS_FILE}. "
                "See gmail_service.py setup instructions."
            )
        _AI_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def is_configured() -> bool:
    return _CREDS_FILE.exists() or _TOKEN_FILE.exists()


def list_events(days_ahead: int = 1) -> list[dict]:
    """Return upcoming events within the next N days."""
    svc = _get_service()
    now    = datetime.now(timezone.utc)
    end    = now + timedelta(days=days_ahead)
    result = svc.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=end.isoformat(),
        maxResults=10,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events: list[dict] = []
    for ev in result.get("items", []):
        start = ev["start"].get("dateTime") or ev["start"].get("date", "")
        summary = ev.get("summary", "(no title)")
        location = ev.get("location", "")
        events.append({"summary": summary, "start": start, "location": location})
    return events


def create_event(summary: str, start_iso: str, duration_minutes: int = 60,
                 description: str = "") -> dict:
    """Create a calendar event. start_iso should be ISO 8601 with timezone."""
    svc = _get_service()
    start_dt = datetime.fromisoformat(start_iso)
    end_dt   = start_dt + timedelta(minutes=duration_minutes)
    event = {
        "summary":     summary,
        "description": description,
        "start":       {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end":         {"dateTime": end_dt.isoformat(),   "timeZone": "UTC"},
    }
    created = svc.events().insert(calendarId="primary", body=event).execute()
    return created


def build_events_spoken(events: list[dict], label: str = "today") -> str:
    """Format event list as a natural spoken summary."""
    if not events:
        return f"You have nothing on your calendar {label}."
    count = len(events)
    intro = f"You have {count} event{'s' if count > 1 else ''} {label}. "
    parts: list[str] = []
    for ev in events:
        start_str = ev.get("start", "")
        # Parse time for speech
        try:
            dt = datetime.fromisoformat(start_str)
            time_str = dt.strftime("%-I:%M %p") if hasattr(dt, "hour") else start_str
        except Exception:
            time_str = start_str
        loc = f" at {ev['location']}" if ev.get("location") else ""
        parts.append(f"{ev['summary']} at {time_str}{loc}.")
    return intro + " ".join(parts)
