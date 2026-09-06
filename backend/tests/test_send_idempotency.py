"""
test_send_idempotency.py — PersistentSendStore unit tests: the atomic
claim/complete state machine that makes send idempotency survive process
restarts and fresh transport instances.

Every test uses an isolated tmp_path-backed store — never the real default
backend/data/whatsapp_send_idempotency.db.
"""
from __future__ import annotations

import threading
import time

import pytest

from api.integrations.whatsapp.send_idempotency import PersistentSendStore, payload_hash


@pytest.fixture
def store(tmp_path) -> PersistentSendStore:
    return PersistentSendStore(path=tmp_path / "idempotency.db")


class TestClaimStateMachine:
    def test_first_claim_succeeds(self, store):
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        r = store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        assert r.status == "claimed"

    def test_second_claim_before_complete_is_pending(self, store):
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        r2 = store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        assert r2.status == "pending"

    def test_claim_after_completion_returns_completed_result(self, store):
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        store.complete("key1", True, message_id="MSG1")
        r2 = store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        assert r2.status == "completed"
        assert r2.message_id == "MSG1"

    def test_claim_after_failure_allows_retry(self, store):
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        store.complete("key1", False, error_code="PROVIDER_ERROR", error_message="boom")
        r2 = store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        assert r2.status == "claimed"  # definite failure -> safe to retry same key

    def test_pending_stays_pending_forever_until_resolved(self, store):
        # Simulates a crash mid-send — complete() never called.
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store.claim("key1", "send_text", "923@s.whatsapp.net", h)
        for _ in range(3):
            r = store.claim("key1", "send_text", "923@s.whatsapp.net", h)
            assert r.status == "pending"


class TestPayloadConflict:
    def test_same_key_different_recipient_is_conflict(self, store):
        h1 = payload_hash("send_text", "923_tayyab@s.whatsapp.net", "hi", None, None)
        h2 = payload_hash("send_text", "923_ali@s.whatsapp.net", "hi", None, None)
        store.claim("key1", "send_text", "923_tayyab@s.whatsapp.net", h1)
        r2 = store.claim("key1", "send_text", "923_ali@s.whatsapp.net", h2)
        assert r2.status == "conflict"

    def test_same_key_different_text_is_conflict(self, store):
        h1 = payload_hash("send_text", "923@s.whatsapp.net", "hello", None, None)
        h2 = payload_hash("send_text", "923@s.whatsapp.net", "different", None, None)
        store.claim("key1", "send_text", "923@s.whatsapp.net", h1)
        r2 = store.claim("key1", "send_text", "923@s.whatsapp.net", h2)
        assert r2.status == "conflict"

    def test_conflict_does_not_mutate_stored_row(self, store):
        h1 = payload_hash("send_text", "923@s.whatsapp.net", "hello", None, None)
        h2 = payload_hash("send_text", "923@s.whatsapp.net", "different", None, None)
        store.claim("key1", "send_text", "923@s.whatsapp.net", h1)
        store.complete("key1", True, message_id="MSG-ORIGINAL")
        store.claim("key1", "send_text", "923@s.whatsapp.net", h2)  # conflicting claim attempt
        row = store.get("key1")
        assert row["message_id"] == "MSG-ORIGINAL"
        assert row["payload_hash"] == h1


class TestPayloadHashStability:
    def test_same_inputs_same_hash(self):
        h1 = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        h2 = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        assert h1 == h2

    def test_different_action_type_different_hash(self):
        h1 = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        h2 = payload_hash("reply", "923@s.whatsapp.net", "hi", None, None)
        assert h1 != h2

    def test_never_contains_raw_message_text(self):
        # The stored artifact is a hash, not the message — spec requirement:
        # "Do not persist unnecessary sensitive message content."
        h = payload_hash("send_text", "923@s.whatsapp.net", "a very secret message", None, None)
        assert "secret" not in h
        assert len(h) == 64  # sha256 hex digest


class TestCrossInstancePersistence:
    def test_fresh_store_object_same_db_file_sees_completed_state(self, tmp_path):
        """Simulates a process restart: a brand new PersistentSendStore
        instance pointed at the same db file must see prior state."""
        db_path = tmp_path / "shared.db"
        store1 = PersistentSendStore(path=db_path)
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store1.claim("key1", "send_text", "923@s.whatsapp.net", h)
        store1.complete("key1", True, message_id="MSG1")

        store2 = PersistentSendStore(path=db_path)  # "process restart"
        r = store2.claim("key1", "send_text", "923@s.whatsapp.net", h)
        assert r.status == "completed"
        assert r.message_id == "MSG1"

    def test_fresh_store_object_same_db_file_sees_pending_state(self, tmp_path):
        db_path = tmp_path / "shared.db"
        store1 = PersistentSendStore(path=db_path)
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store1.claim("key1", "send_text", "923@s.whatsapp.net", h)  # never completed

        store2 = PersistentSendStore(path=db_path)
        r = store2.claim("key1", "send_text", "923@s.whatsapp.net", h)
        assert r.status == "pending"  # must NOT silently allow a second send


class TestConcurrentClaim:
    def test_concurrent_claims_only_one_wins(self, tmp_path):
        db_path = tmp_path / "concurrent.db"
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        results = []
        lock = threading.Lock()

        def worker():
            # Each thread gets its own store/connection — mirrors separate
            # processes/workers more faithfully than sharing one connection.
            s = PersistentSendStore(path=db_path)
            r = s.claim("racey-key", "send_text", "923@s.whatsapp.net", h)
            with lock:
                results.append(r.status)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 10
        assert results.count("claimed") == 1, f"expected exactly 1 claim winner, got {results}"
        assert results.count("pending") == 9

    def test_new_action_id_after_completion_is_a_fresh_claim(self, store):
        """A NEW user command (new idempotency_key) is always allowed —
        never suppressed by a prior, different, completed action."""
        h = payload_hash("send_text", "923@s.whatsapp.net", "hi", None, None)
        store.claim("action-1", "send_text", "923@s.whatsapp.net", h)
        store.complete("action-1", True, message_id="MSG1")

        r2 = store.claim("action-2", "send_text", "923@s.whatsapp.net", h)  # same payload, new key
        assert r2.status == "claimed"
