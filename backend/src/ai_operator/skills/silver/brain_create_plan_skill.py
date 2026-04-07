#!/usr/bin/env python3
"""
Personal AI Employee - brain_create_plan Skill
Silver Tier - M4: Plan-First Workflow

Purpose: Generate detailed plan files for external actions requiring approval.
Tier: Silver
Skill ID: 16

CRITICAL: This skill creates plans but does NOT execute them.
Execution requires approval (M5) and MCP integration (M6).
"""

import os
import sys
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List


class PlanGenerator:
    """Generates structured plan files from task descriptions."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize plan generator with configuration.

        Args:
            config: Optional configuration dict with paths and settings
        """
        self.config = config or self._default_config()
        self.template_path = Path(self.config['template_path'])
        self.plans_dir = Path(self.config['plans_dir'])
        self.system_log = Path(self.config['system_log'])

    def _default_config(self) -> Dict:
        """Return default configuration."""
        base_dir = get_repo_root()
        return {
            'template_path': base_dir / 'templates' / 'plan_template.md',
            'plans_dir': base_dir / 'Plans',
            'system_log': base_dir / 'system_log.md',
        }

    def generate_plan_id(self, task_slug: str) -> str:
        """
        Generate unique plan ID.

        Format: PLAN_<YYYYMMDD-HHMM>__<slug>

        Args:
            task_slug: Short identifier for the task (e.g., 'reply_email')

        Returns:
            Unique plan ID string
        """
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
        # Sanitize slug
        safe_slug = re.sub(r'[^a-z0-9_-]', '_', task_slug.lower())
        safe_slug = re.sub(r'_+', '_', safe_slug).strip('_')
        return f"PLAN_{timestamp}__{safe_slug}"

    def requires_plan(self, task_description: str, task_metadata: Optional[Dict] = None) -> bool:
        """
        Determine if a task requires a plan based on criteria from spec.

        Plans MUST be created for:
        1. External communication (emails, GitHub, social media)
        2. MCP tool invocations with side effects
        3. File operations outside Done/ folder (destructive/risky)
        4. Scheduled tasks (automation requires documentation)
        5. Multi-step tasks (>3 actions with dependencies)

        Args:
            task_description: Full task description text
            task_metadata: Optional metadata dict (priority, risk, etc.)

        Returns:
            True if plan required, False otherwise
        """
        desc_lower = task_description.lower()

        # Check for external communication keywords
        external_keywords = [
            'email', 'send', 'reply', 'forward', 'post', 'publish',
            'github', 'issue', 'pr', 'pull request', 'commit', 'push',
            'tweet', 'slack', 'message', 'notify'
        ]
        if any(kw in desc_lower for kw in external_keywords):
            return True

        # Check for MCP keywords
        mcp_keywords = ['mcp', 'api call', 'external api', 'webhook']
        if any(kw in desc_lower for kw in mcp_keywords):
            return True

        # Check for file operations (outside Done/)
        file_ops = ['delete', 'remove', 'modify', 'update file', 'change file']
        if any(op in desc_lower for op in file_ops):
            return True

        # Check for scheduling keywords
        if 'schedule' in desc_lower or 'automate' in desc_lower:
            return True

        # Check metadata for risk level
        if task_metadata:
            if task_metadata.get('risk_level') in ['Medium', 'High']:
                return True
            if task_metadata.get('approval_required', False):
                return True

        # Count steps (heuristic: numbered lists or bullet points with verbs)
        step_patterns = [
            r'^\d+\.',  # Numbered lists
            r'^-\s+\w+',  # Bullet points with actions
        ]
        steps = 0
        for line in task_description.split('\n'):
            line = line.strip()
            for pattern in step_patterns:
                if re.match(pattern, line):
                    steps += 1
                    break

        if steps > 3:
            return True

        return False

    def extract_task_info(self, task_file_path: Path) -> Dict:
        """
        Extract task information from a task file.

        Args:
            task_file_path: Path to task file in Needs_Action/

        Returns:
            Dict with extracted task info
        """
        if not task_file_path.exists():
            raise FileNotFoundError(f"Task file not found: {task_file_path}")

        content = task_file_path.read_text(encoding='utf-8')

        # Extract YAML frontmatter if present
        metadata = {}
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                yaml_content = parts[1].strip()
                for line in yaml_content.split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        metadata[key.strip()] = value.strip()

        # Extract title (first # heading or filename)
        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = title_match.group(1) if title_match else task_file_path.stem.replace('_', ' ').title()

        # Extract summary (content before first ##)
        summary_match = re.search(r'---\n\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else content[:500]

        return {
            'title': title,
            'summary': summary,
            'file_path': str(task_file_path),
            'metadata': metadata,
            'full_content': content
        }

    def create_plan(
        self,
        task_info: Dict,
        objective: str,
        risk_level: str = 'Medium',
        status: str = 'Draft'
    ) -> Path:
        """
        Create a plan file from task information.

        Args:
            task_info: Dict from extract_task_info()
            objective: One-sentence goal statement
            risk_level: Low/Medium/High
            status: Initial status (typically 'Draft')

        Returns:
            Path to created plan file
        """
        # Load template
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template not found: {self.template_path}")

        template = self.template_path.read_text(encoding='utf-8')

        # Generate plan ID and filename
        task_slug = Path(task_info['file_path']).stem
        plan_id = self.generate_plan_id(task_slug)
        plan_filename = f"{plan_id}.md"
        plan_path = self.plans_dir / plan_filename

        # Ensure Plans/ directory exists
        self.plans_dir.mkdir(parents=True, exist_ok=True)

        # Fill template with actual values
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        plan_content = template.replace('[Task Title]', task_info['title'])
        plan_content = plan_content.replace('[YYYY-MM-DD HH:MM UTC]', now_utc)
        plan_content = plan_content.replace(
            '[Draft / Pending_Approval / Approved / Rejected / Executed]',
            status
        )
        plan_content = plan_content.replace(
            '[Link to Needs_Action/<task-file>.md]',
            f"[{Path(task_info['file_path']).name}]({task_info['file_path']})"
        )
        plan_content = plan_content.replace('[Low / Medium / High]', risk_level)
        plan_content = plan_content.replace(
            '[One-sentence goal statement describing what this plan will achieve]',
            objective
        )

        # Add source task summary to Inputs/Context section
        plan_content = plan_content.replace(
            '[Description of originating task]',
            task_info['summary'][:200] + '...' if len(task_info['summary']) > 200 else task_info['summary']
        )
        plan_content = plan_content.replace(
            '[Email/intake source]',
            task_info['metadata'].get('from', 'Unknown')
        )

        # Write plan file
        plan_path.write_text(plan_content, encoding='utf-8')

        # Log creation
        self._log_plan_creation(plan_id, task_info['title'], status)

        return plan_path

    def _log_plan_creation(self, plan_id: str, title: str, status: str):
        """Log plan creation to system_log.md."""
        log_entry = f"""
[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] PLAN CREATED
- Plan ID: {plan_id}
- Title: {title}
- Status: {status}
- Skill: brain_create_plan (M4)
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
    """CLI interface for brain_create_plan skill."""
    parser = argparse.ArgumentParser(
        description='Brain Create Plan - Generate structured plan files for external actions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create plan from task file
  python brain_create_plan_skill.py --task Needs_Action/inbox__gmail__20260211-1612__mock001a.md \\
      --objective "Reply to Q1 hackathon update email"

  # Create plan with custom risk level
  python brain_create_plan_skill.py --task Needs_Action/test_task.md \\
      --objective "Send test email" \\
      --risk-level High \\
      --status Pending_Approval

  # Check if task requires a plan
  python brain_create_plan_skill.py --task Needs_Action/test_task.md --check-only
        """
    )

    parser.add_argument(
        '--task',
        required=True,
        help='Path to task file in Needs_Action/'
    )
    parser.add_argument(
        '--objective',
        help='One-sentence goal statement for the plan'
    )
    parser.add_argument(
        '--risk-level',
        choices=['Low', 'Medium', 'High'],
        default='Medium',
        help='Risk level for the plan (default: Medium)'
    )
    parser.add_argument(
        '--status',
        choices=['Draft', 'Pending_Approval'],
        default='Draft',
        help='Initial status (default: Draft)'
    )
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Only check if plan is required, do not create'
    )

    args = parser.parse_args()

    # Initialize generator
    generator = PlanGenerator()

    # Extract task info
    task_path = Path(args.task)
    if not task_path.exists():
        print(f"Error: Task file not found: {task_path}")
        sys.exit(1)

    try:
        task_info = generator.extract_task_info(task_path)

        # Check if plan required
        plan_required = generator.requires_plan(
            task_info['full_content'],
            task_info['metadata']
        )

        if args.check_only:
            print(f"Task: {task_info['title']}")
            print(f"Plan Required: {'YES' if plan_required else 'NO'}")
            if plan_required:
                print("Reason: Task involves external actions, MCP calls, or multiple steps")
            else:
                print("Reason: Task is internal, read-only, or simple")
            sys.exit(0 if plan_required else 1)

        # Require objective if creating plan
        if not args.objective:
            print("Error: --objective required when creating a plan")
            print(f"Suggested objective: {task_info['title']}")
            sys.exit(1)

        # Create plan
        plan_path = generator.create_plan(
            task_info=task_info,
            objective=args.objective,
            risk_level=args.risk_level,
            status=args.status
        )

        print(f"✓ Plan created successfully")
        print(f"  Plan ID: {plan_path.stem}")
        print(f"  Path: {plan_path}")
        print(f"  Status: {args.status}")
        print(f"  Risk Level: {args.risk_level}")
        print()
        print(f"Next steps:")
        if args.status == 'Draft':
            print(f"  1. Review and refine the plan at: {plan_path}")
            print(f"  2. When ready, change Status to 'Pending_Approval'")
            print(f"  3. Move plan to Pending_Approval/ folder for user review")
        else:
            print(f"  1. Plan is ready for approval")
            print(f"  2. Move to Pending_Approval/ folder for user review")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
