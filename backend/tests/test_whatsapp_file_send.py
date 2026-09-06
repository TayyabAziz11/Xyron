"""
test_whatsapp_file_send.py — comprehensive tests for Phase 3 WhatsApp
file-send modules:
  - contact_resolver.py (contact resolution + disambiguation)
  - send_security.py (outbound-file security policy)
  - file_send.py (plan/execute orchestration)

All tests are hermetic — they use tmp_path for real files and mocks for
the transport layer. No sidecar or network required.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(root: Path, name: str, content: bytes = b"test", mtime_offset: float = 0.0) -> Path:
    """Create a file under root and optionally set mtime to now - offset."""
    p = root / name
    p.write_bytes(content)
    if mtime_offset:
        t = time.time() - mtime_offset
        os.utime(p, (t, t))
    return p


@pytest.fixture
def safe_tmp(tmp_path_factory):
    """
    Temporary directory outside AppData — pytest's default tmp_path sits under
    AppData/Local/Temp which is blocked by send_security.py's segment policy.
    We use a dedicated directory inside the repo so tests get real paths that
    pass security validation.
    """
    import shutil
    base = Path(r"E:\Xyron\backend\data\_test_temp")
    base.mkdir(exist_ok=True)
    # Clean old runs older than 1 hour
    now = time.time()
    for child in list(base.iterdir()):
        if child.is_dir() and now - child.stat().st_mtime > 3600:
            shutil.rmtree(child, ignore_errors=True)
    d = base / f"pytest_{os.getpid()}_{int(now)}"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_image(root: Path, name: str, mtime_offset: float = 0.0) -> Path:
    """Minimal 1x1 white PNG (67 bytes)."""
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return _make_file(root, name, png, mtime_offset)


def _mock_transport(contacts: list | None = None) -> MagicMock:
    t = MagicMock()
    t.find_contact = MagicMock(return_value=contacts or [])
    t.verify_on_whatsapp = MagicMock(return_value=None)  # verification unavailable by default
    t.send_image = MagicMock()
    t.send_file = MagicMock()
    return t


# ===========================================================================
# 1. contact_resolver.py
# ===========================================================================

class TestContactResolverExactJID:
    def test_personal_jid_s_whatsapp_net(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        r = ContactResolver(t).resolve("1234567890@s.whatsapp.net")
        assert r.status == "resolved"
        assert r.chat_id == "1234567890@s.whatsapp.net"
        assert r.matched_by == "exact_jid"
        t.find_contact.assert_not_called()

    def test_lid_jid(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        r = ContactResolver(t).resolve("176016366547081@lid")
        assert r.status == "resolved"
        assert r.chat_id == "176016366547081@lid"
        assert r.matched_by == "exact_jid"

    def test_group_jid(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        r = ContactResolver(t).resolve("120363123456@g.us")
        assert r.status == "resolved"
        assert r.chat_id == "120363123456@g.us"

    def test_newsletter_jid(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        r = ContactResolver(t).resolve("1234567890@newsletter")
        assert r.status == "resolved"


class TestContactResolverPhone:
    def test_exact_phone_match_in_contacts(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali Hassan", "phone": "+213555123456",
             "push_name": "Ali", "chat_id": "213555123456@s.whatsapp.net"},
        ])
        r = ContactResolver(t).resolve("+213555123456")
        assert r.status == "resolved"
        assert r.matched_by == "phone"
        assert r.chat_id == "213555123456@s.whatsapp.net"
        assert r.display_name == "Ali Hassan"

    def test_exact_phone_match_falls_back_to_contact_id_when_chat_id_null(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "83202777579720@lid", "display_name": "Imran",
             "phone": "+213555123456", "push_name": "Imran", "chat_id": None},
        ])
        r = ContactResolver(t).resolve("+213555123456")
        assert r.status == "resolved"
        assert r.matched_by == "phone"
        assert r.chat_id == "83202777579720@lid"

    def test_phone_single_substring_match(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali", "phone": "", "push_name": "Ali",
             "chat_id": "111@lid"},
        ])
        r = ContactResolver(t).resolve("213555123456")
        assert r.status == "resolved"
        assert r.matched_by == "phone_constructed"
        assert r.chat_id == "111@lid"

    def test_phone_multiple_matches_ambiguous(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali", "phone": "123", "push_name": "A",
             "chat_id": "111@lid"},
            {"contact_id": "2", "display_name": "Bob", "phone": "123", "push_name": "B",
             "chat_id": "222@lid"},
        ])
        r = ContactResolver(t).resolve("1234567")
        assert r.status == "ambiguous"
        assert len(r.candidates) == 2

    def test_phone_no_match_constructs_jid(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([])
        # verification unavailable (None) → constructed-JID fallback
        r = ContactResolver(t).resolve("+213555999999")
        assert r.status == "resolved"
        assert r.chat_id == "213555999999@s.whatsapp.net"
        assert r.matched_by == "phone_constructed"

    def test_phone_verified_on_whatsapp_lid_identity(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([])
        # WhatsApp directory confirms the number; canonical identity is a LID
        t.verify_on_whatsapp = MagicMock(return_value={
            "exists": True, "jid": "123456789012345@lid", "phone": "923001234567",
        })
        r = ContactResolver(t).resolve("+923001234567")
        assert r.status == "resolved"
        assert r.chat_id == "123456789012345@lid"  # NOT the constructed @s.whatsapp.net
        assert r.matched_by == "on_whatsapp_verified"

    def test_phone_verified_on_whatsapp_standard_jid(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([])
        t.verify_on_whatsapp = MagicMock(return_value={
            "exists": True, "jid": "923001234567@s.whatsapp.net", "phone": "923001234567",
        })
        r = ContactResolver(t).resolve("+923001234567")
        assert r.status == "resolved"
        assert r.chat_id == "923001234567@s.whatsapp.net"
        assert r.matched_by == "on_whatsapp_verified"

    def test_phone_not_on_whatsapp_not_found(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([])
        # Number provably not registered — must NOT construct a JID
        t.verify_on_whatsapp = MagicMock(return_value={
            "exists": False, "jid": None, "phone": "923001234567",
        })
        r = ContactResolver(t).resolve("+923001234567")
        assert r.status == "not_found"
        assert r.chat_id is None
        assert "not registered" in r.detail

    def test_phone_verification_exception_falls_back(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([])
        t.verify_on_whatsapp = MagicMock(side_effect=RuntimeError("sidecar down"))
        r = ContactResolver(t).resolve("+213555999999")
        assert r.status == "resolved"
        assert r.matched_by == "phone_constructed"

    def test_phone_too_short(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        # "123" doesn't match phone regex (<7 chars) → falls to name → not found
        r = ContactResolver(t).resolve("123")
        assert r.status == "not_found"

    def test_phone_only_punctuation_invalid(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        # "-------" matches phone regex (7 chars of allowed chars) but extracts 0 digits
        r = ContactResolver(t).resolve("-------")
        assert r.status == "invalid"


class TestContactResolverName:
    def test_exact_full_display_name(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali Hassan", "phone": "+213...",
             "push_name": "Ali", "chat_id": "111@lid"},
        ])
        r = ContactResolver(t).resolve("Ali Hassan")
        assert r.status == "resolved"
        assert r.matched_by == "exact_name"
        assert r.chat_id == "111@lid"

    def test_unique_substring_match(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali Hassan", "phone": "",
             "push_name": "Ali", "chat_id": "111@lid"},
        ])
        r = ContactResolver(t).resolve("Ali")
        assert r.status == "resolved"
        assert r.matched_by == "unique_name_match"

    def test_multiple_name_matches_ambiguous(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali Hassan", "phone": "",
             "push_name": "Ali", "chat_id": "111@lid"},
            {"contact_id": "2", "display_name": "Ali Sara", "phone": "",
             "push_name": "Sara", "chat_id": "222@lid"},
        ])
        r = ContactResolver(t).resolve("Ali")
        assert r.status == "ambiguous"
        assert len(r.candidates) == 2

    def test_name_not_found(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([])
        r = ContactResolver(t).resolve("Nonexistent Person")
        assert r.status == "not_found"

    def test_empty_reference(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        r = ContactResolver(t).resolve("")
        assert r.status == "invalid"

    def test_whitespace_only_reference(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport()
        r = ContactResolver(t).resolve("   ")
        assert r.status == "invalid"

    def test_exact_name_falls_back_to_contact_id_when_chat_id_null(self):
        # LID-migration / history-synced contact: sidecar never populated
        # chat_id, only contact_id (an @lid). An exact name match must not
        # silently resolve to chat_id=None.
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "83202777579720@lid", "display_name": "Imran",
             "phone": None, "push_name": "Imran", "chat_id": None},
        ])
        r = ContactResolver(t).resolve("Imran")
        assert r.status == "resolved"
        assert r.matched_by == "exact_name"
        assert r.chat_id == "83202777579720@lid"

    def test_unique_substring_match_falls_back_to_contact_id(self):
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "555@lid", "display_name": "Imran Khan",
             "phone": "", "push_name": "Imran", "chat_id": None},
        ])
        r = ContactResolver(t).resolve("Imran")
        assert r.status == "resolved"
        assert r.matched_by == "unique_name_match"
        assert r.chat_id == "555@lid"

    def test_chat_id_takes_priority_over_contact_id_when_both_present(self):
        # Normal (non-LID-only) contacts must be unaffected by the fallback.
        from api.integrations.whatsapp.contact_resolver import ContactResolver
        t = _mock_transport([
            {"contact_id": "999@lid", "display_name": "Ali Hassan",
             "phone": "+213...", "push_name": "Ali",
             "chat_id": "111@s.whatsapp.net"},
        ])
        r = ContactResolver(t).resolve("Ali Hassan")
        assert r.status == "resolved"
        assert r.chat_id == "111@s.whatsapp.net"


# ===========================================================================
# 2. send_security.py
# ===========================================================================

class TestSendSecurityBlocks:
    def test_block_secrets_directory(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        secrets = safe_tmp / ".secrets"
        secrets.mkdir()
        f = secrets / "creds.json"
        f.write_text("{}")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "blocked_directory"

    def test_block_env_file(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / ".env"
        f.write_text("SECRET=abc")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "credential_like_filename"

    def test_block_pem_key(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "server.pem"
        f.write_text("-----BEGIN CERTIFICATE-----")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "key_material"

    def test_block_credential_in_name(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "my_credentials.txt"
        f.write_text("user: admin")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "credential_like_filename"

    def test_block_password_in_name(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "password_backup.txt"
        f.write_text("admin123")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert "password" in v.detail

    def test_block_node_modules(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        nm = safe_tmp / "node_modules"
        nm.mkdir()
        f = nm / "pkg.json"
        f.write_text("{}")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "blocked_directory"

    def test_block_id_rsa_key(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "id_rsa"
        f.write_text("ssh-rsa AAAA...")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "key_material"

    def test_block_token_in_name(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "access_token.json"
        f.write_text("{}")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "credential_like_filename"

    def test_block_hidden_git_dir(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        git = safe_tmp / ".git"
        git.mkdir()
        f = git / "config"
        f.write_text("[core]")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "blocked_directory"


class TestSendSecurityAllow:
    def test_allow_regular_image(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = _make_image(safe_tmp, "vacation.jpg")
        v = validate_sendable_path(str(f))
        assert v.ok
        assert v.media_kind == "image"
        assert v.size_bytes > 0

    def test_allow_regular_pdf(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "report.pdf"
        f.write_bytes(b"%PDF-1.4 content")
        v = validate_sendable_path(str(f))
        assert v.ok
        assert v.media_kind == "document"
        assert v.mime_type == "application/pdf"

    def test_allow_text_file(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "notes.txt"
        f.write_text("hello world")
        v = validate_sendable_path(str(f))
        assert v.ok
        assert v.media_kind == "document"


class TestSendSecurityEdgeCases:
    def test_file_not_found(self):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        v = validate_sendable_path("/nonexistent/path/file.txt")
        assert not v.ok
        assert v.reason == "file_not_found"

    def test_directory_not_file(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        v = validate_sendable_path(str(safe_tmp))
        assert not v.ok
        assert v.reason == "not_a_file"

    def test_empty_path(self):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        v = validate_sendable_path("")
        assert not v.ok
        assert v.reason == "invalid"

    def test_empty_file(self, safe_tmp):
        from api.integrations.whatsapp.send_security import validate_sendable_path
        f = safe_tmp / "empty.txt"
        f.write_bytes(b"")
        v = validate_sendable_path(str(f))
        assert not v.ok
        assert v.reason == "empty_file"

    def test_detect_media_kind_image(self, safe_tmp):
        from api.integrations.whatsapp.send_security import detect_media_kind
        f = safe_tmp / "photo.png"
        f.write_bytes(b"\x89PNG")
        assert detect_media_kind(f) == "image"

    def test_detect_media_kind_pdf(self, safe_tmp):
        from api.integrations.whatsapp.send_security import detect_media_kind
        f = safe_tmp / "doc.pdf"
        f.write_bytes(b"%PDF")
        assert detect_media_kind(f) == "document"


# ===========================================================================
# 3. file_send.py
# ===========================================================================

class TestFileSendPlanExactPath:
    def test_valid_image(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        f = _make_image(safe_tmp, "photo.jpg")
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        assert plan.send_method == "send_image"
        assert plan.media_kind == "image"
        assert plan.size_bytes > 0
        assert plan.selection_reason == "explicitly provided path"

    def test_valid_pdf(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        f = safe_tmp / "report.pdf"
        f.write_bytes(b"%PDF-1.4 content")
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        assert plan.send_method == "send_file"
        assert plan.media_kind == "document"

    def test_missing_file(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": str(safe_tmp / "nonexistent.txt")},
            "176016366547081@lid",
        )
        assert plan.status == "not_found"

    def test_blocked_secrets_path(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        secrets = safe_tmp / ".secrets"
        secrets.mkdir()
        f = secrets / "creds.json"
        f.write_text("{}")
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "blocked"
        assert "blocked" in plan.detail.lower()

    def test_empty_path(self):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": ""},
            "176016366547081@lid",
        )
        assert plan.status == "error"


class TestFileSendPlanLatest:
    def test_latest_image_on_desktop(self, safe_tmp, monkeypatch):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()

        # Create a mock Desktop directory inside safe_tmp
        desktop = safe_tmp / "Desktop"
        desktop.mkdir()
        _make_image(desktop, "old.jpg", mtime_offset=3600)
        newest = _make_image(desktop, "newest.jpg", mtime_offset=10)
        _make_image(desktop, "mid.jpg", mtime_offset=1800)

        # Monkeypatch _known_folders to return our tmp Desktop
        import api.integrations.whatsapp.file_send as fs_mod
        monkeypatch.setattr(fs_mod, "_known_folders", lambda: {"desktop": [desktop], "all": [desktop]})

        plan = FileSendPlanner(t).plan(
            {"kind": "latest", "type": "image", "location": "desktop"},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        assert plan.file_path == str(newest)
        assert "newest" in plan.selection_reason.lower()

    def test_latest_no_files(self, safe_tmp, monkeypatch):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        empty = safe_tmp / "empty_desktop"
        empty.mkdir()
        import api.integrations.whatsapp.file_send as fs_mod
        monkeypatch.setattr(fs_mod, "_known_folders", lambda: {"desktop": [empty], "all": [empty]})

        plan = FileSendPlanner(t).plan(
            {"kind": "latest", "type": "image", "location": "desktop"},
            "176016366547081@lid",
        )
        assert plan.status == "not_found"

    def test_latest_pdf_in_downloads(self, safe_tmp, monkeypatch):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        dl = safe_tmp / "Downloads"
        dl.mkdir()
        _make_file(dl, "old.pdf", b"%PDF", mtime_offset=3600)
        newest_pdf = _make_file(dl, "invoice.pdf", b"%PDF-1.4", mtime_offset=60)

        import api.integrations.whatsapp.file_send as fs_mod
        monkeypatch.setattr(fs_mod, "_known_folders", lambda: {"downloads": [dl], "all": [dl]})

        plan = FileSendPlanner(t).plan(
            {"kind": "latest", "type": "pdf", "location": "downloads"},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        assert plan.file_path == str(newest_pdf)
        assert plan.send_method == "send_file"


class TestFileSendPlanFilename:
    def test_exact_filename_match(self, safe_tmp, monkeypatch):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        desktop = safe_tmp / "Desktop"
        desktop.mkdir()
        target = _make_file(desktop, "report_Q3.pdf", b"%PDF-1.4")

        import api.integrations.whatsapp.file_send as fs_mod
        monkeypatch.setattr(fs_mod, "_known_folders", lambda: {
            "desktop": [desktop], "all": [desktop],
            "downloads": [], "documents": [], "pictures": [], "videos": [],
        })

        plan = FileSendPlanner(t).plan(
            {"kind": "filename", "name": "report_Q3.pdf"},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        assert plan.file_path == str(target)

    def test_filename_not_found(self, safe_tmp, monkeypatch):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        empty = safe_tmp / "empty"
        empty.mkdir()
        import api.integrations.whatsapp.file_send as fs_mod
        monkeypatch.setattr(fs_mod, "_known_folders", lambda: {
            "desktop": [empty], "all": [empty],
            "downloads": [], "documents": [], "pictures": [], "videos": [],
        })
        plan = FileSendPlanner(t).plan(
            {"kind": "filename", "name": "nonexistent.txt"},
            "176016366547081@lid",
        )
        assert plan.status == "not_found"


class TestFileSendPlanContext:
    def test_context_file_resolver_open(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.services.file_resolver import ResolveResult
        t = _mock_transport()
        f = _make_image(safe_tmp, "context_photo.jpg")

        fake_result = ResolveResult(
            decision="open", path=f, confidence=0.85,
            tier=3, tier_name="recent", candidates=[], breakdown={}, snapshot={},
        )

        with patch("api.services.file_resolver.resolve", return_value=fake_result):
            plan = FileSendPlanner(t).plan(
                {"kind": "context", "query": "the photo"},
                "176016366547081@lid",
            )
        assert plan.status == "ready"
        assert plan.file_path == str(f)

    def test_context_file_resolver_choices(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.services.file_resolver import ResolveResult, Candidate
        t = _mock_transport()
        f1 = _make_image(safe_tmp, "a.jpg")
        f2 = _make_image(safe_tmp, "b.jpg")

        fake_result = ResolveResult(
            decision="choices", path=None, confidence=0.3,
            tier=9, tier_name="filename_index",
            candidates=[Candidate(f1, 0.5, 9), Candidate(f2, 0.4, 9)],
            breakdown={}, snapshot={},
        )

        with patch("api.services.file_resolver.resolve", return_value=fake_result):
            plan = FileSendPlanner(t).plan(
                {"kind": "context", "query": "some image"},
                "176016366547081@lid",
            )
        assert plan.status == "needs_clarification"
        assert len(plan.candidates) == 2


class TestFileSendPlanContactAmbiguity:
    def test_ambiguous_contact_blocks_planning(self):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport([
            {"contact_id": "1", "display_name": "Ali", "phone": "", "push_name": "A", "chat_id": "111@lid"},
            {"contact_id": "2", "display_name": "Ali", "phone": "", "push_name": "B", "chat_id": "222@lid"},
        ])
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": "/some/file.jpg"},
            "Ali",
        )
        assert plan.status == "needs_clarification"
        assert plan.contact_resolution.status == "ambiguous"

    def test_not_found_contact_blocks_planning(self):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport([])
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": "/some/file.jpg"},
            "Nonexistent Person",
        )
        assert plan.status == "not_found"
        assert plan.contact_resolution.status == "not_found"


class TestFileSendExecute:
    def test_execute_without_approval(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        f = _make_image(safe_tmp, "photo.jpg")
        plan = FileSendPlanner(t).plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        # Execute without approval
        result_plan = FileSendPlanner(t).execute(plan, approved=False)
        t.send_image.assert_not_called()
        assert "not approved" in result_plan.detail

    def test_execute_approved_send_image(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WhatsAppResult
        t = _mock_transport()
        t.send_image.return_value = WhatsAppResult(
            success=True, message_id="msg_001", chat_id="176016366547081@lid",
        )
        f = _make_image(safe_tmp, "photo.jpg")
        planner = FileSendPlanner(t)
        plan = planner.plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        result_plan = planner.execute(plan, approved=True, caption="Hello!")
        t.send_image.assert_called_once()
        assert result_plan.result.success
        assert result_plan.result.message_id == "msg_001"

    def test_execute_approved_send_file(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WhatsAppResult
        t = _mock_transport()
        t.send_file.return_value = WhatsAppResult(
            success=True, message_id="msg_002", chat_id="176016366547081@lid",
        )
        f = safe_tmp / "report.pdf"
        f.write_bytes(b"%PDF-1.4 content")
        planner = FileSendPlanner(t)
        plan = planner.plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "ready"
        result_plan = planner.execute(plan, approved=True)
        t.send_file.assert_called_once()
        assert result_plan.result.success

    def test_execute_blocked_plan(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        secrets = safe_tmp / ".secrets"
        secrets.mkdir()
        f = secrets / "creds.json"
        f.write_text("{}")
        planner = FileSendPlanner(t)
        plan = planner.plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        assert plan.status == "blocked"
        result_plan = planner.execute(plan, approved=True)
        t.send_image.assert_not_called()
        t.send_file.assert_not_called()
        assert "blocked" in result_plan.detail.lower() or "cannot" in result_plan.detail.lower()

    def test_idempotency_key_content_based(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WhatsAppResult
        t = _mock_transport()
        t.send_image.return_value = WhatsAppResult(success=True, message_id="msg_001")
        f = _make_image(safe_tmp, "photo.jpg")
        planner = FileSendPlanner(t)
        plan = planner.plan(
            {"kind": "exact_path", "path": str(f)},
            "176016366547081@lid",
        )
        planner.execute(plan, approved=True)

        # The call should include an idempotency_key in the request
        call_args = t.send_image.call_args
        request = call_args[0][0]
        assert request.idempotency_key is not None
        assert "send:" in request.idempotency_key
        assert plan.chat_id in request.idempotency_key


class TestFileSendUnknownKind:
    def test_unknown_file_ref_kind(self):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        plan = FileSendPlanner(t).plan(
            {"kind": "unknown_kind", "data": "something"},
            "176016366547081@lid",
        )
        assert plan.status == "error"
        assert "unknown" in plan.detail.lower()
