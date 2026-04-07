#!/usr/bin/env python3
"""
Personal AI Employee - brain_monitor_approvals Skill
Silver Tier - M5: Human-in-the-Loop Approval Pipeline

Purpose: Monitor Approved/ and Rejected/ folders and update plan status.
Tier: Silver
Skill ID: 22

CRITICAL: This skill monitors and updates status but does NOT execute plans.
Execution requires M6 (brain_execute_with_mcp).
"""

import os
import sys
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def get_repo_root() -> Path:
    """Return the repository root (4 levels up from this file's location)."""
    return Path(__file__).parent.parent.parent.parent.resolve()


class ApprovalMonitor:
    """Monitor approval folders and update plan status."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize approval monitor.

        Args:
            config: Optional configuration dict with paths
        """
        self.config = config or self._default_config()
        self.plans_dir = Path(self.config['plans_dir'])
        self.pending_dir = Path(self.config['pending_approval_dir'])
        self.approved_dir = Path(self.config['approved_dir'])
        self.rejected_dir = Path(self.config['rejected_dir'])
        self.system_log = Path(self.config['system_log'])

        # Ensure directories exist
        self.approved_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)

        # Ensure processed subdirectories exist
        (self.approved_dir / 'processed').mkdir(parents=True, exist_ok=True)
        (self.rejected_dir / 'processed').mkdir(parents=True, exist_ok=True)

    def _default_config(self) -> Dict:
        """Return default configuration."""
        base_dir = get_repo_root()
        return {
            'plans_dir': base_dir / 'Plans',
            'pending_approval_dir': base_dir / 'Pending_Approval',
            'approved_dir': base_dir / 'Approved',
            'rejected_dir': base_dir / 'Rejected',
            'system_log': base_dir / 'system_log.md',
        }

    def read_approval_request(self, approval_path: Path) -> Dict:
        """
        Read and parse approval request file.

        Args:
            approval_path: Path to approval request file

        Returns:
            Dict with approval metadata
        """
        if not approval_path.exists():
            raise FileNotFoundError(f"Approval file not found: {approval_path}")

        content = approval_path.read_text(encoding='utf-8')

        # Extract YAML frontmatter
        metadata = {}
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                yaml_content = parts[1].strip()
                for line in yaml_content.split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        metadata[key.strip()] = value.strip()

        return {
            'file_path': str(approval_path),
            'related_plan': metadata.get('related_plan', ''),
            'plan_id': metadata.get('plan_id', ''),
            'risk_level': metadata.get('risk_level', 'Unknown'),
            'status': metadata.get('status', 'pending'),
            'metadata': metadata
        }

    def find_plan_file(self, plan_filename: str) -> Optional[Path]:
        """
        Find plan file by name in Plans/ or Pending_Approval/.

        Args:
            plan_filename: Name of plan file (e.g., PLAN_20260212-0336__test.md)

        Returns:
            Path to plan file, or None if not found
        """
        # Check Plans/ directory
        plan_path = self.plans_dir / plan_filename
        if plan_path.exists():
            return plan_path

        # Check Pending_Approval/ directory
        plan_path = self.pending_dir / plan_filename
        if plan_path.exists():
            return plan_path

        return None

    def update_plan_status(self, plan_path: Path, new_status: str, note: str = '') -> None:
        """
        Update plan file status.

        Args:
            plan_path: Path to plan file
            new_status: New status value
            note: Optional note to add to plan
        """
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")

        content = plan_path.read_text(encoding='utf-8')

        # Update Status in header
        status_pattern = r'(\*\*Status:\*\*\s*)\[?([^\]\n]+)\]?'
        old_status_match = re.search(status_pattern, content)
        old_status = old_status_match.group(2) if old_status_match else 'Unknown'

        new_content = re.sub(
            status_pattern,
            f'\\1{new_status}',
            content,
            count=1
        )

        # Also update in state machine section if present
        state_pattern = r'(\*\*Current Status:\*\*\s*)\[?([^\]\n]+)\]?'
        new_content = re.sub(
            state_pattern,
            f'\\1{new_status}',
            new_content,
            count=1
        )

        # Add note to Execution Log section if provided
        if note:
            now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            log_section_pattern = r'(## Execution Log \(Populated During Execution\)\n\n)(.*?)(\n##|\Z)'

            def add_log_entry(match):
                section_header = match.group(1)
                existing_content = match.group(2)
                next_section = match.group(3)

                new_entry = f"**Status Change:** {old_status} → {new_status} at {now_utc}\n**Note:** {note}\n\n"

                return section_header + new_entry + existing_content + next_section

            new_content = re.sub(
                log_section_pattern,
                add_log_entry,
                new_content,
                flags=re.DOTALL
            )

        plan_path.write_text(new_content, encoding='utf-8')

    def process_approval(self, approval_path: Path) -> Dict:
        """
        Process an approval request file in Approved/.

        Args:
            approval_path: Path to approval file in Approved/

        Returns:
            Dict with processing results
        """
        # Read approval request
        approval_info = self.read_approval_request(approval_path)

        # Find related plan
        plan_path = self.find_plan_file(approval_info['related_plan'])
        if not plan_path:
            raise FileNotFoundError(
                f"Related plan not found: {approval_info['related_plan']}"
            )

        # Update plan status to Approved
        self.update_plan_status(
            plan_path,
            'Approved',
            note='Plan approved by user (file moved to Approved/)'
        )

        # Move approval file to processed/
        processed_path = self.approved_dir / 'processed' / approval_path.name
        approval_path.rename(processed_path)

        # Log approval
        self._log_status_change(
            plan_id=approval_info['plan_id'],
            old_status='Pending_Approval',
            new_status='Approved',
            plan_file=plan_path.name,
            approval_file=approval_path.name
        )

        return {
            'plan': str(plan_path),
            'approval_file': str(processed_path),
            'status': 'Approved',
            'action': 'Plan approved and ready for execution (M6)'
        }

    def process_rejection(self, approval_path: Path) -> Dict:
        """
        Process a rejection (approval file in Rejected/).

        Args:
            approval_path: Path to approval file in Rejected/

        Returns:
            Dict with processing results
        """
        # Read approval request
        approval_info = self.read_approval_request(approval_path)

        # Find related plan
        plan_path = self.find_plan_file(approval_info['related_plan'])
        if not plan_path:
            raise FileNotFoundError(
                f"Related plan not found: {approval_info['related_plan']}"
            )

        # Update plan status to Rejected
        self.update_plan_status(
            plan_path,
            'Rejected',
            note='Plan rejected by user (file moved to Rejected/)'
        )

        # Move approval file to processed/
        processed_path = self.rejected_dir / 'processed' / approval_path.name
        approval_path.rename(processed_path)

        # Log rejection
        self._log_status_change(
            plan_id=approval_info['plan_id'],
            old_status='Pending_Approval',
            new_status='Rejected',
            plan_file=plan_path.name,
            approval_file=approval_path.name
        )

        return {
            'plan': str(plan_path),
            'approval_file': str(processed_path),
            'status': 'Rejected',
            'action': 'Plan rejected and archived'
        }

    def monitor(self, dry_run: bool = False) -> Dict:
        """
        Monitor Approved/ and Rejected/ folders for approval decisions.

        Args:
            dry_run: If True, only report what would be processed

        Returns:
            Dict with monitoring results
        """
        results = {
            'approved': [],
            'rejected': [],
            'errors': []
        }

        # Check Approved/ folder
        approved_files = list(self.approved_dir.glob('ACTION_*.md'))
        for approval_file in approved_files:
            try:
                if dry_run:
                    approval_info = self.read_approval_request(approval_file)
                    results['approved'].append({
                        'file': str(approval_file),
                        'plan': approval_info['related_plan'],
                        'action': 'Would approve (dry-run)'
                    })
                else:
                    result = self.process_approval(approval_file)
                    results['approved'].append(result)
            except Exception as e:
                results['errors'].append({
                    'file': str(approval_file),
                    'error': str(e)
                })

        # Check Rejected/ folder
        rejected_files = list(self.rejected_dir.glob('ACTION_*.md'))
        for approval_file in rejected_files:
            try:
                if dry_run:
                    approval_info = self.read_approval_request(approval_file)
                    results['rejected'].append({
                        'file': str(approval_file),
                        'plan': approval_info['related_plan'],
                        'action': 'Would reject (dry-run)'
                    })
                else:
                    result = self.process_rejection(approval_file)
                    results['rejected'].append(result)
            except Exception as e:
                results['errors'].append({
                    'file': str(approval_file),
                    'error': str(e)
                })

        return results

    def _log_status_change(
        self,
        plan_id: str,
        old_status: str,
        new_status: str,
        plan_file: str,
        approval_file: str
    ):
        """Log status change to system_log.md."""
        log_entry = f"""
[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] PLAN STATUS CHANGE
- Plan ID: {plan_id}
- Plan File: {plan_file}
- Approval File: {approval_file}
- Status: {old_status} → {new_status}
- Skill: brain_monitor_approvals (M5)
- Outcome: OK

"""

        # Append to system_log.md
        if self.system_log.exists():
            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        else:
            print(f"Warning: system_log.md not found at {self.system_log}")
            print(f"Log entry would be:\n{log_entry}")


def main():
    """CLI interface for brain_monitor_approvals skill."""
    parser = argparse.ArgumentParser(
        description='Brain Monitor Approvals - Monitor and process approval decisions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Monitor for approvals and rejections
  python brain_monitor_approvals_skill.py

  # Dry-run mode (preview what would happen)
  python brain_monitor_approvals_skill.py --dry-run

  # Verbose output
  python brain_monitor_approvals_skill.py --verbose
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be processed without making changes'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    args = parser.parse_args()

    # Initialize monitor
    monitor = ApprovalMonitor()

    try:
        # Monitor approval folders
        results = monitor.monitor(dry_run=args.dry_run)

        # Display results
        total = len(results['approved']) + len(results['rejected'])

        if args.dry_run:
            print(f"DRY-RUN MODE: Preview only, no changes made")
            print()

        print(f"✓ Approval monitoring complete")
        print(f"  Approved: {len(results['approved'])}")
        print(f"  Rejected: {len(results['rejected'])}")
        print(f"  Errors: {len(results['errors'])}")
        print()

        if results['approved']:
            print("APPROVED PLANS:")
            for item in results['approved']:
                print(f"  ✓ {Path(item.get('plan', item.get('file', 'Unknown'))).name}")
                if args.verbose:
                    print(f"    Status: {item.get('status', 'Unknown')}")
                    print(f"    Action: {item.get('action', 'Unknown')}")
            print()

        if results['rejected']:
            print("REJECTED PLANS:")
            for item in results['rejected']:
                print(f"  ✗ {Path(item.get('plan', item.get('file', 'Unknown'))).name}")
                if args.verbose:
                    print(f"    Status: {item.get('status', 'Unknown')}")
                    print(f"    Action: {item.get('action', 'Unknown')}")
            print()

        if results['errors']:
            print("ERRORS:")
            for error in results['errors']:
                print(f"  ⚠ {Path(error['file']).name}: {error['error']}")
            print()

        if total == 0 and len(results['errors']) == 0:
            print("No approval files found in Approved/ or Rejected/ folders.")
            print()
            print("Workflow:")
            print("  1. Create plan with brain_create_plan")
            print("  2. Request approval with brain_request_approval")
            print("  3. Manually move ACTION_*.md file to Approved/ or Rejected/")
            print("  4. Run this monitor to process the decision")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
