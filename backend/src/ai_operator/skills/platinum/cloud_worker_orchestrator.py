#!/usr/bin/env python3
"""
Personal AI Employee — Cloud Worker Orchestrator Skill
Platinum Tier — PT-2 / PT-3: Cloud-side draft-only executor

CRITICAL SAFETY RULES (enforced by this module):
  1. NEVER write to Dashboard.md
  2. NEVER send email, post social, process payments, or execute irreversible actions
  3. NEVER read or log .secrets/, .env, or session files
  4. ONLY write to:
       - In_Progress/cloud/         (claimed items)
       - Pending_Approval/<domain>/ (draft plans)
       - Updates/cloud/             (status notes)
  5. HALT when In_Progress/cloud/ count >= MAX_PENDING_APPROVALS

Workflow per item:
  1. Scan Needs_Action/<domain>/ for unprocessed items
  2. Claim-by-move → In_Progress/cloud/<filename>
  3. Generate draft plan → Pending_Approval/<domain>/plan__<domain>__<slug>__<ts>.md
  4. Write status note → Updates/cloud/update__<ts>.md
  5. (Optional) git commit + push

CLI flags:
  --once               Process one scan cycle and exit
  --loop-seconds N     Interval between scans (default: 60)
  --dry-run            Show what would happen; don't move/create files
  --max-items N        Max items to process per scan (default: 5)
  --max-pending-approvals N  Halt threshold (default: 10)
  --git-commit         Commit changes after scan
  --git-push           Push commits after scan (requires --git-commit)
  --sync git           Sync backend (only 'git' supported in Phase 1)
  --vault-root PATH    Path to vault root (default: CWD)
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("cloud_worker_orchestrator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FORBIDDEN_WRITE_PATHS = {"Dashboard.md"}

INTAKE_DOMAINS = [
    ("email", "Needs_Action/email"),
    ("social", "Needs_Action/social"),
    ("accounting", "Needs_Action/accounting"),
]


# ---------------------------------------------------------------------------
# Helper: timestamp slug
# ---------------------------------------------------------------------------
def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _slugify(name: str) -> str:
    """Basic slugify: lowercase, replace spaces/underscores with hyphens."""
    import re
    return re.sub(r"[^a-z0-9-]", "-", name.lower().replace("_", "-")).strip("-")


# ---------------------------------------------------------------------------
# Safety guard — called before any file write
# ---------------------------------------------------------------------------
def _assert_safe_write(dest: Path, vault_root: Path):
    """Raise ValueError if dest is a forbidden path."""
    try:
        rel = dest.relative_to(vault_root)
    except ValueError:
        raise ValueError(f"Write target {dest} is outside vault root {vault_root}")
    if str(rel) in FORBIDDEN_WRITE_PATHS:
        raise ValueError(
            f"SAFETY VIOLATION: Cloud worker attempted to write {rel}. "
            "Cloud MUST NOT modify Dashboard.md."
        )
    # No writes to Approved/, Rejected/, Done/ (local-only)
    first_part = rel.parts[0] if rel.parts else ""
    if first_part in {"Approved", "Rejected", "Done"}:
        raise ValueError(
            f"SAFETY VIOLATION: Cloud worker attempted to write to {first_part}/. "
            "These are local-only directories."
        )


# ---------------------------------------------------------------------------
# Draft plan generator
# ---------------------------------------------------------------------------
def _generate_draft_plan(
    item_path: Path, domain: str, vault_root: Path
) -> str:
    """
    Read the intake item and produce a draft plan Markdown string.
    The plan uses the existing M4.1 action JSON format understood by the
    local executor.
    """
    try:
        content = item_path.read_text(encoding="utf-8")
    except Exception:
        content = "(could not read item — check permissions)"

    slug = _slugify(item_path.stem)
    ts = _now_ts()

    action_block = _build_action_block(domain, item_path, content)

    plan = f"""# Draft Plan — {domain.upper()} — {slug}
**Status:** DRAFT CREATED BY CLOUD — REQUIRES LOCAL APPROVAL
**Domain:** {domain}
**Source Item:** {item_path.name}
**Created:** {ts}
**Agent:** cloud_worker_orchestrator

---

## Summary

Cloud Worker has triaged the following {domain} item and generated a draft action.
**This plan must be reviewed and approved by the Local Executive before any action is taken.**

---

## Source Item Content

```
{content[:2000]}
```

---

## Proposed Action

```json
{action_block}
```

---

## Approval Instructions

To approve this plan:
1. Review the action JSON above.
2. Copy this file to `Approved/` directory.
3. Run: `python scripts/brain_execute_with_mcp_skill.py --plan Approved/{item_path.stem}__plan.md --execute`

To reject:
1. Copy this file to `Rejected/` directory.
2. Cloud will be notified on next pull.

---

> ⚠️ CLOUD DRAFT ONLY — DO NOT EXECUTE WITHOUT LOCAL APPROVAL
"""
    return plan


def _build_action_block(domain: str, item_path: Path, content: str) -> str:
    """Return a domain-specific action JSON stub."""
    import json
    import re

    if domain == "email":
        # Try to extract subject from intake file
        subject_match = re.search(r"(?i)subject:\s*(.+)", content)
        subject = subject_match.group(1).strip() if subject_match else "Re: (no subject)"
        from_match = re.search(r"(?i)from:\s*(.+)", content)
        reply_to = from_match.group(1).strip() if from_match else "unknown@example.com"

        action = {
            "action": "send_email",
            "domain": "email",
            "source_item": item_path.name,
            "parameters": {
                "to": reply_to,
                "subject": f"Re: {subject}",
                "body": "(Draft reply — fill in before approval)",
                "method": "gmail_mcp",
            },
            "requires_approval": True,
            "draft_by": "cloud_worker_orchestrator",
        }
    elif domain == "social":
        platform_match = re.search(r"(?i)platform:\s*(\w+)", content)
        platform = platform_match.group(1) if platform_match else "linkedin"
        action = {
            "action": "post_social",
            "domain": "social",
            "source_item": item_path.name,
            "parameters": {
                "platform": platform,
                "content": "(Draft post — fill in before approval)",
                "schedule": None,
            },
            "requires_approval": True,
            "draft_by": "cloud_worker_orchestrator",
        }
    elif domain == "accounting":
        action = {
            "action": "odoo_draft_invoice",
            "domain": "accounting",
            "source_item": item_path.name,
            "parameters": {
                "partner": "(unknown — review source item)",
                "amount": 0.0,
                "currency": "USD",
                "note": "(Draft invoice — fill in before approval)",
            },
            "requires_approval": True,
            "draft_by": "cloud_worker_orchestrator",
        }
    else:
        action = {
            "action": f"handle_{domain}",
            "domain": domain,
            "source_item": item_path.name,
            "parameters": {},
            "requires_approval": True,
            "draft_by": "cloud_worker_orchestrator",
        }

    return json.dumps(action, indent=2)


# ---------------------------------------------------------------------------
# Main orchestrator class
# ---------------------------------------------------------------------------
class CloudWorkerOrchestrator:
    """
    Cloud-side draft-only orchestrator.
    Scans intake queues, claims items, drafts plans, writes updates.
    """

    READY_SIGNAL_PATH = Path("/tmp/cloud-worker-orchestrator.ready")

    def __init__(
        self,
        vault_root: Path,
        dry_run: bool = True,
        max_items: int = 5,
        max_pending_approvals: int = 10,
        git_commit: bool = False,
        git_push: bool = False,
        sync: str = "git",
    ):
        self.vault_root = Path(vault_root).resolve()
        self.dry_run = dry_run
        self.max_items = max_items
        self.max_pending_approvals = max_pending_approvals
        self.git_commit = git_commit
        self.git_push = git_push
        self.sync = sync

        # Vault paths
        self.in_progress_cloud = self.vault_root / "In_Progress" / "cloud"
        self.pending_approval = self.vault_root / "Pending_Approval"
        self.updates_cloud = self.vault_root / "Updates" / "cloud"

        # Stats
        self.items_processed = 0
        self.plans_created = 0
        self.errors: list[str] = []

    # ------------------------------------------------------------------
    # Safety: dashboard write guard
    # ------------------------------------------------------------------
    def _safe_write(self, dest: Path, content: str) -> bool:
        """Write content to dest after safety checks. Returns True on success."""
        _assert_safe_write(dest, self.vault_root)
        if self.dry_run:
            logger.info("[DRY-RUN] Would write: %s", dest.relative_to(self.vault_root))
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        logger.info("Wrote: %s", dest.relative_to(self.vault_root))
        return True

    # ------------------------------------------------------------------
    # Claim-by-move
    # ------------------------------------------------------------------
    def _claim_item(self, item_path: Path) -> Optional[Path]:
        """
        Atomically move item from Needs_Action/<domain>/ to In_Progress/cloud/.
        Returns new path on success, None if already claimed.
        """
        dest = self.in_progress_cloud / item_path.name
        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would claim: %s → In_Progress/cloud/", item_path.name
            )
            return dest  # pretend success for dry-run
        try:
            self.in_progress_cloud.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item_path), str(dest))
            logger.info("Claimed: %s → In_Progress/cloud/", item_path.name)
            return dest
        except FileNotFoundError:
            logger.info("Item already claimed by another agent: %s", item_path.name)
            return None
        except Exception as exc:
            logger.warning("Claim failed for %s: %s", item_path.name, exc)
            return None

    # ------------------------------------------------------------------
    # Check approval flood
    # ------------------------------------------------------------------
    def _check_flood_halt(self) -> bool:
        """Return True if we should halt (too many pending approvals)."""
        if not self.in_progress_cloud.exists():
            return False
        in_flight = [
            f for f in self.in_progress_cloud.iterdir()
            if not f.name.startswith(".")
        ]
        if len(in_flight) >= self.max_pending_approvals:
            logger.warning(
                "HALT: %d items in In_Progress/cloud/ >= max_pending_approvals (%d). "
                "Waiting for Local to process existing approvals.",
                len(in_flight),
                self.max_pending_approvals,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Process single item
    # ------------------------------------------------------------------
    def _process_item(self, item_path: Path, domain: str) -> bool:
        """Claim, draft, and write update for one item. Returns True on success."""
        logger.info("Processing item: %s (domain: %s)", item_path.name, domain)

        # 1. Claim
        claimed_path = self._claim_item(item_path)
        if claimed_path is None:
            return False

        # 2. Generate draft plan
        plan_content = _generate_draft_plan(claimed_path, domain, self.vault_root)
        ts = _now_ts()
        slug = _slugify(item_path.stem)[:40]
        plan_filename = f"plan__{domain}__{slug}__{ts}.md"
        plan_dest = self.pending_approval / domain / plan_filename

        ok = self._safe_write(plan_dest, plan_content)
        if not ok:
            self.errors.append(f"Failed to write plan for {item_path.name}")
            return False

        # 3. Write cloud update
        update_content = (
            f"# Cloud Update — {ts}\n\n"
            f"**Domain:** {domain}\n"
            f"**Item:** {item_path.name}\n"
            f"**Plan:** {plan_filename}\n"
            f"**Status:** Pending approval\n\n"
            f"Cloud Worker drafted a {domain} action plan. "
            f"Review `Pending_Approval/{domain}/{plan_filename}` on your local machine.\n"
        )
        update_dest = self.updates_cloud / f"update__{ts}.md"
        self._safe_write(update_dest, update_content)

        self.plans_created += 1
        self.items_processed += 1
        return True

    # ------------------------------------------------------------------
    # Scan cycle
    # ------------------------------------------------------------------
    def scan_once(self) -> int:
        """
        One scan cycle: check all intake domains, process up to max_items.
        Returns count of items processed.
        """
        # Git pull first
        if self.sync == "git" and not self.dry_run:
            self._git_pull()

        if self._check_flood_halt():
            return 0

        items_this_cycle = 0

        for domain, folder_rel in INTAKE_DOMAINS:
            if items_this_cycle >= self.max_items:
                break
            folder = self.vault_root / folder_rel
            if not folder.exists():
                continue

            for item in sorted(folder.iterdir()):
                if items_this_cycle >= self.max_items:
                    break
                if self._check_flood_halt():
                    break
                if item.is_file() and not item.name.startswith(".") and item.suffix == ".md":
                    if self._process_item(item, domain):
                        items_this_cycle += 1

        # Git commit + push
        if items_this_cycle > 0 and not self.dry_run:
            if self.sync == "git" and self.git_commit:
                self._git_commit_and_push()

        return items_this_cycle

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------
    def _git_pull(self):
        try:
            from ai_operator.core.git_sync import git_pull
            ok, msg = git_pull(self.vault_root)
            if not ok:
                logger.warning("git pull warning: %s", msg)
        except ImportError:
            logger.warning("git_sync not importable; skipping git pull")

    def _git_commit_and_push(self):
        try:
            from ai_operator.core.git_sync import git_commit_all, git_push
            ts = _now_ts()
            msg = f"platinum(cloud): drafted {self.plans_created} approval(s) + updates [{ts}]"
            ok_c, msg_c = git_commit_all(self.vault_root, msg)
            if not ok_c:
                logger.warning("git commit warning: %s", msg_c)
                return
            if self.git_push:
                ok_p, msg_p = git_push(self.vault_root)
                if not ok_p:
                    logger.warning("git push warning: %s", msg_p)
        except ImportError:
            logger.warning("git_sync not importable; skipping git commit/push")

    # ------------------------------------------------------------------
    # Ready signal
    # ------------------------------------------------------------------
    def _signal_ready(self):
        """Touch /tmp/<service>.ready and print SERVICE_READY."""
        try:
            self.READY_SIGNAL_PATH.write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8"
            )
        except Exception:
            pass  # Non-fatal
        print("SERVICE_READY", flush=True)

    # ------------------------------------------------------------------
    # Run (loop or once)
    # ------------------------------------------------------------------
    def run(self, once: bool = False, loop_seconds: int = 60):
        import time

        logger.info(
            "Cloud Worker Orchestrator starting | dry_run=%s | max_items=%d | "
            "max_pending_approvals=%d | loop_seconds=%d | once=%s",
            self.dry_run, self.max_items, self.max_pending_approvals,
            loop_seconds, once,
        )

        if self.dry_run:
            logger.info("DRY-RUN MODE: No files will be moved or created.")

        self._signal_ready()

        while True:
            logger.info("--- Scan cycle starting ---")
            count = self.scan_once()
            logger.info("--- Scan cycle complete: %d items processed ---", count)

            if once:
                break

            logger.info("Sleeping %d seconds until next scan...", loop_seconds)
            time.sleep(loop_seconds)

        logger.info(
            "Cloud Worker done. Total: items=%d, plans=%d, errors=%d",
            self.items_processed, self.plans_created, len(self.errors),
        )
        return self.errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Cloud Worker Orchestrator — Platinum Tier (draft-only)"
    )
    parser.add_argument(
        "--vault-root",
        default=os.getcwd(),
        help="Path to vault root (default: current directory)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan cycle and exit",
    )
    parser.add_argument(
        "--loop-seconds",
        type=int,
        default=60,
        metavar="N",
        help="Seconds between scan cycles (default: 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen; don't move or create files",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=5,
        metavar="N",
        help="Max items to process per scan cycle (default: 5)",
    )
    parser.add_argument(
        "--max-pending-approvals",
        type=int,
        default=10,
        metavar="N",
        help="Halt if In_Progress/cloud/ has this many items (default: 10)",
    )
    parser.add_argument(
        "--git-commit",
        action="store_true",
        help="Commit changes after each scan cycle",
    )
    parser.add_argument(
        "--git-push",
        action="store_true",
        help="Push commits (requires --git-commit)",
    )
    parser.add_argument(
        "--sync",
        default="git",
        choices=["git"],
        help="Sync backend (default: git)",
    )

    args = parser.parse_args()

    if args.git_push and not args.git_commit:
        parser.error("--git-push requires --git-commit")

    orchestrator = CloudWorkerOrchestrator(
        vault_root=Path(args.vault_root),
        dry_run=args.dry_run,
        max_items=args.max_items,
        max_pending_approvals=args.max_pending_approvals,
        git_commit=args.git_commit,
        git_push=args.git_push,
        sync=args.sync,
    )

    errors = orchestrator.run(
        once=args.once,
        loop_seconds=args.loop_seconds,
    )

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
