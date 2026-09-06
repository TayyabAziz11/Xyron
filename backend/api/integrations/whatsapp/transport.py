"""
transport.py — WhatsAppTransport: the only interface the rest of Xyron may
depend on for WhatsApp I/O.

Deliberately thin (9 methods, no business logic). A transport implementation
may expose lookup primitives (find_contact, get_messages) but must NEVER
decide which match is correct or whether an action is authorized — contact
disambiguation and approval stay above this layer (existing object/travel
resolvers + the approval-gate convention already used by whatsapp_mcp).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from .models import WhatsAppRequest, WhatsAppResult


class WhatsAppTransport(ABC):
    @abstractmethod
    def healthcheck(self) -> Dict[str, Any]:
        """Return sidecar/session status. Must never raise — return a structured error dict."""

    @abstractmethod
    def send_text(self, request: WhatsAppRequest) -> WhatsAppResult:
        ...

    @abstractmethod
    def send_file(self, request: WhatsAppRequest) -> WhatsAppResult:
        ...

    @abstractmethod
    def send_image(self, request: WhatsAppRequest) -> WhatsAppResult:
        ...

    @abstractmethod
    def reply(self, request: WhatsAppRequest) -> WhatsAppResult:
        """request.reply_to_message_id is required."""

    @abstractmethod
    def get_messages(self, limit: int = 10, unread_only: bool = True) -> List[Dict[str, Any]]:
        """Perception only. Returns [] on failure — never raises."""

    @abstractmethod
    def find_contact(self, query: str) -> List[Dict[str, Any]]:
        """Returns candidate matches. Disambiguation among them is the caller's job."""

    @abstractmethod
    def verify_on_whatsapp(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Authoritative phone-number → WhatsApp-identity check (Baileys usync).
        Returns {exists: bool, jid: str|None, phone: str} — the jid may be an
        @lid identity (LID migration), which is why callers must NOT assume
        <digits>@s.whatsapp.net. Returns None on failure — never raises.
        """

    @abstractmethod
    def mark_read(self, chat_id: str) -> bool:
        ...

    @abstractmethod
    def subscribe_messages(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Step-1 scaffold only. Establishes the inbound-event mechanism but is
        not called from anywhere in this phase — no autonomous routing yet.
        """

    @abstractmethod
    def download_media(self, chat_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Download media for a specific message. Returns normalized dict with
        local_path on success, or None on failure. Never raises.
        The caller must NOT control the destination path — transport decides.
        """

    @abstractmethod
    def get_latest_media(
        self, chat_id: Optional[str] = None, sender_id: Optional[str] = None,
        media_type: Optional[str] = None, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Query latest media messages. Returns [] on failure — never raises.
        Will power future 'that image' / 'his document' resolution.
        """

    @abstractmethod
    def get_media_message(self, chat_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up a specific media message by exact ID. Returns None if not found.
        """
