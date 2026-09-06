"""
contact_resolver.py — resolve a human reference ("Ali", "Sara", "+213...",
"123456@s.whatsapp.net") into an exact WhatsApp chat_id, with strict
anti-ambiguity guards.

Resolution strategy (strict priority, never fuzzy-pick):

  1. Exact JID    — input already looks like <number>@<domain> (s.whatsapp.net,
                    lid, g.us, newsletter) → accept immediately.
  2. Phone number — all digits with optional '+' →
                    a. find_contact with the number
                    b. exact phone match (case-insensitive) → resolved
                    c. single substring match → resolved with flag
                    d. zero local matches → verify_on_whatsapp() authoritative
                       check (Baileys usync):
                         • exists + jid → resolved ("on_whatsapp_verified") —
                           the jid may be @lid, never assume @s.whatsapp.net
                         • exists=False → not_found (number not on WhatsApp,
                           never construct a JID for it)
                         • check unavailable → constructed-JID fallback with
                           warning (LID migration makes this unreliable)
  3. Name         — find_contact with the name
                    a. single result with exact display_name (case-insensitive)
                       → resolved (matched_by="exact_name")
                    b. exactly one substring match total
                       → resolved (matched_by="unique_name_match",
                          contact shown for approval — never auto-send)
                    c. multiple matches → ambiguous, return candidates
                    d. zero matches → not_found

The caller (WhatsAppAgent / intent layer) must always surface the resolution
to the user before sending. This module never auto-sends and never guesses.

This module never raises — every method returns a structured result.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("wa_contact_resolver")

# Matches <digits or lid>@<valid-domain>
_JID_RE = re.compile(
    r'^[\w.\-]+@(s\.whatsapp\.net|lid|g\.us|newsletter|broadcast)$',
    re.IGNORECASE,
)

# Loose phone number: optional '+' then digits, spaces, dashes, parens (≥7 digits)
_PHONE_RE = re.compile(r'^\+?[\d\s\-\(\)]{7,}$')


def _extract_digits(s: str) -> str:
    """Strip everything except digits (and a leading '+')."""
    if not s:
        return ""
    out = s.lstrip("+")
    return re.sub(r'\D', '', out)


def _chat_id_of(c: Dict[str, Any]) -> Optional[str]:
    """
    A contact record's usable identifier — 'chat_id' when the sidecar has
    a resolved identity (normal case), else 'contact_id' (an @lid), which
    is what a contact synced via WhatsApp history import often has instead
    (Baileys v7's LID migration — see handoff §24: "many @lid identities,
    few display names, few/no directly stored phone numbers"). Live testing
    found a genuinely real, exact-name-matched contact whose 'chat_id' was
    null but whose 'contact_id' held a usable @lid — without this fallback,
    a fully correct name match still resolved to chat_id=None, silently
    failing every downstream send.
    """
    return c.get("chat_id") or c.get("contact_id")


@dataclass
class ContactResolution:
    status: str = "not_found"          # resolved | ambiguous | not_found | invalid
    chat_id: Optional[str] = None
    display_name: Optional[str] = None
    matched_by: Optional[str] = None   # exact_jid | phone | phone_constructed
                                       # | exact_name | unique_name_match
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    detail: Optional[str] = None       # human-readable explanation


class ContactResolver:
    """
    Resolve a human reference to an exact WhatsApp chat_id.

    Parameters
    ----------
    transport : WhatsAppTransport
        Any concrete transport (BaileysTransport, etc.) that exposes
        find_contact(query) -> list[dict].
    """

    def __init__(self, transport) -> None:
        self._transport = transport

    # ── public API ──────────────────────────────────────────────────────────

    def resolve(self, ref: str) -> ContactResolution:
        """
        Resolve a single contact reference. Never raises.

        ref : str
            The human reference — JID, phone number, or name.
        """
        if not ref or not ref.strip():
            return ContactResolution(status="invalid", detail="empty contact reference")

        ref = ref.strip()

        # 1. Exact JID — already fully-qualified
        if _JID_RE.match(ref):
            return ContactResolution(
                status="resolved",
                chat_id=ref,
                matched_by="exact_jid",
                detail=f"exact WhatsApp JID: {ref}",
            )

        # 2. Phone number
        if _PHONE_RE.match(ref):
            return self._resolve_phone(ref)

        # 3. Name / display name
        return self._resolve_name(ref)

    # ── internals ───────────────────────────────────────────────────────────

    def _resolve_phone(self, raw: str) -> ContactResolution:
        digits = _extract_digits(raw)
        if len(digits) < 7:
            return ContactResolution(
                status="invalid",
                detail=f"phone number has only {len(digits)} digits",
            )

        # Query sidecar contacts — look for an exact phone match first
        try:
            hits = self._transport.find_contact(digits)
        except Exception as e:
            logger.warning("[CONTACT_RESOLVE] find_contact failed for phone: %s", e)
            hits = []

        # Exact phone match (digits must appear in the stored phone field)
        for c in hits:
            stored = _extract_digits(str(c.get("phone", "")))
            if stored and digits in stored:
                return ContactResolution(
                    status="resolved",
                    chat_id=_chat_id_of(c),
                    display_name=c.get("display_name") or c.get("push_name"),
                    matched_by="phone",
                    detail=f"phone match: {c.get('display_name') or c.get('push_name', '?')}",
                )

        # Single substring match — accept with flag (it's still a phone-based
        # match, just via the name field rather than phone field)
        if len(hits) == 1:
            c = hits[0]
            return ContactResolution(
                status="resolved",
                chat_id=_chat_id_of(c),
                display_name=c.get("display_name") or c.get("push_name"),
                matched_by="phone_constructed",
                detail=(
                    f"single contact found for {raw}: "
                    f"{c.get('display_name') or c.get('push_name', '?')} "
                    f"({_chat_id_of(c) or '?'})"
                ),
            )

        if len(hits) > 1:
            return ContactResolution(
                status="ambiguous",
                candidates=hits,
                detail=f"{len(hits)} contacts matched '{raw}' — which one?",
            )

        # Zero local matches — ask WhatsApp itself (Baileys usync query).
        # This is the authoritative phone → identity resolution: the returned
        # jid may be an @lid identity (LID migration), which is exactly why we
        # must not blindly construct <digits>@s.whatsapp.net.
        try:
            verification = self._transport.verify_on_whatsapp(digits)
        except Exception as e:
            logger.warning("[CONTACT_RESOLVE] verify_on_whatsapp failed: %s", e)
            verification = None

        if verification is not None:
            if verification.get("exists") and verification.get("jid"):
                jid = verification["jid"]
                domain = jid.split("@")[1] if "@" in jid else "?"
                return ContactResolution(
                    status="resolved",
                    chat_id=jid,
                    matched_by="on_whatsapp_verified",
                    detail=(
                        f"WhatsApp confirms {raw} is registered; canonical identity "
                        f"is {domain} (returned by WhatsApp's own directory query)"
                    ),
                )
            # exists=False — the number is provably NOT on WhatsApp.
            return ContactResolution(
                status="not_found",
                detail=(
                    f"WhatsApp reports {raw} is not registered on WhatsApp "
                    "— refusing to construct a JID for a number that cannot receive messages"
                ),
            )

        # Verification unavailable (old sidecar / query failure) — fall back to
        # constructing a JID from digits. LID migration makes this unreliable,
        # so we flag it for approval and the sidecar will fail with a clear
        # error if the constructed JID doesn't exist.
        constructed = f"{digits}@s.whatsapp.net"
        logger.warning(
            "[CONTACT_RESOLVE] no contact found for %s and verification unavailable — "
            "constructing JID %s (may not reach the intended person due to LID migration)",
            raw, constructed,
        )
        return ContactResolution(
            status="resolved",
            chat_id=constructed,
            matched_by="phone_constructed",
            detail=(
                f"No known contact for {raw} and WhatsApp verification unavailable — "
                f"constructed JID {constructed}. "
                "Please verify this is the correct recipient before approving."
            ),
        )

    def _resolve_name(self, name: str) -> ContactResolution:
        name_l = name.lower()
        try:
            hits = self._transport.find_contact(name)
        except Exception as e:
            logger.warning("[CONTACT_RESOLVE] find_contact failed for name: %s", e)
            hits = []

        if not hits:
            return ContactResolution(
                status="not_found",
                detail=f"no WhatsApp contact matches '{name}'",
            )

        # Exact display_name match (case-insensitive, full name)
        exact = [c for c in hits if (c.get("display_name") or "").lower() == name_l]
        if len(exact) == 1:
            c = exact[0]
            return ContactResolution(
                status="resolved",
                chat_id=_chat_id_of(c),
                display_name=c.get("display_name") or c.get("push_name"),
                matched_by="exact_name",
                detail=f"exact name match: {c.get('display_name') or c.get('push_name', '?')}",
            )

        # Single substring hit — accept but flag
        if len(hits) == 1:
            c = hits[0]
            return ContactResolution(
                status="resolved",
                chat_id=_chat_id_of(c),
                display_name=c.get("display_name") or c.get("push_name"),
                matched_by="unique_name_match",
                detail=(
                    f"unique name match for '{name}': "
                    f"{c.get('display_name') or c.get('push_name', '?')} "
                    f"({_chat_id_of(c) or '?'})"
                ),
            )

        # Multiple matches — ambiguous, refuse to guess
        return ContactResolution(
            status="ambiguous",
            candidates=hits,
            detail=(
                f"{len(hits)} contacts match '{name}' — "
                "please specify which one (full name or phone number)"
            ),
        )
