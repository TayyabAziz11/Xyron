"""
wa_identity.py — canonical WhatsApp contact identity layer (Phase 4).

Fast, cached name -> JID resolution so a known contact ("Tayyab") never
needs a network find_contact()/verify_on_whatsapp() round trip once it has
been resolved with confidence once. Identities are DATA, never parser
logic: wa_intent.py never hardcodes a person's name, it only asks this
store "do we already know this reference?".

PN JID and LID belong to the SAME logical identity when confidently
correlated (see learn()) — Baileys v7's LID migration means a contact can
surface under either identity depending on how WhatsApp routed the lookup.

Persistence
-----------
Identities are stored as JSON under backend/data/whatsapp_identities.json,
mirroring wa_context.py's persistence convention exactly (same directory,
same load-on-init/save-on-mutate shape, same missing-file-is-empty
semantics). Thread safety: a module-level Lock guards all mutations.

This module never raises — every public method returns a value or None.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("wa_identity")

_MAX_IDENTITIES = 500

# Confidence a ContactResolver match must clear before it is worth caching.
# "phone_constructed" and low-confidence substring matches are deliberately
# excluded — caching an unreliable resolution would let a later command
# silently reuse a wrong JID at O(1) speed, which is worse than the network
# round trip it was meant to save.
_LEARNABLE_MATCH_KINDS: Dict[str, float] = {
    "exact_name": 0.95,
    "phone": 0.9,
    "on_whatsapp_verified": 0.85,
}


def _norm(ref: str) -> str:
    """Case/punctuation/whitespace normalization — mirrors wa_context._norm
    so a name typed or spoken with different casing/punctuation still hits
    the same cache entry (Part 27 — STT-friendly identity)."""
    s = (ref or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _edit_distance(a: str, b: str) -> int:
    """Standard Levenshtein distance, O(len(a)*len(b)), no dependency.
    Used only for the bounded fuzzy-alias fallback below — deterministic,
    not phonetic/ML matching."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[len(b)]


@dataclass
class WhatsAppContactIdentity:
    canonical_name: str
    aliases: List[str] = field(default_factory=list)
    phone: Optional[str] = None
    pn_jid: Optional[str] = None
    lid: Optional[str] = None
    display_name: Optional[str] = None
    verified_on_whatsapp: bool = False
    source: str = "learned"          # bootstrap | learned | manual
    confidence: float = 0.0
    last_verified: Optional[float] = None
    last_used: Optional[float] = None

    def primary_chat_id(self) -> Optional[str]:
        """The chat_id to use for transport calls — PN JID preferred over LID."""
        return self.pn_jid or self.lid


def _default_path() -> Path:
    try:
        from api.config import settings
        base = settings.repo_root / "backend" / "data"
    except Exception:
        base = Path(__file__).resolve().parents[3] / "data"
    return base / "whatsapp_identities.json"


# Bootstrap identity — Phase 3's live-verified contact (handoff §25/§39).
# DATA only: adding Ali/Sara/... later means appending here or (more
# realistically) letting learn() populate them from real resolutions —
# never adding name-specific logic to the parser or this store.
_BOOTSTRAP: List[dict] = [
    dict(
        canonical_name="Tayyab Aziz",
        aliases=["Tayyab", "Tayyab Aziz"],
        phone="+923001234567",
        pn_jid="923001234567@s.whatsapp.net",
        display_name="Tayyab Aziz",
        verified_on_whatsapp=True,
        source="bootstrap",
        confidence=1.0,
    ),
]


class WhatsAppIdentityStore:
    """
    O(1) canonical-name / alias -> WhatsAppContactIdentity cache.

    Resolution priority this store implements for the caller:
        exact canonical-name cache -> exact alias cache -> (miss)
    Everything past that (contextual pronouns, ContactResolver, network
    verification) is the caller's job — this store only ever does in-memory
    dict lookups, never I/O beyond its own load/save.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _default_path()
        self._lock = threading.Lock()
        self._identities: List[WhatsAppContactIdentity] = []
        self._by_name: Dict[str, WhatsAppContactIdentity] = {}
        self._by_alias: Dict[str, WhatsAppContactIdentity] = {}
        self.load()

    # ── persistence ──────────────────────────────────────────────────────

    def load(self) -> "WhatsAppIdentityStore":
        if not self._path.exists():
            logger.info("[WA_IDENTITY] no identity file at %s — seeding bootstrap", self._path)
            with self._lock:
                self._identities = [WhatsAppContactIdentity(**d) for d in _BOOTSTRAP]
                self._rebuild_indexes_locked()
            self.save()
            return self

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("[WA_IDENTITY] load failed (%s) — starting empty", e)
            raw = {}

        idents: List[WhatsAppContactIdentity] = []
        for d in raw.get("identities", []) if isinstance(raw, dict) else []:
            try:
                idents.append(WhatsAppContactIdentity(
                    canonical_name=d.get("canonical_name", ""),
                    aliases=list(d.get("aliases") or []),
                    phone=d.get("phone"),
                    pn_jid=d.get("pn_jid"),
                    lid=d.get("lid"),
                    display_name=d.get("display_name"),
                    verified_on_whatsapp=bool(d.get("verified_on_whatsapp", False)),
                    source=d.get("source", "learned"),
                    confidence=float(d.get("confidence", 0.0)),
                    last_verified=d.get("last_verified"),
                    last_used=d.get("last_used"),
                ))
            except (TypeError, ValueError):
                continue

        with self._lock:
            self._identities = idents
            self._rebuild_indexes_locked()
        return self

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {
                    "version": 1,
                    "updated": time.time(),
                    "identities": [asdict(i) for i in self._identities],
                }
            self._path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            logger.warning("[WA_IDENTITY] save failed: %s", e)

    def _rebuild_indexes_locked(self) -> None:
        """Caller must hold self._lock."""
        self._by_name = {}
        self._by_alias = {}
        for ident in self._identities:
            n = _norm(ident.canonical_name)
            if n:
                self._by_name[n] = ident
            for alias in ident.aliases:
                a = _norm(alias)
                if a:
                    self._by_alias[a] = ident

    # ── lookup ───────────────────────────────────────────────────────────

    def resolve_cached(self, ref: str, allow_fuzzy: bool = False) -> Optional[WhatsAppContactIdentity]:
        """
        O(1) canonical-name/alias lookup. No network, no disk I/O beyond
        the touch()-triggered save. Returns None on a miss — the caller
        must fall through to ContactResolver, never guess.

        allow_fuzzy (Phase 5 — voice STT spelling variants, handoff §4):
        when the exact lookup misses, also try a bounded edit-distance
        match against every known canonical name/alias (see
        _resolve_fuzzy_locked). Off by default — every existing caller
        (including IntentRouter's Tier 0.5 commit check) keeps its exact
        behavior unless it explicitly opts in. A fuzzy hit is still just a
        contact resolution, not a bypass of anything: a send still goes
        through the normal confirm_required prompt naming the resolved
        person, so a wrong fuzzy guess surfaces there for the user to
        reject rather than silently sending to the wrong contact.
        """
        n = _norm(ref)
        if not n:
            return None
        with self._lock:
            ident = self._by_name.get(n) or self._by_alias.get(n)
            if ident is None and allow_fuzzy:
                ident = self._resolve_fuzzy_locked(n)
        if ident is not None:
            self.touch(ident)
            logger.debug("[WA_IDENTITY] cache hit: %r -> %s", ref, ident.canonical_name)
        return ident

    def _resolve_fuzzy_locked(self, n: str) -> Optional[WhatsAppContactIdentity]:
        """
        Bounded, deterministic edit-distance fallback — never phonetic/ML
        guessing, just a tight Levenshtein threshold against names already
        known to this store (never invented, never hardcoded to any
        specific person). Caller must hold self._lock.

        Guardrails against a wrong silent match:
          - threshold scales with name length (short names need an almost
            exact match) and is capped, never "close enough" for very
            short strings ("hi"/"ok" never fuzzy-match anything)
          - if more than one DISTINCT identity falls within threshold,
            refuse entirely rather than pick one — ambiguity is a miss,
            not a coin flip
          - does NOT learn the variant as a new alias automatically; that
            stays a deliberate add_alias() call, never implicit
        """
        if len(n) < 3:
            return None  # too short for a safe distance comparison
        threshold = max(1, len(n) // 5)

        matches: Dict[str, WhatsAppContactIdentity] = {}  # canonical_name -> identity
        for ident in self._identities:
            candidates = [ident.canonical_name] + list(ident.aliases)
            for cand in candidates:
                cand_n = _norm(cand)
                if not cand_n or abs(len(cand_n) - len(n)) > threshold:
                    continue
                if _edit_distance(n, cand_n) <= threshold:
                    matches[ident.canonical_name] = ident
                    break

        if len(matches) == 1:
            ident = next(iter(matches.values()))
            logger.info("[WA_IDENTITY] fuzzy match: %r -> %s (threshold=%d)", n, ident.canonical_name, threshold)
            return ident
        if len(matches) > 1:
            logger.debug("[WA_IDENTITY] fuzzy match ambiguous for %r: %s", n, list(matches.keys()))
        return None

    def touch(self, ident: WhatsAppContactIdentity) -> None:
        ident.last_used = time.time()
        self.save()

    # ── learning ─────────────────────────────────────────────────────────

    def learn(
        self,
        canonical_name: str,
        chat_id: str,
        display_name: Optional[str] = None,
        matched_by: str = "",
        phone: Optional[str] = None,
    ) -> Optional[WhatsAppContactIdentity]:
        """
        Cache a ContactResolver resolution for future O(1) lookup. Only
        called by the caller for high-confidence matches — matched_by must
        be one of _LEARNABLE_MATCH_KINDS or this is a no-op (never poison
        the cache with an ambiguous/constructed guess).

        Merges into an existing identity by canonical_name when present, so
        a PN JID and a LID discovered for the same person on different
        occasions correlate onto one identity instead of creating a second,
        conflicting record (handoff §24 — "belong to the SAME logical
        identity when confidently correlated").
        """
        if not canonical_name or not chat_id:
            return None
        confidence = _LEARNABLE_MATCH_KINDS.get(matched_by)
        if confidence is None:
            logger.debug("[WA_IDENTITY] refusing to learn low-confidence match_by=%r", matched_by)
            return None

        n = _norm(canonical_name)
        now = time.time()
        with self._lock:
            ident = self._by_name.get(n)
            if ident is None:
                ident = WhatsAppContactIdentity(
                    canonical_name=canonical_name,
                    aliases=[canonical_name],
                    source="learned",
                )
                self._identities.insert(0, ident)
                if len(self._identities) > _MAX_IDENTITIES:
                    self._identities = self._identities[:_MAX_IDENTITIES]

            if chat_id.endswith("@lid"):
                ident.lid = chat_id
            else:
                ident.pn_jid = chat_id
            if phone:
                ident.phone = phone
            if display_name:
                ident.display_name = display_name
            ident.verified_on_whatsapp = True
            ident.confidence = max(ident.confidence, confidence)
            ident.last_verified = now
            ident.last_used = now
            self._rebuild_indexes_locked()

        self.save()
        logger.info(
            "[WA_IDENTITY] learned: %r -> chat_id=%s matched_by=%s confidence=%.2f",
            canonical_name, chat_id, matched_by, confidence,
        )
        return ident

    def add_alias(self, canonical_name: str, alias: str) -> bool:
        """
        Explicit alias learning only (handoff §27 — no unconstrained
        phonetic matching). Returns False if canonical_name is unknown.
        """
        n = _norm(canonical_name)
        a = _norm(alias)
        if not n or not a:
            return False
        with self._lock:
            ident = self._by_name.get(n)
            if ident is None:
                return False
            if alias not in ident.aliases:
                ident.aliases.append(alias)
            self._rebuild_indexes_locked()
        self.save()
        logger.info("[WA_IDENTITY] alias added: %r -> %s", alias, canonical_name)
        return True

    def all(self) -> List[WhatsAppContactIdentity]:
        with self._lock:
            return list(self._identities)

    def clear(self) -> None:
        with self._lock:
            self._identities = []
            self._by_name = {}
            self._by_alias = {}
        self.save()


_default_store: Optional[WhatsAppIdentityStore] = None
_default_store_lock = threading.Lock()


def get_default_identity_store() -> WhatsAppIdentityStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = WhatsAppIdentityStore()
        return _default_store
