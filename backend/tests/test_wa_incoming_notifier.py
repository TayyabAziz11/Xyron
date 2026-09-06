"""
test_wa_incoming_notifier.py — the guard rails around the WhatsApp
incoming-message notifier: never react to own sends, never announce
groups or non-text messages, coalesce a burst via debounce, always record
context (for "him"/"her" resolution) even when no voice session is live to
hear the announcement, and never send anything on its own (this replaced
the earlier auto-reply design — the notifier only speaks and records
context, it never calls execute_reply/execute_send_text).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from api.services.wa_incoming_notifier import WAIncomingNotifier


def _svc() -> WAIncomingNotifier:
    s = WAIncomingNotifier()
    s._transport = MagicMock()
    return s


def _incoming(**overrides) -> dict:
    base = {
        "message_id": "MSG1", "chat_id": "923001234567@s.whatsapp.net",
        "sender_id": "923001234567@s.whatsapp.net", "sender_name": "Qasim",
        "message_type": "text", "text": "hey, you there?", "is_group": False,
        "from_me": False, "timestamp": "2026-09-03T00:00:00.000Z",
    }
    base.update(overrides)
    return base


class TestNeverReactsToOwnSends:
    def test_from_me_true_never_buffers(self):
        svc = _svc()
        svc._on_incoming(_incoming(from_me=True))
        assert svc._pending_buffer == {}


class TestGroupAndNonTextAreSkipped:
    def test_group_message_never_buffers(self):
        svc = _svc()
        svc._on_incoming(_incoming(is_group=True))
        assert svc._pending_buffer == {}

    def test_non_text_message_never_buffers(self):
        svc = _svc()
        svc._on_incoming(_incoming(message_type="image", text=None))
        assert svc._pending_buffer == {}

    def test_empty_text_never_buffers(self):
        svc = _svc()
        svc._on_incoming(_incoming(text="   "))
        assert svc._pending_buffer == {}

    def test_missing_chat_id_never_buffers(self):
        svc = _svc()
        svc._on_incoming(_incoming(chat_id=None))
        assert svc._pending_buffer == {}


class TestDebounceCoalescing:
    def test_burst_of_two_messages_coalesces_into_one_flush(self):
        svc = _svc()
        with patch("threading.Timer") as timer_cls:
            fake_timer = MagicMock()
            timer_cls.return_value = fake_timer

            svc._on_incoming(_incoming(message_id="M1", text="hey"))
            svc._on_incoming(_incoming(message_id="M2", text="you there?"))

            timer_cls.assert_called_once()
            chat_id = "923001234567@s.whatsapp.net"
            assert [i["text"] for i in svc._pending_buffer[chat_id]] == ["hey", "you there?"]

    def test_flush_with_no_buffered_items_is_a_noop(self):
        svc = _svc()
        with patch.object(WAIncomingNotifier, "_handle_notification") as handle:
            svc._flush_chat("nonexistent_chat")
        handle.assert_not_called()

    def test_single_message_flush_calls_handle_notification_once(self):
        svc = _svc()
        with patch("threading.Timer") as timer_cls, \
             patch.object(WAIncomingNotifier, "_handle_notification") as handle:
            fake_timer = MagicMock()

            def _start():
                svc._flush_chat("923001234567@s.whatsapp.net")
            fake_timer.start = _start
            timer_cls.return_value = fake_timer

            svc._on_incoming(_incoming(message_id="M1", text="hi"))

        handle.assert_called_once()
        chat_id, items = handle.call_args.args
        assert chat_id == "923001234567@s.whatsapp.net"
        assert len(items) == 1


class TestHandleNotificationRecordsContextAndAnnounces:
    def test_records_interaction_with_action_received_and_incoming_message_id(self):
        svc = _svc()
        fake_ctx = MagicMock()
        items = [{"text": "hi", "message_id": "MSG1", "sender_name": "Qasim"}]

        with patch(
            "api.integrations.whatsapp.wa_context.get_default_context",
            return_value=fake_ctx,
        ), patch("api.services.voice_announcer.announce", return_value=True):
            svc._handle_notification("923001234567@s.whatsapp.net", items)

        fake_ctx.record_interaction.assert_called_once_with(
            chat_id="923001234567@s.whatsapp.net", display_name="Qasim",
            action="received", message_id="MSG1",
        )

    def test_context_recorded_even_when_no_voice_session_is_active(self):
        # announce() returning False (no live session) must not stop
        # context recording — "him" must still resolve if the user talks
        # to Xyron later, after reading the message on their phone.
        svc = _svc()
        fake_ctx = MagicMock()
        items = [{"text": "hi", "message_id": "MSG1", "sender_name": "Qasim"}]

        with patch(
            "api.integrations.whatsapp.wa_context.get_default_context",
            return_value=fake_ctx,
        ), patch("api.services.voice_announcer.announce", return_value=False):
            svc._handle_notification("923001234567@s.whatsapp.net", items)

        fake_ctx.record_interaction.assert_called_once()

    def test_announce_called_with_readable_single_message_text(self):
        svc = _svc()
        items = [{"text": "I'm coming", "message_id": "MSG1", "sender_name": "Qasim"}]

        with patch("api.integrations.whatsapp.wa_context.get_default_context", return_value=MagicMock()), \
             patch("api.services.voice_announcer.announce", return_value=True) as ann:
            svc._handle_notification("C1", items)

        payload = ann.call_args.args[0]
        assert payload["text"] == 'Qasim sent you a message on WhatsApp: "I\'m coming"'

    def test_announce_called_with_combined_text_for_a_burst(self):
        svc = _svc()
        items = [
            {"text": "hey", "message_id": "M1", "sender_name": "Qasim"},
            {"text": "you there?", "message_id": "M2", "sender_name": "Qasim"},
        ]
        with patch("api.integrations.whatsapp.wa_context.get_default_context", return_value=MagicMock()), \
             patch("api.services.voice_announcer.announce", return_value=True) as ann:
            svc._handle_notification("C1", items)

        payload = ann.call_args.args[0]
        assert "2 messages" in payload["text"]
        assert "you there?" in payload["text"]

    def test_never_calls_any_send_or_reply_method(self):
        # The notifier must never itself send/reply — that's the whole
        # point of replacing the old auto-reply design.
        svc = _svc()
        items = [{"text": "hi", "message_id": "MSG1", "sender_name": "Qasim"}]
        fake_handler = MagicMock()

        with patch("api.integrations.whatsapp.wa_context.get_default_context", return_value=MagicMock()), \
             patch("api.services.voice_announcer.announce", return_value=True), \
             patch(
                 "api.integrations.whatsapp.wa_command_handler.get_default_command_handler",
                 return_value=fake_handler,
             ):
            svc._handle_notification("C1", items)

        fake_handler.execute_reply.assert_not_called()
        fake_handler.execute_send_text.assert_not_called()

    def test_context_recording_failure_does_not_block_announcement(self):
        svc = _svc()
        items = [{"text": "hi", "message_id": "MSG1", "sender_name": "Qasim"}]
        fake_ctx = MagicMock()
        fake_ctx.record_interaction.side_effect = RuntimeError("disk full")

        with patch(
            "api.integrations.whatsapp.wa_context.get_default_context",
            return_value=fake_ctx,
        ), patch("api.services.voice_announcer.announce", return_value=True) as ann:
            svc._handle_notification("C1", items)

        ann.assert_called_once()


class TestStartGating:
    def test_start_noop_when_disabled(self, monkeypatch):
        import api.config as config_mod
        monkeypatch.setattr(config_mod.settings, "wa_incoming_announce_enabled", False)
        svc = WAIncomingNotifier()
        svc.start()
        assert svc._running is False

    def test_start_noop_when_sidecar_key_missing(self, monkeypatch):
        import api.config as config_mod
        monkeypatch.setattr(config_mod.settings, "wa_incoming_announce_enabled", True)
        monkeypatch.setattr(config_mod.settings, "wa_sidecar_api_key", "")
        svc = WAIncomingNotifier()
        svc.start()
        assert svc._running is False

    def test_start_subscribes_when_fully_configured(self, monkeypatch):
        import api.config as config_mod
        monkeypatch.setattr(config_mod.settings, "wa_incoming_announce_enabled", True)
        monkeypatch.setattr(config_mod.settings, "wa_sidecar_api_key", "shared-secret")
        fake_transport = MagicMock()
        with patch(
            "api.integrations.whatsapp.baileys_transport.BaileysTransport.from_settings",
            return_value=fake_transport,
        ):
            svc = WAIncomingNotifier()
            svc.start()
        assert svc._running is True
        fake_transport.subscribe_messages.assert_called_once_with(svc._on_incoming)

    def test_start_is_idempotent(self, monkeypatch):
        import api.config as config_mod
        monkeypatch.setattr(config_mod.settings, "wa_incoming_announce_enabled", True)
        monkeypatch.setattr(config_mod.settings, "wa_sidecar_api_key", "shared-secret")
        fake_transport = MagicMock()
        with patch(
            "api.integrations.whatsapp.baileys_transport.BaileysTransport.from_settings",
            return_value=fake_transport,
        ):
            svc = WAIncomingNotifier()
            svc.start()
            svc.start()
        fake_transport.subscribe_messages.assert_called_once()

    def test_stop_calls_stop_subscription(self):
        svc = _svc()
        svc._running = True
        svc.stop()
        svc._transport.stop_subscription.assert_called_once()
        assert svc._running is False
