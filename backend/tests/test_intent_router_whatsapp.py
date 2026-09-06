"""
test_intent_router_whatsapp.py — Phase 4 Tier 0.5 fast-path integration
with the shared IntentRouter singleton.

These tests exercise the ACTUAL default identity store / context files
under backend/data/ (same as production), because IntentRouter's Tier 0.5
lazily imports get_default_identity_store() itself — there is no injection
seam at the router level (this mirrors how Tier 2's object_resolver /
store_agent imports work today). The bootstrap identity (Tayyab Aziz) is
therefore always present; tests rely only on that bootstrap contact plus
genuinely-unknown names, never on mutable state another test might leave
behind.

LOCAL_ONLY_MODE=true keeps Tier 3 (semantic classifier) from loading a
SentenceTransformer model — irrelevant to Tier 0.5, but keeps these tests
fast and network-free.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("LOCAL_ONLY_MODE", "true")

from api.services.intent_router import IntentRouter  # noqa: E402


@pytest.fixture
def router():
    # A fresh instance per test avoids Tier 1 cache bleed between cases.
    return IntentRouter()


class TestKnownContactFastPath:
    def test_explicit_whatsapp_keyword_bypasses_llm(self, router):
        r = router.route("whatsapp Tayyab, I'll be there.")
        assert r.tool_name == "wa_send_text"
        assert r.tier == 0
        assert r.confidence == 1.0

    def test_show_chat_known_contact(self, router):
        r = router.route("Show me Tayyab's WhatsApp.")
        assert r.tool_name == "wa_show_chat"
        assert r.params["contact"] == "Tayyab"

    def test_send_file_known_contact(self, router):
        r = router.route("Send this PDF to Tayyab.")
        assert r.tool_name == "wa_send_file"
        assert r.params["contact"] == "Tayyab"

    def test_get_messages_known_contact(self, router):
        r = router.route("What did Tayyab say?")
        assert r.tool_name == "wa_get_messages"

    def test_ambiguous_verb_shape_commits_for_known_contact(self, router):
        # "message X ..." has no "whatsapp" keyword — must only commit
        # because "Tayyab" is a known (bootstrap) identity.
        r = router.route("message Tayyab I'm outside")
        assert r.tool_name == "wa_send_text"
        assert r.params["message"] == "I'm outside"


class TestUnknownContactFallsThrough:
    """The core anti-hijack guarantee (handoff §29): an ambiguous verb
    shape for a contact Xyron has never heard of must NOT commit to the
    WhatsApp route — it must return no match so the normal pipeline
    (Tier2/Tier3/LLM) handles it exactly as it would without Phase 4."""

    def test_message_unknown_name_falls_through(self, router):
        r = router.route("message Zzqx Wibblethorpe about the report")
        assert r.tool_name != "wa_send_text"

    def test_tell_unknown_name_falls_through(self, router):
        r = router.route("tell Zzqx Wibblethorpe about the meeting")
        assert r.tool_name != "wa_send_text"

    def test_open_chat_bare_unknown_name_falls_through(self, router):
        # "chat" (not "whatsapp") for an unknown name — requires_cache_hit.
        r = router.route("open Zzqx Wibblethorpe's chat")
        assert r.tool_name != "wa_show_chat"


class TestNegativeCasesNeverHijacked:
    @pytest.mark.parametrize("text", [
        "what is the weather today",
        "open chrome",
        "create a new folder on my desktop",
        "what time is it",
        "read my messages",
        "message board for the office",
    ])
    def test_unrelated_query_not_routed_to_whatsapp(self, router, text):
        r = router.route(text)
        assert r.tool_name not in {"wa_send_text", "wa_send_file", "wa_reply", "wa_show_chat", "wa_get_messages"}


class TestExplicitWhatsappKeywordAlwaysCommits:
    """Explicit 'whatsapp' mentions are unambiguous by construction — they
    must commit even for a contact name Xyron has never seen (resolution
    failure is then the tool's job to report gracefully, not the router's
    job to silently swallow)."""

    def test_whatsapp_keyword_commits_for_unknown_name(self, router):
        r = router.route("whatsapp Zzqx Wibblethorpe, running late")
        assert r.tool_name == "wa_send_text"


class TestNoNetworkOrFilesystemAtRouteTime:
    def test_route_call_does_not_touch_a_transport(self, router, monkeypatch):
        import api.integrations.whatsapp.contact_resolver as cr_mod

        called = {"n": 0}

        def _boom(*a, **kw):
            called["n"] += 1
            raise AssertionError("ContactResolver must not be invoked at route() time")

        monkeypatch.setattr(cr_mod.ContactResolver, "resolve", _boom)
        router.route("whatsapp Tayyab, hi")
        router.route("message Tayyab hi")
        router.route("show me Tayyab's whatsapp")
        assert called["n"] == 0
