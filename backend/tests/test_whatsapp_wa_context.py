"""
test_whatsapp_wa_context.py — comprehensive tests for Phase 3 context
modules:
  - wa_context.py (conversational contact carryover)
  - screenshot_resolver.py (contextual screenshot resolution)

All tests are hermetic — tmp_path for real files, sqlite stubs for the
sidecar message store. No sidecar or network required.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def safe_tmp(tmp_path_factory):
    """
    Temp dir outside AppData — pytest's default tmp_path sits under
    AppData/Local/Temp which is blocked by send_security.py.
    """
    base = Path(r"E:\Xyron\backend\data\_test_temp")
    base.mkdir(exist_ok=True)
    now = time.time()
    for child in list(base.iterdir()):
        if child.is_dir() and now - child.stat().st_mtime > 3600:
            shutil.rmtree(child, ignore_errors=True)
    d = base / f"ctx_{os.getpid()}_{int(now)}"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_image(
    directory: Path, name: str, content: bytes = _PNG,
    mtime_offset: float = 0.0, size_pad: int = 0,
) -> Path:
    """Create an image file. mtime_offset is ADDED to now (positive = future)."""
    p = directory / name
    p.write_bytes(content + b"\x00" * size_pad)
    if mtime_offset:
        t = time.time() + mtime_offset
        os.utime(p, (t, t))
    return p


def _make_sidecar_db(
    tmp: Path,
    messages: List[tuple],
    contacts: Optional[List[tuple]] = None,
) -> Path:
    db_path = tmp / "test_whatsapp_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE whatsapp_messages (
            message_id TEXT, chat_id TEXT, message_type TEXT,
            timestamp TEXT, from_me INTEGER, media_type TEXT
        );
        CREATE TABLE whatsapp_contacts (
            contact_id TEXT, display_name TEXT, push_name TEXT, phone TEXT
        );
    """)
    for m in messages:
        conn.execute(
            "INSERT INTO whatsapp_messages "
            "(message_id, chat_id, message_type, timestamp, from_me) "
            "VALUES (?, ?, ?, ?, ?)",
            m,
        )
    for c in (contacts or []):
        conn.execute(
            "INSERT INTO whatsapp_contacts "
            "(contact_id, display_name, push_name, phone) "
            "VALUES (?, ?, ?, ?)",
            c,
        )
    conn.commit()
    conn.close()
    return db_path


# ===========================================================================
# 1. is_contextual_contact_reference
# ===========================================================================

class TestIsContextualContactReference:
    def test_same_contact(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("same contact") is True

    def test_same_person(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("the same person") is True

    def test_him(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("him") is True

    def test_her(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("her") is True

    def test_them(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("them") is True

    def test_send_to_him_too(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("send it to him too") is True

    def test_last_contact(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("last contact") is True

    def test_case_insensitive(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("SAME CONTACT") is True
        assert is_contextual_contact_reference("Him") is True

    def test_with_punctuation(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("the same contact.") is True
        assert is_contextual_contact_reference("send it to him!") is True

    def test_not_contextual_name(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("Tayyab Aziz") is False

    def test_not_contextual_phone(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("+923001234567") is False

    def test_not_contextual_empty(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("") is False
        assert is_contextual_contact_reference("   ") is False

    def test_not_contextual_shimmer(self):
        """'shimmer' contains 'him' as a substring but not as a whole word."""
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("shimmer") is False

    def test_not_contextual_herbal(self):
        from api.integrations.whatsapp.wa_context import is_contextual_contact_reference
        assert is_contextual_contact_reference("herbal tea") is False


# ===========================================================================
# 2. WhatsAppContext lifecycle
# ===========================================================================

class TestWhatsAppContextLifecycle:
    def test_empty_context(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        assert ctx.last_interaction() is None

    def test_record_and_last(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        inter = ctx.record_interaction(
            chat_id="123@s.whatsapp.net", display_name="Test",
            action="send_image", message_id="MSG1",
        )
        assert ctx.last_interaction() is inter
        assert ctx.last_interaction().chat_id == "123@s.whatsapp.net"

    def test_newest_first(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction("1@s.whatsapp.net", timestamp=1000.0)
        ctx.record_interaction("2@s.whatsapp.net", timestamp=2000.0)
        ctx.record_interaction("3@s.whatsapp.net", timestamp=3000.0)
        assert ctx.last_interaction().chat_id == "3@s.whatsapp.net"

    def test_persistence_roundtrip(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        p = safe_tmp / "ctx.json"
        ctx1 = WhatsAppContext(path=p)
        ctx1.record_interaction(
            "99@s.whatsapp.net", display_name="Persist",
            action="send_image", message_id="PM1", timestamp=1700000000.0,
        )
        # Load in a new instance
        ctx2 = WhatsAppContext(path=p)
        assert ctx2.last_interaction() is not None
        assert ctx2.last_interaction().chat_id == "99@s.whatsapp.net"
        assert ctx2.last_interaction().display_name == "Persist"
        assert ctx2.last_interaction().message_id == "PM1"

    def test_clear(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction("x@s.whatsapp.net")
        ctx.clear()
        assert ctx.last_interaction() is None
        # Re-load should also be empty.
        ctx2 = WhatsAppContext(path=safe_tmp / "ctx.json")
        assert ctx2.last_interaction() is None

    def test_cap_at_max(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        for i in range(60):
            ctx.record_interaction(f"{i}@s.whatsapp.net")
        # Capped at _MAX_INTERACTIONS (50).
        assert len(ctx._interactions) == 50


# ===========================================================================
# 3. resolve_contact_reference
# ===========================================================================

class TestResolveContactReference:
    def test_same_contact_resolves(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction(
            "923001234567@s.whatsapp.net", display_name="Tayyab Aziz",
            action="send_image", message_id="M1",
        )
        r = ctx.resolve_contact_reference("same contact")
        assert r.chat_id == "923001234567@s.whatsapp.net"
        assert r.display_name == "Tayyab Aziz"
        assert r.matched_by == "context_carryover"
        assert r.action == "send_image"
        assert r.message_id == "M1"

    def test_him_resolves(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction("jid@s.whatsapp.net", display_name="Ali")
        r = ctx.resolve_contact_reference("him")
        assert r.chat_id == "jid@s.whatsapp.net"
        assert r.matched_by == "context_carryover"

    def test_send_to_him_too_resolves(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction("jid@s.whatsapp.net", display_name="Ali")
        r = ctx.resolve_contact_reference("send it to him too")
        assert r.chat_id == "jid@s.whatsapp.net"
        assert r.matched_by == "context_carryover"

    def test_non_contextual_fallthrough(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction("jid@s.whatsapp.net")
        r = ctx.resolve_contact_reference("Tayyab Aziz")
        assert r.matched_by == "not_contextual"
        assert r.chat_id == ""

    def test_empty_ref_fallthrough(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        r = ctx.resolve_contact_reference("")
        assert r.matched_by == "not_contextual"

    def test_no_interaction_fails(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        r = ctx.resolve_contact_reference("same contact")
        assert r.chat_id == ""
        assert "no WhatsApp interaction" in r.detail

    def test_expired_context_fails(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_interaction("jid@s.whatsapp.net", timestamp=1000.0)
        r = ctx.resolve_contact_reference("him", max_age_s=1.0)
        assert r.chat_id == ""
        assert "expired" in r.detail


# ===========================================================================
# 4. seed_from_message_store
# ===========================================================================

class TestSeedFromMessageStore:
    def test_picks_newest_outgoing(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        db = _make_sidecar_db(safe_tmp, messages=[
            ("OLD1", "176016366547081@lid", "document", "2026-08-30T13:55:16Z", 1),
            ("NEW1", "923001234567@s.whatsapp.net", "image", "2026-08-31T10:17:06Z", 1),
        ])
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        inter = ctx.seed_from_message_store(str(db), display_name="Tayyab Aziz")
        assert inter is not None
        assert inter.chat_id == "923001234567@s.whatsapp.net"
        assert inter.display_name == "Tayyab Aziz"
        assert inter.action == "send_image"
        assert inter.message_id == "NEW1"

    def test_does_not_pick_older_lid_contact(self, safe_tmp):
        """The lid test contact must NEVER be chosen over a newer send."""
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        db = _make_sidecar_db(safe_tmp, messages=[
            ("OLD_LID", "176016366547081@lid", "document", "2026-08-30T13:55:16Z", 1),
            ("NEW_TAY", "923001234567@s.whatsapp.net", "image", "2026-08-31T10:17:06Z", 1),
        ])
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        inter = ctx.seed_from_message_store(str(db))
        assert inter.chat_id != "176016366547081@lid"

    def test_display_name_from_contacts_table(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        db = _make_sidecar_db(
            safe_tmp,
            messages=[("M1", "123@s.whatsapp.net", "text", "2026-08-31T12:00:00Z", 1)],
            contacts=[("123@s.whatsapp.net", "Sara Ahmed", "Sara", "+213555000001")],
        )
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        inter = ctx.seed_from_message_store(str(db), display_name="Fallback Name")
        assert inter.display_name == "Sara Ahmed"  # contacts table wins

    def test_display_name_hint_when_no_contacts(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        db = _make_sidecar_db(safe_tmp, messages=[
            ("M1", "999@s.whatsapp.net", "text", "2026-08-31T12:00:00Z", 1),
        ])
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        inter = ctx.seed_from_message_store(str(db), display_name="Hint Name")
        assert inter.display_name == "Hint Name"

    def test_no_outgoing_returns_none(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        db = _make_sidecar_db(safe_tmp, messages=[])
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        assert ctx.seed_from_message_store(str(db)) is None

    def test_invalid_db_returns_none(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        # A non-existent path creates an empty sqlite DB — SELECT on
        # whatsapp_messages fails (no such table) → returns None.
        bad = safe_tmp / "no_such.db"
        assert ctx.seed_from_message_store(str(bad)) is None

    def test_action_mapping(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        db = _make_sidecar_db(safe_tmp, messages=[
            ("M1", "1@s.whatsapp.net", "document", "2026-08-31T12:00:00Z", 1),
        ])
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        assert ctx.seed_from_message_store(str(db)).action == "send_file"


# ===========================================================================
# 5. ScreenshotResolver — single selection
# ===========================================================================

class TestScreenshotResolverSingle:
    def test_single_fresh_screenshot_selected(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 60.0  # injected: 60 s ahead
        shots = safe_tmp / "Screenshots"
        shots.mkdir()
        _make_image(shots, "Screenshot 2026-08-31 151918.png", mtime_offset=30.0)

        r = ScreenshotResolver(
            dirs=[shots], primary_dirs=[shots], now=now,
        ).resolve()
        assert r.status == "selected"
        assert r.selected is not None
        assert "Screenshot 2026-08-31 151918.png" in r.selected.path
        assert r.selected.in_screenshot_dir is True
        assert r.selected.name_is_screenshot_like is True
        assert r.selected.class_score == 1.0
        assert r.selected.confidence > 0.7
        assert r.selected.reason

    def test_name_pattern_on_desktop_selected(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 60.0
        desk = safe_tmp / "Desktop"
        desk.mkdir()
        _make_image(desk, "Screen Shot 2026-08.png", mtime_offset=30.0)

        r = ScreenshotResolver(
            dirs=[desk], primary_dirs=[], now=now,
        ).resolve()
        assert r.status == "selected"
        assert r.selected.classification == "name_pattern"
        assert r.selected.class_score == 0.70
        assert r.selected.in_screenshot_dir is False
        assert r.selected.name_is_screenshot_like is True


# ===========================================================================
# 6. ScreenshotResolver — ambiguity
# ===========================================================================

class TestScreenshotResolverAmbiguous:
    def test_two_close_screenshots_ambiguous(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 120.0
        shots = safe_tmp / "Screenshots"
        shots.mkdir()
        _make_image(shots, "Screenshot 2026-08-31 151918.png", mtime_offset=70.0)
        _make_image(
            shots, "Screenshot 2026-08-31 151918oo.png",
            mtime_offset=93.0, size_pad=64,
        )

        r = ScreenshotResolver(
            dirs=[shots], primary_dirs=[shots], now=now,
        ).resolve()
        assert r.status == "ambiguous"
        assert r.selected is None
        assert len(r.candidates) == 2
        for c in r.candidates:
            assert c.in_screenshot_dir
            assert c.reason

    def test_candidates_sorted_newest_first(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 120.0
        shots = safe_tmp / "Screenshots"
        shots.mkdir()
        _make_image(shots, "older.png", mtime_offset=70.0)
        _make_image(shots, "newer.png", mtime_offset=93.0, size_pad=32)

        r = ScreenshotResolver(
            dirs=[shots], primary_dirs=[shots], now=now,
        ).resolve()
        assert r.candidates[0].filename == "newer.png"


# ===========================================================================
# 7. ScreenshotResolver — priority rule
# ===========================================================================

class TestScreenshotResolverPriority:
    def test_screenshot_beats_newer_generic(self, safe_tmp):
        """A 70 s-old screenshot wins over a 10 s-old generic desktop image."""
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 120.0
        shots = safe_tmp / "Screenshots"
        desk = safe_tmp / "Desktop"
        shots.mkdir()
        desk.mkdir()
        _make_image(shots, "Screenshot 2026-08-31.png", mtime_offset=50.0)
        _make_image(desk, "wallpaper.png", mtime_offset=90.0)

        r = ScreenshotResolver(
            dirs=[shots, desk], primary_dirs=[shots], now=now,
        ).resolve()
        assert r.status == "selected"
        assert "Screenshot" in r.selected.filename
        assert r.selected.in_screenshot_dir is True


# ===========================================================================
# 8. ScreenshotResolver — staleness
# ===========================================================================

class TestScreenshotResolverStale:
    def test_old_screenshots_need_clarification(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 25 * 3600.0  # injected: 25 h ahead
        shots = safe_tmp / "Screenshots"
        shots.mkdir()
        # File ctime = actual now; injected now is 25 h ahead → age 25 h > 24 h.
        _make_image(shots, "Screenshot 2026-05-18.png")

        r = ScreenshotResolver(
            dirs=[shots], primary_dirs=[shots], now=now,
        ).resolve()
        assert r.status == "needs_clarification"
        assert r.selected is None
        assert "just took" in r.detail


# ===========================================================================
# 9. ScreenshotResolver — empty / no candidates
# ===========================================================================

class TestScreenshotResolverEmpty:
    def test_empty_dir_not_found(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        shots = safe_tmp / "Screenshots"
        shots.mkdir()
        r = ScreenshotResolver(
            dirs=[shots], primary_dirs=[shots], now=time.time(),
        ).resolve()
        assert r.status == "not_found"

    def test_non_image_ignored(self, safe_tmp):
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        shots = safe_tmp / "Screenshots"
        shots.mkdir()
        (shots / "notes.txt").write_text("not an image")
        r = ScreenshotResolver(
            dirs=[shots], primary_dirs=[shots], now=time.time(),
        ).resolve()
        assert r.status == "not_found"


# ===========================================================================
# 10. ScreenshotResolver — weak candidates only
# ===========================================================================

class TestScreenshotResolverWeak:
    def test_only_generic_images_needs_clarification(self, safe_tmp):
        """No screenshots anywhere, only a generic recent desktop image."""
        from api.integrations.whatsapp.screenshot_resolver import ScreenshotResolver
        now = time.time() + 60.0
        desk = safe_tmp / "Desktop"
        desk.mkdir()
        _make_image(desk, "random_wallpaper.jpg", mtime_offset=30.0)

        r = ScreenshotResolver(
            dirs=[desk], primary_dirs=[], now=now,
        ).resolve()
        assert r.status == "needs_clarification"
        assert r.selected is None
        assert r.candidates  # the generic image is listed
        assert "generic" in r.detail.lower() or "screenshot-like" in r.detail.lower()
