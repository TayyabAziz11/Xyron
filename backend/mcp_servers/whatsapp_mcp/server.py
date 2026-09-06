#!/usr/bin/env python3
"""
WhatsApp MCP Server — JSON-RPC 2.0 over stdin/stdout.

Exposes WhatsApp Web automation as MCP tools for Claude CLI.

Tools:
  get_messages(limit, unread_only)         [Baileys transport — Phase 2]
  find_chat(query)                          [Baileys transport — Phase 2]
  open_chat(chat_id)                        [Playwright — unchanged]
  mark_read(chat_id)                        [Baileys transport — Phase 2]
  send_message(to, message)                 [Baileys transport — Phase 2]
  send_file(to, file_path, caption)         [Baileys transport — Phase 2]
  send_image(to, file_path, caption)        [Baileys transport — Phase 2]
  reply_to_message(to, message, reply_to_message_id)  [Baileys transport — Phase 2]
  healthcheck()                             [Baileys transport — Phase 2]

Architecture (Phase 2):
  - Perception tool open_chat still runs on the original Playwright
    WhatsAppWebClient — unchanged, not migrated.
  - All other tools (get_messages/find_chat/send_message/send_file/send_image/
    reply_to_message/mark_read/healthcheck) now go through BaileysTransport,
    an HTTP client to the local Baileys sidecar
    (backend/integrations/whatsapp/sidecar) — see
    backend/api/integrations/whatsapp/baileys_transport.py. The Playwright
    path stays available for open_chat until Baileys replacements are
    fully verified in real use.
  - File lock at /tmp/wa_mcp.lock prevents concurrent server instances.
  - CTRL-C / SIGTERM → clean shutdown (browser + transport both closed).

Usage:
    python3 mcp_servers/whatsapp_mcp/server.py

Register in Claude CLI (~/.claude/mcp_config.json):
    {
      "mcpServers": {
        "whatsapp": {
          "command": "python3",
          "args": ["/absolute/path/to/mcp_servers/whatsapp_mcp/server.py"]
        }
      }
    }
"""

import sys
import os
import hashlib
import json
import fcntl
import signal
import logging
import traceback
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure repo/src on path
_HERE = Path(__file__).parent.resolve()
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

logging.basicConfig(
    level=logging.WARNING,          # suppress noisy Playwright logs on stderr
    format="[wa-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("wa_mcp")

# ── Lock file to prevent multiple server instances ────────────────────────────

LOCK_PATH = Path("/tmp/wa_mcp.lock")
_lock_fd = None


def _acquire_lock():
    global _lock_fd
    _lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
    except BlockingIOError:
        sys.stderr.write("[wa-mcp] Another WhatsApp MCP server is already running.\n")
        sys.exit(1)


def _release_lock():
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass


# ── Tool definitions (MCP spec) ───────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_messages",
        "description": (
            "Fetch WhatsApp messages. Returns list of messages with sender, text, timestamp. "
            "PERCEPTION ONLY — does not modify any messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "description": "Max messages to return"},
                "unread_only": {"type": "boolean", "default": True, "description": "Only return unread"},
            },
        },
    },
    {
        "name": "send_message",
        "description": (
            "Send a WhatsApp message. REQUIRES approved plan — never call autonomously. "
            "Use 'to' as E.164 phone number (+923001234567) or chat_id from get_messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Phone number (E.164) or chat_id"},
                "message": {"type": "string", "description": "Message text to send"},
            },
            "required": ["to", "message"],
        },
    },
    {
        "name": "find_chat",
        "description": "Search for a chat by name or phone number. Returns matching chat metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or phone to search for"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_chat",
        "description": "Open a specific chat by chat_id. Returns recent messages from that chat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "chat_id from get_messages or find_chat"},
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "mark_read",
        "description": "Mark a chat as read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "chat_id to mark as read"},
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "send_file",
        "description": (
            "Send a document/file over WhatsApp. REQUIRES approved plan — never call autonomously. "
            "Use 'to' as E.164 phone number or chat_id from get_messages. 'file_path' must be an "
            "absolute local path readable by the backend process."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Phone number (E.164) or chat_id"},
                "file_path": {"type": "string", "description": "Absolute local path to the file to send"},
                "caption": {"type": "string", "description": "Optional caption to send with the file"},
            },
            "required": ["to", "file_path"],
        },
    },
    {
        "name": "send_image",
        "description": (
            "Send an image over WhatsApp. REQUIRES approved plan — never call autonomously. "
            "Use 'to' as E.164 phone number or chat_id from get_messages. 'file_path' must be an "
            "absolute local path readable by the backend process."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Phone number (E.164) or chat_id"},
                "file_path": {"type": "string", "description": "Absolute local path to the image to send"},
                "caption": {"type": "string", "description": "Optional caption to send with the image"},
            },
            "required": ["to", "file_path"],
        },
    },
    {
        "name": "reply_to_message",
        "description": (
            "Send a quoted reply to a specific WhatsApp message. REQUIRES approved plan — never call "
            "autonomously. 'reply_to_message_id' comes from a message returned by get_messages/open_chat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Phone number (E.164) or chat_id"},
                "message": {"type": "string", "description": "Reply text"},
                "reply_to_message_id": {"type": "string", "description": "Message id being replied to"},
            },
            "required": ["to", "message", "reply_to_message_id"],
        },
    },
    {
        "name": "healthcheck",
        "description": "Check WhatsApp connection status (Baileys sidecar).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# ── Lazy browser singleton (perception tools — unchanged Playwright path) ─────

_client: Optional[Any] = None
_client_started = False


def _ensure_client():
    global _client, _client_started
    if _client_started and _client is not None:
        return _client

    from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient
    _client = WhatsAppWebClient(headless=True)
    _client.start()
    _client_started = True

    if not _client.is_logged_in():
        logger.warning("WhatsApp not logged in — run: python3 scripts/wa_setup.py")

    return _client


# ── Lazy transport singleton (Baileys sidecar — Phase 2) ─────────────────

_transport: Optional[Any] = None


def _ensure_transport():
    global _transport
    if _transport is not None:
        return _transport

    from api.integrations.whatsapp import BaileysTransport
    _transport = BaileysTransport.from_settings()
    return _transport


def _idempotency_key(*parts: str) -> str:
    """
    Deterministic key from the call's own arguments (no timestamp/nonce) so
    that a genuine duplicate call — e.g. a JSON-RPC client retrying after a
    dropped response — dedupes, while two independent calls with different
    content never collide.
    """
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8"))
    return h.hexdigest()[:32]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_get_messages(args: Dict) -> str:
    limit = int(args.get("limit", 10))
    unread_only = bool(args.get("unread_only", True))

    transport = _ensure_transport()
    messages = transport.get_messages(limit=limit, unread_only=unread_only)
    return json.dumps(messages, ensure_ascii=False)


def _result_to_json(to: str, result) -> str:
    payload = {
        "success": result.success,
        "to": to[:5] + "****",   # partial redaction in response
        "message_id": result.message_id,
        "deduped": result.deduped,
    }
    if not result.success:
        payload["error_code"] = result.error_code.value if result.error_code else None
        payload["error"] = result.error_message
    return json.dumps(payload, ensure_ascii=False)


def handle_send_message(args: Dict) -> str:
    to = args.get("to", "")
    message = args.get("message", "")

    if not to or not message:
        return json.dumps({"success": False, "error": "Both 'to' and 'message' are required"})

    from api.integrations.whatsapp import WAAction, WhatsAppRequest

    transport = _ensure_transport()
    request = WhatsAppRequest(
        action=WAAction.SEND_TEXT,
        recipient=to,
        content=message,
        idempotency_key=_idempotency_key("send_text", to, message),
    )
    result = transport.send_text(request)
    return _result_to_json(to, result)


def handle_send_file(args: Dict) -> str:
    to = args.get("to", "")
    file_path = args.get("file_path", "")
    caption = args.get("caption", "")

    if not to or not file_path:
        return json.dumps({"success": False, "error": "Both 'to' and 'file_path' are required"})

    from api.integrations.whatsapp import WAAction, WhatsAppRequest

    transport = _ensure_transport()
    request = WhatsAppRequest(
        action=WAAction.SEND_FILE,
        recipient=to,
        content=caption,
        attachment=file_path,
        idempotency_key=_idempotency_key("send_file", to, file_path, caption),
    )
    result = transport.send_file(request)
    return _result_to_json(to, result)


def handle_send_image(args: Dict) -> str:
    to = args.get("to", "")
    file_path = args.get("file_path", "")
    caption = args.get("caption", "")

    if not to or not file_path:
        return json.dumps({"success": False, "error": "Both 'to' and 'file_path' are required"})

    from api.integrations.whatsapp import WAAction, WhatsAppRequest

    transport = _ensure_transport()
    request = WhatsAppRequest(
        action=WAAction.SEND_IMAGE,
        recipient=to,
        content=caption,
        attachment=file_path,
        idempotency_key=_idempotency_key("send_image", to, file_path, caption),
    )
    result = transport.send_image(request)
    return _result_to_json(to, result)


def handle_reply_to_message(args: Dict) -> str:
    to = args.get("to", "")
    message = args.get("message", "")
    reply_to_message_id = args.get("reply_to_message_id", "")

    if not to or not message or not reply_to_message_id:
        return json.dumps({
            "success": False,
            "error": "'to', 'message' and 'reply_to_message_id' are all required",
        })

    from api.integrations.whatsapp import WAAction, WhatsAppRequest

    transport = _ensure_transport()
    request = WhatsAppRequest(
        action=WAAction.REPLY,
        recipient=to,
        content=message,
        reply_to_message_id=reply_to_message_id,
        idempotency_key=_idempotency_key("reply", to, message, reply_to_message_id),
    )
    result = transport.reply(request)
    return _result_to_json(to, result)


def handle_find_chat(args: Dict) -> str:
    query = args.get("query", "")
    transport = _ensure_transport()
    contacts = transport.find_contact(query)
    return json.dumps(contacts, ensure_ascii=False)


def handle_open_chat(args: Dict) -> str:
    chat_id = args.get("chat_id", "")
    client = _ensure_client()
    # For now, open_chat is equivalent to a targeted search
    found = client._open_chat_by_search(chat_id)
    if found:
        msgs = client.get_unread_messages(limit=10)
        filtered = [m for m in msgs if m.get("chat_id") == chat_id]
        return json.dumps({"opened": True, "messages": filtered or msgs[:5]}, ensure_ascii=False)
    return json.dumps({"opened": False, "error": f"Chat not found: {chat_id}"})


def handle_mark_read(args: Dict) -> str:
    chat_id = args.get("chat_id", "")
    transport = _ensure_transport()
    success = transport.mark_read(chat_id)
    return json.dumps({"success": success, "chat_id": chat_id})


def handle_healthcheck(_args: Dict) -> str:
    try:
        transport = _ensure_transport()
        status = transport.healthcheck()
    except Exception as e:
        status = {"status": "error", "error": str(e)}
    return json.dumps(status, ensure_ascii=False)


HANDLERS = {
    "get_messages": handle_get_messages,
    "send_message": handle_send_message,
    "send_file": handle_send_file,
    "send_image": handle_send_image,
    "reply_to_message": handle_reply_to_message,
    "find_chat": handle_find_chat,
    "open_chat": handle_open_chat,
    "mark_read": handle_mark_read,
    "healthcheck": handle_healthcheck,
}

# ── JSON-RPC 2.0 server loop ──────────────────────────────────────────────────

def send(obj: Dict):
    """Write a JSON-RPC message to stdout."""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def error_response(req_id: Any, code: int, message: str) -> Dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def ok_response(req_id: Any, result: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def dispatch(msg: Dict) -> Optional[Dict]:
    """Handle a single JSON-RPC message. Return None for notifications."""
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    # Notifications (no id) — don't need a response
    if req_id is None and method not in ("initialize",):
        return None

    # ── Standard MCP handshake ────────────────────────────────────────────────
    if method == "initialize":
        return ok_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "whatsapp-web", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return None  # notification, no response needed

    if method == "tools/list":
        return ok_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if not handler:
            return error_response(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result_text = handler(tool_args)
            return ok_response(req_id, {
                "content": [{"type": "text", "text": result_text}]
            })
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Tool {tool_name} failed: {e}\n{tb}")
            return ok_response(req_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True,
            })

    if method == "ping":
        return ok_response(req_id, {})

    return error_response(req_id, -32601, f"Method not found: {method}")


def _shutdown(sig, frame):
    logger.info("Shutting down …")
    global _client, _transport
    if _client:
        try:
            _client.stop()
        except Exception:
            pass
    if _transport:
        try:
            _transport.close()
        except Exception:
            pass
    _release_lock()
    sys.exit(0)


def main():
    _acquire_lock()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("WhatsApp MCP server started — listening on stdin")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            send(error_response(None, -32700, f"Parse error: {e}"))
            continue

        try:
            response = dispatch(msg)
        except Exception as e:
            response = error_response(msg.get("id"), -32603, f"Internal error: {e}")

        if response is not None:
            send(response)

    _shutdown(None, None)


if __name__ == "__main__":
    main()
