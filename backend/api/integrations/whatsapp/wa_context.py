"""
wa_context.py — narrow Phase 3 conversational context for WhatsApp.

Keeps a rolling record of recent WhatsApp interactions (sends, most recent
contact) so that follow-up references like "same contact", "him", "her", or
"send it to him too" can be resolved to an exact WhatsApp identity from the
recent interaction context.

Also tracks recent ARTIFACTS (local files / WhatsApp attachments) separately
from contacts, so references like "it", "that PDF", "same file", or "the
file I just sent" resolve to an exact file for file-semantics intents.
Artifact context distinguishes "referenced" (the user mentioned it) from
"sent" (confirmed transport success) — only the latter is authoritative
for "send it again"-style follow-ups.

The module is deliberately small and isolated. It is intended to be replaced
later by a broader World State integration; the public API
(`is_contextual_contact_reference`, `resolve_contact_reference`) is the
stable seam that must be preserved regardless of the backing store.

Persistence
-----------
Interactions are stored as JSON under backend/data/whatsapp_context.json.
The file is created on first write; absence on load yields an empty context.
The path is derived from `api.config.settings.repo_root` with a file-relative
fallback for scripts running outside the normal app lifecycle.

Thread safety: a module-level Lock guards all mutations of the in-memory
interaction list. The disk write is outside the critical section.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("wa_context")

DEFAULT_MAX_AGE_S = 24.0 * 3600.0  # 24 h carryover window
_MAX_INTERACTIONS = 50

# References that mean "the contact from my recent WhatsApp interaction".
# Multi-word phrases are matched as substrings of the normalised reference;
# single-word pronouns are matched exactly to avoid false positives
# ("him" must not match "shimmer").
_CONTEXTUAL_PHRASES = (
    "same contact", "same person", "same one", "same number",
    "the same guy", "the same person",
    "last contact", "previous contact",
    "that contact", "that person",
)
_CONTEXTUAL_PRONOUNS = frozenset({"him", "her", "them"})

# References that mean "the file/media artifact from my recent interaction".
# Multi-word phrases match as substrings of the normalised reference. Bare
# "it" is handled separately and ONLY counts as an artifact reference when
# the surrounding intent requires a file/media object (see
# is_contextual_artifact_reference) — "it" must not blindly mean the last
# file for every intent.
_ARTIFACT_PHRASES = (
    "that file", "that document", "that pdf", "that image", "that photo",
    "that picture", "that screenshot",
    "same file", "same document", "same pdf", "same image", "same photo",
    "the same file", "the same document", "the same pdf",
    "the same image", "the same photo", "the same picture",
    "the file i just sent", "the pdf i just sent",
    "the image i just sent", "the document i just sent",
    "the photo i just sent",
)
# Phrases that explicitly point at the last SENT artifact (no fallback to
# merely-referenced artifacts).
_ARTIFACT_SENT_PHRASES = ("i just sent", "i sent")
# Intents whose direct object is a file/media object. Only for these does a
# bare "it" resolve as an artifact.
_FILE_SEMANTIC_ACTIONS = frozenset({"send", "open", "save", "share"})

_MAX_ARTIFACTS = 50


def _norm(ref: str) -> str:
    s = (ref or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_contextual_contact_reference(ref: str) -> bool:
    """
    True if *ref* is an anaphoric carryover like "same contact", "him", "her"
    — i.e. a reference that should be resolved from recent WhatsApp context
    instead of being passed to ContactResolver.

    Single-word pronouns are matched exactly. Within longer phrases, pronouns
    are matched as whole words so "send it to him too" works but "shimmer"
    and "herbal tea" do not.
    """
    n = _norm(ref)
    if not n:
        return False
    # Exact pronoun match.
    if n in _CONTEXTUAL_PRONOUNS:
        return True
    # Pronoun as a whole word inside a longer phrase:
    # "send it to him too" → matches "him".
    for pronoun in _CONTEXTUAL_PRONOUNS:
        if re.search(rf"\b{re.escape(pronoun)}\b", n):
            return True
    return any(p in n for p in _CONTEXTUAL_PHRASES)


def is_contextual_artifact_reference(ref: str, action: Optional[str] = None) -> bool:
    """
    True if *ref* is an anaphoric artifact/file carryover like "that PDF",
    "same file", or — when *action* implies a file/media object ("send",
    "open", "save", "share") — a bare "it".

    Bare "it" is deliberately NOT an artifact reference for non-file
    intents: "it" only means the last file when the surrounding intent
    requires one ("send it", "open it", "save it"), so "it" never
    blindly resolves to the last file for unrelated commands.
    """
    n = _norm(ref)
    if not n:
        return False
    if any(p in n for p in _ARTIFACT_PHRASES):
        return True
    if action is not None and _norm(action) in _FILE_SEMANTIC_ACTIONS:
        if re.search(r"\bit\b", n):
            return True
    return False


def artifact_reference_kind(ref: str) -> Optional[str]:
    """
    Extract the media-kind constraint from an artifact reference, if any.

    Returns "pdf", "image", "document", "file" (any file), or None (no
    constraint — e.g. bare "it"). Resolution must not silently return an
    artifact of a different kind than the one the user named.
    """
    n = _norm(ref)
    if not n:
        return None
    if re.search(r"\bpdf\b", n):
        return "pdf"
    if re.search(r"\b(image|photo|picture|screenshot)\b", n):
        return "image"
    if re.search(r"\b(document|doc)\b", n):
        return "document"
    if re.search(r"\bfile\b", n):
        return "file"
    return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WAInteraction:
    """A recorded WhatsApp interaction (send or future receive hook)."""

    chat_id: str
    display_name: Optional[str] = None
    action: str = "send"                 # send_image | send_file | send_text
    message_id: Optional[str] = None
    file_path: Optional[str] = None
    timestamp: float = 0.0               # epoch seconds (UTC)


@dataclass
class ContactReference:
    """
    Resolved view over the latest interaction for the intent layer.

    Fields
    ------
    chat_id, display_name :
        The resolved WhatsApp identity. Empty chat_id signals failure
        (check `detail` for the reason).
    matched_by :
        "context_carryover" on success. "not_contextual" when *ref* was not
        an anaphoric reference. Empty string on failure.
    interaction_ts :
        Epoch seconds of the underlying interaction.
    action, message_id :
        Echoes of the underlying interaction for reporting.
    detail :
        Human-readable explanation (always populated, including on failure).
    """

    chat_id: str = ""
    display_name: Optional[str] = None
    matched_by: str = ""
    interaction_ts: float = 0.0
    action: Optional[str] = None
    message_id: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class WAArtifact:
    """
    A local file or WhatsApp attachment that entered the conversation.

    status
    ------
    "referenced" : the user referred to it / a plan resolved it — NOT sent.
    "sent"       : confirmed transport success — authoritative for
                   "send it again"-style resolution.
    "failed"     : a send was attempted and definitively failed — kept for
                   audit only, never resolved.

    kind
    ----
    "local_file"    : has a local `path` (screenshot, PDF, ...).
    "wa_attachment" : an inbound WhatsApp attachment identified by
                      (chat_id, message_id) — a future "open it" flow
                      resolves it via download_media() → local path.
    """

    filename: str
    kind: str = "local_file"                  # local_file | wa_attachment
    path: Optional[str] = None
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    mime_type: Optional[str] = None
    media_kind: Optional[str] = None          # "image" | "document"
    size_bytes: Optional[int] = None
    status: str = "referenced"                # referenced | sent | failed
    source: Optional[str] = None              # how it entered the context
    error_code: Optional[str] = None          # failed sends only
    timestamp: float = 0.0                    # epoch seconds (UTC)


@dataclass
class ArtifactReference:
    """
    Resolved view over the artifact context for the intent layer.

    Empty path AND empty chat_id signal failure (check `detail`).
    matched_by is "context_carryover" on success, "not_contextual" when
    *ref* was not an anaphoric artifact reference. resolution_tier reports
    which context tier answered: "last_sent" or "last_referenced".
    """

    filename: str = ""
    kind: str = ""                            # local_file | wa_attachment
    path: Optional[str] = None
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    mime_type: Optional[str] = None
    media_kind: Optional[str] = None
    size_bytes: Optional[int] = None
    matched_by: str = ""
    resolution_tier: str = ""
    artifact_ts: float = 0.0
    detail: Optional[str] = None


_WA_ARTIFACT_FIELDS = frozenset({
    "filename", "kind", "path", "chat_id", "message_id", "mime_type",
    "media_kind", "size_bytes", "status", "source", "error_code", "timestamp",
})


def _parse_artifacts(raw: list) -> List[WAArtifact]:
    """Parse persisted artifact dicts, tolerating unknown/future keys."""
    out: List[WAArtifact] = []
    for d in raw or []:
        try:
            if isinstance(d, dict):
                out.append(WAArtifact(**{
                    k: v for k, v in d.items() if k in _WA_ARTIFACT_FIELDS
                }))
        except TypeError:
            continue
    return out


def _artifact_matches_kind(art: WAArtifact, kind: Optional[str]) -> bool:
    """True if the artifact satisfies the reference's media-kind constraint."""
    if kind is None or kind == "file":
        return True
    if kind == "image":
        return art.media_kind == "image"
    if kind == "document":
        return art.media_kind == "document"
    if kind == "pdf":
        if art.mime_type and "pdf" in art.mime_type.lower():
            return True
        return bool(art.filename and art.filename.lower().endswith(".pdf"))
    return True


def _guess_mime(path: str) -> Optional[str]:
    try:
        import mimetypes
        mime, _ = mimetypes.guess_type(path)
        return mime
    except Exception:
        return None


def _default_path() -> Path:
    try:
        from api.config import settings
        base = settings.repo_root / "backend" / "data"
    except Exception:
        base = Path(__file__).resolve().parents[3] / "data"
    return base / "whatsapp_context.json"


def _parse_iso(ts_str: str) -> float:
    """Best-effort ISO 8601 → epoch seconds; returns 0.0 on failure."""
    if not ts_str:
        return 0.0
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# WhatsAppContext
# ---------------------------------------------------------------------------

class WhatsAppContext:
    """
    Narrow Phase 3 conversational context for WhatsApp interactions.

    Persists a rolling window of recent interactions AND artifacts to disk
    so the "same contact" / "him" / "her" contact carryover and the "it" /
    "that PDF" / "same file" artifact carryover both survive across
    processes. Designed to be replaced later by the broader World State
    Engine (the artifact records map cleanly onto a ContextStack push).
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path is not None else _default_path()
        self._lock = threading.Lock()
        self._interactions: List[WAInteraction] = []
        self._artifacts: List[WAArtifact] = []
        self.load()

    # ── persistence ───────────────────────────────────────────────────────

    def load(self) -> "WhatsAppContext":
        with self._lock:
            if not self._path.is_file():
                self._interactions = []
                self._artifacts = []
                return self
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._interactions = [
                    WAInteraction(**d) for d in data.get("interactions", [])
                ]
                # Absent in v1 files — empty list keeps contact carryover
                # working against pre-artifact context snapshots.
                self._artifacts = _parse_artifacts(data.get("artifacts", []))
            except Exception as e:
                logger.warning("[WA_CONTEXT] load failed: %s — resetting", e)
                self._interactions = []
                self._artifacts = []
        return self

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 2,
                "updated": datetime.now(timezone.utc).isoformat(),
                "interactions": [asdict(i) for i in self._interactions],
                "artifacts": [asdict(a) for a in self._artifacts],
            }
            self._path.write_text(
                json.dumps(payload, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("[WA_CONTEXT] save failed: %s", e)

    # ── recording ─────────────────────────────────────────────────────────

    def record_interaction(
        self,
        chat_id: str,
        display_name: Optional[str] = None,
        action: str = "send",
        message_id: Optional[str] = None,
        file_path: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> WAInteraction:
        inter = WAInteraction(
            chat_id=chat_id,
            display_name=display_name,
            action=action,
            message_id=message_id,
            file_path=file_path,
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        with self._lock:
            self._interactions.insert(0, inter)
            if len(self._interactions) > _MAX_INTERACTIONS:
                self._interactions = self._interactions[:_MAX_INTERACTIONS]
        self.save()
        logger.info(
            "[WA_CONTEXT] recorded: chat=%s action=%s msg=%s",
            chat_id, action, message_id,
        )
        return inter

    def last_interaction(self) -> Optional[WAInteraction]:
        with self._lock:
            return self._interactions[0] if self._interactions else None

    # ── artifact recording ────────────────────────────────────────────────

    def record_artifact(self, artifact: WAArtifact) -> WAArtifact:
        """Append an artifact record (newest first). Caller picks status."""
        with self._lock:
            self._artifacts.insert(0, artifact)
            if len(self._artifacts) > _MAX_ARTIFACTS:
                self._artifacts = self._artifacts[:_MAX_ARTIFACTS]
        self.save()
        logger.info(
            "[WA_CONTEXT] artifact recorded: file=%s status=%s source=%s",
            artifact.filename, artifact.status, artifact.source,
        )
        return artifact

    def record_referenced_artifact(
        self,
        path: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        media_kind: Optional[str] = None,
        size_bytes: Optional[int] = None,
        source: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> WAArtifact:
        """The user referenced a local file (a plan resolved it). NOT a send."""
        p = Path(path)
        return self.record_artifact(WAArtifact(
            filename=filename or p.name, kind="local_file", path=str(p),
            mime_type=mime_type, media_kind=media_kind, size_bytes=size_bytes,
            status="referenced", source=source or "plan",
            timestamp=timestamp if timestamp is not None else time.time(),
        ))

    def record_sent_artifact(
        self,
        path: str,
        chat_id: str,
        message_id: Optional[str] = None,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        media_kind: Optional[str] = None,
        size_bytes: Optional[int] = None,
        timestamp: Optional[float] = None,
    ) -> WAArtifact:
        """Confirmed transport success — authoritative for send carryover."""
        p = Path(path)
        return self.record_artifact(WAArtifact(
            filename=filename or p.name, kind="local_file", path=str(p),
            chat_id=chat_id, message_id=message_id, mime_type=mime_type,
            media_kind=media_kind, size_bytes=size_bytes,
            status="sent", source="send",
            timestamp=timestamp if timestamp is not None else time.time(),
        ))

    def record_failed_send(
        self,
        path: str,
        chat_id: Optional[str] = None,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        media_kind: Optional[str] = None,
        error_code: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> WAArtifact:
        """A definite send failure — audit trail only, never resolved."""
        p = Path(path)
        return self.record_artifact(WAArtifact(
            filename=filename or p.name, kind="local_file", path=str(p),
            chat_id=chat_id, mime_type=mime_type, media_kind=media_kind,
            status="failed", source="send", error_code=error_code,
            timestamp=timestamp if timestamp is not None else time.time(),
        ))

    def record_attachment_artifact(
        self,
        chat_id: str,
        message_id: str,
        filename: str,
        mime_type: Optional[str] = None,
        media_kind: Optional[str] = None,
        status: str = "referenced",
        timestamp: Optional[float] = None,
    ) -> WAArtifact:
        """An inbound WhatsApp attachment (future "open it" support)."""
        return self.record_artifact(WAArtifact(
            filename=filename, kind="wa_attachment", chat_id=chat_id,
            message_id=message_id, mime_type=mime_type, media_kind=media_kind,
            status=status, source="inbound",
            timestamp=timestamp if timestamp is not None else time.time(),
        ))

    def last_artifact(self, status: Optional[str] = None) -> Optional[WAArtifact]:
        """Newest artifact with the given status (None = any status)."""
        with self._lock:
            for a in self._artifacts:
                if status is None or a.status == status:
                    return a
        return None

    def last_sent_artifact(self) -> Optional[WAArtifact]:
        return self.last_artifact("sent")

    def last_referenced_artifact(self) -> Optional[WAArtifact]:
        return self.last_artifact("referenced")

    # ── resolution ────────────────────────────────────────────────────────

    def resolve_contact_reference(
        self,
        ref: str,
        max_age_s: float = DEFAULT_MAX_AGE_S,
    ) -> ContactReference:
        """
        Resolve an anaphoric contact reference from the recent interaction
        context. Returns a ContactReference with the resolved identity or a
        reason in `detail` describing why resolution failed.

        matched_by is "context_carryover" on success, "not_contextual" when
        *ref* is not an anaphoric reference (caller should fall through to
        ContactResolver), and an empty string on failure.
        """
        if not is_contextual_contact_reference(ref):
            return ContactReference(
                detail=f"'{ref}' is not a contextual contact reference",
                matched_by="not_contextual",
            )

        last = self.last_interaction()
        if last is None:
            return ContactReference(
                detail=(
                    f"no WhatsApp interaction on record — cannot resolve "
                    f"'{ref}' from context"
                ),
            )

        if max_age_s and (time.time() - last.timestamp) > max_age_s:
            age_h = (time.time() - last.timestamp) / 3600.0
            return ContactReference(
                detail=(
                    f"WhatsApp context expired ({age_h:.1f} h ago, "
                    f"max={max_age_s / 3600:.0f} h) — cannot resolve '{ref}'"
                ),
            )

        age_s = time.time() - last.timestamp
        return ContactReference(
            chat_id=last.chat_id,
            display_name=last.display_name,
            matched_by="context_carryover",
            interaction_ts=last.timestamp,
            action=last.action,
            message_id=last.message_id,
            detail=(
                f"'{ref}' → last WhatsApp interaction ({last.action}) with "
                f"{last.display_name or last.chat_id} "
                f"({age_s / 60.0:.1f} min ago, message_id={last.message_id})"
            ),
        )

    # ── artifact resolution ───────────────────────────────────────────────

    def resolve_artifact_reference(
        self,
        ref: str,
        action: Optional[str] = None,
        max_age_s: float = DEFAULT_MAX_AGE_S,
    ) -> ArtifactReference:
        """
        Resolve an anaphoric artifact reference ("it", "that PDF", "the
        file I just sent", ...) from the recent artifact context.

        Tier order is deterministic:
          - explicit "... I (just) sent" phrases → sent tier ONLY
          - send intents                → sent tier, then referenced tier
          - open/save intents (or none) → referenced tier, then sent tier

        Only artifacts within *max_age_s* and matching the reference's media
        kind (when it names one) are eligible. A local artifact whose file no
        longer exists fails resolution instead of silently falling back to
        an older artifact. Failed-send records are never resolved.
        """
        if not is_contextual_artifact_reference(ref, action):
            return ArtifactReference(
                matched_by="not_contextual",
                detail=(
                    f"'{ref}' is not a contextual artifact reference"
                    + ("" if action is None else f" for action '{action}'")
                ),
            )

        kind = artifact_reference_kind(ref)
        n = _norm(ref)
        if any(p in n for p in _ARTIFACT_SENT_PHRASES):
            tiers = ("sent",)
        elif action is not None and _norm(action) == "send":
            tiers = ("sent", "referenced")
        else:
            tiers = ("referenced", "sent")

        now = time.time()
        tier_label = {"sent": "last_sent", "referenced": "last_referenced"}

        with self._lock:
            artifacts = list(self._artifacts)

        for tier in tiers:
            fresh = [
                a for a in artifacts
                if a.status == tier
                and _artifact_matches_kind(a, kind)
                and (not max_age_s or now - a.timestamp <= max_age_s)
            ]
            if not fresh:
                continue
            art = fresh[0]
            if art.kind == "local_file" and art.path and not Path(art.path).is_file():
                return ArtifactReference(
                    detail=(
                        f"artifact '{art.filename}' no longer exists on disk "
                        f"({art.path}) — refusing to resolve '{ref}' to it"
                    ),
                )
            age_s = max(0.0, now - art.timestamp)
            return ArtifactReference(
                filename=art.filename, kind=art.kind, path=art.path,
                chat_id=art.chat_id, message_id=art.message_id,
                mime_type=art.mime_type, media_kind=art.media_kind,
                size_bytes=art.size_bytes, matched_by="context_carryover",
                resolution_tier=tier_label[tier], artifact_ts=art.timestamp,
                detail=(
                    f"'{ref}' → {tier_label[tier]} artifact '{art.filename}' "
                    f"({age_s / 60.0:.1f} min ago"
                    + (f", message_id={art.message_id}" if art.message_id else "")
                    + ")"
                ),
            )

        if not artifacts:
            return ArtifactReference(
                detail=f"no artifact context on record — cannot resolve '{ref}'",
            )
        window = f" within {max_age_s / 3600.0:.0f} h" if max_age_s else ""
        return ArtifactReference(
            detail=(
                f"no {'recent ' if max_age_s else ''}"
                f"{kind + ' ' if kind else ''}artifact in context matching "
                f"'{ref}'{window}"
            ),
        )

    # ── seeding from the sidecar message store ────────────────────────────

    def seed_from_message_store(
        self,
        db_path: str,
        display_name: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[WAInteraction]:
        """
        Derive the latest interaction from the sidecar message store and
        record it in this context.

        The display name for the chat is looked up from whatsapp_contacts
        first, then falls back to the explicit `display_name` hint. The hint
        carries user-provided ground truth at bootstrap time — it does not
        leak into the resolution path itself.
        """
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT message_id, chat_id, message_type, timestamp "
                "FROM whatsapp_messages WHERE from_me=1 "
                "ORDER BY timestamp DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                logger.info("[WA_CONTEXT] seed: no outgoing messages found")
                return None

            msg_id, chat_id, msg_type, ts_str = row
            ts = _parse_iso(ts_str) if ts_str else (now or time.time())

            # Contacts table: authoritative display name when present.
            name = display_name
            try:
                cur.execute(
                    "SELECT display_name, push_name FROM whatsapp_contacts "
                    "WHERE contact_id = ? LIMIT 1",
                    (chat_id,),
                )
                crow = cur.fetchone()
                if crow and (crow[0] or crow[1]):
                    name = crow[0] or crow[1]
            except sqlite3.Error:
                pass
            conn.close()

            action = {
                "image": "send_image", "document": "send_file",
                "text": "send_text", "video": "send_file",
                "audio": "send_file", "sticker": "send_file",
            }.get(msg_type, "send")

            inter = self.record_interaction(
                chat_id=chat_id,
                display_name=name,
                action=action,
                message_id=msg_id,
                timestamp=ts,
            )
            logger.info(
                "[WA_CONTEXT] seeded from store: chat=%s name=%s msg_id=%s",
                chat_id, name, msg_id,
            )
            return inter
        except sqlite3.Error as e:
            logger.warning("[WA_CONTEXT] seed failed: %s", e)
            return None

    # ── artifact bootstrap ────────────────────────────────────────────────

    def bootstrap_artifacts_from_interactions(self) -> int:
        """
        One-time migration: derive sent artifacts from recorded interactions.

        Interactions are recorded only AFTER confirmed transport success, so
        an interaction carrying a file_path is a legitimate "sent" artifact.
        No-op when artifact records already exist (e.g. after a restart).
        """
        with self._lock:
            if self._artifacts:
                return 0
            seeds = [i for i in self._interactions if i.file_path]
        count = 0
        for inter in seeds:
            media_kind = "image" if inter.action == "send_image" else "document"
            self.record_artifact(WAArtifact(
                filename=Path(inter.file_path).name,
                kind="local_file", path=inter.file_path,
                chat_id=inter.chat_id, message_id=inter.message_id,
                mime_type=_guess_mime(inter.file_path),
                media_kind=media_kind, status="sent",
                source="interaction_bootstrap", timestamp=inter.timestamp,
            ))
            count += 1
        if count:
            logger.info(
                "[WA_CONTEXT] bootstrapped %d artifact(s) from interactions", count,
            )
        return count

    # ── helpers ───────────────────────────────────────────────────────────

    def clear(self) -> None:
        with self._lock:
            self._interactions = []
            self._artifacts = []
        self.save()

    def __repr__(self) -> str:
        return f"WhatsAppContext(path={self._path!r}, n={len(self._interactions)})"


# ---------------------------------------------------------------------------
# Module-level default instance (lazy — not instantiated on import).
# ---------------------------------------------------------------------------

_default_ctx: Optional[WhatsAppContext] = None


def get_default_context() -> WhatsAppContext:
    """Return the module-level default WhatsAppContext instance."""
    global _default_ctx
    if _default_ctx is None:
        _default_ctx = WhatsAppContext()
    return _default_ctx
