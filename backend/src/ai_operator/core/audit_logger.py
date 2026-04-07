#!/usr/bin/env python3
"""
Audit Logger — Hackathon-compliant JSON action logging.
Gold Tier — G9: Comprehensive audit logging.

Writes every AI-executed action to Logs/YYYY-MM-DD.json in the required schema:

{
  "timestamp": "2026-01-07T10:30:00Z",
  "action_type": "odoo_invoice_create",
  "actor": "claude_code",
  "target": "optional — e.g. invoice_id or recipient",
  "parameters": {"key": "value"},
  "approval_status": "approved",
  "approval_ref": "WEBPLAN_20260107_Odoo_create_invoice_...",
  "approved_by": "human",
  "result": "success",
  "error": null
}

Storage: Logs/YYYY-MM-DD.json (one file per day).
Retention: 90 days minimum.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class AuditLogger:
    """Writes structured audit entries to Logs/YYYY-MM-DD.json."""

    def __init__(self, logs_dir: str):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _log_file(self) -> Path:
        """Return today's log file path."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.logs_dir / f"{today}.json"

    def log(
        self,
        action_type: str,
        actor: str = "claude_code",
        target: Optional[str] = None,
        parameters: Optional[Dict] = None,
        approval_status: str = "approved",
        approval_ref: Optional[str] = None,
        approved_by: str = "human",
        result: str = "success",
        error: Optional[str] = None,
    ) -> Dict:
        """
        Append a structured audit entry.

        Args:
            action_type: Machine-readable action (e.g. 'odoo_invoice_create')
            actor:        Who triggered the action (default: 'claude_code')
            target:       Optional identifier (invoice_id, email address, etc.)
            parameters:   Dict of key action parameters (avoid PII where possible)
            approval_status: 'approved' | 'pending' | 'rejected' | 'auto'
            approval_ref: Filename of the approval plan that authorized this action
            approved_by:  'human' | 'auto_approved' | 'system'
            result:       'success' | 'failure' | 'partial'
            error:        Error message if result != 'success'

        Returns:
            The entry dict that was written.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action_type": action_type,
            "actor": actor,
            "parameters": parameters or {},
            "approval_status": approval_status,
            "approved_by": approved_by,
            "result": result,
        }
        if target:
            entry["target"] = target
        if approval_ref:
            entry["approval_ref"] = approval_ref
        if error:
            entry["error"] = error

        self._append(entry)
        return entry

    def _append(self, entry: Dict):
        """Append entry to today's JSON log (newline-delimited JSON)."""
        log_file = self._log_file()
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            # Never let audit logging failure break the main action
            import sys
            print(f"[audit_logger] WARNING: Failed to write audit log: {e}", file=sys.stderr)

    def read_today(self) -> list:
        """Return all entries logged today."""
        return self._read_file(self._log_file())

    def read_date(self, date_str: str) -> list:
        """Return all entries for a specific date (YYYY-MM-DD)."""
        return self._read_file(self.logs_dir / f"{date_str}.json")

    def _read_file(self, path: Path) -> list:
        if not path.exists():
            return []
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return entries

    def purge_old_logs(self, retention_days: int = 90):
        """Delete log files older than retention_days. Run periodically."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for log_file in self.logs_dir.glob("*.json"):
            try:
                file_date = datetime.strptime(log_file.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    log_file.unlink()
            except (ValueError, OSError):
                continue


def get_audit_logger() -> AuditLogger:
    """Convenience factory — resolves repo root automatically."""
    # Walk up from this file to find repo root
    current = Path(__file__).parent
    while current != current.parent:
        if (current / "system_log.md").exists():
            return AuditLogger(str(current / "Logs"))
        current = current.parent
    # Fallback: use relative path
    return AuditLogger(str(Path(__file__).parent.parent.parent.parent.parent / "Logs"))
