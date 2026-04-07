#!/usr/bin/env python3
"""
Personal AI Employee - brain_request_approval Skill
Silver Tier - M5: Human-in-the-Loop Approval Pipeline

Purpose: Request user approval for plans before execution.
Tier: Silver
Skill ID: 17

CRITICAL: This skill requests approval but does NOT execute actions.
Execution requires M6 (brain_execute_with_mcp).
"""

import os
import sys
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class ApprovalRequest:
    """Request user approval for plan execution."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize approval request handler.

        Args:
            config: Optional configuration dict with paths
        """
        self.config = config or self._default_config()
        self.plans_dir = Path(self.config['plans_dir'])
        self.pending_dir = Path(self.config['pending_approval_dir'])
        self.system_log = Path(self.config['system_log'])

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

    def read_plan(self, plan_path: Path) -> Dict:
        """
        Read and parse plan file.

        Args:
            plan_path: Path to plan file

        Returns:
            Dict with plan metadata and content
        """
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")

        content = plan_path.read_text(encoding='utf-8')

        # Extract header metadata
        metadata = {}
        lines = content.split('\n')

        # Extract title (first # heading)
        title_match = re.search(r'^# Plan: (.+)$', content, re.MULTILINE)
        title = title_match.group(1) if title_match else plan_path.stem

        # Extract metadata from header section
        for line in lines[1:20]:  # Check first 20 lines
            if line.startswith('**') and ':**' in line:
                key_match = re.match(r'\*\*(.+?):\*\*\s*(.+)', line)
                if key_match:
                    key = key_match.group(1).strip()
                    value = key_match.group(2).strip()
                    metadata[key.lower().replace(' ', '_')] = value

        # Extract objective
        obj_match = re.search(r'## Objective\n\n(.+?)(?=\n##|\Z)', content, re.DOTALL)
        objective = obj_match.group(1).strip() if obj_match else "No objective specified"

        # Extract MCP tools
        mcp_section_match = re.search(
            r'## MCP Tools Required\n\n(.*?)(?=\n##|\Z)',
            content,
            re.DOTALL
        )
        mcp_tools = []
        if mcp_section_match:
            mcp_content = mcp_section_match.group(1)
            # Parse table rows
            for line in mcp_content.split('\n'):
                if line.startswith('|') and 'Tool Name' not in line and '---' not in line:
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if len(parts) >= 3 and parts[0] and parts[0] != 'Tool Name':
                        mcp_tools.append({
                            'tool': parts[0],
                            'operation': parts[1],
                            'parameters': parts[2] if len(parts) > 2 else ''
                        })

        return {
            'title': title,
            'file_path': str(plan_path),
            'metadata': metadata,
            'objective': objective,
            'mcp_tools': mcp_tools,
            'full_content': content,
            'risk_level': metadata.get('risk_level', 'Unknown'),
            'status': metadata.get('status', 'Unknown')
        }

    def create_approval_request(self, plan_info: Dict) -> Path:
        """
        Create approval request file in Pending_Approval/.

        Args:
            plan_info: Dict from read_plan()

        Returns:
            Path to created approval request file
        """
        # Ensure Pending_Approval/ exists
        self.pending_dir.mkdir(parents=True, exist_ok=True)

        # Generate approval request filename
        plan_path = Path(plan_info['file_path'])
        plan_id = plan_path.stem

        # Remove PLAN_ prefix if present
        if plan_id.startswith('PLAN_'):
            plan_id = plan_id[5:]

        approval_filename = f"ACTION_{plan_id}.md"
        approval_path = self.pending_dir / approval_filename

        # Generate approval request content
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        # Build MCP tools list
        mcp_tools_text = ""
        if plan_info['mcp_tools']:
            for tool in plan_info['mcp_tools']:
                mcp_tools_text += f"- {tool['tool']}.{tool['operation']}: {tool['parameters']}\n"
        else:
            mcp_tools_text = "- None specified (may be read-only or internal operations)\n"

        approval_content = f"""---
action_type: plan_execution
related_plan: {plan_path.name}
plan_id: {plan_id}
requested_at: {now_utc}
risk_level: {plan_info['risk_level']}
status: pending
---

# Approval Request: {plan_info['title']}

**Created:** {now_utc}
**Status:** Pending User Approval
**Related Plan:** [{plan_path.name}](../Plans/{plan_path.name})

---

## What Will Happen

**Objective:** {plan_info['objective']}

**Risk Level:** {plan_info['risk_level']}

---

## MCP Actions Required

{mcp_tools_text}
---

## Approval Instructions

**To APPROVE this plan:**
1. Review the full plan file at: `Plans/{plan_path.name}`
2. Move this approval request file to: `Approved/` folder
3. The system will detect the approval and mark the plan as ready for execution

**To REJECT this plan:**
1. Move this approval request file to: `Rejected/` folder
2. The plan will be marked as rejected and archived

**IMPORTANT:**
- Approval is file-based (no command-line approval)
- All external actions require explicit user approval
- Moving file to Approved/ does NOT execute the plan (requires M6)
- This is a safety gate to prevent unauthorized external actions

---

## Next Steps After Approval

1. Plan status will be updated to: `Approved`
2. Plan will remain in `Approved/` folder
3. Execute with: `brain_execute_with_mcp` (M6 - not yet implemented)
4. Execution will include:
   - Dry-run phase (preview only, no real actions)
   - User confirmation after dry-run
   - Real execution with full logging

---

**Approval Required By:** User (manual file move)
**Requested:** {now_utc}
"""

        # Write approval request file
        approval_path.write_text(approval_content, encoding='utf-8')

        return approval_path

    def update_plan_status(self, plan_path: Path, new_status: str) -> None:
        """
        Update plan file status.

        Args:
            plan_path: Path to plan file
            new_status: New status value
        """
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")

        content = plan_path.read_text(encoding='utf-8')

        # Update Status in header
        status_pattern = r'(\*\*Status:\*\*\s*)\[?([^\]\n]+)\]?'
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

        plan_path.write_text(new_content, encoding='utf-8')

    def request_approval(self, plan_path: Path, move_plan: bool = False) -> Dict:
        """
        Request user approval for a plan.

        Args:
            plan_path: Path to plan file in Plans/
            move_plan: If True, move plan to Pending_Approval/ (default: False)

        Returns:
            Dict with approval request details
        """
        # Read plan
        plan_info = self.read_plan(plan_path)

        # Create approval request
        approval_path = self.create_approval_request(plan_info)

        # Update plan status
        self.update_plan_status(plan_path, 'Pending_Approval')

        # Optionally move plan to Pending_Approval/
        if move_plan:
            new_plan_path = self.pending_dir / plan_path.name
            plan_path.rename(new_plan_path)
            plan_path = new_plan_path

        # Display approval request to console
        self._display_approval_request(plan_info, approval_path)

        # Log approval request
        self._log_approval_request(plan_info, approval_path)

        return {
            'plan': str(plan_path),
            'approval_request': str(approval_path),
            'status': 'Pending_Approval',
            'risk_level': plan_info['risk_level']
        }

    def _display_approval_request(self, plan_info: Dict, approval_path: Path):
        """Display formatted approval request to console."""
        print()
        print("═" * 70)
        print("  APPROVAL REQUIRED")
        print("═" * 70)
        print(f"Plan: {plan_info['title']}")
        print(f"File: {approval_path.relative_to(Path.cwd())}")
        print(f"Risk Level: {plan_info['risk_level']}")
        print()
        print(f"Objective: {plan_info['objective']}")
        print()
        if plan_info['mcp_tools']:
            print("MCP Actions Required:")
            for tool in plan_info['mcp_tools']:
                print(f"  - {tool['tool']}.{tool['operation']}: {tool['parameters']}")
        else:
            print("MCP Actions: None (internal operations only)")
        print()
        print("To approve: Move file to Approved/ folder")
        print("To reject:  Move file to Rejected/ folder")
        print("─" * 70)
        print()

    def _log_approval_request(self, plan_info: Dict, approval_path: Path):
        """Log approval request to system_log.md."""
        log_entry = f"""
[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] APPROVAL REQUESTED
- Plan: {plan_info['title']}
- Plan ID: {Path(plan_info['file_path']).stem}
- Approval File: {approval_path.name}
- Risk Level: {plan_info['risk_level']}
- Status: Draft → Pending_Approval
- Skill: brain_request_approval (M5)
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
    """CLI interface for brain_request_approval skill."""
    parser = argparse.ArgumentParser(
        description='Brain Request Approval - Request user approval for plan execution',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Request approval for a plan
  python brain_request_approval_skill.py --plan Plans/PLAN_20260212-0336__inbox_gmail_20260211-1612_mock001a.md

  # Request approval and move plan to Pending_Approval/
  python brain_request_approval_skill.py \\
      --plan Plans/PLAN_20260212-0336__manual_test_schedule_meeting.md \\
      --move-plan
        """
    )

    parser.add_argument(
        '--plan',
        required=True,
        help='Path to plan file in Plans/'
    )
    parser.add_argument(
        '--move-plan',
        action='store_true',
        help='Move plan file to Pending_Approval/ folder'
    )

    args = parser.parse_args()

    # Initialize approval request handler
    handler = ApprovalRequest()

    # Request approval
    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"Error: Plan file not found: {plan_path}")
        sys.exit(1)

    try:
        result = handler.request_approval(plan_path, move_plan=args.move_plan)

        print(f"✓ Approval request created successfully")
        print(f"  Plan: {result['plan']}")
        print(f"  Approval Request: {result['approval_request']}")
        print(f"  Status: {result['status']}")
        print(f"  Risk Level: {result['risk_level']}")
        print()
        print(f"Next steps:")
        print(f"  1. Review the plan at: {result['plan']}")
        print(f"  2. Review the approval request at: {result['approval_request']}")
        print(f"  3. Move approval file to Approved/ to execute, or Rejected/ to cancel")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
