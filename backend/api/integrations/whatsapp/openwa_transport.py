"""
openwa_transport.py — WhatsAppTransport implementation backed by the local
open-wa sidecar (backend/integrations/whatsapp/sidecar, pinned to
@open-wa/wa-automate@4.76.0 — see that package.json for why 4.76.0
specifically, not v5/alpha/latest).

Talks to the sidecar over plain HTTP on localhost only. Treats the sidecar
as an unreliable external process throughout: every method catches
connection errors, timeouts, and malformed responses and converts them into
a structured WhatsAppResult / empty list / False — this transport must never
raise into caller code.

HTTP contract (confirmed against the INSTALLED 4.76.0 package source,
node_modules/@open-wa/wa-automate/dist/api/Client.js — this is
client.middleware(), which sidecar/server.js mounts directly, NOT the
CLI-driven "Easy API" the first draft of this file assumed):

  POST /<clientMethodName>   body: {"args": [positional, args, in, order]}
    -> 200 {"success": true,  "response": <raw client method return value>}
    -> 200 {"success": false, "error": {"name","message","data"}}   (thrown)
    -> 404 "Cannot find method: X"   (plain text — wrong method name)

  Methods used here, each verified to exist with this exact name/arg order
  in dist/api/Client.d.ts for 4.76.0:
    sendText(to, content)
    sendFile(to, file, filename, caption)      file: DataURL | FilePath | URL
    sendImage(to, file, filename, caption)     same AdvancedFile union
    reply(to, content, quotedMsgId)
    getAllUnreadMessages()
    getAllChats(withNewMessageOnly)
    getAllContacts()
    sendSeen(chatId)
    isConnected()
    getConnectionState()                       -> STATE enum, e.g. "CONNECTED"

  A successful dispatch (`success: true`) does not always mean the WhatsApp
  action itself succeeded — several client methods return `Promise<boolean>`
  and can resolve to `false` on failure without throwing. This module checks
  for that explicitly (see _call) rather than trusting `success` alone.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .dedup import SendDeduplicator, SendState
from .models import WAAction, WAErrorCode, WhatsAppRequest, WhatsAppResult
from .transport import WhatsAppTransport

logger = logging.getLogger("wa_transport")

_PHONE_RE = re.compile(r'\+?[\d\s\-().]{7,}')

# STATE values (dist/api/model/index.d.ts) that mean the session needs
# re-authentication / is not usable — anything else (OPENING, PAIRING,
# SYNCING, TIMEOUT, CONNECTED) is either healthy or transient.
_DISCONNECTED_STATES = {"CONFLICT", "UNLAUNCHED", "UNPAIRED", "UNPAIRED_IDLE"}


def _redact_phone(text: str) -> str:
    """
    Mirrors ai_operator.core.whatsapp_web_helper.redact_phone. Duplicated
    (2 lines) rather than imported so this module never depends on
    backend/src being on sys.path — that setup is a runtime convention of
    the MCP server / main.py, not guaranteed for every caller of this
    transport (tests included).
    """
    return _PHONE_RE.sub('[PHONE]', text)


def _file_to_data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


class OpenWATransport(WhatsAppTransport):
    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        timeout_s: float = 20.0,
        dedup_ttl_s: float = 600.0,
    ):
        if not api_key:
            raise ValueError("api_key is required — refusing to talk to an unauthenticated sidecar")
        self._client = httpx.Client(
            base_url=f"http://{host}:{port}",
            headers={"x-api-key": api_key, "content-type": "application/json"},
            timeout=timeout_s,
        )
        self._dedup = SendDeduplicator(ttl_seconds=dedup_ttl_s)
        self._poll_stop: Optional[threading.Event] = None
        self._poll_thread: Optional[threading.Thread] = None

    @classmethod
    def from_settings(cls) -> "OpenWATransport":
        from api.config import settings
        return cls(
            host=settings.wa_sidecar_host,
            port=settings.wa_sidecar_port,
            api_key=settings.wa_sidecar_api_key,
            timeout_s=settings.wa_sidecar_timeout_s,
        )

    def close(self) -> None:
        self._client.close()
        if self._poll_stop is not None:
            self._poll_stop.set()

    # ── internal RPC helper (client.middleware() contract) ─────────────────

    def _call(self, method: str, args: list) -> Tuple[Any, Optional[WAErrorCode], Optional[str]]:
        """
        POST /<method> {"args": args} against client.middleware().
        Returns (response_value, error_code, error_message) — error_code is
        None on success, response_value is the raw open-wa return value.
        """
        try:
            resp = self._client.post(f"/{method}", json={"args": args})
        except httpx.TimeoutException:
            return None, WAErrorCode.SIDECAR_TIMEOUT, f"Sidecar timed out calling {method}"
        except httpx.ConnectError:
            return None, WAErrorCode.SIDECAR_UNAVAILABLE, "Cannot reach WhatsApp sidecar — is it running?"
        except httpx.HTTPError as e:
            return None, WAErrorCode.SIDECAR_UNAVAILABLE, str(e)

        if resp.status_code == 401:
            return None, WAErrorCode.SESSION_NOT_AUTHENTICATED, "Sidecar rejected the API key"
        if resp.status_code == 404:
            return None, WAErrorCode.INVALID_REQUEST, f"Sidecar has no method '{method}'"
        if resp.status_code >= 500:
            return None, WAErrorCode.OPENWA_ERROR, f"Sidecar error {resp.status_code}: {resp.text[:200]}"
        if resp.status_code >= 400:
            return None, WAErrorCode.INVALID_REQUEST, f"Sidecar rejected request ({resp.status_code}): {resp.text[:200]}"

        try:
            body = resp.json()
        except ValueError:
            return None, WAErrorCode.MALFORMED_RESPONSE, "Sidecar returned a non-JSON response"
        if not isinstance(body, dict):
            return None, WAErrorCode.MALFORMED_RESPONSE, "Unexpected sidecar response shape"

        if not body.get("success", False):
            err = body.get("error")
            message = err.get("message") if isinstance(err, dict) else str(err) if err else None
            return None, WAErrorCode.OPENWA_ERROR, message or "open-wa reported failure"

        return body.get("response"), None, None

    def _send_via_rpc(self, method: str, args: list) -> WhatsAppResult:
        """
        Only for send-type actions (sendText/sendFile/sendImage/reply). Unlike
        _call() in general, a `False` response here specifically means the
        send failed — those methods are Promise<boolean|MessageId>, and
        client.middleware() reports success:true as soon as dispatch didn't
        throw, even when the wrapped method resolved to false. Query methods
        (isConnected, sendSeen) call _call() directly instead, since `false`
        is legitimate data for them, not a failure signal.
        """
        response, error_code, error_message = self._call(method, args)
        if error_code is not None:
            return WhatsAppResult(success=False, error_code=error_code, error_message=error_message)
        if response is False:
            return WhatsAppResult(success=False, error_code=WAErrorCode.OPENWA_ERROR,
                                   error_message=f"{method} returned false")
        message_id = response if isinstance(response, str) else None
        return WhatsAppResult(success=True, message_id=message_id)

    # ── dedup wrapper — applies to every outbound send action ──────────────

    def _guarded_send(
        self, action: WAAction, request: WhatsAppRequest, do_send: Callable[[], WhatsAppResult],
    ) -> WhatsAppResult:
        key = request.idempotency_key
        if key:
            existing = self._dedup.begin(key)
            if existing is not None:
                if existing.state == SendState.SUCCESS:
                    logger.info(f"[WA_SEND_DEDUPED] action={action.value} reason=already_succeeded")
                    return WhatsAppResult(
                        success=True, message_id=existing.message_id, chat_id=existing.chat_id, deduped=True,
                    )
                if existing.state == SendState.PENDING:
                    logger.warning(f"[WA_SEND_DEDUPED] action={action.value} reason=ambiguous_prior_attempt")
                    return WhatsAppResult(
                        success=False, error_code=WAErrorCode.DUPLICATE_SUPPRESSED, deduped=True,
                        error_message=(
                            "A previous send with this idempotency_key has an unknown outcome "
                            "(likely a timeout). Refusing to resend blindly — verify via "
                            "get_messages() before retrying manually."
                        ),
                    )
                # FAILED entries fall through to a normal retry below.

        logger.info(f"[WA_SEND_START] action={action.value} recipient={_redact_phone(request.recipient)}")
        result = do_send()
        if result.chat_id is None:
            result.chat_id = request.recipient

        if key:
            if result.success:
                self._dedup.complete(key, True, result.message_id, result.chat_id)
            elif result.error_code == WAErrorCode.SIDECAR_TIMEOUT:
                pass  # ambiguous outcome — leave PENDING, do not clear
            else:
                self._dedup.complete(key, False, None, None)
                self._dedup.forget(key)  # definite failure — safe to retry with the same key

        if result.success:
            logger.info(f"[WA_SEND_SUCCESS] action={action.value} message_id={result.message_id}")
        else:
            logger.warning(f"[WA_SEND_FAILED] action={action.value} error_code={result.error_code}")
        return result

    # ── WhatsAppTransport ────────────────────────────────────────────────────

    def healthcheck(self) -> Dict[str, Any]:
        connected_response, error_code, error_message = self._call("isConnected", [])
        if error_code is not None:
            return {"status": "error", "error_code": error_code.value, "error": error_message}

        state_response, state_error, _ = self._call("getConnectionState", [])
        result: Dict[str, Any] = {
            "status": "connected" if connected_response else "disconnected",
            "connected": bool(connected_response),
        }
        if state_error is None and isinstance(state_response, str):
            result["connection_state"] = state_response
            if state_response in _DISCONNECTED_STATES:
                logger.warning(f"[WA_SESSION_DISCONNECTED] state={state_response}")
        return result

    def send_text(self, request: WhatsAppRequest) -> WhatsAppResult:
        def _do() -> WhatsAppResult:
            return self._send_via_rpc("sendText", [request.recipient, request.content or ""])
        return self._guarded_send(WAAction.SEND_TEXT, request, _do)

    def reply(self, request: WhatsAppRequest) -> WhatsAppResult:
        if not request.reply_to_message_id:
            return WhatsAppResult(
                success=False, error_code=WAErrorCode.INVALID_REQUEST,
                error_message="reply_to_message_id is required for reply()",
            )

        def _do() -> WhatsAppResult:
            return self._send_via_rpc(
                "reply", [request.recipient, request.content or "", request.reply_to_message_id],
            )
        return self._guarded_send(WAAction.REPLY, request, _do)

    def send_file(self, request: WhatsAppRequest) -> WhatsAppResult:
        if not request.attachment:
            return WhatsAppResult(
                success=False, error_code=WAErrorCode.INVALID_REQUEST,
                error_message="attachment path is required for send_file()",
            )
        path = Path(request.attachment)
        if not path.is_file():
            return WhatsAppResult(
                success=False, error_code=WAErrorCode.FILE_NOT_FOUND,
                error_message=f"No such file: {request.attachment}",
            )

        def _do() -> WhatsAppResult:
            return self._send_via_rpc(
                "sendFile", [request.recipient, _file_to_data_uri(path), path.name, request.content or ""],
            )
        return self._guarded_send(WAAction.SEND_FILE, request, _do)

    def send_image(self, request: WhatsAppRequest) -> WhatsAppResult:
        if not request.attachment:
            return WhatsAppResult(
                success=False, error_code=WAErrorCode.INVALID_REQUEST,
                error_message="attachment path is required for send_image()",
            )
        path = Path(request.attachment)
        if not path.is_file():
            return WhatsAppResult(
                success=False, error_code=WAErrorCode.FILE_NOT_FOUND,
                error_message=f"No such file: {request.attachment}",
            )

        def _do() -> WhatsAppResult:
            return self._send_via_rpc(
                "sendImage", [request.recipient, _file_to_data_uri(path), path.name, request.content or ""],
            )
        return self._guarded_send(WAAction.SEND_IMAGE, request, _do)

    def get_messages(self, limit: int = 10, unread_only: bool = True) -> List[Dict[str, Any]]:
        method, args = ("getAllUnreadMessages", []) if unread_only else ("getAllChats", [False])
        response, error_code, error_message = self._call(method, args)
        if error_code is not None:
            logger.warning(f"[WA_GET_MESSAGES_FAILED] {error_code}: {error_message}")
            return []
        if not isinstance(response, list):
            logger.warning(f"[WA_GET_MESSAGES_FAILED] unexpected {method} response shape")
            return []
        return response[:limit]

    def find_contact(self, query: str) -> List[Dict[str, Any]]:
        response, error_code, error_message = self._call("getAllContacts", [])
        if error_code is not None:
            logger.warning(f"[WA_FIND_CONTACT_FAILED] {error_code}: {error_message}")
            return []
        if not isinstance(response, list):
            logger.warning("[WA_FIND_CONTACT_FAILED] unexpected getAllContacts response shape")
            return []
        q = query.lower()
        return [
            c for c in response
            if isinstance(c, dict) and (
                q in str(c.get("name", "")).lower()
                or q in str(c.get("pushname", "")).lower()
                or q in str(c.get("id", "")).lower()
            )
        ][:10]

    def mark_read(self, chat_id: str) -> bool:
        response, error_code, error_message = self._call("sendSeen", [chat_id])
        if error_code is not None:
            logger.warning(f"[WA_MARK_READ_FAILED] {error_code}: {error_message}")
            return False
        return bool(response)

    def verify_on_whatsapp(self, phone: str) -> Optional[Dict[str, Any]]:
        """Not supported by the open-wa middleware — Baileys only."""
        logger.warning("[WA_ON_WHATSAPP] not supported by OpenWATransport")
        return None

    def subscribe_messages(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Step-1 scaffold: starts a background poll of get_messages() and invokes
        `callback` for each not-yet-seen message. Nothing in this codebase
        calls subscribe_messages() yet — inbound autonomous routing is
        explicitly out of scope for Step 1.
        """
        if self._poll_thread is not None:
            return  # already subscribed

        self._poll_stop = threading.Event()

        def _poll() -> None:
            seen_ids: set = set()
            while self._poll_stop is not None and not self._poll_stop.is_set():
                try:
                    for msg in self.get_messages(limit=20, unread_only=True):
                        mid = msg.get("id") or msg.get("chat_id")
                        if mid and mid not in seen_ids:
                            seen_ids.add(mid)
                            callback(msg)
                except Exception as e:
                    logger.warning(f"[WA_SUBSCRIBE_POLL_FAILED] {e}")
                self._poll_stop.wait(5.0)

        self._poll_thread = threading.Thread(target=_poll, daemon=True, name="wa-subscribe-poll")
        self._poll_thread.start()

    def stop_subscription(self) -> None:
        if self._poll_stop is not None:
            self._poll_stop.set()
        self._poll_thread = None

    # ── Phase 2.1 stubs (not supported by open-wa transport) ──────────────

    def download_media(self, chat_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """Not supported by OpenWA transport."""
        logger.warning("[WA_DOWNLOAD_MEDIA] not supported by OpenWATransport")
        return None

    def get_latest_media(
        self, chat_id: Optional[str] = None, sender_id: Optional[str] = None,
        media_type: Optional[str] = None, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Not supported by OpenWA transport."""
        logger.warning("[WA_GET_LATEST_MEDIA] not supported by OpenWATransport")
        return []

    def get_media_message(self, chat_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """Not supported by OpenWA transport."""
        logger.warning("[WA_GET_MEDIA_MESSAGE] not supported by OpenWATransport")
        return None
