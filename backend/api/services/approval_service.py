"""
Approval Service — reads/writes approval files from the filesystem.

Approval files live in:
  backend/src/ai_operator/skills/gold/Pending_Approval/   ← pending items
  Approved/    ← approved items
  Rejected/    ← rejected items

File format: markdown with YAML front-matter.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..config import settings
from ..schemas.approval import ApprovalItem, ApprovalStatus


def _parse_frontmatter(content: str) -> Dict[str, str]:
    """Extract YAML front-matter key/value pairs from markdown."""
    meta: Dict[str, str] = {}
    if not content.startswith("---"):
        return meta
    parts = content.split("---", 2)
    if len(parts) < 3:
        return meta
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta


def _file_to_approval(path: Path, status: ApprovalStatus) -> Optional[ApprovalItem]:
    """Parse a markdown approval file into an ApprovalItem."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    meta = _parse_frontmatter(content)

    # Derive a stable ID from the file name
    file_id = hashlib.md5(path.name.encode()).hexdigest()[:16]

    # Extract title from first H1 heading
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    # Extract description from objective section or first paragraph after front-matter
    desc_match = re.search(r"\*\*Objective:\*\*\s*(.+)", content)
    if not desc_match:
        desc_match = re.search(r"## What Will Happen\n\n\*\*Objective:\*\*\s*(.+)", content)
    description = desc_match.group(1).strip() if desc_match else title

    created_at = meta.get("requested_at", "")
    if not created_at:
        try:
            stat = path.stat()
            created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat() + "Z"
        except OSError:
            created_at = datetime.now(timezone.utc).isoformat() + "Z"

    return ApprovalItem(
        id=file_id,
        title=title,
        description=description,
        action_type=meta.get("action_type", "plan_execution"),
        risk_level=meta.get("risk_level", "medium"),
        status=status,
        created_at=created_at,
        file_path=str(path),
        related_plan=meta.get("related_plan"),
        details={k: v for k, v in meta.items()},
    )


class ApprovalService:
    """File-system backed approval service."""

    def _ensure_dirs(self) -> None:
        settings.approved_dir.mkdir(parents=True, exist_ok=True)
        settings.rejected_dir.mkdir(parents=True, exist_ok=True)

    def list_approvals(self, status_filter: str = "all") -> List[ApprovalItem]:
        """Return approval items filtered by status."""
        items: List[ApprovalItem] = []

        if status_filter in ("all", "pending"):
            items.extend(self._read_dir(settings.pending_approval_dir, ApprovalStatus.pending))

        if status_filter in ("all", "approved"):
            items.extend(self._read_dir(settings.approved_dir, ApprovalStatus.approved))

        if status_filter in ("all", "rejected"):
            items.extend(self._read_dir(settings.rejected_dir, ApprovalStatus.rejected))

        # Sort newest first
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items

    def _read_dir(self, directory: Path, status: ApprovalStatus) -> List[ApprovalItem]:
        if not directory.exists():
            return []
        items = []
        for path in sorted(directory.glob("*.md")):
            item = _file_to_approval(path, status)
            if item:
                items.append(item)
        return items

    def get(self, approval_id: str) -> Optional[ApprovalItem]:
        for status, directory in [
            (ApprovalStatus.pending, settings.pending_approval_dir),
            (ApprovalStatus.approved, settings.approved_dir),
            (ApprovalStatus.rejected, settings.rejected_dir),
        ]:
            if not directory.exists():
                continue
            for path in directory.glob("*.md"):
                file_id = hashlib.md5(path.name.encode()).hexdigest()[:16]
                if file_id == approval_id:
                    return _file_to_approval(path, status)
        return None

    def _find_pending_path(self, approval_id: str) -> Optional[Path]:
        if not settings.pending_approval_dir.exists():
            return None
        for path in settings.pending_approval_dir.glob("*.md"):
            if hashlib.md5(path.name.encode()).hexdigest()[:16] == approval_id:
                return path
        return None

    def approve(self, approval_id: str, note: Optional[str] = None) -> Optional[ApprovalItem]:
        """Move a pending approval to the Approved/ directory."""
        self._ensure_dirs()
        source = self._find_pending_path(approval_id)
        if not source:
            return None
        dest = settings.approved_dir / source.name
        source.rename(dest)
        return _file_to_approval(dest, ApprovalStatus.approved)

    def reject(self, approval_id: str, note: Optional[str] = None) -> Optional[ApprovalItem]:
        """Move a pending approval to the Rejected/ directory."""
        self._ensure_dirs()
        source = self._find_pending_path(approval_id)
        if not source:
            return None
        dest = settings.rejected_dir / source.name
        source.rename(dest)
        return _file_to_approval(dest, ApprovalStatus.rejected)


# Module-level singleton
approval_service = ApprovalService()
