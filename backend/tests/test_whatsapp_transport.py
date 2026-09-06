"""
Tests for api.integrations.whatsapp.openwa_transport.OpenWATransport.

The sidecar is never actually started here — every test mocks the transport's
internal httpx.Client so this suite runs with no live WhatsApp session and no
Node process. Live behavior (real QR pairing, real sends) is a separate
manual procedure — see the WhatsApp integration build notes / sidecar README.

The mocked response shapes here mirror the VERIFIED client.middleware()
contract from the installed @open-wa/wa-automate@4.76.0 package
(dist/api/Client.js): {"success": true, "response": <value>} or
{"success": false, "error": {...}} — not a guessed REST envelope.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import httpx
import pytest

from api.integrations.whatsapp.models import WAAction, WAErrorCode, WhatsAppRequest
from api.integrations.whatsapp.openwa_transport import OpenWATransport


def make_transport() -> OpenWATransport:
    return OpenWATransport(host="127.0.0.1", port=8734, api_key="test-key", timeout_s=1.0)


def json_response(status_code: int, payload) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "http://x"))


def rpc_success(response_value) -> httpx.Response:
    return json_response(200, {"success": True, "response": response_value})


def rpc_error(name: str, message: str) -> httpx.Response:
    return json_response(200, {"success": False, "error": {"name": name, "message": message, "data": None}})


# ── construction / config ──────────────────────────────────────────────────

def test_api_key_is_required():
    with pytest.raises(ValueError):
        OpenWATransport(host="127.0.0.1", port=8734, api_key="")


def test_from_settings_reads_config(monkeypatch):
    import api.config as config_module

    monkeypatch.setattr(config_module.settings, "wa_sidecar_host", "127.0.0.1")
    monkeypatch.setattr(config_module.settings, "wa_sidecar_port", 9999)
    monkeypatch.setattr(config_module.settings, "wa_sidecar_api_key", "from-settings-key")
    monkeypatch.setattr(config_module.settings, "wa_sidecar_timeout_s", 5.0)

    transport = OpenWATransport.from_settings()
    try:
        assert str(transport._client.base_url) == "http://127.0.0.1:9999"
    finally:
        transport.close()


def test_openwa_session_dir_is_separate_from_playwright_session_dir():
    from api.config import settings
    assert settings.whatsapp_openwa_session_dir != settings.secrets_dir / "whatsapp_session"
    assert settings.whatsapp_openwa_session_dir.name == "whatsapp_openwa_session"


# ── healthcheck (isConnected + getConnectionState) ──────────────────────────

def test_healthcheck_success():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=[
        rpc_success(True),               # isConnected
        rpc_success("CONNECTED"),        # getConnectionState
    ])
    result = transport.healthcheck()
    assert result["status"] == "connected"
    assert result["connected"] is True
    assert result["connection_state"] == "CONNECTED"


def test_healthcheck_sidecar_unavailable():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
    result = transport.healthcheck()
    assert result["status"] == "error"
    assert result["error_code"] == WAErrorCode.SIDECAR_UNAVAILABLE.value


def test_healthcheck_flags_disconnected_session():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=[
        rpc_success(False),              # isConnected -> not connected
        rpc_success("UNPAIRED"),         # getConnectionState
    ])
    result = transport.healthcheck()
    assert result["connected"] is False
    assert result["connection_state"] == "UNPAIRED"


# ── send_text ────────────────────────────────────────────────────────────────

def test_send_text_success():
    transport = make_transport()
    transport._client.post = MagicMock(
        return_value=rpc_success("true_923001234567@c.us_3EB0645E623D91006252"),
    )
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is True
    assert result.message_id == "true_923001234567@c.us_3EB0645E623D91006252"
    transport._client.post.assert_called_once()
    called_path = transport._client.post.call_args.args[0]
    called_body = transport._client.post.call_args.kwargs["json"]
    assert called_path == "/sendText"
    assert called_body == {"args": ["+923001234567", "hi"]}


def test_send_text_dispatch_error_reported_by_sidecar():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_error("SomeError", "not-connected"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.OPENWA_ERROR
    assert "not-connected" in result.error_message


def test_send_text_underlying_method_returns_false_without_throwing():
    """client.middleware() reports success:true even when the wrapped method
    (Promise<boolean>) resolves to false — this must still surface as a failed send."""
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success(False))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.OPENWA_ERROR


def test_send_text_timeout():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.TimeoutException("slow"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.SIDECAR_TIMEOUT


def test_send_malformed_response():
    transport = make_transport()
    bad_resp = httpx.Response(200, content=b"not json", request=httpx.Request("POST", "http://x"))
    transport._client.post = MagicMock(return_value=bad_resp)
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.MALFORMED_RESPONSE


def test_send_unauthenticated_sidecar():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        401, json={"success": False, "error": {"name": "UNAUTHORIZED", "message": "bad key"}},
        request=httpx.Request("POST", "http://x"),
    ))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.SESSION_NOT_AUTHENTICATED


def test_send_unknown_method_maps_to_invalid_request():
    """client.middleware() 404s with plain text for a wrong/unknown method name —
    catches a real regression if a method name here ever drifts from open-wa's API."""
    transport = make_transport()
    transport._client.post = MagicMock(return_value=httpx.Response(
        404, content=b"Cannot find method: sendText", request=httpx.Request("POST", "http://x"),
    ))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    result = transport.send_text(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.INVALID_REQUEST


# ── send_file / send_image ──────────────────────────────────────────────────

def test_send_file_missing_file_never_calls_network(tmp_path):
    transport = make_transport()
    transport._client.post = MagicMock()
    missing = tmp_path / "does-not-exist.pdf"

    request = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="+923001234567", attachment=str(missing))
    result = transport.send_file(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.FILE_NOT_FOUND
    transport._client.post.assert_not_called()


def test_send_file_success(tmp_path):
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success("true_x@c.us_msgfile1"))
    real_file = tmp_path / "doc.pdf"
    real_file.write_bytes(b"%PDF-1.4 fake content")

    request = WhatsAppRequest(action=WAAction.SEND_FILE, recipient="+923001234567",
                               attachment=str(real_file), content="here you go")
    result = transport.send_file(request)

    assert result.success is True
    assert result.message_id == "true_x@c.us_msgfile1"
    called_path = transport._client.post.call_args.args[0]
    body = transport._client.post.call_args.kwargs["json"]
    assert called_path == "/sendFile"
    to, file_data, filename, caption = body["args"]
    assert to == "+923001234567"
    assert file_data.startswith("data:")
    assert filename == "doc.pdf"
    assert caption == "here you go"


def test_send_image_uses_sendimage_method(tmp_path):
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success("true_x@c.us_msgimg1"))
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")

    request = WhatsAppRequest(action=WAAction.SEND_IMAGE, recipient="+923001234567", attachment=str(img))
    result = transport.send_image(request)

    assert result.success is True
    called_path = transport._client.post.call_args.args[0]
    assert called_path == "/sendImage"


# ── reply ────────────────────────────────────────────────────────────────────

def test_reply_requires_message_id():
    transport = make_transport()
    request = WhatsAppRequest(action=WAAction.REPLY, recipient="+923001234567", content="ok")
    result = transport.reply(request)

    assert result.success is False
    assert result.error_code == WAErrorCode.INVALID_REQUEST


def test_reply_calls_reply_method_with_quoted_id():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success("true_x@c.us_msgreply1"))

    request = WhatsAppRequest(action=WAAction.REPLY, recipient="+923001234567",
                               content="got it", reply_to_message_id="original-msg-id")
    result = transport.reply(request)

    assert result.success is True
    called_path = transport._client.post.call_args.args[0]
    body = transport._client.post.call_args.kwargs["json"]
    assert called_path == "/reply"
    assert body["args"] == ["+923001234567", "got it", "original-msg-id"]


# ── duplicate-send protection ────────────────────────────────────────────────

def test_duplicate_send_is_suppressed_on_ambiguous_timeout():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.TimeoutException("slow"))

    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567",
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
    transport._client.post = MagicMock(return_value=rpc_success("msg-once"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567",
                               content="hi", idempotency_key="fixed-key-2")
    first = transport.send_text(request)
    second = transport.send_text(request)

    assert first.success is True and second.success is True
    assert second.deduped is True
    assert second.message_id == first.message_id
    assert transport._client.post.call_count == 1


def test_definite_failure_allows_retry_with_same_key():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_error("SomeError", "temporary"))
    request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567",
                               content="hi", idempotency_key="fixed-key-3")
    first = transport.send_text(request)
    second = transport.send_text(request)

    assert first.success is False and second.success is False
    assert second.error_code == WAErrorCode.OPENWA_ERROR  # not DUPLICATE_SUPPRESSED
    assert transport._client.post.call_count == 2  # a definite failure is retryable


# ── "sidecar restart" — recovers once the sidecar is back ─────────────────

def test_transport_recovers_after_sidecar_comes_back():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=[
        httpx.ConnectError("refused"),
        rpc_success("msg-after-restart"),
    ])
    request_1 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="hi")
    down = transport.send_text(request_1)
    assert down.success is False and down.error_code == WAErrorCode.SIDECAR_UNAVAILABLE

    request_2 = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="+923001234567", content="still there?")
    up = transport.send_text(request_2)
    assert up.success is True
    assert up.message_id == "msg-after-restart"


# ── get_messages / find_contact / mark_read ─────────────────────────────────

def test_get_messages_returns_empty_list_on_failure():
    transport = make_transport()
    transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
    assert transport.get_messages() == []


def test_get_messages_unread_calls_getAllUnreadMessages():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success([{"id": "c1", "name": "Ali"}]))
    result = transport.get_messages(limit=5, unread_only=True)
    assert result == [{"id": "c1", "name": "Ali"}]
    assert transport._client.post.call_args.args[0] == "/getAllUnreadMessages"


def test_get_messages_all_calls_getAllChats():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success([{"id": "c1"}]))
    transport.get_messages(limit=5, unread_only=False)
    assert transport._client.post.call_args.args[0] == "/getAllChats"


def test_find_contact_filters_by_query():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success([
        {"id": "1@c.us", "name": "Ali Khan"},
        {"id": "2@c.us", "name": "Sara"},
    ]))
    result = transport.find_contact("ali")
    assert len(result) == 1
    assert result[0]["name"] == "Ali Khan"
    assert transport._client.post.call_args.args[0] == "/getAllContacts"


def test_mark_read_success_and_failure():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success(True))
    assert transport.mark_read("chat-1") is True
    assert transport._client.post.call_args.kwargs["json"] == {"args": ["chat-1"]}

    transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
    assert transport.mark_read("chat-1") is False


# ── subscribe_messages scaffold ──────────────────────────────────────────────

def test_subscribe_messages_invokes_callback_for_new_items():
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success([{"id": "new-msg-1", "name": "Ali"}]))
    received = []
    transport.subscribe_messages(received.append)
    try:
        import time
        for _ in range(50):
            if received:
                break
            time.sleep(0.02)
    finally:
        transport.stop_subscription()

    assert len(received) == 1
    assert received[0]["id"] == "new-msg-1"


# ── logging hygiene ──────────────────────────────────────────────────────────

def test_no_phone_number_or_message_body_in_logs(caplog):
    transport = make_transport()
    transport._client.post = MagicMock(return_value=rpc_success("msg-log-test"))
    secret_phone = "+923009998877"
    secret_body = "the secret passphrase is banana49"

    with caplog.at_level(logging.INFO, logger="wa_transport"):
        request = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient=secret_phone, content=secret_body)
        transport.send_text(request)

    log_text = "\n".join(r.message for r in caplog.records)
    assert secret_phone not in log_text
    assert secret_body not in log_text
    assert "WA_SEND_START" in log_text
    assert "WA_SEND_SUCCESS" in log_text
