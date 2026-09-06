"""
wa_command_handler.py — orchestration between a parsed WAIntent and the
existing Phase 3 primitives.

This module owns sequencing and latency instrumentation only (handoff
§30/§34). It never reimplements contact disambiguation (ContactResolver),
file resolution (FileSendPlanner), contextual pronoun resolution
(wa_context.py), or visual surfacing (WhatsAppUIAdapter) — it calls them
in the right order and turns the result into a WAOutcome the tool layer
can render.

Every public method returns (WAOutcome, LatencyTimer) and never raises —
transport/UI/resolver failures become a failed WAOutcome, matching the
"never raises" convention every other module in this package follows.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .baileys_transport import BaileysTransport
from .contact_resolver import ContactResolution, ContactResolver
from .file_send import FileSendPlan, FileSendPlanner
from .models import WAAction, WhatsAppRequest
from .wa_context import WhatsAppContext, get_default_context, is_contextual_contact_reference
from .wa_identity import WhatsAppIdentityStore, get_default_identity_store
from .wa_ui_adapter import WhatsAppUIAdapter, WhatsAppUITarget, get_default_ui_adapter

logger = logging.getLogger("wa_command_handler")


class LatencyTimer:
    """Collects named checkpoints relative to construction (handoff §34)."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.marks: Dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks[name] = round((time.perf_counter() - self._t0) * 1000.0, 2)

    def total_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000.0, 2)

    def as_dict(self) -> Dict[str, float]:
        d = dict(self.marks)
        d["total_ms"] = self.total_ms()
        return d


@dataclass
class ResolvedContact:
    chat_id: str
    display_name: Optional[str] = None
    message_id: Optional[str] = None   # populated only for contextual (him/her) resolution
    method: str = ""                   # contextual | cached | resolved_network
    matched_by: str = ""


@dataclass
class WAOutcome:
    ok: bool
    spoken: str
    text: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    needs_confirmation: bool = False
    ambiguous: bool = False
    candidates: List[Dict[str, Any]] = field(default_factory=list)


class WACommandHandler:
    def __init__(
        self,
        transport: Optional[BaileysTransport] = None,
        identity_store: Optional[WhatsAppIdentityStore] = None,
        context: Optional[WhatsAppContext] = None,
        ui_adapter: Optional[WhatsAppUIAdapter] = None,
    ) -> None:
        # Transport is lazy — BaileysTransport.from_settings() raises if
        # wa_sidecar_api_key isn't configured. show_chat (and any
        # cached-identity resolution) never calls a transport method at
        # all, so constructing it eagerly here would make a pure UI action
        # ("show me X's WhatsApp") fail just because Baileys isn't set up
        # yet — exactly the coupling the Phase 4 sidecar note calls out.
        # Only send/reply/get_messages/network-fallback-resolve actually
        # need it, and they build it on first real use via _transport_ready().
        self._transport_obj: Optional[BaileysTransport] = transport
        self._identities = identity_store or get_default_identity_store()
        self._context = context or get_default_context()
        self._contact_resolver_obj: Optional[ContactResolver] = None
        self._file_planner_obj: Optional[FileSendPlanner] = None
        # Lazy — constructing WhatsAppUIAdapter probes for WhatsApp Desktop
        # on first use; never do that at handler-construction time.
        self._ui_adapter = ui_adapter

    def _transport_ready(self) -> BaileysTransport:
        """Construct (or reuse) the Baileys transport. Only called by code
        paths that actually need it — send/reply/get_messages, or the
        network-fallback branch of resolve_contact. Raises ValueError if
        wa_sidecar_api_key isn't configured; callers must catch this and
        turn it into a clean WAOutcome, never let it escape uncaught."""
        if self._transport_obj is None:
            self._transport_obj = BaileysTransport.from_settings()
        return self._transport_obj

    def _contact_resolver(self) -> ContactResolver:
        if self._contact_resolver_obj is None:
            self._contact_resolver_obj = ContactResolver(self._transport_ready())
        return self._contact_resolver_obj

    def _file_planner(self) -> FileSendPlanner:
        if self._file_planner_obj is None:
            self._file_planner_obj = FileSendPlanner(self._transport_ready(), self._context)
        return self._file_planner_obj

    def _ui(self) -> WhatsAppUIAdapter:
        if self._ui_adapter is None:
            self._ui_adapter = get_default_ui_adapter(allow_web_fallback=True)
        return self._ui_adapter

    # ── contact resolution ──────────────────────────────────────────────
    # Priority: contextual pronoun -> identity cache (O(1), no network) ->
    # ContactResolver (network find_contact/verify_on_whatsapp). Matches
    # handoff §26 exactly; the first two never touch the transport.

    def resolve_contact(
        self, ref: str, timer: Optional[LatencyTimer] = None,
    ) -> Tuple[Optional[ResolvedContact], str, Optional[ContactResolution]]:
        """
        Returns (resolved, status, raw_resolution).
        status: "contextual" | "cached" | "resolved_network" | "ambiguous"
              | "not_found" | "invalid"
        raw_resolution carries .candidates/.detail on ambiguous/not_found.
        """
        timer = timer or LatencyTimer()
        if not ref or not ref.strip():
            return None, "invalid", None

        if is_contextual_contact_reference(ref):
            cr = self._context.resolve_contact_reference(ref)
            timer.mark("contact_resolution_ms")
            if cr.chat_id:
                return ResolvedContact(
                    chat_id=cr.chat_id, display_name=cr.display_name,
                    message_id=cr.message_id, method="contextual",
                    matched_by="context_carryover",
                ), "contextual", None
            return None, "not_found", None

        ident = self._identities.resolve_cached(ref)
        if ident and ident.primary_chat_id():
            timer.mark("contact_resolution_ms")
            return ResolvedContact(
                chat_id=ident.primary_chat_id(),
                display_name=ident.display_name or ident.canonical_name,
                method="cached", matched_by="identity_cache",
            ), "cached", None

        # Phase 5 — voice STT spelling variants ("Tayab" for "Tayyab"): a
        # bounded edit-distance fallback against known names/aliases, tried
        # only after the exact lookup misses. Still no network call — this
        # is the identity store's own in-memory index. A wrong fuzzy guess
        # is never silently sent: the resolved display_name still goes
        # through the normal spoken/typed confirmation prompt.
        ident = self._identities.resolve_cached(ref, allow_fuzzy=True)
        if ident and ident.primary_chat_id():
            timer.mark("contact_resolution_ms")
            return ResolvedContact(
                chat_id=ident.primary_chat_id(),
                display_name=ident.display_name or ident.canonical_name,
                method="cached_fuzzy", matched_by="identity_fuzzy",
            ), "cached", None

        try:
            resolver = self._contact_resolver()
        except Exception as e:
            logger.warning("[WA_CMD] transport unavailable for network contact resolution: %s", e)
            timer.mark("contact_resolution_ms")
            return None, "not_found", None

        res = resolver.resolve(ref)
        timer.mark("contact_resolution_ms")
        if res.status == "resolved":
            if res.matched_by in ("exact_name", "phone", "on_whatsapp_verified") and res.display_name:
                self._identities.learn(
                    canonical_name=res.display_name, chat_id=res.chat_id,
                    display_name=res.display_name, matched_by=res.matched_by,
                )
            return ResolvedContact(
                chat_id=res.chat_id, display_name=res.display_name,
                method="resolved_network", matched_by=res.matched_by or "",
            ), "resolved_network", res
        if res.status == "ambiguous":
            return None, "ambiguous", res
        if res.status == "not_found":
            return None, "not_found", res
        return None, "invalid", res

    def _ambiguous_outcome(self, contact_ref: str, res: Optional[ContactResolution]) -> WAOutcome:
        cands = res.candidates if res else []
        names = [c.get("display_name") or c.get("push_name") or "someone" for c in cands[:3]]
        return WAOutcome(
            ok=False, ambiguous=True, candidates=cands,
            spoken=f"I found more than one contact matching {contact_ref} — {', '.join(names)}. Which one did you mean?",
            text=(res.detail if res else f"ambiguous contact reference '{contact_ref}'"),
        )

    def _not_found_outcome(self, contact_ref: str, res: Optional[ContactResolution]) -> WAOutcome:
        return WAOutcome(
            ok=False,
            spoken=f"I couldn't find {contact_ref} on WhatsApp.",
            text=(res.detail if res else f"no WhatsApp contact matches '{contact_ref}'"),
        )

    def _ambiguous_file_outcome(self, plan: FileSendPlan) -> WAOutcome:
        """Disambiguation prompt for 2+ files matching the same name/query —
        a DIFFERENT kind of 'needs_clarification' than a contact being
        ambiguous. plan.candidates here are FileCandidate objects (path/
        filename/location), not the ContactResolution candidate dicts that
        _ambiguous_outcome expects. Bug this fixes: plan_send_file() used to
        call _ambiguous_outcome(contact_ref, plan.contact_resolution) for
        BOTH cases — but on a file-name collision, plan.contact_resolution
        is the single successfully-resolved contact (status='resolved',
        candidates=[]), so that produced a broken spoken response ("I found
        more than one contact matching resume.pdf — . Which one did you
        mean?") with an empty name list."""
        cands = plan.candidates or []
        names = [c.filename for c in cands[:3]]
        return WAOutcome(
            ok=False, ambiguous=True,
            candidates=[
                {"filename": c.filename, "path": c.path, "location": c.location}
                for c in cands
            ],
            spoken=(
                f"I found {len(cands)} files named that — {', '.join(names)}. "
                "Which one did you mean?"
            ),
            text=plan.detail or "ambiguous file reference",
        )

    # ── send_text ─────────────────────────────────────────────────────────

    def plan_send_text(self, contact_ref: str, message: str) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        if not message or not message.strip():
            return WAOutcome(ok=False, spoken="What should I say?", text="empty message"), timer
        resolved, status, raw = self.resolve_contact(contact_ref, timer)
        if status == "ambiguous":
            return self._ambiguous_outcome(contact_ref, raw), timer
        if resolved is None:
            return self._not_found_outcome(contact_ref, raw), timer
        timer.mark("planning_ms")
        prompt = f'Send "{message}" to {resolved.display_name or "this contact"}?'
        return WAOutcome(
            ok=False, needs_confirmation=True, spoken=prompt, text=prompt,
            data={
                "resolved_chat_id": resolved.chat_id,
                "resolved_display_name": resolved.display_name,
                "contact_matched_by": resolved.matched_by,
                "contact_resolution_method": resolved.method,
                "latency_ms": timer.as_dict(),
            },
        ), timer

    def execute_send_text(
        self, chat_id: str, display_name: Optional[str], message: str,
        idempotency_key: str, show_ui: bool = False,
    ) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        try:
            transport = self._transport_ready()
        except Exception as e:
            return WAOutcome(ok=False, spoken="WhatsApp isn't set up yet.",
                              text=f"transport unavailable: {e}"), timer
        request = WhatsAppRequest(
            action=WAAction.SEND_TEXT, recipient=chat_id, content=message,
            idempotency_key=idempotency_key,
        )
        result = transport.send_text(request)
        timer.mark("transport_ms")
        name = display_name or "them"
        if not result.success:
            return WAOutcome(
                ok=False, spoken=f"I couldn't send that to {name}.",
                text=f"send failed: {result.error_message or result.error_code}",
                data={"latency_ms": timer.as_dict()},
            ), timer
        try:
            self._context.record_interaction(
                chat_id=chat_id, display_name=display_name,
                action="send_text", message_id=result.message_id,
            )
        except Exception:
            logger.debug("[WA_CMD] context recording failed (non-fatal)", exc_info=True)
        ui_note = self._maybe_surface_ui(chat_id, display_name, show_ui, timer)
        spoken = f"Sent it to {name}." + ui_note
        return WAOutcome(
            ok=True, spoken=spoken, text=spoken,
            data={"message_id": result.message_id, "deduped": result.deduped, "latency_ms": timer.as_dict()},
        ), timer

    # ── reply ─────────────────────────────────────────────────────────────

    def plan_reply(self, contact_ref: str, message: str) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        if not message or not message.strip():
            return WAOutcome(ok=False, spoken="What should I reply?", text="empty message"), timer
        resolved, status, raw = self.resolve_contact(contact_ref, timer)
        if status == "ambiguous":
            return self._ambiguous_outcome(contact_ref, raw), timer
        if resolved is None:
            return self._not_found_outcome(contact_ref, raw), timer
        if not resolved.message_id:
            name = resolved.display_name or contact_ref
            return WAOutcome(
                ok=False, spoken=f"I don't have a recent message from {name} to reply to.",
                text="no message_id available in context for this contact",
            ), timer
        timer.mark("planning_ms")
        prompt = f'Reply "{message}" to {resolved.display_name or "this contact"}?'
        return WAOutcome(
            ok=False, needs_confirmation=True, spoken=prompt, text=prompt,
            data={
                "resolved_chat_id": resolved.chat_id,
                "resolved_display_name": resolved.display_name,
                "resolved_message_id": resolved.message_id,
                "latency_ms": timer.as_dict(),
            },
        ), timer

    def execute_reply(
        self, chat_id: str, display_name: Optional[str], message: str,
        reply_to_message_id: str, idempotency_key: str, show_ui: bool = False,
    ) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        try:
            transport = self._transport_ready()
        except Exception as e:
            return WAOutcome(ok=False, spoken="WhatsApp isn't set up yet.",
                              text=f"transport unavailable: {e}"), timer
        request = WhatsAppRequest(
            action=WAAction.REPLY, recipient=chat_id, content=message,
            reply_to_message_id=reply_to_message_id, idempotency_key=idempotency_key,
        )
        result = transport.reply(request)
        timer.mark("transport_ms")
        name = display_name or "them"
        if not result.success:
            return WAOutcome(
                ok=False, spoken=f"I couldn't send that reply to {name}.",
                text=f"reply failed: {result.error_message or result.error_code}",
                data={"latency_ms": timer.as_dict()},
            ), timer
        try:
            self._context.record_interaction(
                chat_id=chat_id, display_name=display_name,
                action="reply", message_id=result.message_id,
            )
        except Exception:
            logger.debug("[WA_CMD] context recording failed (non-fatal)", exc_info=True)
        ui_note = self._maybe_surface_ui(chat_id, display_name, show_ui, timer)
        spoken = f"Replied to {name}." + ui_note
        return WAOutcome(
            ok=True, spoken=spoken, text=spoken,
            data={"message_id": result.message_id, "deduped": result.deduped, "latency_ms": timer.as_dict()},
        ), timer

    # ── send_file ─────────────────────────────────────────────────────────
    # Delegates entirely to FileSendPlanner — resolves contact + file +
    # security in one call (handoff §8/§9/§30: never reimplement these).

    def plan_send_file(self, contact_ref: str, file_ref: Dict[str, Any]) -> Tuple[WAOutcome, LatencyTimer, Optional[FileSendPlan]]:
        timer = LatencyTimer()
        try:
            planner = self._file_planner()
        except Exception as e:
            timer.mark("planning_ms")
            return WAOutcome(ok=False, spoken="WhatsApp isn't set up yet.",
                              text=f"transport unavailable: {e}"), timer, None
        plan = planner.plan(file_ref, contact_ref)
        timer.mark("planning_ms")
        if plan.status == "needs_clarification":
            # Two distinct ambiguity shapes share this status: the CONTACT
            # being ambiguous (FileSendPlanner.plan()'s Step 1, before any
            # file lookup runs — plan.contact_resolution.status=="ambiguous")
            # vs. the FILE being ambiguous (2+ files match a name/query —
            # plan.contact_resolution is the single successfully-resolved
            # contact here, and plan.candidates holds FileCandidate objects
            # instead). Conflating these used to build a broken "I found
            # more than one contact matching resume.pdf" response off an
            # empty candidate list for the file case.
            if plan.contact_resolution and plan.contact_resolution.status == "ambiguous":
                return self._ambiguous_outcome(contact_ref, plan.contact_resolution), timer, plan
            return self._ambiguous_file_outcome(plan), timer, plan
        if plan.status == "not_found":
            return WAOutcome(ok=False, spoken=plan.detail or "I couldn't find that file or contact.",
                              text=plan.detail or ""), timer, plan
        if plan.status == "blocked":
            return WAOutcome(
                ok=False,
                spoken="I can't send that file — it looks like a protected or sensitive file.",
                text=plan.detail or "",
            ), timer, plan
        if plan.status != "ready":
            return WAOutcome(ok=False, spoken="I couldn't prepare that file to send.", text=plan.detail or ""), timer, plan

        prompt = f'Send {plan.filename} to {plan.contact_name or "this contact"}?'
        return WAOutcome(
            ok=False, needs_confirmation=True, spoken=prompt, text=prompt,
            data={"latency_ms": timer.as_dict()},
        ), timer, plan

    def execute_send_file(self, plan: FileSendPlan, show_ui: bool = False) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        try:
            planner = self._file_planner()
        except Exception as e:
            timer.mark("transport_ms")
            return WAOutcome(ok=False, spoken="WhatsApp isn't set up yet.",
                              text=f"transport unavailable: {e}"), timer
        plan = planner.execute(plan, approved=True)
        timer.mark("transport_ms")
        name = plan.contact_name or "them"
        if not plan.result or not plan.result.success:
            return WAOutcome(
                ok=False, spoken=f"I couldn't send that to {name}.", text=plan.detail or "",
                data={"latency_ms": timer.as_dict()},
            ), timer
        ui_note = self._maybe_surface_ui(plan.chat_id, plan.contact_name, show_ui, timer)
        spoken = f"Sent it to {name}." + ui_note
        return WAOutcome(
            ok=True, spoken=spoken, text=spoken,
            data={
                "message_id": plan.result.message_id, "deduped": plan.result.deduped,
                "latency_ms": timer.as_dict(),
            },
        ), timer

    # ── show_chat / get_messages — perception + UI, never gated by approval ─

    def open_whatsapp(self) -> Tuple[WAOutcome, LatencyTimer]:
        """Bare 'open whatsapp' — no contact named. Reuses an already-open,
        already-logged-in WhatsApp Web tab (WhatsAppUIAdapter.open_whatsapp()
        does CDP tab reuse / window activation, never navigates a specific
        chat, never opens a new tab if one is already found). No identity
        lookup, no transport — this never touches Baileys."""
        timer = LatencyTimer()
        report = self._ui().open_whatsapp()
        timer.mark("ui_surface_ms")
        spoken = "WhatsApp is open." if report.ok else "I couldn't open WhatsApp."
        return WAOutcome(
            ok=report.ok, spoken=spoken, text=report.detail,
            data={"latency_ms": timer.as_dict()},
        ), timer

    def show_chat(self, contact_ref: str) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        resolved, status, raw = self.resolve_contact(contact_ref, timer)
        if status == "ambiguous":
            return self._ambiguous_outcome(contact_ref, raw), timer
        if resolved is None:
            return self._not_found_outcome(contact_ref, raw), timer
        target = WhatsAppUITarget.from_chat_id(resolved.chat_id, resolved.display_name)
        report = self._ui().open_chat(target)
        timer.mark("ui_surface_ms")
        name = resolved.display_name or "their"
        spoken = f"{name}'s chat is open." if report.ok else f"I couldn't open {name}'s WhatsApp chat."
        return WAOutcome(
            ok=report.ok, spoken=spoken, text=report.detail,
            data={
                "latency_ms": timer.as_dict(),
                # Developer/diagnostic fields only — never surfaced in
                # `spoken`. resolved.chat_id is the exact JID the production
                # path used; everything below mirrors UIActionReport so a
                # validation script can report it without a second lookup.
                "resolved_chat_id": resolved.chat_id,
                "resolved_display_name": resolved.display_name,
                "contact_matched_by": resolved.matched_by,
                "contact_resolution_method": resolved.method,
                "ui_target": report.ui_target,
                "launch_method": report.launch_method,
                "cdp_available": report.cdp_available,
                "cdp_tab_reused": report.cdp_tab_reused,
                "window_activated": report.window_activated,
                "verified": report.verified,
            },
        ), timer

    def get_messages(self, contact_ref: Optional[str] = None, limit: int = 10) -> Tuple[WAOutcome, LatencyTimer]:
        timer = LatencyTimer()
        chat_id: Optional[str] = None
        display_name: Optional[str] = None
        if contact_ref:
            resolved, status, raw = self.resolve_contact(contact_ref, timer)
            if status == "ambiguous":
                return self._ambiguous_outcome(contact_ref, raw), timer
            if resolved is None:
                return self._not_found_outcome(contact_ref, raw), timer
            chat_id, display_name = resolved.chat_id, resolved.display_name

        try:
            transport = self._transport_ready()
        except Exception as e:
            timer.mark("transport_ms")
            return WAOutcome(ok=False, spoken="WhatsApp isn't set up yet.",
                              text=f"transport unavailable: {e}"), timer
        msgs = transport.get_messages(limit=max(limit, 20), unread_only=False)
        timer.mark("transport_ms")
        if chat_id:
            msgs = [m for m in msgs if m.get("chat_id") == chat_id]
        msgs = msgs[:limit]

        if not msgs:
            name = display_name or contact_ref
            spoken = f"No recent messages from {name}." if name else "No recent messages."
            return WAOutcome(ok=True, spoken=spoken, text="no messages",
                              data={"messages": [], "latency_ms": timer.as_dict()}), timer

        last = msgs[0]
        preview = (last.get("text") or "")[:200]
        name = display_name or last.get("sender_name") or "them"
        # Count INBOUND messages only (from_me falsy) — "he sent 3 messages"
        # must not count the user's own outgoing messages in that window.
        inbound_count = sum(1 for m in msgs if not m.get("from_me"))
        if inbound_count > 1:
            spoken = (
                f'{name} sent {inbound_count} messages. The latest says, "{preview}"'
                if preview else f"{name} sent {inbound_count} messages."
            )
        else:
            spoken = f'{name} said, "{preview}"' if preview else f"{name} sent a message."
        return WAOutcome(
            ok=True, spoken=spoken, text=spoken,
            data={"messages": msgs, "latency_ms": timer.as_dict()},
        ), timer

    # ── internals ────────────────────────────────────────────────────────

    def _maybe_surface_ui(
        self, chat_id: Optional[str], display_name: Optional[str], show_ui: bool, timer: LatencyTimer,
    ) -> str:
        """Surface WhatsApp AFTER a successful transport action, never before
        (handoff §15/§37 — UI must not delay or gate the send)."""
        if not show_ui or not chat_id:
            return ""
        try:
            report = self._ui().open_chat(WhatsAppUITarget.from_chat_id(chat_id, display_name))
        except Exception:
            logger.debug("[WA_CMD] UI surface failed (non-fatal)", exc_info=True)
            return ""
        timer.mark("ui_surface_ms")
        return " I've opened the chat." if report.ok else ""


_default_handler: Optional[WACommandHandler] = None
_default_handler_lock = threading.Lock()


def get_default_command_handler() -> Optional[WACommandHandler]:
    """
    Lazily construct the singleton. Returns None (never raises) if the
    sidecar isn't configured yet (BaileysTransport.from_settings() requires
    wa_sidecar_api_key) — callers must render that as a clean "WhatsApp
    isn't set up yet" result, not a crash. Constructing this at import time
    would break tool registration for every install without a configured
    sidecar, so it is only ever built on first real use.
    """
    global _default_handler
    with _default_handler_lock:
        if _default_handler is None:
            try:
                _default_handler = WACommandHandler()
            except Exception as e:
                logger.warning("[WA_CMD] command handler unavailable: %s", e)
                return None
        return _default_handler
