"""
Unit tests for api.integrations.whatsapp.dedup.SendDeduplicator.

Pure in-memory logic — no network, no mocking required.
"""
import time

from api.integrations.whatsapp.dedup import SendDeduplicator, SendState


def test_new_key_returns_none_and_reserves_pending():
    dedup = SendDeduplicator()
    assert dedup.begin("k1") is None
    # Second begin() on the same still-pending key returns the entry, not None.
    entry = dedup.begin("k1")
    assert entry is not None
    assert entry.state == SendState.PENDING


def test_success_is_cached_and_returned_on_retry():
    dedup = SendDeduplicator()
    dedup.begin("k1")
    dedup.complete("k1", success=True, message_id="msg-123", chat_id="chat-1")

    entry = dedup.begin("k1")
    assert entry is not None
    assert entry.state == SendState.SUCCESS
    assert entry.message_id == "msg-123"
    assert entry.chat_id == "chat-1"


def test_pending_entry_blocks_retry_until_resolved():
    dedup = SendDeduplicator()
    dedup.begin("k1")  # first attempt in flight, never completed (simulates a timeout)

    entry = dedup.begin("k1")
    assert entry is not None
    assert entry.state == SendState.PENDING


def test_failed_entry_is_forgotten_and_retry_allowed():
    dedup = SendDeduplicator()
    dedup.begin("k1")
    dedup.complete("k1", success=False, message_id=None, chat_id=None)
    dedup.forget("k1")

    # Key is gone — a fresh attempt is allowed.
    assert dedup.begin("k1") is None
    assert dedup.size() == 1  # re-reserved as PENDING by the begin() call above


def test_forget_is_a_noop_for_pending_or_success():
    dedup = SendDeduplicator()
    dedup.begin("k1")
    dedup.forget("k1")  # still PENDING — must not be cleared
    entry = dedup.begin("k1")
    assert entry is not None and entry.state == SendState.PENDING

    dedup.complete("k1", success=True, message_id="m", chat_id="c")
    dedup.forget("k1")  # SUCCESS — must not be cleared
    entry = dedup.begin("k1")
    assert entry is not None and entry.state == SendState.SUCCESS


def test_independent_keys_never_collide():
    dedup = SendDeduplicator()
    assert dedup.begin("a") is None
    assert dedup.begin("b") is None
    dedup.complete("a", success=True, message_id="ma", chat_id="ca")

    entry_a = dedup.begin("a")
    entry_b = dedup.begin("b")
    assert entry_a is not None and entry_a.state == SendState.SUCCESS
    assert entry_b is not None and entry_b.state == SendState.PENDING


def test_ttl_expiry_allows_retry():
    dedup = SendDeduplicator(ttl_seconds=0.05)
    dedup.begin("k1")
    dedup.complete("k1", success=True, message_id="m", chat_id="c")
    time.sleep(0.1)

    # Entry has expired — treated as a brand-new attempt.
    assert dedup.begin("k1") is None
