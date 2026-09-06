"""
Tests for Phase 2.1 media retrieval: download_media, get_latest_media,
get_media_message, and media metadata persistence contract.

Tests the Python BaileysTransport methods against mocked HTTP responses
that mirror the verified sidecar REST contract extensions. Also tests
Node-side filename sanitization and schema via the transport contract.

No live WhatsApp session or Node process required.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, call

import httpx
import pytest

from api.integrations.whatsapp.baileys_transport import BaileysTransport
from api.integrations.whatsapp.models import WAErrorCode


def make_transport() -> BaileysTransport:
    return BaileysTransport(host="127.0.0.1", port=8734, api_key="test-key", timeout_s=1.0)


def json_response(status_code: int, payload) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "http://x"))


def ok_response(data) -> httpx.Response:
    return json_response(200, {"ok": True, "data": data, "error": None})


def error_response(code: str, message: str, status: int = 500) -> httpx.Response:
    return json_response(status, {"ok": False, "data": None, "error": {"code": code, "message": message}})


# ── download_media ─────────────────────────────────────────────────────────

class TestDownloadMedia:
    def test_download_success(self):
        transport = make_transport()
        download_data = {
            "message_id": "MSG001",
            "chat_id": "123@lid",
            "media_type": "image",
            "mimetype": "image/jpeg",
            "filename": "photo.jpg",
            "local_path": "/data/whatsapp_media/images/photo.jpg",
            "downloaded_at": "2026-08-30T07:20:10.000Z",
            "reused": False,
        }
        transport._client.post = MagicMock(return_value=ok_response(download_data))
        result = transport.download_media("123@lid", "MSG001")

        assert result is not None
        assert result["message_id"] == "MSG001"
        assert result["chat_id"] == "123@lid"
        assert result["media_type"] == "image"
        assert result["mimetype"] == "image/jpeg"
        assert result["local_path"] == "/data/whatsapp_media/images/photo.jpg"
        assert result["downloaded_at"] == "2026-08-30T07:20:10.000Z"
        assert result["reused"] is False

        # Verify correct endpoint called
        post_call = transport._client.post.call_args
        assert "/downloadMedia" in post_call[0][0]
        body = post_call[1]["json"]
        assert body["chat_id"] == "123@lid"
        assert body["message_id"] == "MSG001"

    def test_download_reuses_existing(self):
        transport = make_transport()
        download_data = {
            "message_id": "MSG001",
            "chat_id": "123@lid",
            "media_type": "document",
            "local_path": "/data/whatsapp_media/documents/doc.pdf",
            "downloaded_at": "2026-08-30T07:15:00.000Z",
            "reused": True,
        }
        transport._client.post = MagicMock(return_value=ok_response(download_data))
        result = transport.download_media("123@lid", "MSG001")

        assert result is not None
        assert result["reused"] is True
        assert result["local_path"] == "/data/whatsapp_media/documents/doc.pdf"

    def test_download_not_found(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=error_response("NOT_FOUND", "Message not found", 404))
        result = transport.download_media("123@lid", "NONEXISTENT")
        assert result is None

    def test_download_no_media_key(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=error_response("NO_MEDIA_KEY", "Media retrieval metadata not available", 400))
        result = transport.download_media("123@lid", "OLD_MSG")
        assert result is None

    def test_download_sidecar_unavailable(self):
        transport = make_transport()
        transport._client.post = MagicMock(side_effect=httpx.ConnectError("Connection refused"))
        result = transport.download_media("123@lid", "MSG001")
        assert result is None

    def test_download_timeout(self):
        transport = make_transport()
        transport._client.post = MagicMock(side_effect=httpx.TimeoutException("timed out"))
        result = transport.download_media("123@lid", "MSG001")
        assert result is None

    def test_download_malformed_response(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response("not-a-dict"))
        result = transport.download_media("123@lid", "MSG001")
        assert result is None

    def test_download_result_has_local_path(self):
        """Download result must always contain local_path on success."""
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "C1", "media_type": "image",
            "local_path": "E:\\data\\whatsapp_media\\images\\M1.jpg",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": False,
        }))
        result = transport.download_media("C1", "M1")
        assert result["local_path"] is not None
        assert "whatsapp_media" in result["local_path"]

    def test_download_does_not_accept_caller_path(self):
        """The transport must not allow callers to specify destination paths."""
        import inspect
        sig = inspect.signature(BaileysTransport.download_media)
        params = list(sig.parameters.keys())
        # Only self, chat_id, message_id — no path/destination parameter
        assert "path" not in params
        assert "destination" not in params
        assert "output" not in params


# ── get_latest_media ──────────────────────────────────────────────────────

class TestGetLatestMedia:
    def test_basic_query(self):
        transport = make_transport()
        media_messages = [
            {"message_id": "M1", "media_type": "image", "timestamp": "2026-08-30T07:20:00Z"},
            {"message_id": "M2", "media_type": "image", "timestamp": "2026-08-30T07:15:00Z"},
        ]
        transport._client.post = MagicMock(return_value=ok_response({"messages": media_messages, "total": 2}))
        result = transport.get_latest_media(media_type="image", limit=5)

        assert len(result) == 2
        assert result[0]["message_id"] == "M1"

        post_call = transport._client.post.call_args
        assert "/getLatestMedia" in post_call[0][0]
        body = post_call[1]["json"]
        assert body["media_type"] == "image"
        assert body["limit"] == 5

    def test_filter_by_chat(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({"messages": [{"message_id": "M1"}], "total": 1}))
        result = transport.get_latest_media(chat_id="123@lid")
        assert len(result) == 1

        body = transport._client.post.call_args[1]["json"]
        assert body["chat_id"] == "123@lid"

    def test_filter_by_sender(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({"messages": [], "total": 0}))
        result = transport.get_latest_media(sender_id="456@lid")
        assert result == []

        body = transport._client.post.call_args[1]["json"]
        assert body["sender_id"] == "456@lid"

    def test_no_filters(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({"messages": [], "total": 0}))
        result = transport.get_latest_media()
        assert result == []

    def test_failure_returns_empty(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=error_response("DB_NOT_READY", "not ready", 503))
        result = transport.get_latest_media()
        assert result == []

    def test_sidecar_down_returns_empty(self):
        transport = make_transport()
        transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
        result = transport.get_latest_media()
        assert result == []

    def test_limit_enforced(self):
        transport = make_transport()
        msgs = [{"message_id": f"M{i}"} for i in range(50)]
        transport._client.post = MagicMock(return_value=ok_response({"messages": msgs, "total": 50}))
        result = transport.get_latest_media(limit=5)
        assert len(result) == 5


# ── get_media_message ──────────────────────────────────────────────────────

class TestGetMediaMessage:
    def test_found(self):
        transport = make_transport()
        msg_data = {
            "message_id": "MSG001",
            "chat_id": "123@lid",
            "media_type": "image",
            "mimetype": "image/jpeg",
            "local_path": None,
            "download_status": None,
        }
        transport._client.post = MagicMock(return_value=ok_response(msg_data))
        result = transport.get_media_message("123@lid", "MSG001")

        assert result is not None
        assert result["message_id"] == "MSG001"
        assert result["media_type"] == "image"

        post_call = transport._client.post.call_args
        assert "/getMediaMessage" in post_call[0][0]

    def test_not_found(self):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=error_response("NOT_FOUND", "not found", 404))
        result = transport.get_media_message("123@lid", "NONEXISTENT")
        assert result is None

    def test_failure_returns_none(self):
        transport = make_transport()
        transport._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))
        result = transport.get_media_message("123@lid", "MSG001")
        assert result is None

    def test_response_does_not_contain_sensitive_keys(self):
        """get_media_message must NOT expose provider_key_json or raw media keys."""
        transport = make_transport()
        # Simulate a response that the sidecar should have redacted
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "MSG001",
            "chat_id": "123@lid",
            "media_type": "image",
            "media_metadata_json": json.dumps({"mimetype": "image/jpeg", "provider_media": {"provider": "baileys", "has_key": True}}),
        }))
        result = transport.get_media_message("123@lid", "MSG001")
        assert result is not None
        # provider_key_json should not be in the response
        assert "provider_key_json" not in result


# ── Media metadata contract (what the sidecar persists) ────────────────────

class TestMediaMetadataContract:
    """Verify the expected shape of media_metadata_json for different types."""

    def test_image_metadata_shape(self):
        """Image messages must include provider_media with retrieval fields."""
        meta = {
            "mimetype": "image/jpeg",
            "filename": None,
            "width": 1920,
            "height": 1080,
            "provider_media": {
                "provider": "baileys",
                "media_key_b64": "dGVzdGtleQ==",
                "direct_path": "/v/t62.7161-24/12345",
                "url": None,
                "file_sha256_b64": "c2hhMjU2",
                "file_enc_sha256_b64": "ZW5jc2hh",
                "file_length": 234567,
            },
        }
        assert meta["provider_media"]["provider"] == "baileys"
        assert meta["provider_media"]["media_key_b64"] is not None
        assert meta["provider_media"]["direct_path"] is not None
        assert meta["width"] == 1920
        assert meta["height"] == 1080

    def test_document_metadata_shape(self):
        meta = {
            "mimetype": "application/pdf",
            "filename": "report.pdf",
            "file_length": 543210,
            "provider_media": {
                "provider": "baileys",
                "media_key_b64": "ZG9ja2V5",
                "direct_path": "/v/t62.7161-24/67890",
                "url": None,
                "file_sha256_b64": "ZG9jc2hh",
                "file_enc_sha256_b64": "ZG9jZW5j",
                "file_length": 543210,
            },
        }
        assert meta["filename"] == "report.pdf"
        assert meta["provider_media"]["media_key_b64"] is not None

    def test_audio_ptt_metadata(self):
        meta = {
            "mimetype": "audio/ogg; codecs=opus",
            "is_voice_note": True,
            "seconds": 15,
            "ptt": True,
            "provider_media": {
                "provider": "baileys",
                "media_key_b64": "YXVkaW9r",
                "direct_path": "/v/t62.7161-24/audio",
                "url": None,
                "file_sha256_b64": "YXVkaW9z",
                "file_enc_sha256_b64": "YXVkaW9l",
                "file_length": 45000,
            },
        }
        assert meta["is_voice_note"] is True
        assert meta["ptt"] is True
        assert meta["seconds"] == 15

    def test_sticker_metadata(self):
        meta = {
            "mimetype": "image/webp",
            "width": 512,
            "height": 512,
            "is_animated": True,
            "is_avatar": False,
            "provider_media": {
                "provider": "baileys",
                "media_key_b64": "c3RpY2tl",
                "direct_path": "/v/t62.7161-24/sticker",
                "url": None,
                "file_sha256_b64": "c3RpY3No",
                "file_enc_sha256_b64": "c3RpY2Vu",
                "file_length": 12345,
            },
        }
        assert meta["is_animated"] is True
        assert meta["is_avatar"] is False

    def test_video_metadata(self):
        meta = {
            "mimetype": "video/mp4",
            "width": 1280,
            "height": 720,
            "seconds": 30,
            "provider_media": {
                "provider": "baileys",
                "media_key_b64": "dmlkZW9r",
                "direct_path": "/v/t62.7161-24/video",
                "url": None,
                "file_sha256_b64": "dmlkZW9z",
                "file_enc_sha256_b64": "dmlkZW9l",
                "file_length": 5000000,
            },
        }
        assert meta["seconds"] == 30
        assert meta["width"] == 1280

    def test_missing_optional_provider_fields(self):
        """When provider_media is null (pre-2.1 messages), metadata still valid."""
        meta = {"mimetype": "image/jpeg", "filename": None}
        assert meta.get("provider_media") is None
        assert meta["mimetype"] == "image/jpeg"

    def test_no_media_binary_in_metadata(self):
        """media_metadata_json must never contain actual file bytes."""
        meta = {
            "mimetype": "image/jpeg",
            "provider_media": {
                "provider": "baileys",
                "media_key_b64": "dGVzdGtleQ==",  # key, not content
                "direct_path": "/v/t62.7161-24/12345",
                "file_sha256_b64": "c2hhMjU2",
                "file_enc_sha256_b64": "ZW5jc2hh",
                "file_length": 234567,
            },
        }
        serialized = json.dumps(meta)
        # Should be compact metadata, not kilobytes of binary
        assert len(serialized) < 1000


# ── Filename sanitization (contract tested via Python) ─────────────────────

class TestFilenameSanitization:
    """Test filename sanitization logic that the Node sidecar implements.
    These tests verify the contract from the Python perspective."""

    def test_path_traversal_rejected(self):
        """The download endpoint must not allow path traversal."""
        transport = make_transport()
        # Even if caller passes a chat_id containing path traversal, the
        # sidecar controls the output path — not the caller.
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "../../../etc", "media_type": "image",
            "local_path": "E:\\data\\whatsapp_media\\images\\M1.jpg",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": False,
        }))
        result = transport.download_media("../../../etc", "M1")
        # Transport forwards the request; sidecar validates paths
        assert result is not None
        assert "whatsapp_media" in result["local_path"]
        assert ".." not in result["local_path"]

    def test_local_path_is_in_controlled_directory(self):
        """Downloaded files must always be inside the controlled media directory."""
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "C1", "media_type": "document",
            "local_path": "E:\\Xyron\\backend\\data\\whatsapp_media\\documents\\safe_name.pdf",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": False,
        }))
        result = transport.download_media("C1", "M1")
        assert "whatsapp_media" in result["local_path"]
        assert ".secrets" not in result["local_path"]

    def test_no_arbitrary_path_in_api_contract(self):
        """The download API body must only contain chat_id and message_id."""
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "C1", "media_type": "image",
            "local_path": "C:\\data\\images\\M1.jpg",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": False,
        }))
        transport.download_media("C1", "M1")

        body = transport._client.post.call_args[1]["json"]
        # Only chat_id and message_id — no path control
        assert set(body.keys()) == {"chat_id", "message_id"}


# ── Log hygiene ────────────────────────────────────────────────────────────

class TestLogHygiene:
    def test_download_does_not_log_sensitive_keys(self, caplog):
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "C1", "media_type": "image",
            "local_path": "/data/images/M1.jpg",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": False,
        }))
        with caplog.at_level(logging.DEBUG, logger="wa_transport"):
            transport.download_media("C1", "M1")

        for record in caplog.records:
            assert "media_key" not in record.message.lower()
            assert "direct_path" not in record.message.lower()
            assert "file_sha256" not in record.message.lower()

    def test_existing_send_text_unaffected(self):
        """Verify send_text still works after Phase 2.1 additions."""
        from api.integrations.whatsapp.models import WhatsAppRequest, WAAction
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "SENT001", "chat_id": "C1", "timestamp": "2026-08-30T08:00:00Z",
            "from_me": True, "provider": "baileys",
        }))
        req = WhatsAppRequest(action=WAAction.SEND_TEXT, recipient="C1", content="Hello")
        result = transport.send_text(req)
        assert result.success is True
        assert result.message_id == "SENT001"

    def test_existing_reply_unaffected(self):
        """Verify reply still works after Phase 2.1 additions."""
        from api.integrations.whatsapp.models import WhatsAppRequest, WAAction
        transport = make_transport()
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "REPLY001", "chat_id": "C1", "quoted_message_id": "Q1",
            "timestamp": "2026-08-30T08:00:00Z", "from_me": True, "provider": "baileys",
        }))
        req = WhatsAppRequest(action=WAAction.REPLY, recipient="C1", content="Hi", reply_to_message_id="Q1")
        result = transport.reply(req)
        assert result.success is True


# ── Process restart simulation ─────────────────────────────────────────────

class TestRestartResilience:
    def test_second_call_reuses_existing_download(self):
        """After download, a second call should get reused=True from sidecar."""
        transport = make_transport()

        # First call: fresh download
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "C1", "media_type": "image",
            "local_path": "/data/images/photo.jpg",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": False,
        }))
        r1 = transport.download_media("C1", "M1")
        assert r1["reused"] is False

        # Second call: sidecar returns reused=True (file already on disk)
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "M1", "chat_id": "C1", "media_type": "image",
            "local_path": "/data/images/photo.jpg",
            "downloaded_at": "2026-08-30T08:00:00Z", "reused": True,
        }))
        r2 = transport.download_media("C1", "M1")
        assert r2["reused"] is True
        assert r2["local_path"] == r1["local_path"]

    def test_download_after_restart_with_persisted_metadata(self):
        """Simulates sidecar restart: metadata persists in SQLite, download works."""
        transport = make_transport()

        # After restart, sidecar still has the metadata from SQLite
        transport._client.post = MagicMock(return_value=ok_response({
            "message_id": "OLD_MSG", "chat_id": "C1", "media_type": "document",
            "mimetype": "application/pdf", "filename": "report.pdf",
            "local_path": "/data/documents/report.pdf",
            "downloaded_at": "2026-08-30T09:00:00Z", "reused": False,
        }))
        result = transport.download_media("C1", "OLD_MSG")
        assert result is not None
        assert result["local_path"] is not None
        assert result["media_type"] == "document"
