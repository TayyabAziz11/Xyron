"""
test_wa_identity.py — Phase 4 canonical WhatsApp identity layer.

Hermetic: every store is constructed with an explicit tmp_path, never the
default backend/data/whatsapp_identities.json.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from api.integrations.whatsapp.wa_identity import (
    WhatsAppIdentityStore,
    WhatsAppContactIdentity,
    _norm,
)


@pytest.fixture
def store(tmp_path) -> WhatsAppIdentityStore:
    return WhatsAppIdentityStore(path=tmp_path / "whatsapp_identities.json")


class TestBootstrap:
    def test_bootstrap_seeded_on_first_load(self, store):
        ident = store.resolve_cached("Tayyab")
        assert ident is not None
        assert ident.canonical_name == "Tayyab Aziz"
        assert ident.pn_jid == "923001234567@s.whatsapp.net"
        assert ident.verified_on_whatsapp is True
        assert ident.source == "bootstrap"

    def test_bootstrap_persisted_to_disk(self, tmp_path):
        path = tmp_path / "whatsapp_identities.json"
        WhatsAppIdentityStore(path=path)
        assert path.exists()
        # Re-loading from the same file must not re-seed a duplicate.
        store2 = WhatsAppIdentityStore(path=path)
        assert len(store2.all()) == 1


class TestCanonicalAndAliasResolution:
    def test_canonical_name_resolves(self, store):
        ident = store.resolve_cached("Tayyab Aziz")
        assert ident is not None and ident.canonical_name == "Tayyab Aziz"

    def test_alias_resolves_same_identity(self, store):
        ident = store.resolve_cached("Tayyab")
        assert ident is not None and ident.pn_jid == "923001234567@s.whatsapp.net"

    def test_case_normalization(self, store):
        assert store.resolve_cached("TAYYAB") is not None
        assert store.resolve_cached("tAyYaB") is not None

    def test_punctuation_normalization(self, store):
        assert store.resolve_cached("Tayyab!") is not None
        assert store.resolve_cached("  Tayyab  ") is not None

    def test_unknown_contact_misses(self, store):
        assert store.resolve_cached("Ali") is None

    def test_empty_ref_misses(self, store):
        assert store.resolve_cached("") is None
        assert store.resolve_cached(None) is None  # type: ignore[arg-type]


class TestLearning:
    def test_learn_high_confidence_creates_new_identity(self, store):
        ident = store.learn(
            canonical_name="Ali Khan", chat_id="923001234567@s.whatsapp.net",
            display_name="Ali Khan", matched_by="exact_name",
        )
        assert ident is not None
        assert store.resolve_cached("Ali Khan") is ident
        assert store.resolve_cached("ali khan").pn_jid == "923001234567@s.whatsapp.net"

    def test_learn_low_confidence_match_is_refused(self, store):
        # "phone_constructed" is not in _LEARNABLE_MATCH_KINDS — never cache
        # an unreliable/guessed resolution (handoff §26).
        ident = store.learn(
            canonical_name="Random", chat_id="923009999999@s.whatsapp.net",
            matched_by="phone_constructed",
        )
        assert ident is None
        assert store.resolve_cached("Random") is None

    def test_learn_ambiguous_match_is_refused(self, store):
        ident = store.learn(
            canonical_name="Someone", chat_id="923008888888@s.whatsapp.net",
            matched_by="unique_name_match",
        )
        assert ident is None

    def test_learn_merges_into_existing_identity_by_canonical_name(self, store):
        store.learn(canonical_name="Sara Malik", chat_id="923005551111@s.whatsapp.net",
                    display_name="Sara Malik", matched_by="phone")
        # Same person resolved again later via a LID — must correlate onto
        # the SAME identity, not create a second conflicting record.
        store.learn(canonical_name="Sara Malik", chat_id="18885551234@lid",
                    display_name="Sara Malik", matched_by="on_whatsapp_verified")
        idents = [i for i in store.all() if i.canonical_name == "Sara Malik"]
        assert len(idents) == 1
        assert idents[0].pn_jid == "923005551111@s.whatsapp.net"
        assert idents[0].lid == "18885551234@lid"

    def test_learn_persists_across_reload(self, tmp_path):
        path = tmp_path / "whatsapp_identities.json"
        s1 = WhatsAppIdentityStore(path=path)
        s1.learn(canonical_name="Ali Khan", chat_id="923001234567@s.whatsapp.net",
                  display_name="Ali Khan", matched_by="exact_name")
        s2 = WhatsAppIdentityStore(path=path)
        assert s2.resolve_cached("Ali Khan") is not None


class TestAliasLearning:
    def test_add_alias_explicit(self, store):
        ok = store.add_alias("Tayyab Aziz", "Tayab")  # STT-friendly variant
        assert ok is True
        ident = store.resolve_cached("Tayab")
        assert ident is not None and ident.canonical_name == "Tayyab Aziz"

    def test_add_alias_for_unknown_canonical_fails(self, store):
        assert store.add_alias("Nobody", "Nick") is False


class TestNoNetworkOnCacheHit:
    def test_resolve_cached_never_touches_a_transport(self, store):
        # resolve_cached takes no transport at all — a network call is
        # structurally impossible here, which is the whole point of the
        # cache tier (handoff §36 — never verify a stable known contact
        # over the network on every command).
        import inspect
        sig = inspect.signature(store.resolve_cached)
        assert "transport" not in sig.parameters


class TestNorm:
    def test_norm_examples(self):
        assert _norm("Tayyab!") == "tayyab"
        assert _norm("  Tayyab   Aziz ") == "tayyab aziz"
        assert _norm("TAYYAB") == "tayyab"
