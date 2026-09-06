"""
test_whatsapp_artifact_context.py — Phase 3 milestone: conversational
artifact/file context + contact context.

Covers:
  - wa_context.py artifact extensions (WAArtifact, ArtifactReference,
    is_contextual_artifact_reference, resolve_artifact_reference)
  - file_send.py logical action_id idempotency boundary + context hooks
    (referenced/sent/failed recording, missing-file refusal)

Hermetic: real files under safe_tmp, mocked transports, and — for the
idempotency-boundary tests — a REAL BaileysTransport instance whose
_send_via_rest is monkeypatched (no network, real SendDeduplicator).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

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
    d = base / f"art_{os.getpid()}_{int(now)}"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_pdf(root: Path, name: str = "report.pdf") -> Path:
    p = root / name
    p.write_bytes(b"%PDF-1.4\nXyron artifact-context test\n%%EOF\n")
    return p


def _make_image(root: Path, name: str = "photo.png") -> Path:
    p = root / name
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return p


def _mock_transport() -> MagicMock:
    t = MagicMock()
    t.find_contact = MagicMock(return_value=[])
    t.verify_on_whatsapp = MagicMock(return_value=None)
    return t


# ===========================================================================
# 1. is_contextual_artifact_reference
# ===========================================================================

class TestIsContextualArtifactReference:
    def test_explicit_phrases(self):
        from api.integrations.whatsapp.wa_context import is_contextual_artifact_reference
        for ref in ("that file", "that document", "that pdf", "that image",
                    "same file", "the same image", "the file I just sent",
                    "the PDF I just sent"):
            assert is_contextual_artifact_reference(ref) is True, ref

    def test_it_requires_file_semantics_action(self):
        from api.integrations.whatsapp.wa_context import is_contextual_artifact_reference
        # Bare "it" alone is NOT an artifact reference.
        assert is_contextual_artifact_reference("it") is False
        assert is_contextual_artifact_reference("it", action="greet") is False
        # With a file-semantics intent it is.
        assert is_contextual_artifact_reference("it", action="send") is True
        assert is_contextual_artifact_reference("it", action="open") is True
        assert is_contextual_artifact_reference("it", action="save") is True

    def test_it_inside_utterance_with_send_action(self):
        from api.integrations.whatsapp.wa_context import is_contextual_artifact_reference
        assert is_contextual_artifact_reference("send it to him again", action="send") is True
        assert is_contextual_artifact_reference("open it", action="open") is True

    def test_whole_word_it_not_substring(self):
        from api.integrations.whatsapp.wa_context import is_contextual_artifact_reference
        assert is_contextual_artifact_reference("Italy is nice", action="send") is False
        assert is_contextual_artifact_reference("an item", action="send") is False
        assert is_contextual_artifact_reference("with Italy", action="open") is False

    def test_plain_paths_and_names_not_contextual(self):
        from api.integrations.whatsapp.wa_context import is_contextual_artifact_reference
        assert is_contextual_artifact_reference("report.pdf") is False
        assert is_contextual_artifact_reference("C:/docs/report.pdf") is False
        assert is_contextual_artifact_reference("") is False

    def test_contact_phrases_are_not_artifact_refs(self):
        from api.integrations.whatsapp.wa_context import (
            is_contextual_artifact_reference, is_contextual_contact_reference,
        )
        assert is_contextual_artifact_reference("same contact") is False
        assert is_contextual_artifact_reference("him") is False
        # And the reverse: artifact phrases are not contact refs.
        assert is_contextual_contact_reference("same file") is False
        assert is_contextual_contact_reference("that pdf") is False
        # One utterance can legitimately carry BOTH references.
        assert is_contextual_contact_reference("send the same file to him") is True
        assert is_contextual_artifact_reference("send the same file to him") is True


class TestArtifactReferenceKind:
    def test_kinds(self):
        from api.integrations.whatsapp.wa_context import artifact_reference_kind
        assert artifact_reference_kind("that pdf") == "pdf"
        assert artifact_reference_kind("the PDF I just sent") == "pdf"
        assert artifact_reference_kind("the same image") == "image"
        assert artifact_reference_kind("that photo") == "image"
        assert artifact_reference_kind("that document") == "document"
        assert artifact_reference_kind("same file") == "file"
        assert artifact_reference_kind("it") is None
        assert artifact_reference_kind("send it to him again") is None


# ===========================================================================
# 2. Artifact recording
# ===========================================================================

class TestArtifactRecording:
    def test_last_referenced_artifact(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "ref.pdf")
        ctx.record_referenced_artifact(path=str(f), mime_type="application/pdf",
                                       media_kind="document", source="exact_path")
        ref = ctx.last_referenced_artifact()
        assert ref is not None and ref.status == "referenced"
        assert ref.filename == "ref.pdf"
        assert ctx.last_sent_artifact() is None  # reference is not a send

    def test_last_sent_artifact(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "sent.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="923@s.whatsapp.net",
                                 message_id="M1", mime_type="application/pdf",
                                 media_kind="document")
        sent = ctx.last_sent_artifact()
        assert sent is not None and sent.status == "sent"
        assert sent.message_id == "M1"
        assert sent.chat_id == "923@s.whatsapp.net"

    def test_failed_send_does_not_replace_sent_context(self, safe_tmp):
        """attempted A → FAILED must not shadow successful B."""
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        b = _make_pdf(safe_tmp, "b.pdf")
        a = _make_pdf(safe_tmp, "a.pdf")
        ctx.record_sent_artifact(path=str(b), chat_id="c", message_id="MB")
        ctx.record_failed_send(path=str(a), chat_id="c", error_code="whatsapp_disconnected")
        assert ctx.last_sent_artifact().path == str(b)
        assert ctx.last_artifact("failed").path == str(a)
        # "send it again" resolves the SUCCESSFUL artifact, not the failed one.
        r = ctx.resolve_artifact_reference("send it again", action="send")
        assert r.path == str(b)

    def test_wa_attachment_artifact_representable(self, safe_tmp):
        """Inbound attachments are representable for a future "open it"."""
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        ctx.record_attachment_artifact(
            chat_id="923@s.whatsapp.net", message_id="IN1",
            filename="incoming.pdf", mime_type="application/pdf",
            media_kind="document",
        )
        r = ctx.resolve_artifact_reference("it", action="open")
        assert r.matched_by == "context_carryover"
        assert r.kind == "wa_attachment"
        assert r.chat_id == "923@s.whatsapp.net"
        assert r.message_id == "IN1"
        assert r.path is None  # local path materializes only after download_media()


# ===========================================================================
# 3. resolve_artifact_reference
# ===========================================================================

class TestResolveArtifactReference:
    def _ctx(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        return WhatsAppContext(path=safe_tmp / "ctx.json")

    def test_same_file_resolves(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "same.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1",
                                 mime_type="application/pdf", media_kind="document")
        r = ctx.resolve_artifact_reference("same file", action="send")
        assert r.matched_by == "context_carryover"
        assert r.path == str(f)
        assert r.resolution_tier == "last_sent"

    def test_that_pdf_kind_match(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "kind.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1",
                                 mime_type="application/pdf", media_kind="document")
        assert ctx.resolve_artifact_reference("that pdf").path == str(f)

    def test_that_pdf_kind_mismatch_fails(self, safe_tmp):
        """Only an image was sent — "that PDF" must not resolve to it."""
        ctx = self._ctx(safe_tmp)
        img = _make_image(safe_tmp, "only.png")
        ctx.record_sent_artifact(path=str(img), chat_id="c", message_id="M1",
                                 mime_type="image/png", media_kind="image")
        r = ctx.resolve_artifact_reference("that pdf", action="send")
        assert r.path is None and r.chat_id is None
        assert "pdf" in r.detail

    def test_same_image_requires_image_kind(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "doc.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1",
                                 mime_type="application/pdf", media_kind="document")
        r = ctx.resolve_artifact_reference("the same image", action="send")
        assert r.path is None

    def test_file_i_just_sent_uses_sent_tier_only(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "onlyref.pdf")
        ctx.record_referenced_artifact(path=str(f), mime_type="application/pdf",
                                       media_kind="document")
        # Referenced but never sent → explicit sent phrase must fail.
        r = ctx.resolve_artifact_reference("the pdf I just sent", action="send")
        assert r.path is None
        assert "no" in r.detail
        # After a confirmed send it resolves from the sent tier.
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M2",
                                 mime_type="application/pdf", media_kind="document")
        r2 = ctx.resolve_artifact_reference("the pdf I just sent", action="send")
        assert r2.path == str(f)
        assert r2.resolution_tier == "last_sent"

    def test_contextual_it_resolves_for_send(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "it.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1",
                                 mime_type="application/pdf", media_kind="document")
        r = ctx.resolve_artifact_reference("it", action="send")
        assert r.path == str(f)
        # Whole utterance with embedded "it" also resolves.
        r2 = ctx.resolve_artifact_reference("send it to him again", action="send")
        assert r2.path == str(f)

    def test_it_without_file_semantics_not_contextual(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "x.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1")
        r = ctx.resolve_artifact_reference("it")
        assert r.matched_by == "not_contextual"
        assert r.path is None
        r2 = ctx.resolve_artifact_reference("it", action="greet")
        assert r2.matched_by == "not_contextual"

    def test_no_artifact_context_fails(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        r = ctx.resolve_artifact_reference("same file", action="send")
        assert r.path is None
        assert "no artifact context" in r.detail

    def test_expired_context_fails(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "old.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1",
                                 timestamp=1000.0)
        r = ctx.resolve_artifact_reference("it", action="send", max_age_s=3600.0)
        assert r.path is None
        assert "no recent" in r.detail or "artifact" in r.detail

    def test_missing_file_detected_at_resolution(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "gone.pdf")
        ctx.record_sent_artifact(path=str(f), chat_id="c", message_id="M1",
                                 mime_type="application/pdf", media_kind="document")
        f.unlink()
        r = ctx.resolve_artifact_reference("it", action="send")
        assert r.path is None
        assert "no longer exists" in r.detail

    def test_send_falls_back_to_referenced_tier(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "refonly.pdf")
        ctx.record_referenced_artifact(path=str(f), mime_type="application/pdf",
                                       media_kind="document")
        r = ctx.resolve_artifact_reference("it", action="send")
        assert r.path == str(f)
        assert r.resolution_tier == "last_referenced"

    def test_open_prefers_referenced_over_sent(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        pdf = _make_pdf(safe_tmp, "sent.pdf")
        img = _make_image(safe_tmp, "referenced.png")
        ctx.record_sent_artifact(path=str(pdf), chat_id="c", message_id="M1",
                                 mime_type="application/pdf", media_kind="document")
        ctx.record_referenced_artifact(path=str(img), mime_type="image/png",
                                       media_kind="image")
        r_open = ctx.resolve_artifact_reference("it", action="open")
        assert r_open.path == str(img)
        assert r_open.resolution_tier == "last_referenced"
        r_send = ctx.resolve_artifact_reference("it", action="send")
        assert r_send.path == str(pdf)
        assert r_send.resolution_tier == "last_sent"

    def test_him_and_it_resolve_independently(self, safe_tmp):
        ctx = self._ctx(safe_tmp)
        f = _make_pdf(safe_tmp, "independent.pdf")
        ctx.record_interaction("923001234567@s.whatsapp.net", display_name="Tayyab Aziz",
                               action="send_file", message_id="M9")
        ctx.record_sent_artifact(path=str(f), chat_id="923001234567@s.whatsapp.net",
                                 message_id="M9", mime_type="application/pdf",
                                 media_kind="document")
        cr = ctx.resolve_contact_reference("him")
        ar = ctx.resolve_artifact_reference("it", action="send")
        assert cr.chat_id == "923001234567@s.whatsapp.net"
        assert cr.matched_by == "context_carryover"
        assert ar.path == str(f)
        # Artifact activity never disturbs contact carryover.
        other = _make_image(safe_tmp, "other.png")
        ctx.record_referenced_artifact(path=str(other), mime_type="image/png",
                                       media_kind="image")
        cr2 = ctx.resolve_contact_reference("him")
        assert cr2.chat_id == "923001234567@s.whatsapp.net"
        # And contact activity never disturbs artifact carryover.
        ctx.record_interaction("999@s.whatsapp.net", display_name="Someone Else")
        ar2 = ctx.resolve_artifact_reference("it", action="send")
        assert ar2.path == str(f)


# ===========================================================================
# 4. Persistence
# ===========================================================================

class TestArtifactPersistence:
    def test_artifacts_survive_process_restart(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        p = safe_tmp / "ctx.json"
        f = _make_pdf(safe_tmp, "restart.pdf")
        ctx1 = WhatsAppContext(path=p)
        ctx1.record_sent_artifact(path=str(f), chat_id="c", message_id="MR",
                                  mime_type="application/pdf", media_kind="document")
        ctx2 = WhatsAppContext(path=p)  # fresh instance, same disk state
        sent = ctx2.last_sent_artifact()
        assert sent is not None and sent.path == str(f)
        assert sent.message_id == "MR"
        r = ctx2.resolve_artifact_reference("it", action="send")
        assert r.path == str(f)

    def test_v1_schema_loads_without_artifacts(self, safe_tmp):
        """Pre-artifact v1 files keep working — artifacts simply start empty."""
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        p = safe_tmp / "ctx.json"
        p.write_text(json.dumps({
            "version": 1,
            "updated": "2026-08-31T00:00:00+00:00",
            "interactions": [{
                "chat_id": "923001234567@s.whatsapp.net",
                "display_name": "Tayyab Aziz",
                "action": "send_image",
                "message_id": "OLD1",
                "file_path": None,
                "timestamp": 1788171426.0,
            }],
        }, indent=2), encoding="utf-8")
        ctx = WhatsAppContext(path=p)
        assert ctx.last_interaction() is not None
        assert ctx.last_interaction().display_name == "Tayyab Aziz"
        assert ctx.last_artifact() is None

    def test_bootstrap_artifacts_from_interactions(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        p = safe_tmp / "ctx.json"
        img = _make_image(safe_tmp, "boot.png")
        ctx = WhatsAppContext(path=p)
        ctx.record_interaction("923001234567@s.whatsapp.net", display_name="Tayyab Aziz",
                               action="send_image", message_id="BOOT1",
                               file_path=str(img), timestamp=time.time())
        assert ctx.bootstrap_artifacts_from_interactions() == 1
        sent = ctx.last_sent_artifact()
        assert sent is not None and sent.path == str(img)
        assert sent.media_kind == "image"
        assert sent.source == "interaction_bootstrap"
        assert sent.message_id == "BOOT1"
        # Idempotent: a second call must not duplicate records.
        assert ctx.bootstrap_artifacts_from_interactions() == 0
        # Survives restart.
        ctx2 = WhatsAppContext(path=p)
        assert ctx2.last_sent_artifact().path == str(img)


# ===========================================================================
# 5. FileSendPlanner context hooks
# ===========================================================================

class TestPlannerContextHooks:
    def test_plan_records_referenced_not_sent(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "planned.pdf")
        planner = FileSendPlanner(_mock_transport(), context=ctx)
        plan = planner.plan({"kind": "exact_path", "path": str(f)},
                            "176016366547081@lid")
        assert plan.status == "ready"
        ref = ctx.last_artifact()
        assert ref is not None and ref.status == "referenced"
        assert ref.path == str(Path(f).resolve())
        assert ref.source == "exact_path"
        assert ctx.last_sent_artifact() is None  # planning never marks sent

    def test_execute_success_records_sent(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WhatsAppResult
        t = _mock_transport()
        t.send_file.return_value = WhatsAppResult(
            success=True, message_id="MSG_S", chat_id="176016366547081@lid")
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "executed.pdf")
        planner = FileSendPlanner(t, context=ctx)
        plan = planner.plan({"kind": "exact_path", "path": str(f)},
                            "176016366547081@lid")
        planner.execute(plan, approved=True)
        sent = ctx.last_sent_artifact()
        assert sent is not None
        assert sent.path == str(Path(f).resolve())
        assert sent.message_id == "MSG_S"
        assert sent.chat_id == "176016366547081@lid"

    def test_execute_failure_records_failed_not_sent(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WAErrorCode, WhatsAppResult
        t = _mock_transport()
        t.send_file.return_value = WhatsAppResult(
            success=False, error_code=WAErrorCode.WHATSAPP_DISCONNECTED,
            error_message="down")
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "failed.pdf")
        planner = FileSendPlanner(t, context=ctx)
        plan = planner.plan({"kind": "exact_path", "path": str(f)},
                            "176016366547081@lid")
        planner.execute(plan, approved=True)
        assert ctx.last_sent_artifact() is None
        failed = ctx.last_artifact("failed")
        assert failed is not None and failed.path == str(Path(f).resolve())
        assert failed.error_code == "whatsapp_disconnected"

    def test_ambiguous_timeout_not_recorded_as_failed(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WAErrorCode, WhatsAppResult
        t = _mock_transport()
        t.send_file.return_value = WhatsAppResult(
            success=False, error_code=WAErrorCode.SIDECAR_TIMEOUT,
            error_message="timed out")
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "timeout.pdf")
        planner = FileSendPlanner(t, context=ctx)
        plan = planner.plan({"kind": "exact_path", "path": str(f)},
                            "176016366547081@lid")
        planner.execute(plan, approved=True)
        assert ctx.last_artifact("failed") is None
        assert ctx.last_sent_artifact() is None

    def test_declined_send_keeps_sent_context(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        from api.integrations.whatsapp.models import WhatsAppResult
        t = _mock_transport()
        t.send_file.return_value = WhatsAppResult(
            success=True, message_id="OK1", chat_id="176016366547081@lid")
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        b = _make_pdf(safe_tmp, "b_good.pdf")
        a = _make_pdf(safe_tmp, "a_declined.pdf")
        planner = FileSendPlanner(t, context=ctx)
        plan_b = planner.plan({"kind": "exact_path", "path": str(b)},
                              "176016366547081@lid")
        planner.execute(plan_b, approved=True)
        # Now plan A and DECLINE the send.
        plan_a = planner.plan({"kind": "exact_path", "path": str(a)},
                              "176016366547081@lid")
        planner.execute(plan_a, approved=False)
        assert ctx.last_sent_artifact().path == str(Path(b).resolve())
        t.send_file.assert_called_once()  # only B ever reached the transport

    def test_execute_missing_file_refuses(self, safe_tmp):
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        t = _mock_transport()
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "deleted.pdf")
        planner = FileSendPlanner(t, context=ctx)
        plan = planner.plan({"kind": "exact_path", "path": str(f)},
                            "176016366547081@lid")
        assert plan.status == "ready"
        f.unlink()
        out = planner.execute(plan, approved=True)
        assert out.status == "not_found"
        assert "no longer exists" in out.detail
        t.send_file.assert_not_called()
        assert ctx.last_sent_artifact() is None


# ===========================================================================
# 6. Logical action_id boundary (real SendDeduplicator, no network)
# ===========================================================================

class TestActionIdBoundary:
    @staticmethod
    def _transport(calls):
        """Real BaileysTransport + real dedup; _send_via_rest monkeypatched."""
        from api.integrations.whatsapp.baileys_transport import BaileysTransport
        from api.integrations.whatsapp.models import WhatsAppResult
        t = BaileysTransport(host="127.0.0.1", port=1, api_key="test-key")

        def fake_rest(endpoint, body):
            calls.append((endpoint, dict(body)))
            return WhatsAppResult(
                success=True, message_id=f"M{len(calls)}", chat_id=body["chat_id"],
            )

        t._send_via_rest = fake_rest
        return t

    def test_each_plan_is_a_new_logical_action(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        f = _make_pdf(safe_tmp, "doc.pdf")
        planner = FileSendPlanner(self._transport([]))
        p1 = planner.plan({"kind": "exact_path", "path": str(f)},
                          "176016366547081@lid")
        p2 = planner.plan({"kind": "exact_path", "path": str(f)},
                          "176016366547081@lid")
        assert p1.action_id and p2.action_id
        assert p1.action_id != p2.action_id

    def test_retry_of_same_action_remains_deduplicated(self, safe_tmp):
        from api.integrations.whatsapp.file_send import FileSendPlanner
        calls = []
        f = _make_pdf(safe_tmp, "retry.pdf")
        planner = FileSendPlanner(self._transport(calls))
        plan = planner.plan({"kind": "exact_path", "path": str(f)},
                            "176016366547081@lid")
        r1 = planner.execute(plan, approved=True)
        assert r1.result.success and r1.result.message_id == "M1"
        assert r1.result.deduped is False
        # Re-executing the SAME plan (same logical action) dedupes.
        r2 = planner.execute(plan, approved=True)
        assert r2.result.deduped is True
        assert r2.result.message_id == "M1"  # cached result, no new message
        assert len(calls) == 1                # exactly one underlying send

    def test_intentional_send_again_not_suppressed(self, safe_tmp):
        """A NEW user command ('send it again') must NOT be deduplicated."""
        from api.integrations.whatsapp.file_send import FileSendPlanner
        calls = []
        f = _make_pdf(safe_tmp, "again.pdf")
        planner = FileSendPlanner(self._transport(calls))
        plan1 = planner.plan({"kind": "exact_path", "path": str(f)},
                             "176016366547081@lid")
        planner.execute(plan1, approved=True)          # first send → M1
        plan2 = planner.plan({"kind": "exact_path", "path": str(f)},
                             "176016366547081@lid")    # NEW command → new action_id
        assert plan2.action_id != plan1.action_id
        r2 = planner.execute(plan2, approved=True)
        assert r2.result.success
        assert r2.result.deduped is False              # NOT suppressed
        assert r2.result.message_id == "M2"            # a real second message
        assert len(calls) == 2

    def test_repeat_command_full_flow_from_context(self, safe_tmp):
        """'Send it to him again': 'it' → last sent PDF, new plan, new send."""
        from api.integrations.whatsapp.wa_context import WhatsAppContext
        from api.integrations.whatsapp.file_send import FileSendPlanner
        calls = []
        ctx = WhatsAppContext(path=safe_tmp / "ctx.json")
        f = _make_pdf(safe_tmp, "flow.pdf")
        planner = FileSendPlanner(self._transport(calls), context=ctx)
        # Command 1: "Send this PDF to Tayyab." (explicit path)
        plan1 = planner.plan({"kind": "exact_path", "path": str(f)},
                             "176016366547081@lid")
        planner.execute(plan1, approved=True)
        # Command 2: "Send it to him again."
        ar = ctx.resolve_artifact_reference("send it to him again", action="send")
        assert ar.matched_by == "context_carryover"
        assert ar.resolution_tier == "last_sent"
        assert ar.path == str(Path(f).resolve())
        plan2 = planner.plan({"kind": "exact_path", "path": ar.path},
                             "176016366547081@lid")
        assert plan2.action_id != plan1.action_id
        r2 = planner.execute(plan2, approved=True)
        assert r2.result.success and r2.result.deduped is False
        assert r2.result.message_id != plan1.result.message_id  # a NEW message
        assert len(calls) == 2
        # The intentional repeat is now the authoritative last-sent artifact.
        assert ctx.last_sent_artifact().message_id == "M2"
