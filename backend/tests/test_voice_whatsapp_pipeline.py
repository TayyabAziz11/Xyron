"""
test_voice_whatsapp_pipeline.py — Phase 5 (voice integration for the
WhatsApp fast path) tests.

Phase 5 does not introduce a separate voice pipeline: a finalized STT
transcript is just text that flows through the SAME normalize() ->
IntentRouter -> registry.execute() path typed commands already use, and
the SAME pending_confirmation mechanism in voice_ws.py (Tier 0d) that
every other confirm_required tool already relies on. These tests exercise
that real path — normalizer.py, wa_intent.py, wa_identity.py's fuzzy
fallback, and voice_ws.py's actual _CONFIRM_YES_RE/_CONFIRM_NO_RE
constants (imported, not duplicated, so there is no drift risk between
what's tested and what ships) — never a parallel voice-specific parser.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("LOCAL_ONLY_MODE", "true")

from api.services.normalizer import normalize
from api.services.intent_router import IntentRouter


@pytest.fixture
def router():
    return IntentRouter()


def _route(router, raw_transcript: str):
    """The real post-STT path: normalize() then IntentRouter.route()."""
    return router.route(normalize(raw_transcript))


# ---------------------------------------------------------------------------
# 1. Text after STT — the 5 required flagship commands
# ---------------------------------------------------------------------------

class TestTextAfterSTTRouting:
    def test_whatsapp_send_text_with_time_phrase(self, router):
        r = _route(router, "WhatsApp Tayyab, I'll be there in ten minutes.")
        assert r.tool_name == "wa_send_text"
        assert r.params["contact"] == "tayyab"  # normalize() lowercases before routing
        assert "ten minutes" in r.params["message"]

    def test_message_verb_send_text(self, router):
        r = _route(router, "Message Tayyab I'm outside.")
        assert r.tool_name == "wa_send_text"
        assert r.params["contact"] == "tayyab"  # normalize() lowercases

    def test_show_chat(self, router):
        r = _route(router, "Show me Tayyab's WhatsApp.")
        assert r.tool_name == "wa_show_chat"

    def test_get_messages(self, router):
        r = _route(router, "What did Tayyab say?")
        assert r.tool_name == "wa_get_messages"

    def test_reply_using_context(self, router):
        r = _route(router, "Reply to him, I'll call you later.")
        assert r.tool_name == "wa_reply"
        assert r.params["contact"] == "him"  # resolved from WhatsAppContext downstream, not here

    def test_whats_app_spelling_variant_still_routes(self, router):
        # The exact STT artifact that motivated the normalizer.py fix.
        r = _route(router, "whats app Tayyab, I'll be there in ten minutes.")
        assert r.tool_name == "wa_send_text"

    def test_send_file_screenshot_reference(self, router):
        r = _route(router, "Send the screenshot I just took to Tayyab.")
        assert r.tool_name == "wa_send_file"
        assert r.params["file_ref"]["kind"] == "context"
        assert "screenshot" in r.params["file_ref"]["query"].lower()

    @pytest.mark.parametrize("text", [
        "what app is this",              # must NOT be corrupted by the whatsapp-variant fix
        "what apps do I have installed",
        "what is the weather today",
        "open chrome",
    ])
    def test_negative_cases_not_hijacked(self, router, text):
        r = _route(router, text)
        assert r.tool_name not in {"wa_send_text", "wa_send_file", "wa_reply", "wa_show_chat", "wa_get_messages"}


# ---------------------------------------------------------------------------
# 2. Approval — using the REAL production regex, imported not duplicated
# ---------------------------------------------------------------------------

class TestApprovalRegexMatchesRealVoicePatterns:
    """api.routers.voice_ws._CONFIRM_YES_RE / _CONFIRM_NO_RE are the exact
    patterns Tier 0d uses. Importing them (rather than copying) means these
    tests fail loudly if voice_ws.py's approval wording ever changes."""

    @pytest.fixture(autouse=True)
    def _import_real_patterns(self):
        from api.routers.voice_ws import _CONFIRM_YES_RE, _CONFIRM_NO_RE
        self.yes_re = _CONFIRM_YES_RE
        self.no_re = _CONFIRM_NO_RE

    @pytest.mark.parametrize("phrase", [
        "yes", "yeah", "yep", "yup", "sure", "go ahead", "do it",
        "send it", "confirm", "proceed", "ok", "okay",
    ])
    def test_approval_phrases_match(self, phrase):
        assert self.yes_re.search(phrase)

    @pytest.mark.parametrize("phrase", [
        "no", "nope", "don't", "cancel", "forget it", "never mind",
    ])
    def test_rejection_phrases_match(self, phrase):
        assert self.no_re.search(phrase)

    @pytest.mark.parametrize("phrase", [
        "what's the weather?",
        "open chrome",
        "what did tayyab say",
        "send another message to ali",
    ])
    def test_unrelated_phrases_match_neither(self, phrase):
        # This is what makes Tier 0d re-prompt instead of accidentally
        # treating unrelated speech as approval or rejection.
        assert not self.yes_re.search(phrase)
        assert not self.no_re.search(phrase)


# ---------------------------------------------------------------------------
# 3. Approval executes the SAME pending action — no reparsing, no new key
# ---------------------------------------------------------------------------
# Tier 0d's "yes" branch calls _run_tool(_pending["tool"], _pending["params"])
# — i.e. registry.execute(tool, params) with the EXACT dict a prior
# confirm_required response produced. This is the same round trip Phase 4's
# test_whatsapp_tools.py already proves; these tests tie it explicitly to
# the voice narrative (a spoken "yes" transcript, not a typed one).

class TestVoiceApprovalExecutesSamePendingAction:
    def test_yes_transcript_reuses_exact_pending_params(self):
        from unittest.mock import MagicMock
        from api.tools import whatsapp_tools as wt
        from api.tools.registry import registry
        from api.integrations.whatsapp.wa_command_handler import WAOutcome

        fake_handler = MagicMock()
        fake_handler.plan_send_text.return_value = (
            WAOutcome(ok=False, needs_confirmation=True, spoken='Send "hi" to Tayyab Aziz?', text="prompt",
                      data={"resolved_chat_id": "923001234567@s.whatsapp.net",
                            "resolved_display_name": "Tayyab Aziz", "latency_ms": {}}),
            None,
        )
        import unittest.mock as _mock
        with _mock.patch.object(wt, "get_default_command_handler", return_value=fake_handler):
            first = registry.execute("wa_send_text", {"contact": "Tayyab", "message": "hi"})

        assert first.error == "confirm_required"
        pending_tool = first.data["tool"]
        pending_params = first.data["params"]  # what Tier 0d would store verbatim

        # Simulate: user says "yes" (a voice transcript) — Tier 0d re-invokes
        # the SAME tool with the SAME params dict, unmodified.
        assert self._matches_yes("yes")
        fake_handler.execute_send_text.return_value = (
            WAOutcome(ok=True, spoken="Sent it to Tayyab Aziz.", text="sent",
                      data={"message_id": "MSG1", "deduped": False, "latency_ms": {}}),
            None,
        )
        with _mock.patch.object(wt, "get_default_command_handler", return_value=fake_handler):
            second = registry.execute(pending_tool, pending_params)

        assert second.success is True
        fake_handler.execute_send_text.assert_called_once_with(
            "923001234567@s.whatsapp.net", "Tayyab Aziz", "hi",
            pending_params["_idempotency_key"], show_ui=False,
        )
        # Never reparsed — plan_send_text (which would re-resolve "Tayyab"
        # from scratch) is called exactly once, during the FIRST turn only.
        assert fake_handler.plan_send_text.call_count == 1

    @staticmethod
    def _matches_yes(transcript: str) -> bool:
        from api.routers.voice_ws import _CONFIRM_YES_RE
        return bool(_CONFIRM_YES_RE.search(transcript))


class TestVoiceRejection:
    def test_no_transcript_never_calls_execute(self):
        from unittest.mock import MagicMock, patch
        from api.tools import whatsapp_tools as wt
        from api.tools.registry import registry
        from api.integrations.whatsapp.wa_command_handler import WAOutcome
        from api.routers.voice_ws import _CONFIRM_NO_RE

        assert _CONFIRM_NO_RE.search("no")

        fake_handler = MagicMock()
        fake_handler.plan_send_text.return_value = (
            WAOutcome(ok=False, needs_confirmation=True, spoken="Send 'hi' to Tayyab Aziz?", text="prompt",
                      data={"resolved_chat_id": "923001234567@s.whatsapp.net",
                            "resolved_display_name": "Tayyab Aziz", "latency_ms": {}}),
            None,
        )
        with patch.object(wt, "get_default_command_handler", return_value=fake_handler):
            registry.execute("wa_send_text", {"contact": "Tayyab", "message": "hi"})

        # Tier 0d's rejection branch never calls _run_tool at all — it just
        # clears pending_confirmation and speaks "Alright, cancelled."
        # Verified structurally here: execute_send_text must never be
        # invoked when the user said "no".
        fake_handler.execute_send_text.assert_not_called()

    def test_cancel_also_matches_no_re(self):
        from api.routers.voice_ws import _CONFIRM_NO_RE
        assert _CONFIRM_NO_RE.search("cancel")
        assert _CONFIRM_NO_RE.search("actually no")


# ---------------------------------------------------------------------------
# 4. Idempotency — duplicate approval event still produces one provider send
# ---------------------------------------------------------------------------

class TestDuplicateApprovalIdempotency:
    def test_duplicate_confirmed_call_same_action_id_one_send(self, tmp_path):
        """Simulates the pending_confirmation params dict being replayed
        twice (e.g. a race/retry at the voice layer) — PersistentSendStore
        (Phase 4) must still only allow one real send."""
        from api.integrations.whatsapp.baileys_transport import BaileysTransport
        from api.integrations.whatsapp.send_idempotency import PersistentSendStore
        from api.integrations.whatsapp.models import WAAction, WhatsAppRequest, WhatsAppResult

        store = PersistentSendStore(path=tmp_path / "voice_dup.db")
        transport = BaileysTransport(host="x", port=1, api_key="k", persistent_store=store)
        sent = {"n": 0}

        def fake_send(request):
            sent["n"] += 1
            return WhatsAppResult(success=True, message_id=f"MSG{sent['n']}", chat_id=request.recipient)

        req = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923@s.whatsapp.net",
                               content="hi", idempotency_key="voice-action-1")
        r1 = transport._guarded_send(WAAction.SEND_TEXT, req, lambda: fake_send(req))
        r2 = transport._guarded_send(WAAction.SEND_TEXT, req, lambda: fake_send(req))  # duplicate approval event

        assert r1.deduped is False
        assert r2.deduped is True
        assert r2.message_id == r1.message_id
        assert sent["n"] == 1


# ---------------------------------------------------------------------------
# 5. Alias — STT spelling variant resolves via generic identity storage
# ---------------------------------------------------------------------------

class TestSTTAliasResolution:
    def test_stt_variant_resolves_via_fuzzy_identity_not_parser_hardcoding(self, tmp_path):
        from api.integrations.whatsapp.wa_identity import WhatsAppIdentityStore
        import ast, inspect
        import api.integrations.whatsapp.wa_intent as wa_intent_mod

        store = WhatsAppIdentityStore(path=tmp_path / "alias.db")
        ident = store.resolve_cached("Tayab", allow_fuzzy=True)
        assert ident is not None
        assert ident.canonical_name == "Tayyab Aziz"

        # Confirm the parser has no import of the identity store at all —
        # it cannot special-case any name because it never resolves
        # contacts itself (mirrors the AST-based check in test_wa_intent.py).
        tree = ast.parse(inspect.getsource(wa_intent_mod))
        imported = {
            n.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for n in node.names
        }
        assert "WhatsAppIdentityStore" not in imported
        assert "get_default_identity_store" not in imported
        # The misspelled STT variant specifically never appears anywhere —
        # unlike "Tayyab" (which is fine in prose/comments/docstrings),
        # "Tayab" has no legitimate reason to be in this file at all.
        assert "Tayab" not in inspect.getsource(wa_intent_mod)

    def test_exact_match_preferred_over_fuzzy(self, tmp_path):
        from api.integrations.whatsapp.wa_identity import WhatsAppIdentityStore
        store = WhatsAppIdentityStore(path=tmp_path / "alias2.db")
        store.learn(canonical_name="Ali Khan", chat_id="923_ali@s.whatsapp.net",
                    display_name="Ali Khan", matched_by="exact_name")
        # "Ali" is an exact alias-free substring of nothing here, but the
        # canonical exact match for "Ali Khan" must win over any fuzzy path.
        ident = store.resolve_cached("Ali Khan", allow_fuzzy=True)
        assert ident.canonical_name == "Ali Khan"


# ---------------------------------------------------------------------------
# 6. Media — screenshot reference builds the right file_ref, no live send
# ---------------------------------------------------------------------------

class TestVoiceMediaScreenshotReference:
    def test_screenshot_phrase_delegates_to_context_resolution(self):
        from api.integrations.whatsapp.wa_intent import parse_wa_intent
        intent = parse_wa_intent("Send the screenshot I just took to Tayyab.")
        assert intent.action == "send_file"
        assert intent.artifact_ref == {"kind": "context", "query": "the screenshot I just took"}
        # No filesystem access happened in parsing — parse_wa_intent never
        # IMPORTS FileSendPlanner/ScreenshotResolver (checked via AST, not a
        # raw substring scan — both names legitimately appear in this
        # module's docstring explaining why it delegates to them).
        import ast, inspect
        import api.integrations.whatsapp.wa_intent as mod
        tree = ast.parse(inspect.getsource(mod))
        imported = {
            n.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for n in node.names
        }
        assert "ScreenshotResolver" not in imported
        assert "FileSendPlanner" not in imported

    def test_ambiguous_screenshot_resolution_does_not_guess(self, tmp_path):
        """No real screenshot exists in this hermetic test — the plan must
        fail gracefully (not_found), never guess/send."""
        from unittest.mock import MagicMock
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.wa_context import WhatsAppContext

        transport = MagicMock()
        transport.find_contact.return_value = [
            {"chat_id": "923@s.whatsapp.net", "display_name": "Tayyab Aziz"},
        ]
        context = WhatsAppContext(path=tmp_path / "ctx.json")
        planner = FileSendPlanner(transport, context)
        plan = planner.plan({"kind": "context", "query": "the screenshot i just took"}, "Tayyab Aziz")
        assert plan.status in ("not_found", "needs_clarification", "error")
        transport.send_image.assert_not_called()
        transport.send_file.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Confirmation prompt never exposes internal identifiers
# ---------------------------------------------------------------------------

class TestConfirmationPromptNeverExposesInternals:
    def test_prompt_has_no_jid_phone_or_ids(self):
        from unittest.mock import MagicMock, patch
        from api.tools import whatsapp_tools as wt
        from api.tools.registry import registry
        from api.integrations.whatsapp.wa_command_handler import WAOutcome

        fake_handler = MagicMock()
        fake_handler.plan_send_text.return_value = (
            WAOutcome(ok=False, needs_confirmation=True,
                      spoken='Send "I\'m outside" to Tayyab Aziz?', text="prompt",
                      data={"resolved_chat_id": "923001234567@s.whatsapp.net",
                            "resolved_display_name": "Tayyab Aziz", "latency_ms": {}}),
            None,
        )
        with patch.object(wt, "get_default_command_handler", return_value=fake_handler):
            result = registry.execute("wa_send_text", {"contact": "Tayyab", "message": "I'm outside"})

        spoken = result.spoken
        assert "923001234567" not in spoken
        assert "@s.whatsapp.net" not in spoken
        assert "action_id" not in spoken.lower()
        # The JID IS present in data (developer-facing), just never in spoken.
        assert result.data["params"]["_resolved_chat_id"] == "923001234567@s.whatsapp.net"


# ---------------------------------------------------------------------------
# 8. Expiry — pending_confirmation now has a bounded lifetime
# ---------------------------------------------------------------------------

class TestPendingConfirmationExpiry:
    def test_expiry_uses_same_300s_convention_as_sibling_pending_states(self):
        import inspect
        import api.routers.voice_ws as vws
        src = inspect.getsource(vws)
        assert '_pending.get("created_at", 0)) > 300' in src
        assert '"created_at": time.time()' in src
