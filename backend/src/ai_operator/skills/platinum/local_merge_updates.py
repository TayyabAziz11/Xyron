#!/usr/bin/env python3
"""
Personal AI Employee — Local Merge Updates Skill
Platinum Tier — PT-3: Dashboard single-writer / Local Executive

Responsibility:
  - Read Updates/cloud/*.md (cloud status notes)
  - Update Dashboard.md with:
      * "Cloud Updates" section (latest entries)
      * "Pending Approvals count" (from Pending_Approval/ dirs)
  - Move processed cloud updates to Updates/cloud/processed/
  - ONLY local runs this — cloud MUST NOT call this script

This is the ONLY mechanism that writes to Dashboard.md for Platinum-tier cloud updates.
"""

import argparse
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("local_merge_updates")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DASHBOARD_CLOUD_SECTION_HEADER = "## ☁️ Cloud Updates (Platinum)"
DASHBOARD_PENDING_APPROVALS_HEADER = "## 📋 Pending Approvals (Platinum)"
MAX_CLOUD_ENTRIES_IN_DASHBOARD = 10


class LocalMergeUpdates:
    """
    Reads cloud updates and merges them into Dashboard.md.
    Idempotent — safe to run multiple times.
    """

    READY_SIGNAL_PATH = Path("/tmp/local-merge-updates.ready")

    def __init__(self, vault_root: Path, dry_run: bool = False):
        self.vault_root = Path(vault_root).resolve()
        self.dry_run = dry_run

        self.updates_cloud = self.vault_root / "Updates" / "cloud"
        self.updates_processed = self.vault_root / "Updates" / "cloud" / "processed"
        self.pending_approval = self.vault_root / "Pending_Approval"
        self.dashboard = self.vault_root / "Dashboard.md"

    # ------------------------------------------------------------------
    # Count pending approvals
    # ------------------------------------------------------------------
    def _count_pending(self) -> int:
        """Count files in Pending_Approval/<domain>/ directories."""
        count = 0
        if not self.pending_approval.exists():
            return 0
        for domain_dir in self.pending_approval.iterdir():
            if domain_dir.is_dir():
                count += sum(
                    1 for f in domain_dir.iterdir()
                    if f.is_file() and not f.name.startswith(".")
                )
        return count

    # ------------------------------------------------------------------
    # Read unprocessed cloud updates
    # ------------------------------------------------------------------
    def _read_unprocessed_updates(self) -> list[tuple[Path, str]]:
        """Return list of (path, content) for unprocessed update files, sorted oldest-first."""
        if not self.updates_cloud.exists():
            return []

        updates = []
        for f in sorted(self.updates_cloud.iterdir()):
            if (
                f.is_file()
                and f.name.startswith("update__")
                and f.suffix == ".md"
                and not f.name.endswith(".processed")
            ):
                try:
                    content = f.read_text(encoding="utf-8")
                    updates.append((f, content))
                except Exception as exc:
                    logger.warning("Could not read %s: %s", f.name, exc)

        return updates

    # ------------------------------------------------------------------
    # Extract summary line from update content
    # ------------------------------------------------------------------
    def _extract_summary(self, content: str, filename: str) -> str:
        """Pull a one-line summary from update content for Dashboard entry."""
        # Try to find the first non-header, non-blank line
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("**"):
                return stripped[:120]
        return f"Cloud update: {filename}"

    # ------------------------------------------------------------------
    # Build Dashboard cloud section text
    # ------------------------------------------------------------------
    def _build_cloud_section(self, updates: list[tuple[Path, str]], pending_count: int) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            DASHBOARD_CLOUD_SECTION_HEADER,
            "",
            f"*Last merged: {now}*",
            "",
        ]

        if not updates:
            lines.append("*(No new cloud updates)*")
        else:
            lines.append("| Timestamp | Domain | Summary |")
            lines.append("|-----------|--------|---------|")
            # Show latest N entries
            for f, content in updates[-MAX_CLOUD_ENTRIES_IN_DASHBOARD:]:
                # Extract timestamp from filename: update__20260227-143100.md
                ts_match = re.search(r"update__(\d{8}-\d{6})", f.name)
                ts_str = ts_match.group(1) if ts_match else f.stem
                # Extract domain from content
                domain_match = re.search(r"\*\*Domain:\*\*\s*(\w+)", content)
                domain = domain_match.group(1) if domain_match else "—"
                summary = self._extract_summary(content, f.name)
                lines.append(f"| {ts_str} | {domain} | {summary} |")

        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Build pending approvals section
    # ------------------------------------------------------------------
    def _build_pending_section(self, pending_count: int) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            DASHBOARD_PENDING_APPROVALS_HEADER,
            "",
            f"*Last updated: {now}*",
            "",
            f"**Count:** {pending_count} pending approval(s)",
            "",
        ]
        if pending_count > 0:
            lines.append(f"> ⚠️ Action required: {pending_count} item(s) in `Pending_Approval/` await your review.")
            lines.append("")
            lines.append("To review:")
            lines.append("```bash")
            lines.append("ls Pending_Approval/email/ Pending_Approval/social/ Pending_Approval/accounting/")
            lines.append("```")
        else:
            lines.append("> ✅ No pending approvals — all caught up.")

        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Update Dashboard.md (in-place section replacement)
    # ------------------------------------------------------------------
    def _update_dashboard(self, cloud_section: str, pending_section: str) -> bool:
        """
        Replace (or append) the Cloud Updates and Pending Approvals sections
        in Dashboard.md. Idempotent.
        """
        if not self.dashboard.exists():
            logger.warning("Dashboard.md not found at %s — skipping", self.dashboard)
            return False

        content = self.dashboard.read_text(encoding="utf-8")

        def _replace_or_append_section(full_content: str, header: str, new_section: str) -> str:
            """Replace section starting at `header` up to the next ## heading."""
            pattern = re.compile(
                rf"^{re.escape(header)}.*?(?=^## |\Z)",
                re.MULTILINE | re.DOTALL,
            )
            if pattern.search(full_content):
                return pattern.sub(new_section + "\n", full_content)
            else:
                # Append at end
                return full_content.rstrip() + "\n\n" + new_section + "\n"

        content = _replace_or_append_section(content, DASHBOARD_CLOUD_SECTION_HEADER, cloud_section)
        content = _replace_or_append_section(content, DASHBOARD_PENDING_APPROVALS_HEADER, pending_section)

        if self.dry_run:
            logger.info("[DRY-RUN] Would update Dashboard.md")
            return True

        self.dashboard.write_text(content, encoding="utf-8")
        logger.info("Dashboard.md updated.")
        return True

    # ------------------------------------------------------------------
    # Mark updates as processed
    # ------------------------------------------------------------------
    def _mark_processed(self, update_files: list[Path]) -> int:
        """Move processed update files to Updates/cloud/processed/. Returns count moved."""
        if self.dry_run:
            logger.info("[DRY-RUN] Would move %d update files to processed/", len(update_files))
            return len(update_files)

        self.updates_processed.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in update_files:
            dest = self.updates_processed / f.name
            try:
                shutil.move(str(f), str(dest))
                logger.info("Marked processed: %s → processed/", f.name)
                moved += 1
            except Exception as exc:
                logger.warning("Could not move %s: %s", f.name, exc)
        return moved

    # ------------------------------------------------------------------
    # Main merge operation
    # ------------------------------------------------------------------
    def merge(self) -> dict:
        """
        Run the full merge workflow.
        Returns a summary dict.
        """
        # Optional git pull first
        try:
            from ai_operator.core.git_sync import git_pull
            if not self.dry_run:
                ok, msg = git_pull(self.vault_root)
                if not ok:
                    logger.warning("git pull warning: %s", msg)
        except ImportError:
            pass

        updates = self._read_unprocessed_updates()
        pending_count = self._count_pending()

        logger.info(
            "Found %d unprocessed cloud updates, %d pending approvals.",
            len(updates), pending_count,
        )

        cloud_section = self._build_cloud_section(updates, pending_count)
        pending_section = self._build_pending_section(pending_count)

        dashboard_updated = self._update_dashboard(cloud_section, pending_section)

        update_paths = [f for f, _ in updates]
        moved = self._mark_processed(update_paths)

        summary = {
            "updates_found": len(updates),
            "updates_moved_to_processed": moved,
            "pending_approvals": pending_count,
            "dashboard_updated": dashboard_updated,
        }
        logger.info("Merge complete: %s", summary)
        return summary

    # ------------------------------------------------------------------
    # Ready signal
    # ------------------------------------------------------------------
    def _signal_ready(self):
        try:
            self.READY_SIGNAL_PATH.write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8"
            )
        except Exception:
            pass
        print("SERVICE_READY", flush=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Local Merge Updates — Platinum Tier (Dashboard single-writer)"
    )
    parser.add_argument(
        "--vault-root",
        default=os.getcwd(),
        help="Path to vault root (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen; don't write files",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously (check every --loop-seconds seconds)",
    )
    parser.add_argument(
        "--loop-seconds",
        type=int,
        default=30,
        metavar="N",
        help="Seconds between merge cycles when --loop is set (default: 30)",
    )

    args = parser.parse_args()

    merger = LocalMergeUpdates(
        vault_root=Path(args.vault_root),
        dry_run=args.dry_run,
    )

    if args.loop:
        import time
        merger._signal_ready()
        while True:
            merger.merge()
            logger.info("Sleeping %d seconds...", args.loop_seconds)
            time.sleep(args.loop_seconds)
    else:
        summary = merger.merge()
        print(
            f"\nMerge complete:\n"
            f"  Cloud updates processed : {summary['updates_found']}\n"
            f"  Moved to processed/     : {summary['updates_moved_to_processed']}\n"
            f"  Pending approvals       : {summary['pending_approvals']}\n"
            f"  Dashboard updated       : {summary['dashboard_updated']}\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
