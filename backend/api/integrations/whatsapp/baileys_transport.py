"""
baileys_transport.py — WhatsAppTransport implementation backed by the
Baileys sidecar (backend/integrations/whatsapp/sidecar, @whiskeysockets/baileys
v7.0.0-rc14).

Talks to the sidecar over plain HTTP on localhost only. Treats the sidecar
as an unreliable external process throughout: every method catches
connection errors, timeouts, and malformed responses and converts them into
a structured WhatsAppResult / empty list / False — this transport must never
raise into caller code.

HTTP contract (Phase 2 Baileys sidecar, NOT the old open-wa middleware):

  POST /<endpoint>   body: {named parameters}
    -> 200 { ok: true,  data: {...},  error: null }
    -> 200 { ok: false, data: null,   error: { code: "...", message: "..." } }
    -> 401 { ok: false, error: { code: "UNAUTHORIZED", ... } }
    -> 4xx/5xx with same error shape

  GET /healthz   (no auth required)
    -> { ok: bool, state: str, authenticated: bool, provider: "baileys", ... }

  GET /events    (auth required, SSE)
    -> Server-Sent Events stream with heartbeat comments every 15s.
       Each event: id, event_type, timestamp, provider, data.

Endpoints used:
  POST /sendText         { chat_id, text }
  POST /sendFile         { chat_id, file_path, filename?, caption? }
  POST /sendImage        { chat_id, file_path, caption? }
  POST /reply            { chat_id, quoted_message_id, text }
  POST /getMessages      { chat_id?, sender_id?, limit?, unread_only?, history_only? }
  POST /findContact      { query }
  POST /markRead         { chat_id }
  POST /downloadMedia    { chat_id, message_id }
  POST /getLatestMedia   { chat_id?, sender_id?, media_type?, limit? }
  POST /getMediaMessage  { chat_id, message_id }
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .dedup import SendDeduplicator, SendState
from .models import WAAction, WAErrorCode, WhatsAppRequest, WhatsAppResult
from .send_idempotency import PersistentSendStore, get_default_send_store, payload_hash as _payload_hash
from .transport import WhatsAppTransport

logger = logging.getLogger("wa_transport")

_PHONE_RE = re.compile(r'\+?[\d\s\-().]{7,}')

# SSE reconnect tuning
_SSE_INITIAL_BACKOFF_S = 1.0
_SSE_MAX_BACKOFF_S = 30.0
_SSE_BACKOFF_FACTOR = 2.0
_SSE_HEARTBEAT_TIMEOUT_S = 45.0  # 3× sidecar's 15s heartbeat interval


def _redact_phone(text: str) -> str:
    """Mirror ai_operator.core.whatsapp_web_helper.redact_phone."""
    return _PHONE_RE.sub('[PHONE]', text)


class BaileysTransport(WhatsAppTransport):
    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        timeout_s: float = 20.0,
        dedup_ttl_s: float = 600.0,
        persistent_store: Optional[PersistentSendStore] = None,
    ):
        if not api_key:
            raise ValueError("api_key is required — refusing to talk to an unauthenticated sidecar")
        self._host = host
        self._port = port
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._base_url = f"http://{host}:{port}"
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"x-api-key": api_key, "content-type": "application/json"},
            timeout=timeout_s,
        )
        # In-memory dedup is a fast first-level cache (zero I/O, catches a
        # same-process retry instantly). The persistent store below is
        # authoritative — it is what actually survives a process restart or
        # a freshly-constructed transport, which the in-memory-only cache
        # cannot (see send_idempotency.py's module docstring for why this
        # split exists — it was added after live testing proved the same
        # confirmed idempotency_key could otherwise produce two real sends
        # across separate process invocations).
        self._dedup = SendDeduplicator(ttl_seconds=dedup_ttl_s)
        self._persistent_store = persistent_store or get_default_send_store()
        self._sse_stop: Optional[threading.Event] = None
        self._sse_thread: Optional[threading.Thread] = None

    @classmethod
    def from_settings(cls) -> "BaileysTransport":
        from api.config import settings
        return cls(
            host=settings.wa_sidecar_host,
            port=settings.wa_sidecar_port,
            api_key=settings.wa_sidecar_api_key,
            timeout_s=settings.wa_sidecar_timeout_s,
        )

    def close(self) -> None:
        self.stop_subscription()
        self._client.close()

    # ── internal REST helper ──────────────────────────────────────────────

    def _call(
        self, method: str, body: Dict[str, Any], *, auth: bool = True,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[WAErrorCode], Optional[str]]:
        """
        POST /<method> with JSON body.
        Returns (data_dict, error_code, error_message).
        On success: error_code is None, data_dict is response["data"].
        On failure: data_dict is None.
        """
        headers = {"x-api-key": self._api_key} if auth else {}
        try:
            resp = self._client.post(f"/{method}", json=body, headers=headers if auth else None)
        except httpx.TimeoutException:
            return None, WAErrorCode.SIDECAR_TIMEOUT, f"Sidecar timed out calling {method}"
        except httpx.ConnectError:
            return None, WAErrorCode.SIDECAR_UNAVAILABLE, "Cannot reach WhatsApp sidecar — is it running?"
        except httpx.HTTPError as e:
            return None, WAErrorCode.SIDECAR_UNAVAILABLE, str(e)

        if resp.status_code == 401:
            return None, WAErrorCode.SESSION_NOT_AUTHENTICATED, "Sidecar rejected the API key"
        if resp.status_code == 404:
            return None, WAErrorCode.INVALID_REQUEST, f"Sidecar has no endpoint '{method}'"
        if resp.status_code == 503:
            return None, WAErrorCode.WHATSAPP_DISCONNECTED, "WhatsApp not connected to sidecar"

        try:
            envelope = resp.json()
        except ValueError:
            return None, WAErrorCode.MALFORMED_RESPONSE, "Sidecar returned a non-JSON response"
        if not isinstance(envelope, dict):
            return None, WAErrorCode.MALFORMED_RESPONSE, "Unexpected sidecar response shape"

        if not envelope.get("ok", False):
            err = envelope.get("error") or {}
            code = err.get("code", "") if isinstance(err, dict) else ""
            message = err.get("message", "") if isinstance(err, dict) else str(err)
            # Map well-known sidecar error codes
            if code in ("NOT_CONNECTED", "SERVICE_UNAVAILABLE"):
                return None, WAErrorCode.WHATSAPP_DISCONNECTED, message or "WhatsApp not connected"
            if code in ("INVALID_REQUEST", "FILE_NOT_FOUND", "FILE_TOO_LARGE", "FORBIDDEN"):
                return None, WAErrorCode.INVALID_REQUEST if code != "FILE_NOT_FOUND" else WAErrorCode.FILE_NOT_FOUND, message
            if resp.status_code >= 500:
                return None, WAErrorCode.PROVIDER_ERROR, message or f"sidecar error {resp.status_code}"
            if resp.status_code >= 400:
                return None, WAErrorCode.INVALID_REQUEST, message or f"sidecar rejected ({resp.status_code})"
            return None, WAErrorCode.PROVIDER_ERROR, message or "sidecar reported failure"

        return envelope.get("data"), None, None

    def _send_via_rest(self, endpoint: str, body: Dict[str, Any]) -> WhatsAppResult:
        """Send-type action: call endpoint and extract message_id from data."""
        data, error_code, error_message = self._call(endpoint, body)
        if error_code is not None:
            return WhatsAppResult(success=False, error_code=error_code, error_message=error_message)
        message_id = data.get("message_id") if isinstance(data, dict) else None
        chat_id = data.get("chat_id") if isinstance(data, dict) else None
        return WhatsAppResult(success=True, message_id=message_id, chat_id=chat_id)

    # ── dedup wrapper — applies to every outbound send action ──────────────
    # Two layers: SendDeduplicator (in-memory, same-process, zero I/O) is
    # checked first as a fast path; PersistentSendStore (SQLite, survives
    # process restarts / a freshly-constructed transport) is the
    # authoritative layer underneath it. See send_idempotency.py's module
    # docstring for why the persistent layer exists — proven necessary by
    # live testing, not theoretical.

    def _guarded_send(
        self, action: WAAction, request: WhatsAppRequest, do_send: Callable[[], WhatsAppResult],
    ) -> WhatsAppResult:
        key = request.idempotency_key
        if key:
            existing = self._dedup.begin(key)
            if existing is not None:
                if existing.state == SendState.SUCCESS:
                    logger.info(f"[WA_SEND_DEDUPED] action={action.value} reason=already_succeeded_memory")
                    return WhatsAppResult(
                        success=True, message_id=existing.message_id, chat_id=existing.chat_id, deduped=True,
                    )
                if existing.state == SendState.PENDING:
                    logger.warning(f"[WA_SEND_DEDUPED] action={action.value} reason=ambiguous_prior_attempt_memory")
                    return WhatsAppResult(
                        success=False, error_code=WAErrorCode.DUPLICATE_SUPPRESSED, deduped=True,
                        error_message=(
                            "A previous send with this idempotency_key has an unknown outcome "
                            "(likely a timeout). Refusing to resend blindly — verify via "
                            "get_messages() before retrying manually."
                        ),
                    )
                # FAILED entries fall through to a normal retry below.

            # In-memory had no record (fresh process / new transport /
            # nothing cached) — the persistent store is authoritative here.
            phash = _payload_hash(
                action.value, request.recipient, request.content,
                request.attachment, request.reply_to_message_id,
            )
            claim = self._persistent_store.claim(key, action.value, request.recipient, phash)

            if claim.status == "completed":
                logger.info(f"[WA_SEND_DEDUPED] action={action.value} reason=already_succeeded_persistent")
                # Backfill the in-memory cache so a subsequent same-process
                # retry hits the zero-I/O fast path next time.
                self._dedup.complete(key, True, claim.message_id, claim.chat_id)
                return WhatsAppResult(
                    success=True, message_id=claim.message_id,
                    chat_id=claim.chat_id or request.recipient, deduped=True,
                )
            if claim.status == "conflict":
                logger.warning(f"[WA_SEND_CONFLICT] action={action.value} key={key}")
                return WhatsAppResult(
                    success=False, error_code=WAErrorCode.INVALID_REQUEST, deduped=False,
                    error_message=claim.detail,
                )
            if claim.status == "pending":
                logger.warning(f"[WA_SEND_DEDUPED] action={action.value} reason=ambiguous_prior_attempt_persistent")
                return WhatsAppResult(
                    success=False, error_code=WAErrorCode.DUPLICATE_SUPPRESSED, deduped=True,
                    error_message=claim.detail,
                )
            # claim.status == "claimed" — this call owns the send, proceed.

        logger.info(f"[WA_SEND_START] action={action.value} recipient={_redact_phone(request.recipient)}")
        result = do_send()
        if result.chat_id is None:
            result.chat_id = request.recipient

        if key:
            if result.success:
                self._dedup.complete(key, True, result.message_id, result.chat_id)
                self._persistent_store.complete(key, True, message_id=result.message_id)
            elif result.error_code == WAErrorCode.SIDECAR_TIMEOUT:
                pass  # ambiguous outcome — leave PENDING in both layers, do not clear
            else:
                self._dedup.complete(key, False, None, None)
                self._dedup.forget(key)  # definite failure — safe to retry with the same key
                self._persistent_store.complete(
                    key, False,
                    error_code=result.error_code.value if result.error_code else None,
                    error_message=result.error_message,
                )

        if result.success:
            logger.info(f"[WA_SEND_SUCCESS] action={action.value} message_id={result.message_id}")
        else:
            logger.warning(f"[WA_SEND_FAILED] action={action.value} error_code={result.error_code}")
        return result

    # ── WhatsAppTransport ────────────────────────────────────────────────────

    def healthcheck(self) -> Dict[str, Any]:
        """GET /healthz — no auth required, returns sidecar + connection status."""
        try:
            resp = self._client.get("/healthz", headers=None)
        except httpx.TimeoutException:
            return {"status": "error", "error_code": WAErrorCode.SIDECAR_TIMEOUT.value,
                    "error": "Sidecar timed out on healthcheck"}
        except httpx.ConnectError:
            return {"status": "error", "error_code": WAErrorCode.SIDECAR_UNAVAILABLE.value,
                    "error": "Cannot reach WhatsApp sidecar — is it running?"}
        except httpx.HTTPError as e:
            return {"status": "error", "error_code": WAErrorCode.SIDECAR_UNAVAILABLE.value,
                    "error": str(e)}

        try:
            body = resp.json()
        except ValueError:
            return {"status": "error", "error_code": WAErrorCode.MALFORMED_RESPONSE.value,
                    "error": "Sidecar returned non-JSON on /healthz"}

        return {
            "status": "connected" if body.get("ok") else "disconnected",
            "connected": bool(body.get("ok")),
            "state": body.get("state"),
            "authenticated": bool(body.get("authenticated")),
            "provider": body.get("provider", "baileys"),
        }

    def send_text(self, request: WhatsAppRequest) -> WhatsAppResult:
        def _do() -> WhatsAppResult:
            return self._send_via_rest("sendText", {
                "chat_id": request.recipient,
                "text": request.content or "",
            })
        return self._guarded_send(WAAction.SEND_TEXT, request, _do)

    def reply(self, request: WhatsAppRequest) -> WhatsAppResult:
        if not request.reply_to_message_id:
            return WhatsAppResult(
                success=False, error_code=WAErrorCode.INVALID_REQUEST,
                error_message="reply_to_message_id is required for reply()",
            )

        def _do() -> WhatsAppResult:
            return self._send_via_rest("reply", {
                "chat_id": request.recipient,
                "quoted_message_id": request.reply_to_message_id,
                "text": request.content or "",
            })
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
            body: Dict[str, Any] = {
                "chat_id": request.recipient,
                "file_path": str(path),
                "filename": path.name,
            }
            if request.content:
                body["caption"] = request.content
            return self._send_via_rest("sendFile", body)
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
            body: Dict[str, Any] = {
                "chat_id": request.recipient,
                "file_path": str(path),
            }
            if request.content:
                body["caption"] = request.content
            return self._send_via_rest("sendImage", body)
        return self._guarded_send(WAAction.SEND_IMAGE, request, _do)

    def get_messages(self, limit: int = 10, unread_only: bool = True) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {"limit": limit, "unread_only": unread_only}
        data, error_code, error_message = self._call("getMessages", body)
        if error_code is not None:
            logger.warning(f"[WA_GET_MESSAGES_FAILED] {error_code}: {error_message}")
            return []
        messages = data.get("messages", []) if isinstance(data, dict) else []
        if not isinstance(messages, list):
            logger.warning("[WA_GET_MESSAGES_FAILED] unexpected response shape for messages")
            return []
        return messages[:limit]

    def find_contact(self, query: str) -> List[Dict[str, Any]]:
        data, error_code, error_message = self._call("findContact", {"query": query})
        if error_code is not None:
            logger.warning(f"[WA_FIND_CONTACT_FAILED] {error_code}: {error_message}")
            return []
        contacts = data.get("contacts", []) if isinstance(data, dict) else []
        if not isinstance(contacts, list):
            logger.warning("[WA_FIND_CONTACT_FAILED] unexpected response shape for contacts")
            return []
        return contacts

    def verify_on_whatsapp(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Authoritative phone → WhatsApp identity via POST /onWhatsApp.
        Returns {exists, jid, phone} — jid may be @lid. None on failure.
        Never raises.
        """
        try:
            data, error_code, error_message = self._call("onWhatsApp", {"phone": phone})
        except Exception as e:
            logger.warning(f"[WA_ON_WHATSAPP_FAILED] exception: {e}")
            return None
        if error_code is not None:
            logger.warning(f"[WA_ON_WHATSAPP_FAILED] {error_code}: {error_message}")
            return None
        if not isinstance(data, dict) or "exists" not in data:
            logger.warning("[WA_ON_WHATSAPP_FAILED] unexpected response shape")
            return None
        # Log identity domain only — never the raw phone or full JID
        jid = data.get("jid")
        domain = jid.split("@")[1] if jid and "@" in jid else None
        logger.info(f"[WA_ON_WHATSAPP] exists={data.get('exists')} domain={domain}")
        return data

    def mark_read(self, chat_id: str) -> bool:
        data, error_code, error_message = self._call("markRead", {"chat_id": chat_id})
        if error_code is not None:
            logger.warning(f"[WA_MARK_READ_FAILED] {error_code}: {error_message}")
            return False
        return data is not None

    # ── SSE subscribe_messages ──────────────────────────────────────────────

    def subscribe_messages(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Background SSE consumer for GET /events.

        Explicit reconnect loop with:
          - bounded exponential backoff (1s → 30s)
          - heartbeat timeout detection (45s without any data = reconnect)
          - SSE event-ID tracking for duplicate suppression after reconnect
          - graceful shutdown via stop_subscription()
          - no busy loop (uses httpx stream + sleep on error)
        """
        if self._sse_thread is not None:
            return  # already subscribed

        self._sse_stop = threading.Event()

        def _sse_loop() -> None:
            seen_ids: set = set()
            backoff = _SSE_INITIAL_BACKOFF_S
            last_event_id: Optional[str] = None

            while self._sse_stop is not None and not self._sse_stop.is_set():
                try:
                    self._consume_sse_stream(callback, seen_ids, last_event_id)
                    # If consume returns normally, connection was lost — reconnect
                    backoff = _SSE_INITIAL_BACKOFF_S  # reset on clean disconnect
                except Exception as e:
                    logger.warning(f"[WA_SSE_ERROR] {e}")

                # Wait before reconnect with backoff
                if self._sse_stop is not None and not self._sse_stop.is_set():
                    self._sse_stop.wait(backoff)
                    backoff = min(backoff * _SSE_BACKOFF_FACTOR, _SSE_MAX_BACKOFF_S)

        self._sse_thread = threading.Thread(target=_sse_loop, daemon=True, name="wa-sse-consumer")
        self._sse_thread.start()

    def _consume_sse_stream(
        self,
        callback: Callable[[Dict[str, Any]], None],
        seen_ids: set,
        last_event_id: Optional[str],
    ) -> None:
        """
        Open a single SSE connection and consume events until it drops or
        heartbeat timeout fires. Raises on connection failure.
        """
        import json as _json

        headers = {
            "x-api-key": self._api_key,
            "accept": "text/event-stream",
        }
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id

        with httpx.stream(
            "GET",
            f"{self._base_url}/events",
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=_SSE_HEARTBEAT_TIMEOUT_S, write=10.0, pool=10.0),
        ) as response:
            if response.status_code != 200:
                logger.warning(f"[WA_SSE_CONNECT_FAILED] status={response.status_code}")
                return

            logger.info("[WA_SSE_CONNECTED] streaming events from sidecar")
            buffer = ""

            for raw_line in response.iter_lines():
                if self._sse_stop is not None and self._sse_stop.is_set():
                    return

                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")

                # SSE heartbeat comment
                if line.startswith(":"):
                    continue

                # Empty line = end of event frame
                if not line.strip():
                    if buffer.strip():
                        self._process_sse_frame(buffer, callback, seen_ids)
                    buffer = ""
                    continue

                buffer += line + "\n"

    def _process_sse_frame(
        self,
        frame: str,
        callback: Callable[[Dict[str, Any]], None],
        seen_ids: set,
    ) -> None:
        """Parse one complete SSE event frame and dispatch."""
        import json as _json

        event_id = None
        data_lines = []

        for line in frame.strip().split("\n"):
            if line.startswith("id:"):
                event_id = line[3:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            # "event:" lines are informational — we use the JSON payload's event_type

        if not data_lines:
            return

        raw = "\n".join(data_lines)
        try:
            evt = _json.loads(raw)
        except _json.JSONDecodeError:
            logger.warning(f"[WA_SSE_BAD_JSON] {raw[:200]}")
            return

        # Duplicate suppression
        eid = event_id or evt.get("id")
        if eid:
            if eid in seen_ids:
                return
            seen_ids.add(eid)
            # Bound the seen set — evict oldest entries beyond a reasonable window
            if len(seen_ids) > 5000:
                # Simple trimming: keep the most recent 4000
                to_remove = list(seen_ids)[:1000]
                for old_id in to_remove:
                    seen_ids.discard(old_id)

        # Only dispatch live message events to the callback
        event_type = evt.get("event_type", "")
        if event_type == "whatsapp.message":
            try:
                callback(evt.get("data", {}))
            except Exception as e:
                logger.warning(f"[WA_SSE_CALLBACK_ERROR] {e}")

    def stop_subscription(self) -> None:
        if self._sse_stop is not None:
            self._sse_stop.set()
        self._sse_thread = None

    # ── Phase 2.1: media retrieval ────────────────────────────────────────

    def download_media(self, chat_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Download media for a specific message via POST /downloadMedia.
        Returns dict with local_path on success, None on failure. Never raises.
        """
        try:
            data, error_code, error_message = self._call("downloadMedia", {
                "chat_id": chat_id, "message_id": message_id,
            })
        except Exception as e:
            logger.warning(f"[WA_DOWNLOAD_MEDIA_FAILED] exception: {e}")
            return None
        if error_code is not None:
            logger.warning(f"[WA_DOWNLOAD_MEDIA_FAILED] {error_code}: {error_message}")
            return None
        if not isinstance(data, dict):
            logger.warning("[WA_DOWNLOAD_MEDIA_FAILED] unexpected response shape")
            return None
        logger.info(
            f"[WA_DOWNLOAD_MEDIA] message_id={message_id} media_type={data.get('media_type')} "
            f"reused={data.get('reused')}"
        )
        return data

    def get_latest_media(
        self, chat_id: Optional[str] = None, sender_id: Optional[str] = None,
        media_type: Optional[str] = None, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Query latest media messages via POST /getLatestMedia.
        Returns [] on failure — never raises.
        """
        body: Dict[str, Any] = {"limit": limit}
        if chat_id:
            body["chat_id"] = chat_id
        if sender_id:
            body["sender_id"] = sender_id
        if media_type:
            body["media_type"] = media_type
        data, error_code, error_message = self._call("getLatestMedia", body)
        if error_code is not None:
            logger.warning(f"[WA_GET_LATEST_MEDIA_FAILED] {error_code}: {error_message}")
            return []
        messages = data.get("messages", []) if isinstance(data, dict) else []
        if not isinstance(messages, list):
            logger.warning("[WA_GET_LATEST_MEDIA_FAILED] unexpected response shape")
            return []
        return messages[:limit]

    def get_media_message(self, chat_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up a specific media message via POST /getMediaMessage.
        Returns None if not found — never raises.
        """
        data, error_code, error_message = self._call("getMediaMessage", {
            "chat_id": chat_id, "message_id": message_id,
        })
        if error_code is not None:
            logger.warning(f"[WA_GET_MEDIA_MESSAGE_FAILED] {error_code}: {error_message}")
            return None
        return data if isinstance(data, dict) else None
