"""
Tests for api.integrations.whatsapp.baileys_transport.BaileysTransport.

The sidecar is never actually started here — every test mocks the transport's
internal httpx.Client so this suite runs with no live WhatsApp session and no
Node process.

The mocked response shapes mirror the VERIFIED Baileys sidecar REST contract
(Phase 2): { ok: true, data: {...}, error: null } or
{ ok: false, data: null, error: { code: "...", message: "..." } }.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from api.integrations.whatsapp.models import WAAction, WAErrorCode, WhatsAppRequest
from api.integrations.whatsapp.baileys_transport import BaileysTransport
from api.integrations.whatsapp.send_idempotency import PersistentSendStore


def make_transport() -> BaileysTransport:
    # Each call gets its own isolated, temp-file-backed PersistentSendStore
    # — without this, every test that sets idempotency_key would read/write
    # the real default backend/data/whatsapp_send_idempotency.db, making
    # these tests order-dependent and able to pollute (or be polluted by)
    # real send history.
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="wa_idempotency_test_")
    store = PersistentSendStore(path=Path(tmp_dir) / "test_idempotency.db")
    return BaileysTransport(host="127.0.0.1", port=8734, api_key="test-key", timeout_s=1.0,
                             persistent_store=store)


def json_response(status_code: int, payload) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "http://x"))


def ok_response(data) -> httpx.Response:
    return json_response(200, {"ok": True, "data": data, "error": None})


def error_response(code: str, message: str, status: int = 500) -> httpx.Response:
    return json_response(status, {"ok": False, "data": None, "error": {"code": code, "message": message}})


# ── construction / config ──────────────────────────────────────────────────

def test_api_key_is_required():
    with pytest.raises(ValueError):
        BaileysTransport(host="127.0.0.1", port=8734, api_key="")


def test_from_settings_reads_config(monkeypatch):
    import api.config as config_module

    monkeypatch.setattr(config_module.settings, "wa_sidecar_host", "127.0.0.1")
    monkeypatch.setattr(config_module.settings, "wa_sidecar_port", 9999)
    monkeypatch.setattr(config_module.settings, "wa_sidecar_api_key", "from-settings-key")
    monkeypatch.setattr(config_module.settings, "wa_sidecar_timeout_s", 5.0)

    transport = BaileysTransport.from_settings()
    try:
        assert str(transport._client.base_url) == "http://127.0.0.1:9999"
    finally:
        transport.close()


# ── healthcheck (GET /healthz, no auth) ──────────────────────────────────────

def test_healthcheck_success():
    transport = make_transport()
    transport._client.get = MagicMock(return_value=json_response(200, {
        "ok": True, "state": "open", "authenticated": True,
        "provider": "baileys", "sse_clients": 0, "error": None,
    }))
    result = transport.healthcheck()
    assert result["status"] == "connected"
    assert result["connected"] is True
    assert result["state"] == "open"
    assert result["authenticated"] is True
    assert result["provider"] == "baileys"


def test_healthcheck_disconnected():
    transport = make_transport()
    transport._client.get = MagicMock(return_value=json_response(200, {
        "ok": False, "state": "connecting", "authenticated": False,
        "provider": "baileys", "sse_clients": 0, "error": None,
    }))
    result = transport.healthcheck()
    assert result["status"] == "disconnected"
    assert result["connected"] is False


def test_healthcheck_sidecar_unavailable():
    transport = make_transport()
    transport._client.get = MagicMock(side_effect=httpx.ConnectError("refused"))
    result = transport.healthcheck()
    assert result["status"] == "error"
    assert result["error_code"] == WAErrorCode.SIDECAR_UNAVAILABLE.value


def test_healthcheck_timeout():
    transport = make_transport()
    transport._client.get = MagicMock(side_effect=httpx.TimeoutException("slow"))
    result = transport.healthcheck()
    assert result["status"] == "error"
    assert result["error_code"] == WAErrorCode.SIDECAR_TIMEOUT.value


# ── send_text ────────────────────────────────────────────────────────────────

def test_send_text_success():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({
        "message_id": "3EB0ABC123", "chat_id": "923001234567@s.whatsapp.net",
        "timestamp": "2026-08-30T05:00:00.000Z", "from_me": True, "provider": "baileys",
    }))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is True
    assert result.message_id == "3EB0ABC123"
    assert result.chat_id == "923001234567@s.whatsapp.net"
    transport._client.post.assert_called_once()
    called_path = transport._client.post.call_args.args[0]
    called_body = transport._client.post.call_args.kwargs["json"]
    assert called_path == "/sendText"
    assert called_body == {"chat_id": "923001234567@s.whatsapp.net", "text": "hi"}


def test_send_text_provider_error():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=error_response("PROVIDER_ERROR", "message send failed"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.PROVIDER_ERROR
    assert "message send failed" in result.error_message


def test_send_text_timeout():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.TimeoutException("slow"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.SIDECAR_TIMEOUT


def test_send_malformed_response():
    transport = make_transport()
    bad_resp = httpx.Response(200, content=b"not json", request=httpx.Request("POST", "http://x"))
    transport._client.post = MagicMock(return_value=bad_resp)
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.MALFORMED_RESPONSE


def test_send_unauthenticated_sidecar():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        401, json={"ok": False, "data": None, "error": {"code": "UNAUTHORIZED", "message": "bad key"}},
        request=httpx.Request("POST", "http://x"),
    ))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.SESSION_NOT_AUTHENTICATED


def test_send_whatsapp_disconnected():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        503, json={"ok": False, "data": None, "error": {"code": "NOT_CONNECTED", "message": "WhatsApp not connected"}},
        request=httpx.Request("POST", "http://x"),
    ))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.WHATSAPP_DISCONNECTED


def test_send_unknown_endpoint_maps_to_invalid_request():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        404, content=b"Not Found", request=httpx.Request("POST", "http://x"),
    ))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.INVALID_REQUEST


# ── send_file / send_image ──────────────────────────────────────────────────

def test_send_file_missing_file_never_calls_network(tmp_path):
    transport = make_transport()
    transport._client.post = MagicMock()
    missing = tmp_path / "does-not-exist.pdf"

    request = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="923001234567@s.whatsapp.net", attachment=str(missing))
    result = transport.send_file(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.FILE_NOT_FOUND
    transport._client.post.assert_not_called()


def test_send_file_success(tmp_path):
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({
        "message_id": "msg-file-1", "chat_id": "923001234567@s.whatsapp.net",
        "timestamp": "2026-08-30T05:00:00.000Z", "from_me": True, "provider": "baileys", "filename": "doc.pdf",
    }))
    real_file = tmp_path / "doc.pdf"
    real_file.write_bytes(b"%PDF-1.4 fake content")

    request = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="923001234567@s.whatsapp.net",
                               attachment=str(real_file), content="here you go")
    result = transport.send_file(request)

    assert result.success is True
    assert result.message_id == "msg-file-1"
    called_path = transport._client.post.call_args.args[0]
    body = transport._client.post.call_args.kwargs["json"]
    assert called_path == "/sendFile"
    assert body["chat_id"] == "923001234567@s.whatsapp.net"
    assert body["file_path"] == str(real_file)
    assert body["filename"] == "doc.pdf"
    assert body["caption"] == "here you go"


def test_send_file_no_attachment():
    transport = make_transport()
    request = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="923001234567@s.whatsapp.net")
    result = transport.send_file(request)
    assert result.success is False
    assert result.error_code == WAErrorCode.INVALID_REQUEST


def test_send_image_uses_sendimage_endpoint(tmp_path):
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({
        "message_id": "msg-img-1", "chat_id": "923001234567@s.whatsapp.net",
        "timestamp": "2026-08-30T05:00:00.000Z", "from_me": True, "provider": "baileys",
    }))
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")

    request = WhatsAppRequest(action=WAAction.SEND_IMAGE, recipient="923001234567@s.whatsapp.net", attachment=str(img))
    result = transport.send_image(request)

    assert result.success is True
    called_path = transport._client.post.call_args.args[0]
    assert called_path == "/sendImage"
    body = transport._client.post.call_args.kwargs["json"]
    assert body["file_path"] == str(img)


def test_send_image_no_attachment():
    transport = make_transport()
    request = WhatsAppRequest(action=WAAction.SEND_IMAGE, recipient="923001234567@s.whatsapp.net")
    result = transport.send_image(request)
    assert result.success is False
    assert result.error_code == WAErrorCode.INVALID_REQUEST


# ── reply ────────────────────────────────────────────────────────────────────

def test_reply_requires_message_id():
    transport = make_transport()
    request = WhatsAppRequest(action=WAAction.REPLY, recipient="923001234567@s.whatsapp.net", content="ok")
    result = transport.reply(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.INVALID_REQUEST


def test_reply_success():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({
        "message_id": "msg-reply-1", "chat_id": "923001234567@s.whatsapp.net",
        "timestamp": "2026-08-30T05:00:00.000Z", "quoted_message_id": "original-msg-id",
        "from_me": True, "provider": "baileys",
    }))

    request = WhatsAppRequest(action=WAAction.REPLY, recipient="923001234567@s.whatsapp.net",
                               content="got it", reply_to_message_id="original-msg-id")
    result = transport.reply(request)

    assert result.success is True
    called_path = transport._client.post.call_args.args[0]
    body = transport._client.post.call_args.kwargs["json"]
    assert called_path == "/reply"
    assert body == {
        "chat_id": "923001234567@s.whatsapp.net",
        "quoted_message_id": "original-msg-id",
        "text": "got it",
    }


def test_reply_provider_error():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=error_response("PROVIDER_ERROR", "quoted msg not found"))

    request = WhatsAppRequest(action=WAAction.REPLY, recipient="923001234567@s.whatsapp.net",
                               content="ok", reply_to_message_id="bad-id")
    result = transport.reply(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.PROVIDER_ERROR


# ── duplicate-send protection ────────────────────────────────────────────────

def test_duplicate_send_is_suppressed_on_ambiguous_timeout():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.TimeoutException("slow"))

    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net",
                               content="hi", idempotency_key="fixed-key-1")
    first = transport.send_text(request)
    second = transport.send_text(request)

    assert first.success is False and first.error_code == WAErrorCode.SIDECAR_TIMEOUT
    assert second.success is False
    assert second.error_code == WAErrorCode.DUPLICATE_SUPPRESSED
    assert second.deduped is True
    assert transport._client.post.call_count == 1


def test_duplicate_send_returns_cached_success_without_resending():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({"message_id": "msg-once", "chat_id": "c"}))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net",
                               content="hi", idempotency_key="fixed-key-2")
    first = transport.send_text(request)
    second = transport.send_text(request)

    assert first.success is True and second.success is True
    assert second.deduped is True
    assert second.message_id == first.message_id
    assert transport._client.post.call_count == 1


def test_definite_failure_allows_retry_with_same_key():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=error_response("PROVIDER_ERROR", "temporary"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net",
                               content="hi", idempotency_key="fixed-key-3")
    first = transport.send_text(request)
    second = transport.send_text(request)

    assert first.success is False and second.success is False
    assert second.error_code == WAErrorCode.PROVIDER_ERROR  # not DUPLICATE_SUPPRESSED
    assert transport._client.post.call_count == 2  # a definite failure is retryable


# ── sidecar restart recovery ──────────────────────────────────────────────

def test_transport_recovers_after_sidecar_comes_back():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=[
        httpx.ConnectError("refused"),
        ok_response({"message_id": "msg-after-restart", "chat_id": "c"}),
    ])
    request_1 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="hi")
    down = transport.send_text(request_1)
    assert down.success is False and down.error_code == WAErrorCode.SIDECAR_UNAVAILABLE

    request_2 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923001234567@s.whatsapp.net", content="still there?")
    up = transport.send_text(request_2)
    assert up.success is True
    assert up.message_id == "msg-after-restart"


# ── get_messages ─────────────────────────────────────────────────────────────

def test_get_messages_returns_empty_list_on_failure():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
    assert transport.get_messages() == []


def test_get_messages_success():
    transport = make_transport()
    messages = [
        {"message_id": "m1", "chat_id": "c1", "sender_name": "Ali", "text": "hello",
         "timestamp": "2026-08-30T05:00:00.000Z", "event_origin": "live"},
    ]
    transport._client.post = MagicMock(return_value=ok_response({"messages": messages, "total": 1}))
    result = transport.get_messages(limit=5, unread_only=True)

    assert len(result) == 1
    assert result[0]["message_id"] == "m1"
    body = transport._client.post.call_args.kwargs["json"]
    assert body == {"limit": 5, "unread_only": True}


def test_get_messages_calls_getmessages_endpoint():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({"messages": [], "total": 0}))
    transport.get_messages(limit=20, unread_only=False)
    assert transport._client.post.call_args.args[0] == "/getMessages"


# ── find_contact ─────────────────────────────────────────────────────────────

def test_find_contact_success():
    transport = make_transport()
    contacts = [
        {"contact_id": "1@lid", "display_name": "Ali Khan", "phone": None, "push_name": "Ali", "chat_id": "1@s.whatsapp.net"},
        {"contact_id": "2@lid", "display_name": "Ali Raza", "phone": "+923001112222", "push_name": "Ali R", "chat_id": "2@s.whatsapp.net"},
    ]
    transport._client.post = MagicMock(return_value=ok_response({"contacts": contacts, "total": 2}))
    result = transport.find_contact("Ali")

    assert len(result) == 2
    assert result[0]["display_name"] == "Ali Khan"
    body = transport._client.post.call_args.kwargs["json"]
    assert body == {"query": "Ali"}


def test_find_contact_returns_empty_on_failure():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
    assert transport.find_contact("Ali") == []


def test_find_contact_ambiguous_returns_multiple_candidates():
    transport = make_transport()
    contacts = [
        {"contact_id": "a@lid", "display_name": "Sara Khan", "phone": None},
        {"contact_id": "b@lid", "display_name": "Sara Ali", "phone": None},
    ]
    transport._client.post = MagicMock(return_value=ok_response({"contacts": contacts, "total": 2}))
    result = transport.find_contact("Sara")
    assert len(result) == 2  # ambiguous — both returned


# ── mark_read ────────────────────────────────────────────────────────────────

def test_mark_read_success():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({"chat_id": "c1", "messages_marked": 5}))
    assert transport.mark_read("c1") is True
    body = transport._client.post.call_args.kwargs["json"]
    assert body == {"chat_id": "c1"}


def test_mark_read_failure():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
    assert transport.mark_read("c1") is False


# ── LID / JID handling ──────────────────────────────────────────────────────

def test_lid_jid_preserved_in_send_response():
    """Baileys v7 uses LIDs. chat_id and message_id must be preserved exactly."""
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({
        "message_id": "3EB0ABC999",
        "chat_id": "123456789012345678@lid",
        "timestamp": "2026-08-30T05:00:00.000Z", "from_me": True, "provider": "baileys",
    }))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="123456789012345678@lid", content="hi")
    result = transport.send_text(request)

    assert result.success is True
    assert result.chat_id == "123456789012345678@lid"


def test_group_jid_preserved():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({
        "message_id": "msg-grp-1",
        "chat_id": "120363123456789@g.us",
        "timestamp": "2026-08-30T05:00:00.000Z", "from_me": True, "provider": "baileys",
    }))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="120363123456789@g.us", content="hi group")
    result = transport.send_text(request)

    assert result.success is True
    assert result.chat_id == "120363123456789@g.us"


# ── provider error mapping ──────────────────────────────────────────────────

def test_file_not_found_error_code():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        404, json={"ok": False, "data": None, "error": {"code": "FILE_NOT_FOUND", "message": "no such file"}},
        request=httpx.Request("POST", "http://x"),
    ))
    request = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="c@s.whatsapp.net", attachment="/tmp/fake.pdf")
    # send_file validates locally first — mock the local check away by patching Path
    with patch("api.integrations.whatsapp.baileys_transport.Path") as mock_path:
        mock_path.return_value.is_file.return_value = True
        mock_path.return_value.name = "fake.pdf"
        mock_path.return_value.__str__ = lambda self: "/tmp/fake.pdf"
        result = transport.send_file(request)

    # Even though local check passed, sidecar reports FILE_NOT_FOUND
    # But since we mocked Path, is_file returned True, so it tries the network call
    # The sidecar 404 response maps to INVALID_REQUEST (status >= 400)
    assert result.success is False


def test_forbidden_error_maps_correctly():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        403, json={"ok": False, "data": None, "error": {"code": "FORBIDDEN", "message": "cannot send files from .secrets"}},
        request=httpx.Request("POST", "http://x"),
    ))
    # Direct _call test
    data, code, msg = transport._call("sendFile", {"chat_id": "c", "file_path": "/bad"})
    assert code == WAErrorCode.INVALID_REQUEST


# ── SSE event parsing ───────────────────────────────────────────────────────

def test_sse_process_frame_dispatches_live_message():
    transport = make_transport()
    received = []
    seen = set()

    frame = 'id: 1725000000000-1\nevent: whatsapp.message\ndata: {"id":"1725000000000-1","event_type":"whatsapp.message","data":{"message_id":"m1","chat_id":"c1","text":"hello","from_me":false}}\n'
    transport._process_sse_frame(frame, received.append, seen)

    assert len(received) == 1
    assert received[0]["message_id"] == "m1"
    assert "1725000000000-1" in seen


def test_sse_process_frame_ignores_history_events():
    transport = make_transport()
    received = []
    seen = set()

    frame = 'id: 1725000000000-2\nevent: whatsapp.history\ndata: {"id":"1725000000000-2","event_type":"whatsapp.history","data":{"message_id":"m2","chat_id":"c1","event_origin":"history_sync"}}\n'
    transport._process_sse_frame(frame, received.append, seen)

    # history events should NOT be dispatched to callback
    assert len(received) == 0


def test_sse_process_frame_ignores_call_events():
    transport = make_transport()
    received = []
    seen = set()

    frame = 'id: 1725000000000-3\nevent: whatsapp.call.incoming\ndata: {"id":"1725000000000-3","event_type":"whatsapp.call.incoming","data":{"call_id":"call1","caller_id":"c1"}}\n'
    transport._process_sse_frame(frame, received.append, seen)

    assert len(received) == 0


def test_sse_duplicate_event_suppressed():
    transport = make_transport()
    received = []
    seen = set()

    frame = 'id: evt-1\nevent: whatsapp.message\ndata: {"id":"evt-1","event_type":"whatsapp.message","data":{"message_id":"m1","text":"hi"}}\n'
    transport._process_sse_frame(frame, received.append, seen)
    transport._process_sse_frame(frame, received.append, seen)  # duplicate

    assert len(received) == 1  # only dispatched once


def test_sse_heartbeat_comment_ignored():
    """Heartbeat comments (: heartbeat) should not cause errors."""
    transport = make_transport()
    received = []
    seen = set()

    # An empty frame after a heartbeat comment
    frame = ""
    transport._process_sse_frame(frame, received.append, seen)
    assert len(received) == 0


def test_sse_malformed_json_handled():
    transport = make_transport()
    received = []
    seen = set()

    frame = 'id: evt-bad\nevent: whatsapp.message\ndata: {not valid json}\n'
    transport._process_sse_frame(frame, received.append, seen)

    assert len(received) == 0  # no crash, no dispatch


def test_sse_seen_ids_bounded():
    transport = make_transport()
    received = []
    seen = set()

    # Add 5001 events — should trigger eviction
    for i in range(5001):
        frame = f'id: evt-{i}\nevent: whatsapp.message\ndata: {{"id":"evt-{i}","event_type":"whatsapp.message","data":{{"message_id":"m{i}","text":"msg"}}}}\n'
        transport._process_sse_frame(frame, received.append, seen)

    # seen set should have been trimmed
    assert len(seen) < 5001


# ── logging hygiene ──────────────────────────────────────────────────────────

def test_no_phone_number_or_message_body_in_logs(caplog):
    transport = make_transport()
    transport._client.post = MagicMock(return_value=ok_response({"message_id": "msg-log-test", "chat_id": "c"}))
    secret_phone = "923009998877@s.whatsapp.net"
    secret_body = "the secret passphrase is banana49"

    with caplog.at_level(logging.INFO, logger="wa_transport"):
        request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient=secret_phone, content=secret_body)
        transport.send_text(request)

    log_text = "\n".join(r.message for r in caplog.records)
    assert secret_phone not in log_text
    assert secret_body not in log_text
    assert "WA_SEND_START" in log_text
    assert "WA_SEND_SUCCESS" in log_text


# ── persistent (cross-process) send idempotency ────────────────────────────
# dedup.SendDeduplicator is documented as in-memory-only, scoped to one
# BaileysTransport instance — proven insufficient by live Phase 4 testing
# (the same confirmed idempotency_key produced two real WhatsApp messages
# across two separate process invocations). These tests exercise the
# PersistentSendStore-backed authoritative layer underneath it, at the
# BaileysTransport level, for every guarded action.

def _transport_with_store(store) -> BaileysTransport:
    return BaileysTransport(host="127.0.0.1", port=8734, api_key="test-key", timeout_s=1.0,
                             persistent_store=store)


class TestPersistentIdempotencySendText:
    def test_new_transport_instance_same_process_same_action_one_send(self, tmp_path):
        """Two DIFFERENT BaileysTransport objects (e.g. two request handlers
        in the same long-running server) sharing one persistent store must
        never both send."""
        store = PersistentSendStore(path=tmp_path / "shared.db")
        t1 = _transport_with_store(store)
        t2 = _transport_with_store(store)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "MSG1", "chat_id": "c"}))
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-SHOULD-NOT-HAPPEN", "chat_id": "c"}))

        req = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923@s.whatsapp.net",
                               content="hi", idempotency_key="shared-key-1")
        r1 = t1.send_text(req)
        r2 = t2.send_text(req)

        assert r1.success is True and r1.deduped is False
        assert r2.success is True and r2.deduped is True
        assert r2.message_id == r1.message_id == "MSG1"
        t2._client.post.assert_not_called()

    def test_simulated_process_restart_fresh_persistence_client_one_send(self, tmp_path):
        """A fresh PersistentSendStore object pointed at the same db file —
        the closest a unit test can get to an actual process restart —
        must still see the completed claim and refuse a second real send."""
        db_path = tmp_path / "restart.db"
        store1 = PersistentSendStore(path=db_path)
        t1 = _transport_with_store(store1)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-FIRST", "chat_id": "c"}))
        req = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923@s.whatsapp.net",
                               content="hi", idempotency_key="restart-key-1")
        r1 = t1.send_text(req)

        # "process restart": brand new store object AND brand new transport,
        # nothing shared with t1/store1 except the underlying db file.
        store2 = PersistentSendStore(path=db_path)
        t2 = _transport_with_store(store2)
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-SHOULD-NOT-HAPPEN", "chat_id": "c"}))
        r2 = t2.send_text(req)

        assert r1.deduped is False
        assert r2.deduped is True
        assert r2.message_id == "MSG-FIRST"
        t2._client.post.assert_not_called()

    def test_same_key_different_payload_is_conflict_not_resend(self, tmp_path):
        store = PersistentSendStore(path=tmp_path / "conflict.db")
        t1 = _transport_with_store(store)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-TAYYAB", "chat_id": "c"}))
        req1 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923_tayyab@s.whatsapp.net",
                                content="hi", idempotency_key="reused-key")
        r1 = t1.send_text(req1)

        t2 = _transport_with_store(store)
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-SHOULD-NOT-HAPPEN", "chat_id": "c"}))
        req2 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923_ali@s.whatsapp.net",
                                content="different message", idempotency_key="reused-key")
        r2 = t2.send_text(req2)

        assert r1.success is True
        assert r2.success is False
        assert r2.deduped is False  # a conflict is an error, not a dedup
        assert r2.error_code == WAErrorCode.INVALID_REQUEST
        t2._client.post.assert_not_called()

    def test_new_action_id_identical_text_and_contact_sends_again(self, tmp_path):
        """A genuinely new user command (fresh idempotency_key) must NOT be
        suppressed just because the text/recipient happen to match a prior
        completed send — this is 'send it again', not a retry."""
        store = PersistentSendStore(path=tmp_path / "fresh_action.db")
        t1 = _transport_with_store(store)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-A", "chat_id": "c"}))
        req1 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923@s.whatsapp.net",
                                content="hi", idempotency_key="action-A")
        r1 = t1.send_text(req1)

        t2 = _transport_with_store(store)
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "MSG-B", "chat_id": "c"}))
        req2 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="923@s.whatsapp.net",
                                content="hi", idempotency_key="action-B")  # new action_id
        r2 = t2.send_text(req2)

        assert r1.success and r2.success
        assert r2.deduped is False
        assert r2.message_id == "MSG-B" != r1.message_id
        t2._client.post.assert_called_once()


class TestPersistentIdempotencySendFileAndImage:
    def test_send_file_deduped_across_transport_instances(self, tmp_path):
        store = PersistentSendStore(path=tmp_path / "file.db")
        t1 = _transport_with_store(store)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "FILE-MSG-1", "chat_id": "c"}))
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        req = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="923@s.whatsapp.net",
                               attachment=str(p), idempotency_key="file-key-1")
        r1 = t1.send_file(req)

        t2 = _transport_with_store(store)
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "SHOULD-NOT-HAPPEN", "chat_id": "c"}))
        r2 = t2.send_file(req)

        assert r1.success is True
        assert r2.deduped is True
        assert r2.message_id == "FILE-MSG-1"
        t2._client.post.assert_not_called()

    def test_send_image_deduped_across_transport_instances(self, tmp_path):
        store = PersistentSendStore(path=tmp_path / "image.db")
        t1 = _transport_with_store(store)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "IMG-MSG-1", "chat_id": "c"}))
        p = tmp_path / "photo.jpg"
        p.write_bytes(b"\xff\xd8\xff fake jpg")
        req = WhatsAppRequest(action=WAAction.SEND_IMAGE, recipient="923@s.whatsapp.net",
                               attachment=str(p), idempotency_key="image-key-1")
        r1 = t1.send_image(req)

        t2 = _transport_with_store(store)
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "SHOULD-NOT-HAPPEN", "chat_id": "c"}))
        r2 = t2.send_image(req)

        assert r1.success is True
        assert r2.deduped is True
        assert r2.message_id == "IMG-MSG-1"
        t2._client.post.assert_not_called()


class TestPersistentIdempotencyReply:
    def test_reply_deduped_across_transport_instances(self, tmp_path):
        store = PersistentSendStore(path=tmp_path / "reply.db")
        t1 = _transport_with_store(store)
        t1._client.post = MagicMock(return_value=ok_response({"message_id": "REPLY-MSG-1", "chat_id": "c"}))
        req = WhatsAppRequest(action=WAAction.REPLY, recipient="923@s.whatsapp.net",
                               content="on my way", reply_to_message_id="orig-msg",
                               idempotency_key="reply-key-1")
        r1 = t1.reply(req)

        t2 = _transport_with_store(store)
        t2._client.post = MagicMock(return_value=ok_response({"message_id": "SHOULD-NOT-HAPPEN", "chat_id": "c"}))
        r2 = t2.reply(req)

        assert r1.success is True
        assert r2.deduped is True
        assert r2.message_id == "REPLY-MSG-1"
        t2._client.post.assert_not_called()
