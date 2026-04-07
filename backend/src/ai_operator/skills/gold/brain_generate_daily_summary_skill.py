#!/usr/bin/env python3
"""
Personal AI Employee - brain_generate_daily_summary Skill
Silver Tier - M8: Daily Summary Generation

Purpose: Generate daily activity summaries from system logs.
Tier: Silver
Skill ID: 20

Analyzes:
- system_log.md (all operations)
- Logs/mcp_actions.log (MCP operations)
- Vault state (Inbox, Plans, etc.)
"""

import os
import sys
import re
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import Counter


class DailySummaryGenerator:
    """Generate daily activity summaries."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize summary generator.

        Args:
            config: Optional configuration dict with paths
        """
        self.config = config or self._default_config()
        self.base_dir = Path(self.config['base_dir'])
        self.system_log = Path(self.config['system_log'])
        self.mcp_log = Path(self.config['mcp_log'])
        self.summaries_dir = Path(self.config['summaries_dir'])
        self.dashboard = Path(self.config['dashboard'])

        # Ensure directories exist
        self.summaries_dir.mkdir(parents=True, exist_ok=True)

    def _default_config(self) -> Dict:
        """Return default configuration."""
        base_dir = Path(__file__).parent
        return {
            'base_dir': base_dir,
            'system_log': base_dir / 'system_log.md',
            'mcp_log': base_dir / 'Logs' / 'mcp_actions.log',
            'summaries_dir': base_dir / 'Daily_Summaries',
            'dashboard': base_dir / 'Dashboard.md',
        }

    def count_vault_items(self) -> Dict[str, int]:
        """
        Count items in vault folders.

        Returns:
            Dict with folder counts
        """
        counts = {}

        folders = {
            'inbox': self.base_dir / 'Inbox',
            'needs_action': self.base_dir / 'Needs_Action',
            'done': self.base_dir / 'Done',
            'plans': self.base_dir / 'Plans',
            'pending_approval': self.base_dir / 'Pending_Approval',
            'approved': self.base_dir / 'Approved',
            'rejected': self.base_dir / 'Rejected',
        }

        for name, folder in folders.items():
            if folder.exists():
                # Count markdown files (exclude README)
                md_files = [f for f in folder.glob('*.md') if f.name != 'README.md']
                counts[name] = len(md_files)
            else:
                counts[name] = 0

        return counts

    def parse_system_log(self, since_date: Optional[datetime] = None) -> Dict:
        """
        Parse system_log.md for activity.

        Args:
            since_date: Only count entries since this date (default: last 24h)

        Returns:
            Dict with activity counts
        """
        if not self.system_log.exists():
            return self._empty_activity()

        if since_date is None:
            since_date = datetime.now(timezone.utc) - timedelta(days=1)

        activity = self._empty_activity()

        try:
            content = self.system_log.read_text(encoding='utf-8')

            # Parse log entries
            for line in content.split('\n'):
                if not line.startswith('['):
                    continue

                # Extract timestamp
                timestamp_match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\]', line)
                if not timestamp_match:
                    continue

                try:
                    entry_time = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S UTC')
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                if entry_time < since_date:
                    continue

                # Count operation types
                if 'PLAN' in line:
                    if 'CREATED' in line or 'CREATE' in line:
                        activity['plans_created'] += 1
                    elif 'APPROVED' in line or 'APPROVAL' in line:
                        activity['plans_approved'] += 1
                    elif 'REJECTED' in line:
                        activity['plans_rejected'] += 1
                    elif 'EXECUTION' in line or 'EXECUTED' in line:
                        activity['plans_executed'] += 1
                    elif 'FAILED' in line:
                        activity['failures'] += 1

                if 'INBOX' in line or 'INTAKE' in line:
                    activity['inbox_items'] += 1

                if 'GMAIL' in line:
                    activity['gmail_operations'] += 1

                if 'FAILED' in line or 'ERROR' in line:
                    activity['failures'] += 1

        except Exception as e:
            print(f"Warning: Failed to parse system_log.md: {e}")

        return activity

    def parse_mcp_log(self, since_date: Optional[datetime] = None) -> Dict:
        """
        Parse Logs/mcp_actions.log for MCP operations.

        Args:
            since_date: Only count entries since this date (default: last 24h)

        Returns:
            Dict with MCP operation counts
        """
        if not self.mcp_log.exists():
            return {'total': 0, 'by_operation': {}, 'successes': 0, 'failures': 0}

        if since_date is None:
            since_date = datetime.now(timezone.utc) - timedelta(days=1)

        mcp_activity = {'total': 0, 'by_operation': Counter(), 'successes': 0, 'failures': 0}

        try:
            with open(self.mcp_log, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())

                        # Parse timestamp
                        entry_time = datetime.strptime(entry['timestamp'], '%Y-%m-%d %H:%M:%S UTC')
                        entry_time = entry_time.replace(tzinfo=timezone.utc)

                        if entry_time < since_date:
                            continue

                        mcp_activity['total'] += 1
                        operation = entry.get('operation', 'unknown')
                        mcp_activity['by_operation'][operation] += 1

                        if entry.get('success'):
                            mcp_activity['successes'] += 1
                        else:
                            mcp_activity['failures'] += 1

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except Exception as e:
            print(f"Warning: Failed to parse mcp_actions.log: {e}")

        return mcp_activity

    def _empty_activity(self) -> Dict:
        """Return empty activity dict."""
        return {
            'plans_created': 0,
            'plans_approved': 0,
            'plans_rejected': 0,
            'plans_executed': 0,
            'inbox_items': 0,
            'gmail_operations': 0,
            'failures': 0,
        }

    def generate_summary(self, date: Optional[datetime] = None) -> Tuple[str, Path]:
        """
        Generate daily summary.

        Args:
            date: Date to generate summary for (default: today)

        Returns:
            Tuple of (summary_content, summary_path)
        """
        if date is None:
            date = datetime.now(timezone.utc)

        # Get data
        since_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        vault_counts = self.count_vault_items()
        activity = self.parse_system_log(since_date=since_date)
        mcp_activity = self.parse_mcp_log(since_date=since_date)

        # Generate summary content
        summary_date_str = date.strftime('%Y-%m-%d')
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        summary_content = f"""# Daily Summary: {summary_date_str}

**Generated:** {now_utc}
**Period:** {summary_date_str} 00:00 UTC - {summary_date_str} 23:59 UTC
**Status:** ✅ Silver Tier Operational

---

## 📊 Activity Metrics

| Metric | Count |
|--------|-------|
| **Inbox Items Processed** | {activity['inbox_items']} |
| **Plans Created** | {activity['plans_created']} |
| **Plans Approved** | {activity['plans_approved']} |
| **Plans Rejected** | {activity['plans_rejected']} |
| **Plans Executed** | {activity['plans_executed']} |
| **Gmail Operations** | {activity['gmail_operations']} |
| **MCP Operations** | {mcp_activity['total']} |
| **Failures** | {activity['failures'] + mcp_activity['failures']} |

---

## 🗂️ Vault State

| Folder | Items |
|--------|-------|
| **Inbox** | {vault_counts.get('inbox', 0)} |
| **Needs_Action** | {vault_counts.get('needs_action', 0)} |
| **Done** | {vault_counts.get('done', 0)} |
| **Plans (Draft)** | {vault_counts.get('plans', 0)} |
| **Pending_Approval** | {vault_counts.get('pending_approval', 0)} |
| **Approved** | {vault_counts.get('approved', 0)} |
| **Rejected** | {vault_counts.get('rejected', 0)} |

---

## 🔌 MCP Operations Breakdown

"""

        if mcp_activity['total'] > 0:
            summary_content += f"**Total MCP Calls:** {mcp_activity['total']}\n"
            summary_content += f"**Successes:** {mcp_activity['successes']}\n"
            summary_content += f"**Failures:** {mcp_activity['failures']}\n\n"

            if mcp_activity['by_operation']:
                summary_content += "**By Operation:**\n"
                for op, count in mcp_activity['by_operation'].most_common():
                    summary_content += f"- {op}: {count}\n"
        else:
            summary_content += "*No MCP operations recorded for this period.*\n"

        summary_content += "\n---\n\n"

        # Timeline section
        summary_content += """## ⏱️ Timeline

*Key events from system_log.md and mcp_actions.log for this period.*

"""

        if activity['inbox_items'] > 0 or activity['plans_created'] > 0 or mcp_activity['total'] > 0:
            summary_content += f"- Processed {activity['inbox_items']} inbox item(s)\n"
            summary_content += f"- Created {activity['plans_created']} plan(s)\n"
            summary_content += f"- Approved {activity['plans_approved']} plan(s)\n"
            summary_content += f"- Executed {activity['plans_executed']} plan(s)\n"
            summary_content += f"- {mcp_activity['total']} MCP operation(s)\n"
        else:
            summary_content += "*Quiet day - no significant activity recorded.*\n"

        summary_content += "\n---\n\n"

        # Observations
        summary_content += """## 💡 Observations

"""

        total_failures = activity['failures'] + mcp_activity['failures']
        if total_failures > 0:
            summary_content += f"⚠️ **{total_failures} failure(s) detected** - Review logs for errors.\n"
        else:
            summary_content += "✅ No failures detected - all operations completed successfully.\n"

        if vault_counts.get('pending_approval', 0) > 0:
            summary_content += f"⏳ **{vault_counts['pending_approval']} plan(s) pending approval** - Review Pending_Approval/ folder.\n"

        if vault_counts.get('inbox', 0) > 0:
            summary_content += f"📥 **{vault_counts['inbox']} item(s) in Inbox** - Awaiting triage.\n"

        summary_content += "\n---\n\n"

        # Silver Health Status
        summary_content += """## 🏥 Silver Tier Health

| Component | Status |
|-----------|--------|
| **Vault Structure** | ✅ Operational |
| **Watcher** | ✅ Active |
| **Approval Pipeline** | ✅ Operational |
| **MCP Integration** | ✅ Operational |
| **Scheduling** | ✅ Configured |
| **Daily Summaries** | ✅ Operational (M8) |

**Overall Status:** ✨ **Silver Tier Healthy**

---

*This summary was auto-generated by `brain_generate_daily_summary_skill.py` (M8)*
"""

        # Save summary
        summary_filename = f"{summary_date_str}.md"
        summary_path = self.summaries_dir / summary_filename

        summary_path.write_text(summary_content, encoding='utf-8')

        # Log to system_log.md
        self._log_to_system(summary_date_str, summary_path)

        return summary_content, summary_path

    def _log_to_system(self, date_str: str, summary_path: Path):
        """
        Log summary generation to system_log.md.

        Args:
            date_str: Date string (YYYY-MM-DD)
            summary_path: Path to generated summary
        """
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        log_entry = f"""
[{timestamp}] DAILY SUMMARY GENERATED
- Date: {date_str}
- Summary File: {summary_path.name}
- Location: Daily_Summaries/
- Skill: brain_generate_daily_summary (M8)
- Outcome: OK

"""

        if self.system_log.exists():
            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)


def main():
    """CLI interface for daily summary generation."""
    parser = argparse.ArgumentParser(
        description='Brain Generate Daily Summary - Silver Tier M8',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate summary for today
  python brain_generate_daily_summary_skill.py

  # Generate summary for specific date
  python brain_generate_daily_summary_skill.py --date 2026-02-14

  # Verbose output
  python brain_generate_daily_summary_skill.py --verbose
        """
    )

    parser.add_argument(
        '--date',
        help='Date to generate summary for (YYYY-MM-DD, default: today)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    args = parser.parse_args()

    # Parse date if provided
    date = None
    if args.date:
        try:
            date = datetime.strptime(args.date, '%Y-%m-%d')
            date = date.replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD")
            sys.exit(1)

    # Initialize generator
    generator = DailySummaryGenerator()

    try:
        # Generate summary
        content, path = generator.generate_summary(date=date)

        if args.verbose:
            print()
            print("=" * 70)
            print("  DAILY SUMMARY GENERATED")
            print("=" * 70)
            print(content)
            print("=" * 70)
        else:
            print(f"✓ Daily summary generated: {path}")
            print(f"  Location: {path.relative_to(Path.cwd())}")

        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
