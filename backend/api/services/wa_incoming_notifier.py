"""
wa_incoming_notifier.py — announce incoming WhatsApp messages by voice and
set up "him"/"her" contact carryover so a follow-up voice command like
"message him back saying I'm coming" resolves to the right contact,
without Xyron ever answering on its own.

This REPLACES the earlier wa_auto_reply_service.py design (auto-answer via
LLM). The user explicitly chose manual-in-the-loop instead: hear what came
in, then decide what to say, spoken through the normal WhatsApp voice
pipeline (wa_intent.py "message him ..." / "reply to him ..." → the same
wa_send_text/wa_reply tools and confirm_required round trip as any other
voice command).

Subscribes to BaileysTransport's live SSE message stream
(`subscribe_messages`, in baileys_transport.py). For every genuine
incoming text message:
  1. Records it into WhatsAppContext as the most recent interaction
     (action="received", message_id=<incoming id>) — this is what makes
     "him"/"her" resolve to this sender afterward (wa_context.py's
     resolve_contact_reference always resolves pronouns against the most
     recent interaction regardless of direction, and wa_command_handler's
     plan_reply/plan_send_text already call resolve_contact() → the
     contextual branch first). Recorded unconditionally, whether or not a
     voice session is active, so replying still works if the user checks
     WhatsApp on their phone first and talks to Xyron afterward.
  2. Announces it out loud via voice_announcer.announce() — delivered only
     when a voice session is actually connected (announce() returns False
     otherwise, and this service does nothing further in that case: no
     queued backlog of things to say later, since a stale "3 messages ago"
     announcement on your next session would be confusing rather than
     helpful).

Safety/UX guards — the SSE plumbing has none of these on its own:
  - never reacts to from_me=True events. The sidecar emits the SAME
    event_type ("whatsapp.message") for the user's own outgoing sends
    (typed, voice-commanded, or sent from their phone) as it does for
    genuine incoming ones.
  - group chats are skipped entirely.
  - only text messages are announced — media-only messages have no text
    to read aloud (the WhatsApp UI itself is the right place to view them).
  - a short per-chat debounce window coalesces a burst of quick messages
    into ONE announcement that mentions all of them, instead of talking
    over itself with several separate announcements in a row.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger("wa_incoming_notifier")

_DEBOUNCE_S = 3.0        # coalesce messages arriving this close together


class WAIncomingNotifier:
    """Singleton — mirrors the start()/stop()/daemon-thread shape used by
    proactive_service.py and screen_context_service.py, except the work is
    driven by the SSE push stream instead of a polling tick."""

    def __init__(self) -> None:
        self._transport = None
        self._running = False
        self._pending_buffer: Dict[str, List[Dict[str, Any]]] = {}
        self._pending_timers: Dict[str, threading.Timer] = {}
        self._buffer_lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return
        from ..config import settings
        if not (settings.wa_incoming_announce_enabled and settings.wa_sidecar_api_key):
            logger.info("[WA_NOTIFIER] not starting — disabled or sidecar not configured")
            return
        try:
            from ..integrations.whatsapp.baileys_transport import BaileysTransport
            self._transport = BaileysTransport.from_settings()
            self._transport.subscribe_messages(self._on_incoming)
            self._running = True
            logger.info("[WA_NOTIFIER] subscribed to incoming WhatsApp messages")
        except Exception as e:
            logger.warning(f"[WA_NOTIFIER] failed to start: {e}")

    def stop(self) -> None:
        if self._transport is not None:
            try:
                self._transport.stop_subscription()
            except Exception:
                pass
        self._running = False

    # ── SSE callback (runs on the transport's SSE consumer thread) ────────

    def _on_incoming(self, data: Dict[str, Any]) -> None:
        try:
            if data.get("from_me"):
                return
            if data.get("is_group"):
                return
            if data.get("message_type") != "text":
                return
            text = (data.get("text") or "").strip()
            chat_id = data.get("chat_id")
            if not text or not chat_id:
                return

            item = {
                "text": text, "message_id": data.get("message_id"),
                "sender_name": data.get("sender_name"),
            }
            new_timer = None
            with self._buffer_lock:
                self._pending_buffer.setdefault(chat_id, []).append(item)
                if chat_id not in self._pending_timers:
                    new_timer = threading.Timer(_DEBOUNCE_S, self._flush_chat, args=(chat_id,))
                    new_timer.daemon = True
                    self._pending_timers[chat_id] = new_timer
            # start() outside the lock — _flush_chat re-acquires the same
            # (non-reentrant) lock, and must never be able to run while
            # this thread still holds it.
            if new_timer is not None:
                new_timer.start()
        except Exception as e:
            logger.warning(f"[WA_NOTIFIER] on_incoming error: {e}")

    def _flush_chat(self, chat_id: str) -> None:
        with self._buffer_lock:
            items = self._pending_buffer.pop(chat_id, [])
            self._pending_timers.pop(chat_id, None)
        if not items:
            return
        self._handle_notification(chat_id, items)

    def _handle_notification(self, chat_id: str, items: List[Dict[str, Any]]) -> None:
        latest = items[-1]
        message_id = latest.get("message_id")
        sender_name = latest.get("sender_name") or items[0].get("sender_name") or "Someone"

        # 1. Record context FIRST, unconditionally — "him"/"her" must
        # resolve to this sender even if no voice session is listening
        # right now to hear the spoken announcement.
        try:
            from ..integrations.whatsapp.wa_context import get_default_context
            get_default_context().record_interaction(
                chat_id=chat_id, display_name=sender_name,
                action="received", message_id=message_id,
            )
        except Exception as e:
            logger.warning(f"[WA_NOTIFIER] failed to record context for {chat_id}: {e}")

        # 2. Announce — only actually speaks if a voice session is live.
        announcement = self._build_announcement(sender_name, items)
        try:
            from .voice_announcer import announce
            delivered = announce({"text": announcement, "chat_id": chat_id, "sender_name": sender_name})
        except Exception as e:
            logger.warning(f"[WA_NOTIFIER] announce failed for {chat_id}: {e}")
            delivered = False

        logger.info(
            f"[WA_NOTIFIER] {sender_name} ({chat_id}): {len(items)} msg(s) "
            f"— announced={delivered}: {announcement!r}"
        )

    def _build_announcement(self, sender_name: str, items: List[Dict[str, Any]]) -> str:
        if len(items) == 1:
            preview = items[0]["text"][:200]
            return f'{sender_name} sent you a message on WhatsApp: "{preview}"'
        preview = items[-1]["text"][:200]
        return (
            f"{sender_name} sent you {len(items)} messages on WhatsApp. "
            f'The latest says, "{preview}"'
        )


wa_incoming_notifier = WAIncomingNotifier()
