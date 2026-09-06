"""
test_whatsapp_tools.py — Phase 4 tool-registry layer: the confirm_required
round trip that voice_ws.py's pending-confirmation handler relies on.

Pattern under test (see whatsapp_tools.py's module docstring): the first
call to wa_send_text/wa_send_file/wa_reply resolves the contact/file and
returns ToolResult(error="confirm_required", data={tool, params, prompt}).
voice_ws.py then re-invokes the SAME tool with that exact params dict on
"yes" — the second call must skip resolution and actually send, using only
what's already embedded in params (never re-resolving).

All tests patch get_default_command_handler with a fake handler so no
sidecar, network, or registry singleton state leaks between tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.integrations.whatsapp.wa_command_handler import WAOutcome
from api.tools import whatsapp_tools as wt
from api.tools.registry import registry


@pytest.fixture
def fake_handler(monkeypatch):
    h = MagicMock()
    monkeypatch.setattr(wt, "get_default_command_handler", lambda: h)
    return h


class TestWaSendTextConfirmRoundTrip:
    def test_first_call_returns_confirm_required_and_does_not_send(self, fake_handler):
        fake_handler.plan_send_text.return_value = (
            WAOutcome(
                ok=False, needs_confirmation=True,
                spoken='Send "hi" to Tayyab Aziz?', text="prompt",
                data={"resolved_chat_id": "923001234567@s.whatsapp.net",
                      "resolved_display_name": "Tayyab Aziz",
                      "contact_matched_by": "identity_cache",
                      "contact_resolution_method": "cached",
                      "latency_ms": {"contact_resolution_ms": 0.4, "planning_ms": 0.6}},
            ),
            None,
        )
        result = registry.execute("wa_send_text", {"contact": "Tayyab", "message": "hi"})
        assert result.error == "confirm_required"
        assert result.data["tool"] == "wa_send_text"
        assert result.data["params"]["_wa_confirmed"] is True
        assert result.data["params"]["_resolved_chat_id"] == "923001234567@s.whatsapp.net"
        # Validation B's report reads these two top-level fields directly.
        assert result.data["matched_by"] == "identity_cache"
        assert result.data["latency_ms"]["contact_resolution_ms"] == 0.4
        fake_handler.execute_send_text.assert_not_called()

    def test_confirmed_call_sends_without_re_resolving(self, fake_handler):
        fake_handler.execute_send_text.return_value = (
            WAOutcome(ok=True, spoken="Sent it to Tayyab Aziz.", text="sent",
                      data={"message_id": "MSG1", "deduped": False, "latency_ms": {}}),
            None,
        )
        confirmed_params = {
            "contact": "Tayyab", "message": "hi", "show_ui": False,
            "_wa_confirmed": True,
            "_resolved_chat_id": "923001234567@s.whatsapp.net",
            "_resolved_display_name": "Tayyab Aziz",
            "_idempotency_key": "wa_send_text:923001234567@s.whatsapp.net:abc123",
        }
        result = registry.execute("wa_send_text", confirmed_params)
        assert result.success is True
        assert result.spoken == "Sent it to Tayyab Aziz."
        fake_handler.plan_send_text.assert_not_called()
        fake_handler.execute_send_text.assert_called_once_with(
            "923001234567@s.whatsapp.net", "Tayyab Aziz", "hi",
            "wa_send_text:923001234567@s.whatsapp.net:abc123", show_ui=False,
        )

    def test_ambiguous_contact_never_confirms(self, fake_handler):
        fake_handler.plan_send_text.return_value = (
            WAOutcome(ok=False, ambiguous=True, spoken="Which Ali?", text="ambiguous",
                      candidates=[{"display_name": "Ali A"}, {"display_name": "Ali B"}]),
            None,
        )
        result = registry.execute("wa_send_text", {"contact": "Ali", "message": "hi"})
        assert result.error != "confirm_required"
        assert result.success is False
        assert len(result.data["candidates"]) == 2

    def test_missing_contact_is_a_clean_failure(self, fake_handler):
        result = registry.execute("wa_send_text", {"message": "hi"})
        assert result.success is False
        fake_handler.plan_send_text.assert_not_called()


class TestWaSendFileConfirmRoundTrip:
    def test_first_call_embeds_full_plan_for_second_call(self, fake_handler):
        fake_plan = MagicMock(
            file_path="/tmp/x.pdf", filename="x.pdf", mime_type="application/pdf",
            size_bytes=123, media_kind="document", send_method="send_file",
            chat_id="923001234567@s.whatsapp.net", contact_name="Tayyab Aziz",
            action_id="action-1",
        )
        fake_handler.plan_send_file.return_value = (
            WAOutcome(ok=False, needs_confirmation=True, spoken="Send x.pdf to Tayyab Aziz?",
                      text="prompt", data={"latency_ms": {}}),
            None, fake_plan,
        )
        result = registry.execute("wa_send_file", {"contact": "Tayyab", "file_ref": {"kind": "filename", "name": "x.pdf"}})
        assert result.error == "confirm_required"
        p = result.data["params"]
        assert p["_wa_confirmed"] is True
        assert p["_file_path"] == "/tmp/x.pdf"
        assert p["_resolved_chat_id"] == "923001234567@s.whatsapp.net"
        assert p["_action_id"] == "action-1"
        fake_handler.execute_send_file.assert_not_called()

    def test_confirmed_call_executes_without_replanning(self, fake_handler):
        fake_handler.execute_send_file.return_value = (
            WAOutcome(ok=True, spoken="Sent it to Tayyab Aziz.", text="sent",
                      data={"message_id": "MSG2", "deduped": False, "latency_ms": {}}),
            None,
        )
        confirmed_params = {
            "contact": "Tayyab", "file_ref": {"kind": "filename", "name": "x.pdf"}, "show_ui": False,
            "_wa_confirmed": True,
            "_file_path": "/tmp/x.pdf", "_filename": "x.pdf", "_mime_type": "application/pdf",
            "_size_bytes": 123, "_media_kind": "document", "_send_method": "send_file",
            "_resolved_chat_id": "923001234567@s.whatsapp.net", "_resolved_display_name": "Tayyab Aziz",
            "_action_id": "action-1",
        }
        result = registry.execute("wa_send_file", confirmed_params)
        assert result.success is True
        fake_handler.plan_send_file.assert_not_called()
        fake_handler.execute_send_file.assert_called_once()


class TestWaReplyConfirmRoundTrip:
    def test_first_call_embeds_message_id(self, fake_handler):
        fake_handler.plan_reply.return_value = (
            WAOutcome(ok=False, needs_confirmation=True, spoken='Reply "ok" to Tayyab Aziz?', text="prompt",
                      data={"resolved_chat_id": "923001234567@s.whatsapp.net",
                            "resolved_display_name": "Tayyab Aziz",
                            "resolved_message_id": "ORIGMSG", "latency_ms": {}}),
            None,
        )
        result = registry.execute("wa_reply", {"contact": "him", "message": "ok"})
        assert result.error == "confirm_required"
        assert result.data["params"]["_resolved_message_id"] == "ORIGMSG"
        fake_handler.execute_reply.assert_not_called()

    def test_reply_declined_no_message_id_never_confirms(self, fake_handler):
        fake_handler.plan_reply.return_value = (
            WAOutcome(ok=False, spoken="I don't have a recent message from him to reply to.", text="no message"),
            None,
        )
        result = registry.execute("wa_reply", {"contact": "him", "message": "ok"})
        assert result.error != "confirm_required"
        assert result.success is False


class TestWaShowChatBareOpenNoContact:
    def test_empty_contact_dispatches_to_open_whatsapp(self, fake_handler):
        fake_handler.open_whatsapp.return_value = (
            WAOutcome(ok=True, spoken="WhatsApp is open.", text="reused tab", data={"latency_ms": {}}),
            None,
        )
        result = registry.execute("wa_show_chat", {"contact": ""})
        assert result.success is True
        assert result.spoken == "WhatsApp is open."
        fake_handler.open_whatsapp.assert_called_once()
        fake_handler.show_chat.assert_not_called()

    def test_missing_contact_key_also_dispatches_to_open_whatsapp(self, fake_handler):
        fake_handler.open_whatsapp.return_value = (
            WAOutcome(ok=True, spoken="WhatsApp is open.", text="reused tab", data={"latency_ms": {}}),
            None,
        )
        result = registry.execute("wa_show_chat", {})
        assert result.success is True
        fake_handler.open_whatsapp.assert_called_once()


class TestWaShowChatNeverConfirms:
    def test_show_chat_executes_immediately(self, fake_handler):
        fake_handler.show_chat.return_value = (
            WAOutcome(ok=True, spoken="Tayyab Aziz's chat is open.", text="ok", data={"latency_ms": {}}),
            None,
        )
        result = registry.execute("wa_show_chat", {"contact": "Tayyab"})
        assert result.error != "confirm_required"
        assert result.success is True
        fake_handler.plan_send_text.assert_not_called()

    def test_show_chat_never_touches_send_or_reply(self, fake_handler):
        fake_handler.show_chat.return_value = (
            WAOutcome(ok=True, spoken="ok", text="ok", data={"latency_ms": {}}), None,
        )
        registry.execute("wa_show_chat", {"contact": "Tayyab"})
        fake_handler.execute_send_text.assert_not_called()
        fake_handler.execute_send_file.assert_not_called()
        fake_handler.execute_reply.assert_not_called()


class TestWaGetMessagesNeverConfirms:
    def test_get_messages_executes_immediately(self, fake_handler):
        fake_handler.get_messages.return_value = (
            WAOutcome(ok=True, spoken="Tayyab said: hi", text="ok",
                      data={"messages": [{"text": "hi"}], "latency_ms": {}}),
            None,
        )
        result = registry.execute("wa_get_messages", {"contact": "Tayyab"})
        assert result.error != "confirm_required"
        assert result.success is True


class TestRiskLevels:
    def test_send_tools_are_high_risk(self):
        assert registry.get_risk("wa_send_text") == "high"
        assert registry.get_risk("wa_send_file") == "high"
        assert registry.get_risk("wa_reply") == "high"

    def test_view_tools_are_low_risk(self):
        assert registry.get_risk("wa_show_chat") == "low"
        assert registry.get_risk("wa_get_messages") == "low"


class TestUnconfiguredHandlerFailsCleanly:
    def test_none_handler_returns_clean_failure_not_crash(self, monkeypatch):
        monkeypatch.setattr(wt, "get_default_command_handler", lambda: None)
        result = registry.execute("wa_send_text", {"contact": "Tayyab", "message": "hi"})
        assert result.success is False
        assert result.error != "confirm_required"
