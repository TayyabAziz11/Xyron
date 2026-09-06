"""
test_wa_command_handler.py — Phase 4 orchestration layer + tool-registry
confirm_required round trip.

Hermetic: transport, identity store, context, and UI adapter are all
injected fakes — no sidecar, no network, no real Chrome/Windows calls.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api.integrations.whatsapp.contact_resolver import ContactResolution
from api.integrations.whatsapp.file_send import FileCandidate, FileSendPlan
from api.integrations.whatsapp.models import WhatsAppResult
from api.integrations.whatsapp.wa_command_handler import WACommandHandler
from api.integrations.whatsapp.wa_context import WhatsAppContext
from api.integrations.whatsapp.wa_identity import WhatsAppIdentityStore
from api.integrations.whatsapp.wa_ui_adapter import UIActionReport


@pytest.fixture
def safe_tmp(tmp_path_factory):
    base = Path(r"E:\Xyron\backend\data\_test_temp")
    base.mkdir(exist_ok=True)
    d = base / f"wach_{time.time_ns()}"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _mock_transport():
    t = MagicMock()
    t.find_contact = MagicMock(return_value=[])
    t.verify_on_whatsapp = MagicMock(return_value=None)
    t.send_text = MagicMock(return_value=WhatsAppResult(success=True, message_id="MSG1", chat_id="923001234567@s.whatsapp.net"))
    t.reply = MagicMock(return_value=WhatsAppResult(success=True, message_id="MSG2", chat_id="923001234567@s.whatsapp.net"))
    t.get_messages = MagicMock(return_value=[])
    return t


def _mock_ui_adapter(ok=True):
    ui = MagicMock()
    ui.open_chat = MagicMock(return_value=UIActionReport(action="open_chat", ok=ok, detail="stub"))
    return ui


def _handler(safe_tmp, transport=None, prime_identity=True):
    identity_path = safe_tmp / "identities.json"
    context_path = safe_tmp / "context.json"
    identities = WhatsAppIdentityStore(path=identity_path)
    context = WhatsAppContext(path=context_path)
    transport = transport or _mock_transport()
    h = WACommandHandler(
        transport=transport, identity_store=identities, context=context,
        ui_adapter=_mock_ui_adapter(),
    )
    return h, transport, identities, context


class TestContactResolutionPriority:
    def test_cached_identity_never_touches_transport(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        resolved, status, _ = h.resolve_contact("Tayyab")
        assert status == "cached"
        assert resolved.chat_id == "923001234567@s.whatsapp.net"
        transport.find_contact.assert_not_called()
        transport.verify_on_whatsapp.assert_not_called()

    def test_contextual_pronoun_resolves_from_context(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        context.record_interaction(chat_id="923001112222@s.whatsapp.net", display_name="Ali", action="send_text")
        resolved, status, _ = h.resolve_contact("him")
        assert status == "contextual"
        assert resolved.chat_id == "923001112222@s.whatsapp.net"
        transport.find_contact.assert_not_called()

    def test_unknown_name_falls_through_to_network_resolver(self, safe_tmp):
        transport = _mock_transport()
        transport.find_contact = MagicMock(return_value=[
            {"chat_id": "923005556666@s.whatsapp.net", "display_name": "Sara Malik"},
        ])
        h, transport, identities, context = _handler(safe_tmp, transport=transport)
        resolved, status, _ = h.resolve_contact("Sara Malik")
        assert status == "resolved_network"
        transport.find_contact.assert_called_once()

    def test_high_confidence_network_resolution_gets_learned(self, safe_tmp):
        transport = _mock_transport()
        transport.find_contact = MagicMock(return_value=[
            {"chat_id": "923005556666@s.whatsapp.net", "display_name": "Sara Malik"},
        ])
        h, transport, identities, context = _handler(safe_tmp, transport=transport)
        h.resolve_contact("Sara Malik")
        assert identities.resolve_cached("Sara Malik") is not None
        # Second lookup must now be cached — no second network call.
        resolved, status, _ = h.resolve_contact("Sara Malik")
        assert status == "cached"
        assert transport.find_contact.call_count == 1

    def test_ambiguous_contact(self, safe_tmp):
        transport = _mock_transport()
        transport.find_contact = MagicMock(return_value=[
            {"chat_id": "1@s.whatsapp.net", "display_name": "Ali A"},
            {"chat_id": "2@s.whatsapp.net", "display_name": "Ali B"},
        ])
        h, transport, identities, context = _handler(safe_tmp, transport=transport)
        resolved, status, raw = h.resolve_contact("Ali")
        assert status == "ambiguous"
        assert resolved is None
        assert len(raw.candidates) == 2

    def test_not_found_contact(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        resolved, status, _ = h.resolve_contact("Nobody Known")
        assert status == "not_found"
        assert resolved is None


class TestSendTextPlanExecute:
    def test_plan_needs_confirmation(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, timer = h.plan_send_text("Tayyab", "I'm outside")
        assert outcome.needs_confirmation is True
        assert outcome.data["resolved_chat_id"] == "923001234567@s.whatsapp.net"
        transport.send_text.assert_not_called()

    def test_plan_does_not_send(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        h.plan_send_text("Tayyab", "I'm outside")
        transport.send_text.assert_not_called()

    def test_execute_sends_and_records_context(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, timer = h.execute_send_text(
            "923001234567@s.whatsapp.net", "Tayyab Aziz", "I'm outside", "idem-1",
        )
        assert outcome.ok is True
        transport.send_text.assert_called_once()
        last = context.last_interaction()
        assert last is not None and last.message_id == "MSG1"

    def test_latency_fields_present(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, _ = h.plan_send_text("Tayyab", "hi")
        assert "contact_resolution_ms" in outcome.data["latency_ms"]
        assert "planning_ms" in outcome.data["latency_ms"]

        outcome2, _ = h.execute_send_text("923001234567@s.whatsapp.net", "Tayyab Aziz", "hi", "idem-2")
        assert "transport_ms" in outcome2.data["latency_ms"]

    def test_ui_surfaced_only_after_successful_send(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, _ = h.execute_send_text(
            "923001234567@s.whatsapp.net", "Tayyab Aziz", "hi", "idem-3", show_ui=True,
        )
        assert outcome.ok is True
        h._ui().open_chat.assert_called_once()  # noqa: SLF001 — deliberate white-box check

    def test_ui_not_surfaced_when_send_fails(self, safe_tmp):
        transport = _mock_transport()
        transport.send_text = MagicMock(return_value=WhatsAppResult(success=False, error_message="boom"))
        h, transport, identities, context = _handler(safe_tmp, transport=transport)
        outcome, _ = h.execute_send_text(
            "923001234567@s.whatsapp.net", "Tayyab Aziz", "hi", "idem-4", show_ui=True,
        )
        assert outcome.ok is False
        h._ui().open_chat.assert_not_called()  # noqa: SLF001


class TestOpenWhatsappNoContact:
    """Bare 'open whatsapp' — no identity lookup, no transport, just the
    UI adapter's app-root surface (reuses an already-open, logged-in tab)."""

    def test_open_whatsapp_calls_ui_adapter_open_whatsapp(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        ui = h._ui()  # noqa: SLF001
        ui.open_whatsapp.return_value = UIActionReport(action="open_whatsapp", ok=True, detail="reused tab")
        outcome, _ = h.open_whatsapp()
        assert outcome.ok is True
        assert outcome.spoken == "WhatsApp is open."
        ui.open_whatsapp.assert_called_once()
        ui.open_chat.assert_not_called()

    def test_open_whatsapp_never_touches_transport_or_identity(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        h._ui().open_whatsapp.return_value = UIActionReport(action="open_whatsapp", ok=True, detail="ok")
        h.open_whatsapp()
        transport.find_contact.assert_not_called()
        transport.send_text.assert_not_called()

    def test_open_whatsapp_failure_has_clean_spoken_message(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        h._ui().open_whatsapp.return_value = UIActionReport(action="open_whatsapp", ok=False, detail="tab not found")
        outcome, _ = h.open_whatsapp()
        assert outcome.ok is False
        assert outcome.spoken == "I couldn't open WhatsApp."


class TestShowChatNeverSends:
    def test_show_chat_never_calls_send_text(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, _ = h.show_chat("Tayyab")
        assert outcome.ok is True
        transport.send_text.assert_not_called()
        transport.reply.assert_not_called()

    def test_show_chat_exposes_diagnostic_fields_for_live_validation(self, safe_tmp):
        # These fields back the Phase 4 live-validation report (JID,
        # matched_by, tab-reuse) — developer-only, never in `spoken`.
        h, transport, identities, context = _handler(safe_tmp)
        outcome, _ = h.show_chat("Tayyab")
        assert outcome.data["resolved_chat_id"] == "923001234567@s.whatsapp.net"
        assert outcome.data["contact_matched_by"] == "identity_cache"
        assert outcome.data["contact_resolution_method"] == "cached"
        assert "cdp_tab_reused" in outcome.data
        assert "923001234567" not in outcome.spoken  # JID never leaks into user-facing text

    def test_show_chat_ambiguous(self, safe_tmp):
        transport = _mock_transport()
        transport.find_contact = MagicMock(return_value=[
            {"chat_id": "1@s.whatsapp.net", "display_name": "Ali A"},
            {"chat_id": "2@s.whatsapp.net", "display_name": "Ali B"},
        ])
        h, transport, identities, context = _handler(safe_tmp, transport=transport)
        outcome, _ = h.show_chat("Ali")
        assert outcome.ambiguous is True


class TestShowChatNeverRequiresBaileysConfig:
    """
    Regression test for the Phase 4 sidecar-coupling fix: show_chat must
    work purely from the identity cache + UI adapter, with zero dependency
    on wa_sidecar_api_key being configured — WACommandHandler must not
    eagerly construct BaileysTransport in __init__ (it did originally,
    which made show_chat fail if Baileys wasn't set up, despite never
    calling a transport method).
    """

    def test_show_chat_works_with_no_transport_configured(self, safe_tmp, monkeypatch):
        import api.config as config_mod
        monkeypatch.setattr(config_mod.settings, "wa_sidecar_api_key", "", raising=False)

        identities = WhatsAppIdentityStore(path=safe_tmp / "identities.json")
        context = WhatsAppContext(path=safe_tmp / "context.json")
        h = WACommandHandler(
            transport=None,  # deliberately NOT injected — force from_settings() lazily
            identity_store=identities, context=context, ui_adapter=_mock_ui_adapter(),
        )
        outcome, _ = h.show_chat("Tayyab")
        assert outcome.ok is True
        assert h._transport_obj is None  # noqa: SLF001 — never constructed

    def test_send_text_fails_cleanly_without_configured_transport(self, safe_tmp, monkeypatch):
        import api.config as config_mod
        monkeypatch.setattr(config_mod.settings, "wa_sidecar_api_key", "", raising=False)

        identities = WhatsAppIdentityStore(path=safe_tmp / "identities.json")
        context = WhatsAppContext(path=safe_tmp / "context.json")
        h = WACommandHandler(
            transport=None, identity_store=identities, context=context,
            ui_adapter=_mock_ui_adapter(),
        )
        outcome, _ = h.execute_send_text("923001234567@s.whatsapp.net", "Tayyab Aziz", "hi", "idem-x")
        assert outcome.ok is False  # clean failure, not a raised exception


class TestReplyRequiresContextMessageId:
    def test_reply_without_recent_message_declines(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, _ = h.plan_reply("Tayyab", "on my way")
        assert outcome.ok is False
        assert outcome.needs_confirmation is False
        transport.reply.assert_not_called()

    def test_reply_via_contextual_pronoun_has_message_id(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        context.record_interaction(
            chat_id="923001234567@s.whatsapp.net", display_name="Tayyab Aziz",
            action="send_text", message_id="ORIGMSG",
        )
        outcome, _ = h.plan_reply("him", "on my way")
        assert outcome.needs_confirmation is True
        assert outcome.data["resolved_message_id"] == "ORIGMSG"

    def test_execute_reply_sends(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        outcome, _ = h.execute_reply(
            "923001234567@s.whatsapp.net", "Tayyab Aziz", "on my way", "ORIGMSG", "idem-r1",
        )
        assert outcome.ok is True
        transport.reply.assert_called_once()


class TestGetMessagesVoiceResponse:
    """Phase 5 §11 — concise, natural spoken phrasing for 'what did X say'."""

    def test_single_message_uses_said_quote_phrasing(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        transport.get_messages = lambda limit, unread_only: [
            {"chat_id": "923001234567@s.whatsapp.net", "text": "I'm on my way.", "from_me": 0},
        ]
        outcome, _ = h.get_messages("Tayyab")
        assert outcome.spoken == 'Tayyab Aziz said, "I\'m on my way."'

    def test_multiple_inbound_messages_uses_count_phrasing(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        transport.get_messages = lambda limit, unread_only: [
            {"chat_id": "923001234567@s.whatsapp.net", "text": "call me", "from_me": 0},
            {"chat_id": "923001234567@s.whatsapp.net", "text": "are you there", "from_me": 0},
            {"chat_id": "923001234567@s.whatsapp.net", "text": "hey", "from_me": 0},
        ]
        outcome, _ = h.get_messages("Tayyab")
        assert outcome.spoken == 'Tayyab Aziz sent 3 messages. The latest says, "call me"'

    def test_own_outgoing_messages_not_counted_as_multiple(self, safe_tmp):
        # Two outgoing (from_me=1) + one inbound must NOT say "sent 3 messages".
        h, transport, identities, context = _handler(safe_tmp)
        transport.get_messages = lambda limit, unread_only: [
            {"chat_id": "923001234567@s.whatsapp.net", "text": "on my way", "from_me": 0},
            {"chat_id": "923001234567@s.whatsapp.net", "text": "ok see you then", "from_me": 1},
            {"chat_id": "923001234567@s.whatsapp.net", "text": "sure", "from_me": 1},
        ]
        outcome, _ = h.get_messages("Tayyab")
        assert "sent 3 messages" not in outcome.spoken
        assert outcome.spoken == 'Tayyab Aziz said, "on my way"'

    def test_no_messages_short_phrasing(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        transport.get_messages = lambda limit, unread_only: []
        outcome, _ = h.get_messages("Tayyab")
        assert outcome.spoken == "No recent messages from Tayyab Aziz."

    def test_never_reads_provider_metadata_in_spoken_response(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        transport.get_messages = lambda limit, unread_only: [
            {"chat_id": "923001234567@s.whatsapp.net", "text": "hi", "from_me": 0,
             "message_id": "3EB0INTERNAL", "timestamp": "2026-09-02T11:00:34.000Z"},
        ]
        outcome, _ = h.get_messages("Tayyab")
        assert "3EB0INTERNAL" not in outcome.spoken
        assert "923001234567" not in outcome.spoken
        assert "2026-09-02" not in outcome.spoken


class TestPlanSendFileAmbiguityRouting:
    """Real bug fix: plan_send_file() used to treat EVERY
    status=='needs_clarification' plan as a contact-ambiguity, calling
    _ambiguous_outcome(contact_ref, plan.contact_resolution). That's wrong
    when the CONTACT resolved fine and it's the FILE name/query that's
    ambiguous (2+ matches) — plan.contact_resolution in that case is a
    single resolved contact with candidates=[], so the old code produced a
    broken response ('I found more than one contact matching resume.pdf —
    . Which one did you mean?') with an empty, comma-joined name list.
    """

    def _resolved_contact(self) -> ContactResolution:
        return ContactResolution(
            status="resolved", chat_id="923001234567@s.whatsapp.net",
            display_name="Tayyab Aziz", matched_by="identity_cache",
        )

    def test_file_name_ambiguity_produces_file_disambiguation_prompt(self, safe_tmp):
        h, transport, identities, context = _handler(safe_tmp)
        fake_plan = FileSendPlan(
            status="needs_clarification",
            detail="2 files named 'resume.pdf' found — which one?",
            contact_resolution=self._resolved_contact(),
            candidates=[
                FileCandidate(path="C:/Users/x/Desktop/resume.pdf", filename="resume.pdf",
                               mime_type="application/pdf", size_bytes=100, mtime=1.0, location="desktop"),
                FileCandidate(path="C:/Users/x/Downloads/resume.pdf", filename="resume.pdf",
                               mime_type="application/pdf", size_bytes=200, mtime=2.0, location="downloads"),
            ],
        )
        fake_planner = MagicMock()
        fake_planner.plan.return_value = fake_plan
        h._file_planner_obj = fake_planner

        outcome, _, plan = h.plan_send_file("Tayyab", {"kind": "filename", "name": "resume.pdf"})

        assert outcome.ambiguous is True
        # The old bug: names list built from plan.contact_resolution.candidates
        # (empty for a resolved contact) — must NOT reproduce that here.
        assert "found more than one contact" not in outcome.spoken
        assert "resume.pdf" in outcome.spoken
        assert "desktop" in [c["location"] for c in outcome.candidates]
        assert "downloads" in [c["location"] for c in outcome.candidates]
        assert len(outcome.candidates) == 2

    def test_contact_ambiguity_still_produces_contact_disambiguation_prompt(self, safe_tmp):
        # The pre-existing behavior (contact ambiguous, never reached file
        # lookup) must be unaffected by the fix above.
        h, transport, identities, context = _handler(safe_tmp)
        ambiguous_contact = ContactResolution(
            status="ambiguous",
            candidates=[
                {"display_name": "Ali Hassan"}, {"display_name": "Ali Sara"},
            ],
            detail="2 contacts match 'Ali'",
        )
        fake_plan = FileSendPlan(
            status="needs_clarification",
            contact_resolution=ambiguous_contact,
        )
        fake_planner = MagicMock()
        fake_planner.plan.return_value = fake_plan
        h._file_planner_obj = fake_planner

        outcome, _, plan = h.plan_send_file("Ali", {"kind": "filename", "name": "resume.pdf"})

        assert outcome.ambiguous is True
        assert "found more than one contact matching Ali" in outcome.spoken
        assert "Ali Hassan" in outcome.spoken
        assert "Ali Sara" in outcome.spoken
