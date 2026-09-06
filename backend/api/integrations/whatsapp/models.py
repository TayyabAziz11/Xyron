"""
models.py — normalized request/result shapes for the WhatsApp transport layer.

Nothing above this module (MCP handlers, future agents) should ever see a raw
open-wa response. Every call crosses this boundary as WhatsAppRequest /
WhatsAppResult so the underlying automation engine (open-wa today) can be
replaced later without touching call sites.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class WAAction(str, Enum):
    SEND_TEXT = "send_text"
    SEND_FILE = "send_file"
    SEND_IMAGE = "send_image"
    REPLY = "reply"


class WAErrorCode(str, Enum):
    SIDECAR_UNAVAILABLE = "sidecar_unavailable"
    SIDECAR_TIMEOUT = "sidecar_timeout"
    SESSION_NOT_AUTHENTICATED = "session_not_authenticated"
    WHATSAPP_DISCONNECTED = "whatsapp_disconnected"
    MALFORMED_RESPONSE = "malformed_response"
    INVALID_REQUEST = "invalid_request"
    FILE_NOT_FOUND = "file_not_found"
    CONTACT_NOT_FOUND = "contact_not_found"
    OPENWA_ERROR = "openwa_error"  # kept for backward compat with OpenWATransport tests
    PROVIDER_ERROR = "provider_error"  # provider-neutral replacement for OPENWA_ERROR
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"
    UNKNOWN = "unknown"


@dataclass
class WhatsAppRequest:
    action: WAAction
    recipient: str                          # chat_id or E.164 number — already resolved by the caller
    content: Optional[str] = None           # text body / caption
    attachment: Optional[str] = None        # absolute local file path (send_file / send_image)
    reply_to_message_id: Optional[str] = None
    idempotency_key: Optional[str] = None   # caller-generated, stable across retries of the SAME action


@dataclass
class WhatsAppResult:
    success: bool
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    error_code: Optional[WAErrorCode] = None
    error_message: Optional[str] = None
    deduped: bool = False
