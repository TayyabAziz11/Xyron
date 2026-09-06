"""
file_send.py — plan + execute a WhatsApp file/image send from the user's
local filesystem.

The module separates *planning* (resolve + validate, never sends) from
*execution* (builds a WhatsAppRequest and dispatches through the transport).
A human or the approval layer MUST approve the plan before execute() is
called with approved=True.

Usage
-----
    planner = FileSendPlanner(transport)

    # Resolve and plan — never sends.
    plan = planner.plan(
        file_ref={"kind": "latest", "type": "image", "location": "desktop"},
        contact_ref="176016366547081@lid",
    )

    # Show plan to user for approval:
    print(plan.file_path, plan.mime_type, plan.size_bytes,
          plan.selection_reason, plan.chat_id, plan.contact_name)

    # Only then:
    result = planner.execute(plan, approved=True, caption="Here's the image!")

File-reference kinds
--------------------
  {"kind": "exact_path", "path": "C:/Users/.../photo.jpg"}
  {"kind": "filename", "name": "photo.jpg"}
  {"kind": "latest", "type": "image"|"screenshot"|"document"|"pdf"|"video"|"any",
   "location": "desktop"|"downloads"|"documents"|"pictures"|"all"}
  {"kind": "context", "query": "the screenshot I just took"}

This module never raises into the caller. Every plan/execute outcome is a
structured dataclass with an explicit status field.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .contact_resolver import ContactResolution, ContactResolver
from .send_security import SendPathVerdict, detect_media_kind, validate_sendable_path
from .models import WAAction, WAErrorCode, WhatsAppRequest, WhatsAppResult

if TYPE_CHECKING:
    from .wa_context import WhatsAppContext

logger = logging.getLogger("wa_file_send")

# ---------------------------------------------------------------------------
# Constants — Windows user folders
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic", ".avif"}
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
_DOC_EXTS = {
    ".pdf", ".doc", ".docx", ".odt", ".txt", ".rtf",
    ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
}
_SCREENSHOT_HINTS = {"screenshot", "screen shot", "capture", "snip"}


def _user_profile() -> Path:
    """Windows %USERPROFILE% — always available."""
    return Path(os.environ.get("USERPROFILE", Path.home()))


def _known_folders() -> Dict[str, List[Path]]:
    """
    Map a location key to the list of directories to scan for that location.
    Includes OneDrive mirrors when they exist.
    """
    profile = _user_profile()
    onedrive = profile / "OneDrive"
    out: Dict[str, List[Path]] = {}

    desktop_dirs = [profile / "Desktop"]
    if (onedrive / "Desktop").is_dir():
        desktop_dirs.append(onedrive / "Desktop")
    out["desktop"] = desktop_dirs

    downloads_dirs = [profile / "Downloads"]
    if (onedrive / "Downloads").is_dir():
        downloads_dirs.append(onedrive / "Downloads")
    out["downloads"] = downloads_dirs

    documents_dirs = [profile / "Documents"]
    if (onedrive / "Documents").is_dir():
        documents_dirs.append(onedrive / "Documents")
    out["documents"] = documents_dirs

    pictures_dirs = [profile / "Pictures"]
    if (onedrive / "Pictures").is_dir():
        pictures_dirs.append(onedrive / "Pictures")
    out["pictures"] = pictures_dirs

    # Screenshots: explicit sub-folders where Windows Snipping Tool / Snip & Sketch save
    screenshots_dirs = [
        profile / "Pictures" / "Screenshots",
        profile / "Videos" / "Captures",
    ]
    if (onedrive / "Pictures" / "Screenshots").is_dir():
        screenshots_dirs.append(onedrive / "Pictures" / "Screenshots")
    out["screenshots"] = [d for d in screenshots_dirs if d.is_dir()]

    out["videos"] = [profile / "Videos"]

    out["all"] = []
    for key in ("desktop", "downloads", "documents", "pictures", "videos"):
        out["all"].extend(out[key])
    return out


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass
class FileCandidate:
    path: str
    filename: str
    mime_type: Optional[str]
    size_bytes: int
    mtime: float
    location: str  # "desktop" | "downloads" | etc.


@dataclass
class FileSendPlan:
    status: str = "error"  # ready | needs_clarification | blocked | not_found | error
    file_path: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    media_kind: Optional[str] = None     # "image" | "document"
    send_method: Optional[str] = None    # "send_image" | "send_file"
    selection_reason: Optional[str] = None
    chat_id: Optional[str] = None
    contact_name: Optional[str] = None
    contact_resolution: Optional[ContactResolution] = None
    path_verdict: Optional[SendPathVerdict] = None
    candidates: List[FileCandidate] = field(default_factory=list)
    detail: Optional[str] = None

    # Logical-action identity: a NEW plan is a NEW user command. Retries of
    # this plan (transport timeout, re-execute) share the action_id and
    # dedupe, while a fresh command ("send it again") builds a new plan with
    # a new action_id and intentionally sends again. Never hardcoded by the
    # caller — assigned in plan().
    action_id: Optional[str] = None

    # Set only after successful execute():
    result: Optional[WhatsAppResult] = None


# ---------------------------------------------------------------------------
# Scanner helpers
# ---------------------------------------------------------------------------

def _scan_dir_for_latest(
    directory: Path,
    ext_filter: Optional[set],
    name_hint: Optional[set],
    limit: int = 10,
) -> List[FileCandidate]:
    """
    Shallow-scan one directory (no recursion). Return FileCandidate list sorted
    by mtime descending (newest first). Never raises.
    """
    out: List[FileCandidate] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    p = Path(entry.path)
                    ext = p.suffix.lower()
                    if ext_filter and ext not in ext_filter:
                        continue
                    name_l = p.name.lower()
                    if name_hint and not any(h in name_l for h in name_hint):
                        continue
                    stat = entry.stat()
                    out.append(FileCandidate(
                        path=str(p), filename=p.name,
                        mime_type=_mime_of(p), size_bytes=stat.st_size,
                        mtime=stat.st_mtime,
                        location=directory.name,
                    ))
                except OSError:
                    continue
    except OSError as e:
        logger.debug("[FILE_SEND] scandir failed for %s: %s", directory, e)
    out.sort(key=lambda c: -c.mtime)
    return out[:limit]


def _mime_of(p: Path) -> Optional[str]:
    try:
        import mimetypes
        mime, _ = mimetypes.guess_type(str(p))
        return mime
    except Exception:
        return None


def _ext_filter_for(type_key: str) -> Optional[set]:
    if type_key == "image" or type_key == "screenshot":
        return _IMAGE_EXTS
    if type_key == "document":
        return _DOC_EXTS
    if type_key == "pdf":
        return {".pdf"}
    if type_key == "video":
        return _VIDEO_EXTS
    return None


def _name_hint_for(type_key: str) -> Optional[set]:
    if type_key == "screenshot":
        return _SCREENSHOT_HINTS
    return None


def _location_dirs(loc: str) -> List[Path]:
    """
    Translate a location key to its folder list. 'screenshots' maps to the
    dedicated screenshot directories; 'all' expands to every known folder.
    """
    folders = _known_folders()
    if loc == "screenshot" or loc == "screenshots":
        return folders.get("screenshots", [])
    return folders.get(loc, folders.get("all", []))


# ---------------------------------------------------------------------------
# FileSendPlanner
# ---------------------------------------------------------------------------

class FileSendPlanner:
    """
    Build a send plan (resolve contact + file + security check) without
    actually sending. The caller must approve the plan before calling
    execute(plan, approved=True).
    """

    def __init__(self, transport, context: Optional["WhatsAppContext"] = None) -> None:
        self._transport = transport
        self._contact_resolver = ContactResolver(transport)
        # Optional conversational context: when provided, plan() records the
        # resolved file as REFERENCED and execute() records SENT/FAILED
        # outcomes. Reference ≠ sent — only confirmed transport success
        # updates the authoritative "last sent artifact" tier.
        self._context = context

    # ── planning ────────────────────────────────────────────────────────────

    def plan(
        self,
        file_ref: Dict[str, Any],
        contact_ref: str,
    ) -> FileSendPlan:
        """
        Resolve contact + file + security. Returns a FileSendPlan.
        Never sends, never raises.

        file_ref   : one of {"kind": "exact_path", "path": ...},
                            {"kind": "filename", "name": ...},
                            {"kind": "latest", "type": ..., "location": ...},
                            {"kind": "context", "query": ...}
        contact_ref: JID, phone, or name string
        """
        # Step 1 — contact
        contact = self._contact_resolver.resolve(contact_ref)
        if contact.status == "ambiguous":
            return FileSendPlan(
                status="needs_clarification",
                contact_resolution=contact,
                detail=contact.detail,
            )
        if contact.status == "not_found":
            return FileSendPlan(
                status="not_found",
                contact_resolution=contact,
                detail=contact.detail,
            )
        if contact.status == "invalid":
            return FileSendPlan(
                status="error",
                detail=contact.detail,
            )

        # Step 2 — file. Each plan is a NEW logical action: its action_id is
        # the idempotency boundary (see execute()).
        action_id = uuid.uuid4().hex
        kind = (file_ref.get("kind") or "").lower().strip()
        dispatch = {
            "exact_path": self._plan_exact_path,
            "filename": self._plan_filename,
            "latest": self._plan_latest,
            "context": self._plan_context,
        }
        handler = dispatch.get(kind)
        if not handler:
            return FileSendPlan(
                status="error",
                contact_resolution=contact,
                detail=f"unknown file_ref kind '{kind}'",
            )

        plan = handler(file_ref)
        plan.action_id = action_id

        # Attach contact info
        plan.chat_id = contact.chat_id
        plan.contact_name = contact.display_name
        plan.contact_resolution = contact

        # Step 3 — security check (only if a path was resolved and the file was found)
        if plan.file_path and plan.status not in ("not_found", "needs_clarification", "error"):
            verdict = validate_sendable_path(plan.file_path)
            plan.path_verdict = verdict
            if not verdict.ok:
                plan.status = "blocked"
                plan.detail = verdict.detail
            else:
                # Refresh mime/size from verdict (authoritative)
                plan.mime_type = verdict.mime_type
                plan.size_bytes = verdict.size_bytes
                plan.media_kind = verdict.media_kind
                plan.send_method = "send_image" if verdict.media_kind == "image" else "send_file"
                plan.status = "ready"

        # Context: the user REFERENCED this file (the send decision is still
        # pending — approval, transport). Referenced ≠ sent; only confirmed
        # transport success updates the "sent" tier (see execute()).
        if self._context is not None and plan.file_path and plan.status in ("ready", "blocked"):
            try:
                self._context.record_referenced_artifact(
                    path=plan.file_path,
                    filename=plan.filename,
                    mime_type=plan.mime_type,
                    media_kind=plan.media_kind,
                    size_bytes=plan.size_bytes,
                    source=kind or "file_ref",
                )
            except Exception as e:
                logger.debug("[FILE_SEND] context reference recording failed: %s", e)

        return plan

    def execute(
        self,
        plan: FileSendPlan,
        approved: bool = False,
        caption: Optional[str] = None,
    ) -> FileSendPlan:
        """
        Send the file from a resolved plan. Must be called with approved=True
        after the caller has shown the plan to the user and received explicit
        approval. Returns the plan with plan.result populated. Never raises.
        """
        if not approved:
            plan.detail = "execution not approved — refusing to send"
            logger.warning("[FILE_SEND] execute called without approval=True — refusing")
            return plan
        if plan.status != "ready":
            plan.detail = f"cannot execute a plan with status '{plan.status}'"
            return plan
        if not plan.chat_id:
            plan.detail = "no chat_id resolved"
            return plan
        if not plan.file_path:
            plan.detail = "no file_path resolved"
            return plan

        # The file may have been deleted between plan and approval — refuse
        # before touching the transport (missing artifact detected pre-send).
        if not Path(plan.file_path).is_file():
            plan.status = "not_found"
            plan.detail = f"file no longer exists: {plan.file_path}"
            logger.warning("[FILE_SEND] refusing to send missing file: %s", plan.file_path)
            return plan

        # Idempotency key names the LOGICAL action (action_id), not just the
        # content. Retries of the same plan (timeout, re-execute) share the
        # key and dedupe; a NEW user command builds a new plan with a new
        # action_id, so an explicit "send it again" intentionally creates
        # another WhatsApp message instead of being suppressed as a retry.
        try:
            p = Path(plan.file_path)
            stat = p.stat()
            if plan.action_id:
                idem_key = (
                    f"send:{plan.action_id}:{plan.chat_id}:"
                    f"{plan.file_path}:{int(stat.st_mtime)}:{stat.st_size}"
                )
            else:
                # Legacy plan without action_id — keep the content-only key.
                idem_key = f"send:{plan.chat_id}:{plan.file_path}:{int(stat.st_mtime)}:{stat.st_size}"
        except OSError:
            idem_key = None

        action = WAAction.SEND_IMAGE if plan.send_method == "send_image" else WAAction.SEND_FILE
        request = WhatsAppRequest(
            action=action,
            recipient=plan.chat_id,
            attachment=plan.file_path,
            content=caption,
            idempotency_key=idem_key,
        )

        try:
            if action == WAAction.SEND_IMAGE:
                result = self._transport.send_image(request)
            else:
                result = self._transport.send_file(request)
        except Exception as e:
            logger.error("[FILE_SEND] transport raised: %s", e)
            plan.result = WhatsAppResult(
                success=False,
                error_code=None,
                error_message=f"transport exception: {e}",
            )
            return plan

        plan.result = result
        if result.success:
            plan.detail = (
                f"sent via {plan.send_method} → {plan.contact_name or plan.chat_id}"
            )
            logger.info(
                "[FILE_SEND] sent %s to %s (message_id=%s, deduped=%s)",
                plan.filename, plan.chat_id, result.message_id, result.deduped,
            )
            if self._context is not None:
                try:
                    self._context.record_sent_artifact(
                        path=plan.file_path,
                        chat_id=plan.chat_id,
                        message_id=result.message_id,
                        filename=plan.filename,
                        mime_type=plan.mime_type,
                        media_kind=plan.media_kind,
                        size_bytes=plan.size_bytes,
                    )
                except Exception as e:
                    logger.debug("[FILE_SEND] context sent recording failed: %s", e)
        else:
            plan.detail = f"send failed: {result.error_message or result.error_code}"
            logger.warning(
                "[FILE_SEND] failed %s to %s: %s %s",
                plan.filename, plan.chat_id, result.error_code, result.error_message,
            )
            # Definite failures become audit-only "failed" records — they
            # never replace the authoritative last-sent artifact. Ambiguous
            # timeouts are NOT recorded as failed (the send may have landed).
            if self._context is not None and result.error_code != WAErrorCode.SIDECAR_TIMEOUT:
                try:
                    self._context.record_failed_send(
                        path=plan.file_path,
                        chat_id=plan.chat_id,
                        filename=plan.filename,
                        mime_type=plan.mime_type,
                        media_kind=plan.media_kind,
                        error_code=result.error_code.value if result.error_code else None,
                    )
                except Exception as e:
                    logger.debug("[FILE_SEND] context failure recording failed: %s", e)
        return plan

    # ── plan dispatch handlers ──────────────────────────────────────────────

    def _plan_exact_path(self, file_ref: Dict[str, Any]) -> FileSendPlan:
        path_str = (file_ref.get("path") or "").strip()
        if not path_str:
            return FileSendPlan(status="error", detail="exact_path requires 'path'")
        p = Path(path_str)
        if not p.is_file():
            return FileSendPlan(
                status="not_found",
                detail=f"file does not exist: {path_str}",
                file_path=path_str,
            )
        return FileSendPlan(
            status="needs_security",  # set to "ready" by caller after security check
            file_path=str(p.resolve()),
            filename=p.name,
            mime_type=_mime_of(p),
            size_bytes=p.stat().st_size if p.exists() else None,
            media_kind=detect_media_kind(p),
            selection_reason="explicitly provided path",
        )

    def _plan_filename(self, file_ref: Dict[str, Any]) -> FileSendPlan:
        name = (file_ref.get("name") or "").strip()
        if not name:
            return FileSendPlan(status="error", detail="filename requires 'name'")

        # 1. fs_index exact-name search (if populated)
        matches: List[FileCandidate] = []
        try:
            from api.services.fs_index import fs_index
            results = fs_index.search(name, type_filter="file", limit=10)
            for r in results:
                if r.is_file() and r.name.lower() == name.lower():
                    matches.append(FileCandidate(
                        path=str(r), filename=r.name, mime_type=_mime_of(r),
                        size_bytes=r.stat().st_size, mtime=r.stat().st_mtime,
                        location="indexed",
                    ))
        except Exception as e:
            logger.debug("[FILE_SEND] fs_index search failed: %s", e)

        # 2. Direct scan of known folders (depth-1 + one level deeper)
        if not matches:
            folders = _known_folders()["all"]
            for folder in folders:
                matches.extend(_scan_dir_for_latest(folder, ext_filter=None, name_hint=None, limit=5))
            matches = [m for m in matches if m.filename.lower() == name.lower()]

        if len(matches) == 1:
            c = matches[0]
            return FileSendPlan(
                status="needs_security",
                file_path=c.path, filename=c.filename,
                mime_type=c.mime_type, size_bytes=c.size_bytes,
                selection_reason=f"exact filename match in {c.location}",
                candidates=matches,
            )
        if len(matches) > 1:
            return FileSendPlan(
                status="needs_clarification",
                detail=f"{len(matches)} files named '{name}' found — which one?",
                candidates=matches,
            )
        return FileSendPlan(
            status="not_found",
            detail=f"no file named '{name}' found on the computer",
        )

    def _plan_latest(self, file_ref: Dict[str, Any]) -> FileSendPlan:
        type_key = (file_ref.get("type") or "any").lower().strip()
        location = (file_ref.get("location") or "all").lower().strip()
        ext_filter = _ext_filter_for(type_key)
        name_hint = _name_hint_for(type_key)

        # Determine directories to scan
        if location in ("desktop", "downloads", "documents", "pictures", "videos"):
            dirs = _location_dirs(location)
        elif location in ("screenshot", "screenshots"):
            dirs = _location_dirs("screenshots")
        else:
            dirs = _known_folders().get("all", [])

        # For "screenshot", also scan the dedicated screenshot sub-directories
        if type_key == "screenshot":
            dirs = _location_dirs("screenshots")
            if not dirs:
                # Fallback: scan Desktop + Pictures with name hint
                dirs = _location_dirs("desktop") + _location_dirs("pictures")

        all_cands: List[FileCandidate] = []
        for d in dirs:
            all_cands.extend(_scan_dir_for_latest(d, ext_filter, name_hint, limit=10))

        if not all_cands:
            return FileSendPlan(
                status="not_found",
                detail=f"no {type_key} files found in {location}",
            )

        all_cands.sort(key=lambda c: -c.mtime)
        top = all_cands[0]
        from datetime import datetime
        mtime_str = datetime.fromtimestamp(top.mtime).strftime("%Y-%m-%d %H:%M:%S")
        return FileSendPlan(
            status="needs_security",
            file_path=top.path, filename=top.filename,
            mime_type=top.mime_type, size_bytes=top.size_bytes,
            media_kind=detect_media_kind(Path(top.path)),
            selection_reason=f"newest {type_key} in {top.location} (modified {mtime_str})",
            candidates=all_cands[:5],
        )

    def _plan_context(self, file_ref: Dict[str, Any]) -> FileSendPlan:
        query = (file_ref.get("query") or "").strip()
        if not query:
            return FileSendPlan(status="error", detail="context requires 'query'")

        # Infer open_type from query
        q_l = query.lower()
        open_type = "any"
        if any(w in q_l for w in ("image", "photo", "picture", "screenshot")):
            open_type = "image"
        elif any(w in q_l for w in ("video", "clip")):
            open_type = "video"
        elif any(w in q_l for w in ("document", "pdf", "file", "report")):
            open_type = "file"
        elif any(w in q_l for w in ("folder",)):
            open_type = "folder"

        try:
            from api.services.file_resolver import resolve as fs_resolve, ResolveResult
            result: ResolveResult = fs_resolve(query, open_type=open_type)
        except Exception as e:
            logger.warning("[FILE_SEND] file_resolver.resolve() failed: %s", e)
            return FileSendPlan(
                status="error",
                detail=f"file resolver error: {e}",
            )

        if result.decision == "open" and result.path and result.path.is_file():
            tier_name = result.tier_name or "unknown"
            return FileSendPlan(
                status="needs_security",
                file_path=str(result.path.resolve()),
                filename=result.path.name,
                mime_type=_mime_of(result.path),
                size_bytes=result.path.stat().st_size if result.path.exists() else None,
                media_kind=detect_media_kind(result.path),
                selection_reason=f"file_resolver tier '{tier_name}' "
                                 f"(confidence {result.confidence:.2f})",
            )

        if result.decision in ("confirm", "choices"):
            cands = [
                FileCandidate(
                    path=str(c.path), filename=c.path.name,
                    mime_type=_mime_of(c.path),
                    size_bytes=c.path.stat().st_size if c.path.is_file() else 0,
                    mtime=c.path.stat().st_mtime if c.path.is_file() else 0,
                    location="context",
                )
                for c in result.candidates
                if c.path.is_file()
            ]
            return FileSendPlan(
                status="needs_clarification",
                detail=f"file_resolver returned decision='{result.decision}' — "
                       f"{len(cands)} candidates need your choice",
                candidates=cands,
            )

        return FileSendPlan(
            status="not_found",
            detail=f"file_resolver found nothing for '{query}' "
                   f"(decision={result.decision})",
        )
