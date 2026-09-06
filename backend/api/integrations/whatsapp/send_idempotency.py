"""
send_idempotency.py — persistent, cross-process send idempotency for
BaileysTransport's outbound actions (send_text/send_file/send_image/reply).

Why this exists
----------------
dedup.py's SendDeduplicator is documented as in-memory only, scoped to one
BaileysTransport instance — state is lost on process restart. Live testing
(Phase 4 Validation B) proved this precisely: the same confirmed
idempotency_key, replayed from a second process, produced a second real
WhatsApp message because the first process's in-memory dedup state was
gone. This module adds a SQLite-backed, cross-process-authoritative claim
store underneath it. SendDeduplicator remains as a fast first-level cache
(zero I/O for same-process retries); this store is what actually prevents
a duplicate send across process restarts, transport reconstruction, or
(eventually) multiple worker processes.

State machine (per idempotency_key)
------------------------------------
    absent -> claim() inserts a 'pending' row (atomic: PRIMARY KEY collision
              is the mutual-exclusion mechanism, enforced by SQLite itself
              across processes, not by application-level locking)
    pending -> complete(success=True)  -> 'completed' (message_id stored)
    pending -> complete(success=False) -> 'failed' (next claim() may retry)
    pending -> (process dies, never completes) -> stays 'pending' forever.
               A later claim() with the SAME key sees 'pending' and refuses
               to send again — matching SendDeduplicator's existing
               "ambiguous outcome" behavior for a timeout.

Payload conflict protection
----------------------------
Every row also stores a payload_hash (sha256 of action/recipient/content/
attachment/reply-to — never the raw message text at rest is unnecessary,
a hash is enough per the spec this module was built against). If the same
idempotency_key is ever claimed again with a DIFFERENT payload_hash, that
is treated as a conflict and refused — never silently returns a stale
result for a different message.

Documented limitation — the crash window
------------------------------------------
There is one gap this module does not and cannot close by itself: if the
WhatsApp send succeeds at the provider but this process crashes before
complete(success=True) runs, the row is left 'pending' forever. A later
retry of that exact idempotency_key will then be REFUSED (not silently
duplicated — the pending-row check prevents a second send), but it will
also never automatically return the real message_id, because this store
was never told the send actually succeeded. This is a correctness
trade-off deliberately made in the safe direction: an unrecoverable
'pending' row blocks a possibly-duplicate resend rather than risking a
second real message, exactly mirroring the pre-existing PENDING/timeout
behavior SendDeduplicator already had for same-process crashes. Closing
this completely would require reconciling against Baileys/WhatsApp's own
message history after restart, which is out of scope here — see the
final report for a precise callout of this rather than a claim that it is
solved.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("wa_send_idempotency")


def _default_path() -> Path:
    try:
        from api.config import settings
        base = settings.repo_root / "backend" / "data"
    except Exception:
        base = Path(__file__).resolve().parents[3] / "data"
    return base / "whatsapp_send_idempotency.db"


def payload_hash(action: str, recipient: str, content: Optional[str],
                  attachment: Optional[str], reply_to_message_id: Optional[str]) -> str:
    """Stable hash of the logical fields that define WHAT is being sent —
    not the raw text at rest. Used to detect idempotency_key reuse with a
    genuinely different payload (must be refused, never silently resent
    under the old key's "already done" answer)."""
    parts = [
        action or "", recipient or "", content or "",
        attachment or "", reply_to_message_id or "",
    ]
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ClaimResult:
    status: str  # "claimed" | "completed" | "pending" | "conflict"
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    detail: str = ""


class PersistentSendStore:
    """
    Cross-process-authoritative send idempotency. Every public method opens
    its own short-lived connection (SQLite handles cross-process file
    locking natively; this mirrors episodic_memory.py's connection
    convention) and never raises — callers get a ClaimResult/None on
    failure, matching every other module in this package.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _default_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS whatsapp_send_actions (
                    idempotency_key TEXT PRIMARY KEY,
                    action_type     TEXT NOT NULL,
                    chat_id         TEXT NOT NULL,
                    payload_hash    TEXT NOT NULL,
                    status          TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
                    message_id      TEXT,
                    error_code      TEXT,
                    error_message   TEXT,
                    created_at      REAL NOT NULL,
                    completed_at    REAL
                );
                CREATE INDEX IF NOT EXISTS idx_wsa_status ON whatsapp_send_actions (status);
            """)

    # ── claim ────────────────────────────────────────────────────────────

    def claim(self, idempotency_key: str, action_type: str, chat_id: str, payload_hash_: str) -> ClaimResult:
        """
        Atomically claim idempotency_key for a new send attempt.

        The PRIMARY KEY collision on INSERT *is* the cross-process mutual
        exclusion — two processes racing to claim the same key will have
        exactly one INSERT succeed; the other gets sqlite3.IntegrityError
        and falls through to inspect the winner's row. No application-level
        locking can substitute for this across process boundaries; SQLite's
        own file locking is what actually makes this atomic.
        """
        now = time.time()
        try:
            with self._lock, self._conn() as conn:
                try:
                    conn.execute(
                        "INSERT INTO whatsapp_send_actions "
                        "(idempotency_key, action_type, chat_id, payload_hash, status, created_at) "
                        "VALUES (?, ?, ?, ?, 'pending', ?)",
                        (idempotency_key, action_type, chat_id, payload_hash_, now),
                    )
                    conn.commit()
                    return ClaimResult(status="claimed", detail="new claim")
                except sqlite3.IntegrityError:
                    pass  # key already exists — inspect it below

                row = conn.execute(
                    "SELECT * FROM whatsapp_send_actions WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is None:
                    # Vanishingly unlikely (row deleted between the failed
                    # INSERT and this SELECT) — refuse rather than guess.
                    return ClaimResult(status="pending", detail="claim state indeterminate — refusing to send")

                if row["payload_hash"] != payload_hash_:
                    logger.warning(
                        "[WA_IDEMPOTENCY] payload conflict for key=%s: stored hash differs from this request",
                        idempotency_key,
                    )
                    return ClaimResult(
                        status="conflict",
                        detail=(
                            f"idempotency_key {idempotency_key!r} was already used for a different "
                            "send (different recipient/content) — refusing to reuse it"
                        ),
                    )

                if row["status"] == "completed":
                    return ClaimResult(
                        status="completed", message_id=row["message_id"], chat_id=row["chat_id"],
                        detail="already sent — returning stored result",
                    )

                if row["status"] == "failed":
                    # Definite prior failure — safe to allow a fresh attempt
                    # under the SAME key (mirrors SendDeduplicator.forget()
                    # for definite failures). Re-claim by flipping back to
                    # pending; another IntegrityError-style race here is
                    # covered by the UPDATE's WHERE clause only matching the
                    # 'failed' state, so a concurrent winner is harmless.
                    updated = conn.execute(
                        "UPDATE whatsapp_send_actions SET status='pending', created_at=?, "
                        "message_id=NULL, error_code=NULL, error_message=NULL, completed_at=NULL "
                        "WHERE idempotency_key=? AND status='failed'",
                        (now, idempotency_key),
                    )
                    conn.commit()
                    if updated.rowcount:
                        return ClaimResult(status="claimed", detail="re-claimed after prior failure")
                    # Someone else re-claimed it first in the meantime.
                    return ClaimResult(status="pending", detail="another attempt just re-claimed this key")

                # status == 'pending' — an attempt is in flight or crashed
                # mid-send with an unknown outcome. Never resend blindly.
                return ClaimResult(
                    status="pending",
                    detail=(
                        f"a previous attempt for {idempotency_key!r} has an unknown outcome "
                        "(in flight or crashed before completion) — refusing to send again"
                    ),
                )
        except Exception as e:
            logger.warning("[WA_IDEMPOTENCY] claim failed (%s) — refusing to send to be safe", e)
            return ClaimResult(status="pending", detail=f"idempotency store error: {e}")

    # ── complete ─────────────────────────────────────────────────────────

    def complete(
        self, idempotency_key: str, success: bool,
        message_id: Optional[str] = None,
        error_code: Optional[str] = None, error_message: Optional[str] = None,
    ) -> None:
        """Resolve a 'pending' claim. Never raises. Do NOT call this for an
        ambiguous/timeout outcome — leave the row 'pending' (mirrors
        SendDeduplicator's contract exactly)."""
        now = time.time()
        try:
            with self._lock, self._conn() as conn:
                if success:
                    conn.execute(
                        "UPDATE whatsapp_send_actions SET status='completed', message_id=?, completed_at=? "
                        "WHERE idempotency_key=?",
                        (message_id, now, idempotency_key),
                    )
                else:
                    conn.execute(
                        "UPDATE whatsapp_send_actions SET status='failed', error_code=?, error_message=?, "
                        "completed_at=? WHERE idempotency_key=?",
                        (error_code, error_message, now, idempotency_key),
                    )
                conn.commit()
        except Exception as e:
            logger.warning("[WA_IDEMPOTENCY] complete() failed for key=%s: %s", idempotency_key, e)

    def get(self, idempotency_key: str) -> Optional[sqlite3.Row]:
        """Read-only lookup — mainly for diagnostics/tests."""
        try:
            with self._conn() as conn:
                return conn.execute(
                    "SELECT * FROM whatsapp_send_actions WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
        except Exception as e:
            logger.warning("[WA_IDEMPOTENCY] get() failed: %s", e)
            return None


_default_store: Optional[PersistentSendStore] = None
_default_store_lock = threading.Lock()


def get_default_send_store() -> PersistentSendStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = PersistentSendStore()
        return _default_store
