"""
dedup.py — best-effort outbound-send deduplication.

WhatsApp / open-wa have no server-side idempotency key — a caller-generated
`idempotency_key` (stable across retries of the SAME logical send, not
regenerated per attempt) is the only mechanism available. This guard exists
to stop Xyron from blindly re-sending a message after an ambiguous timeout —
it does NOT guarantee exactly-once delivery.

State machine per key:
  (no entry)         -> begin() reserves PENDING, caller proceeds with the real send
  PENDING  + retry    -> begin() returns the PENDING entry; caller must NOT resend
                         (outcome of the first attempt is unknown — could have
                         succeeded on WhatsApp's side even though the HTTP call
                         to the sidecar timed out)
  SUCCESS  + retry    -> begin() returns the SUCCESS entry; caller returns the
                         cached result instead of sending again
  FAILED (definite)   -> forget() clears the key immediately so a legitimate
                         retry is not blocked forever

Known limitations (documented, not fixed in Step 1):
  - In-memory only — state is lost on process restart. A message sent right
    before a crash and retried right after will not be deduped.
  - Not shared across multiple transport instances/processes.
  - A PENDING entry that never resolves (sidecar killed mid-call) blocks
    retries of that exact idempotency_key until TTL expiry, by design —
    Xyron cannot know whether WhatsApp actually delivered it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class SendState(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class DedupEntry:
    state: SendState
    message_id: Optional[str]
    chat_id: Optional[str]
    created_at: float


class SendDeduplicator:
    """Thread-safe, TTL-bound, in-memory idempotency guard keyed by idempotency_key."""

    def __init__(self, ttl_seconds: float = 600.0):
        self._ttl = ttl_seconds
        self._entries: Dict[str, DedupEntry] = {}
        self._lock = threading.Lock()

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, e in self._entries.items() if now - e.created_at > self._ttl]
        for k in expired:
            del self._entries[k]

    def begin(self, key: str) -> Optional[DedupEntry]:
        """
        Call before attempting a send. Returns the existing entry if this key
        was already attempted (caller must not resend); returns None and
        reserves the key as PENDING if this is a genuinely new attempt.
        """
        with self._lock:
            self._evict_expired_locked()
            existing = self._entries.get(key)
            if existing is not None:
                return existing
            self._entries[key] = DedupEntry(SendState.PENDING, None, None, time.monotonic())
            return None

    def complete(self, key: str, success: bool, message_id: Optional[str], chat_id: Optional[str]) -> None:
        """Resolve a PENDING entry. Do NOT call this for an ambiguous/timeout outcome — leave it PENDING."""
        with self._lock:
            self._entries[key] = DedupEntry(
                SendState.SUCCESS if success else SendState.FAILED,
                message_id, chat_id, time.monotonic(),
            )

    def forget(self, key: str) -> None:
        """Clear a definitively-FAILED entry so the same key may be retried."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.state == SendState.FAILED:
                del self._entries[key]

    def size(self) -> int:
        with self._lock:
            return len(self._entries)
