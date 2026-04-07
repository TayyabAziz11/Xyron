"""
Approval Monitor — checks Pending_Approval/, Approved/, and Rejected/ directories
and surfaces a summary for the ApprovalAgent.

This is a clean wrapper around the legacy brain_monitor_approvals_skill logic,
providing a simple public API for the new agent layer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List


class ApprovalMonitor:
    """
    Lightweight approval monitor that reads filesystem directories.

    Designed to be imported by ApprovalAgent and the API approval_service.
    Does not move files — use the API endpoints for approve/reject actions.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or self._find_base_dir()

    def _find_base_dir(self) -> Path:
        current = Path(__file__).parent
        while current != current.parent:
            if (current / "README.md").exists() and (current / "backend").exists():
                return current
            current = current.parent
        return Path(__file__).parent.parent.parent.parent.parent

    @property
    def pending_dir(self) -> Path:
        return self._base / "backend" / "src" / "ai_operator" / "skills" / "gold" / "Pending_Approval"

    @property
    def approved_dir(self) -> Path:
        return self._base / "Approved"

    @property
    def rejected_dir(self) -> Path:
        return self._base / "Rejected"

    def list_pending(self) -> List[Path]:
        if not self.pending_dir.exists():
            return []
        return sorted(self.pending_dir.glob("*.md"))

    def list_approved(self) -> List[Path]:
        if not self.approved_dir.exists():
            return []
        return sorted(self.approved_dir.glob("*.md"))

    def list_rejected(self) -> List[Path]:
        if not self.rejected_dir.exists():
            return []
        return sorted(self.rejected_dir.glob("*.md"))

    def get_summary(self) -> str:
        """Return a formatted markdown summary of the current approval queue."""
        pending = self.list_pending()
        approved = self.list_approved()
        rejected = self.list_rejected()

        total = len(pending) + len(approved) + len(rejected)
        if total == 0:
            return "**Approval Queue**\n\nNo approval items found. The queue is clear."

        lines = [
            "**Approval Queue**\n",
            f"- Pending review: **{len(pending)}**",
            f"- Approved: **{len(approved)}**",
            f"- Rejected: **{len(rejected)}**",
        ]

        if pending:
            lines.append("\n**Pending Approvals:**")
            for path in pending[:10]:
                lines.append(f"  - {path.name}")

        return "\n".join(lines)

    def counts(self) -> Dict[str, int]:
        return {
            "pending": len(self.list_pending()),
            "approved": len(self.list_approved()),
            "rejected": len(self.list_rejected()),
        }
