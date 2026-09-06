"""
WhatsApp Tools — Phase 4 fast path (wa_command_handler.py orchestration
surfaced as registry tools).

Tools registered:
  wa_send_text     confirm-then-send a text message           risk=high
  wa_send_file     confirm-then-send a local file/image        risk=high
  wa_reply         confirm-then-reply to the last known message risk=high
  wa_show_chat     open/focus a contact's WhatsApp chat         risk=low
  wa_get_messages  read recent messages (perception only)       risk=low

Confirmation pattern (matches the existing smart_open / confirm_required
convention in system_tools.py, NOT the dormant safety_gate.py machinery —
see the Phase 4 takeover report for why): the first call resolves the
contact/file and returns error="confirm_required" with the SAME tool name
and the resolved identity embedded in `params["_resolved_*"]` plus a
`_wa_confirmed` marker. voice_ws.py's pending-confirmation handler
re-invokes the exact same tool with that exact params dict on "yes" — the
second call skips resolution entirely (it already has the resolved
chat_id/message_id/file plan) and performs the actual send. This also
means send/reply/file confirm+execute always resolves the contact/file
exactly once, never twice.

wa_show_chat / wa_get_messages never confirm — they are perception/UI
actions, not sends (handoff §33).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from api.integrations.whatsapp.file_send import FileSendPlan
from api.integrations.whatsapp.wa_command_handler import get_default_command_handler

from .registry import ToolResult, registry

logger = logging.getLogger("wa_tools")


def _unavailable() -> ToolResult:
    return ToolResult(
        success=False,
        text="WhatsApp is not configured (missing wa_sidecar_api_key) or the sidecar is unreachable.",
        spoken="WhatsApp isn't set up yet.",
    )


# ── wa_send_text ─────────────────────────────────────────────────────────

def _exec_wa_send_text(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    handler = get_default_command_handler()
    if handler is None:
        return _unavailable()

    contact = (params.get("contact") or "").strip()
    message = (params.get("message") or "").strip()
    show_ui = bool(params.get("show_ui", False))
    if not contact:
        return ToolResult(success=False, text="contact is required", spoken="Who should I message?")

    if params.get("_wa_confirmed"):
        chat_id = params.get("_resolved_chat_id")
        display_name = params.get("_resolved_display_name")
        idem_key = params.get("_idempotency_key") or f"wa_send_text:{chat_id}:{uuid.uuid4().hex}"
        if not chat_id:
            return ToolResult(success=False, text="missing resolved chat_id on confirmed call",
                               spoken="Something went wrong — let's try that again.")
        outcome, _ = handler.execute_send_text(chat_id, display_name, message, idem_key, show_ui=show_ui)
        return ToolResult(success=outcome.ok, text=outcome.text, spoken=outcome.spoken, data=outcome.data)

    outcome, _ = handler.plan_send_text(contact, message)
    if outcome.needs_confirmation:
        chat_id = outcome.data["resolved_chat_id"]
        return ToolResult(
            success=False, text=outcome.text, spoken=outcome.spoken, error="confirm_required",
            data={
                "tool": "wa_send_text",
                "params": {
                    "contact": contact, "message": message, "show_ui": show_ui,
                    "_wa_confirmed": True,
                    "_resolved_chat_id": chat_id,
                    "_resolved_display_name": outcome.data.get("resolved_display_name"),
                    "_idempotency_key": f"wa_send_text:{chat_id}:{uuid.uuid4().hex}",
                },
                "prompt": outcome.spoken,
                "matched_by": outcome.data.get("contact_matched_by"),
                "latency_ms": outcome.data.get("latency_ms", {}),
            },
        )
    if outcome.ambiguous:
        return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken,
                           data={"candidates": outcome.candidates})
    return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken)


# ── wa_reply ─────────────────────────────────────────────────────────────

def _exec_wa_reply(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    handler = get_default_command_handler()
    if handler is None:
        return _unavailable()

    contact = (params.get("contact") or "").strip()
    message = (params.get("message") or "").strip()
    show_ui = bool(params.get("show_ui", False))
    if not contact:
        return ToolResult(success=False, text="contact is required", spoken="Who should I reply to?")

    if params.get("_wa_confirmed"):
        chat_id = params.get("_resolved_chat_id")
        display_name = params.get("_resolved_display_name")
        reply_to = params.get("_resolved_message_id")
        idem_key = params.get("_idempotency_key") or f"wa_reply:{chat_id}:{uuid.uuid4().hex}"
        if not chat_id or not reply_to:
            return ToolResult(success=False, text="missing resolved reply fields on confirmed call",
                               spoken="Something went wrong — let's try that again.")
        outcome, _ = handler.execute_reply(chat_id, display_name, message, reply_to, idem_key, show_ui=show_ui)
        return ToolResult(success=outcome.ok, text=outcome.text, spoken=outcome.spoken, data=outcome.data)

    outcome, _ = handler.plan_reply(contact, message)
    if outcome.needs_confirmation:
        chat_id = outcome.data["resolved_chat_id"]
        return ToolResult(
            success=False, text=outcome.text, spoken=outcome.spoken, error="confirm_required",
            data={
                "tool": "wa_reply",
                "params": {
                    "contact": contact, "message": message, "show_ui": show_ui,
                    "_wa_confirmed": True,
                    "_resolved_chat_id": chat_id,
                    "_resolved_display_name": outcome.data.get("resolved_display_name"),
                    "_resolved_message_id": outcome.data["resolved_message_id"],
                    "_idempotency_key": f"wa_reply:{chat_id}:{uuid.uuid4().hex}",
                },
                "prompt": outcome.spoken,
            },
        )
    if outcome.ambiguous:
        return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken,
                           data={"candidates": outcome.candidates})
    return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken)


# ── wa_send_file ─────────────────────────────────────────────────────────

def _exec_wa_send_file(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    handler = get_default_command_handler()
    if handler is None:
        return _unavailable()

    contact = (params.get("contact") or "").strip()
    file_ref = params.get("file_ref") or {}
    show_ui = bool(params.get("show_ui", False))
    if not contact or not file_ref:
        return ToolResult(success=False, text="contact and file_ref are required",
                           spoken="Which file should I send, and to whom?")

    if params.get("_wa_confirmed"):
        plan = FileSendPlan(
            status="ready",
            file_path=params.get("_file_path"),
            filename=params.get("_filename"),
            mime_type=params.get("_mime_type"),
            size_bytes=params.get("_size_bytes"),
            media_kind=params.get("_media_kind"),
            send_method=params.get("_send_method"),
            chat_id=params.get("_resolved_chat_id"),
            contact_name=params.get("_resolved_display_name"),
            action_id=params.get("_action_id"),
        )
        if not plan.chat_id or not plan.file_path:
            return ToolResult(success=False, text="missing resolved plan fields on confirmed call",
                               spoken="Something went wrong — let's try that again.")
        outcome, _ = handler.execute_send_file(plan, show_ui=show_ui)
        return ToolResult(success=outcome.ok, text=outcome.text, spoken=outcome.spoken, data=outcome.data)

    outcome, _, plan = handler.plan_send_file(contact, file_ref)
    if outcome.needs_confirmation and plan is not None:
        return ToolResult(
            success=False, text=outcome.text, spoken=outcome.spoken, error="confirm_required",
            data={
                "tool": "wa_send_file",
                "params": {
                    "contact": contact, "file_ref": file_ref, "show_ui": show_ui,
                    "_wa_confirmed": True,
                    "_file_path": plan.file_path, "_filename": plan.filename,
                    "_mime_type": plan.mime_type, "_size_bytes": plan.size_bytes,
                    "_media_kind": plan.media_kind, "_send_method": plan.send_method,
                    "_resolved_chat_id": plan.chat_id, "_resolved_display_name": plan.contact_name,
                    "_action_id": plan.action_id,
                },
                "prompt": outcome.spoken,
            },
        )
    if outcome.ambiguous:
        return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken,
                           data={"candidates": outcome.candidates})
    return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken)


# ── wa_show_chat / wa_get_messages — perception + UI, never gated ─────────

def _exec_wa_show_chat(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    handler = get_default_command_handler()
    if handler is None:
        return _unavailable()
    contact = (params.get("contact") or "").strip()
    if not contact:
        # No contact named ("open whatsapp" / "show me whatsapp") — just
        # surface the WhatsApp app/web root, reusing an already-open tab.
        # Never falls back to "whose WhatsApp?" for this shape; that error
        # is for a genuinely garbled/empty contact reference elsewhere, not
        # for a deliberate bare "open whatsapp".
        outcome, _ = handler.open_whatsapp()
        return ToolResult(success=outcome.ok, text=outcome.text, spoken=outcome.spoken, data=outcome.data)
    outcome, _ = handler.show_chat(contact)
    if outcome.ambiguous:
        return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken,
                           data={"candidates": outcome.candidates})
    return ToolResult(success=outcome.ok, text=outcome.text, spoken=outcome.spoken, data=outcome.data)


def _exec_wa_get_messages(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    handler = get_default_command_handler()
    if handler is None:
        return _unavailable()
    contact = (params.get("contact") or "").strip() or None
    try:
        limit = int(params.get("limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    outcome, _ = handler.get_messages(contact, limit=limit)
    if outcome.ambiguous:
        return ToolResult(success=False, text=outcome.text, spoken=outcome.spoken,
                           data={"candidates": outcome.candidates})
    return ToolResult(success=outcome.ok, text=outcome.text, spoken=outcome.spoken, data=outcome.data)


# ── registration ─────────────────────────────────────────────────────────

registry.register(
    name="wa_send_text",
    definition={
        "type": "function",
        "function": {
            "name": "wa_send_text",
            "description": (
                "Send a WhatsApp text message to a known contact. Use for: "
                "'message Tayyab I'm outside', 'whatsapp Ali I'll be late', "
                "'send a whatsapp to Sara saying ...'. Always confirms with the "
                "user before actually sending."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Contact name, phone number, or pronoun (him/her)"},
                    "message": {"type": "string", "description": "The message text to send"},
                    "show_ui": {"type": "boolean", "description": "Also open the WhatsApp chat after sending"},
                },
                "required": ["contact", "message"],
            },
        },
    },
    executor=_exec_wa_send_text,
    risk="high",
    category="communication",
)

registry.register(
    name="wa_reply",
    definition={
        "type": "function",
        "function": {
            "name": "wa_reply",
            "description": (
                "Reply to the most recent WhatsApp message from a contact. Use for: "
                "'reply to him I'll call later', 'reply to Tayyab: on my way'. "
                "Only works when there is a recent message from that contact in "
                "context. Always confirms before sending."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Contact name or pronoun (him/her)"},
                    "message": {"type": "string", "description": "The reply text"},
                    "show_ui": {"type": "boolean", "description": "Also open the WhatsApp chat after sending"},
                },
                "required": ["contact", "message"],
            },
        },
    },
    executor=_exec_wa_reply,
    risk="high",
    category="communication",
)

registry.register(
    name="wa_send_file",
    definition={
        "type": "function",
        "function": {
            "name": "wa_send_file",
            "description": (
                "Send a local file/image/PDF/screenshot to a WhatsApp contact. Use "
                "for: 'send this PDF to Tayyab', 'send Ali this image', 'send the "
                "screenshot I just took to Sara'. Always confirms before sending."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Contact name, phone number, or pronoun (him/her)"},
                    "file_ref": {
                        "type": "object",
                        "description": (
                            "One of {kind:'exact_path', path}, {kind:'filename', name}, "
                            "{kind:'latest', type, location}, {kind:'context', query}"
                        ),
                    },
                    "show_ui": {"type": "boolean", "description": "Also open the WhatsApp chat after sending"},
                },
                "required": ["contact", "file_ref"],
            },
        },
    },
    executor=_exec_wa_send_file,
    risk="high",
    category="communication",
)

registry.register(
    name="wa_show_chat",
    definition={
        "type": "function",
        "function": {
            "name": "wa_show_chat",
            "description": (
                "Open/focus WhatsApp in the UI without sending anything. With a "
                "contact: 'show me Tayyab's WhatsApp', 'open Ali's chat' — opens "
                "that specific conversation. Without one: 'open WhatsApp', 'show "
                "me WhatsApp' — just surfaces the WhatsApp app/web root, reusing "
                "an already-open tab. Never requires confirmation — read-only view action."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Optional contact name, phone number, or pronoun (him/her) — omit to just open WhatsApp itself"},
                },
                "required": [],
            },
        },
    },
    executor=_exec_wa_show_chat,
    risk="low",
    category="communication",
)

registry.register(
    name="wa_get_messages",
    definition={
        "type": "function",
        "function": {
            "name": "wa_get_messages",
            "description": (
                "Read recent WhatsApp messages, optionally filtered to one contact. "
                "Use for: 'what did Tayyab say', 'any new WhatsApp messages'. "
                "Perception only — never sends anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Optional contact name/phone to filter to"},
                    "limit": {"type": "integer", "description": "Max messages to return (default 10)"},
                },
                "required": [],
            },
        },
    },
    executor=_exec_wa_get_messages,
    risk="low",
    category="communication",
)
