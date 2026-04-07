#!/usr/bin/env python3
"""
Personal AI Employee - brain_ralph_loop_orchestrator Skill
Gold Tier - G-M7: Ralph Loop Autonomous Orchestrator

Purpose: Bounded autonomous loop that observes vault state, decides next best actions,
creates plans (never executes directly), respects approval gates, and logs all decisions.

Tier: Gold
Skill ID: G-M7-T01

CRITICAL SAFETY:
- Never bypasses approval gates
- Never executes actions directly
- Bounded iterations (max 10)
- Bounded plans per iteration (max 5)
- Timeout per iteration (5 minutes)
- Stops immediately when approval required
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class RalphLoopOrchestrator:
    """Bounded autonomous orchestrator with approval gate respect."""

    def __init__(self, dry_run: bool = True, max_iterations: int = 10,
                 max_plans_per_iteration: int = 5):
        """
        Initialize Ralph loop orchestrator.

        Args:
            dry_run: If True, show decisions without creating plans
            max_iterations: Maximum loop iterations (default 10)
            max_plans_per_iteration: Maximum plans per iteration (default 5)
        """
        self.dry_run = dry_run
        self.max_iterations = max_iterations
        self.max_plans_per_iteration = max_plans_per_iteration
        self.iteration_timeout = 300  # 5 minutes per iteration in seconds

        # Paths
        self.base_dir = Path(__file__).parent
        self.social_inbox = self.base_dir / 'Social' / 'Inbox'
        self.business_accounting = self.base_dir / 'Business' / 'Accounting'
        self.needs_action = self.base_dir / 'Needs_Action'
        self.pending_approval = self.base_dir / 'Pending_Approval'
        self.plans_dir = self.base_dir / 'Plans'
        self.briefings_dir = self.base_dir / 'Business' / 'Briefings'
        self.system_log = self.base_dir / 'system_log.md'
        self.ralph_log = self.base_dir / 'Logs' / 'ralph_loop.log'

        # State
        self.current_iteration = 0
        self.total_plans_created = 0
        self.halt_reason = None
        self.decisions_made = []

    def _ensure_logs_dir(self):
        """Ensure Logs directory exists."""
        logs_dir = self.base_dir / 'Logs'
        logs_dir.mkdir(exist_ok=True)

    def _log_to_file(self, message: str, log_file: Path):
        """Append timestamped message to log file."""
        timestamp = datetime.now(timezone.utc).isoformat()
        log_entry = f"[{timestamp}] {message}\n"

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    def _log_iteration(self, iteration: int, status: str, decisions: int, plans: int, reason: str = ""):
        """Log iteration summary to ralph_loop.log."""
        message = f"Iteration {iteration}/{self.max_iterations} | Status: {status} | Decisions: {decisions} | Plans: {plans}"
        if reason:
            message += f" | Reason: {reason}"

        self._log_to_file(message, self.ralph_log)
        print(f"  📊 {message}")

    def _log_to_system(self, message: str):
        """Append entry to system_log.md."""
        timestamp = datetime.now(timezone.utc).isoformat()
        log_entry = f"- **{timestamp}**: Ralph Loop - {message}\n"

        with open(self.system_log, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    def _scan_vault_state(self) -> Dict:
        """
        Scan vault directories for actionable items.

        Returns:
            Dict with counts and items from each source
        """
        state = {
            'social_inbox': [],
            'accounting_intake': [],
            'needs_action': [],
            'pending_approval': [],
            'latest_ceo_briefing': None,
            'scan_timestamp': datetime.now(timezone.utc).isoformat()
        }

        # Scan Social/Inbox/
        if self.social_inbox.exists():
            for item in self.social_inbox.glob('inbox__*.md'):
                age_hours = self._get_file_age_hours(item)
                state['social_inbox'].append({
                    'file': item.name,
                    'path': str(item),
                    'age_hours': age_hours
                })

        # Scan Business/Accounting/ for intake wrappers
        if self.business_accounting.exists():
            for item in self.business_accounting.glob('intake__*.md'):
                state['accounting_intake'].append({
                    'file': item.name,
                    'path': str(item)
                })

        # Scan Needs_Action/
        if self.needs_action.exists():
            for item in self.needs_action.glob('*.md'):
                state['needs_action'].append({
                    'file': item.name,
                    'path': str(item)
                })

        # Check Pending_Approval/ (critical: stops loop if non-empty)
        if self.pending_approval.exists():
            approval_files = [f for f in self.pending_approval.glob('*.md') if f.name != 'README.md']
            state['pending_approval'] = [f.name for f in approval_files]

        # Get latest CEO briefing (optional context)
        if self.briefings_dir.exists():
            briefings = sorted(self.briefings_dir.glob('CEO_Briefing__*.md'))
            if briefings:
                state['latest_ceo_briefing'] = str(briefings[-1])

        return state

    def _get_file_age_hours(self, file_path: Path) -> float:
        """Calculate file age in hours from modification time."""
        if not file_path.exists():
            return 0.0

        mtime = file_path.stat().st_mtime
        age_seconds = time.time() - mtime
        return age_seconds / 3600

    def _decide_next_actions(self, state: Dict) -> List[Dict]:
        """
        Decide next best actions based on vault state using mock heuristic.

        Decision Logic (Priority Order):
        1. If Pending_Approval exists → STOP loop immediately
        2. If failure remediation exists in Needs_Action → prioritize
        3. If overdue invoice wrapper exists → create plan to follow up
        4. If social intake pending > 24h → create plan to respond
        5. If high outstanding AR % (>40%) from CEO briefing → create plan to review

        Args:
            state: Vault state from _scan_vault_state()

        Returns:
            List of action decisions (each is a Dict with action details)
        """
        decisions = []

        # CRITICAL: Stop if approval pending
        if state['pending_approval']:
            self.halt_reason = f"Approval required ({len(state['pending_approval'])} pending)"
            return []

        # Priority 1: Failure remediation tasks
        for item in state['needs_action']:
            if 'remediation' in item['file'].lower() or 'failed' in item['file'].lower():
                decisions.append({
                    'priority': 'high',
                    'action_type': 'remediation',
                    'source_file': item['file'],
                    'task_slug': 'remediate_failure',
                    'description': f"Address failure remediation in {item['file']}"
                })
                if len(decisions) >= self.max_plans_per_iteration:
                    break

        # Priority 2: Overdue invoices (from accounting intake)
        if len(decisions) < self.max_plans_per_iteration:
            for item in state['accounting_intake']:
                if 'overdue' in item['file'].lower() or 'invoice' in item['file'].lower():
                    decisions.append({
                        'priority': 'medium',
                        'action_type': 'invoice_followup',
                        'source_file': item['file'],
                        'task_slug': 'followup_overdue_invoice',
                        'description': f"Follow up on overdue invoice from {item['file']}"
                    })
                    if len(decisions) >= self.max_plans_per_iteration:
                        break

        # Priority 3: Social intake pending > 24h
        if len(decisions) < self.max_plans_per_iteration:
            for item in state['social_inbox']:
                if item['age_hours'] > 24:
                    decisions.append({
                        'priority': 'medium',
                        'action_type': 'social_response',
                        'source_file': item['file'],
                        'task_slug': 'respond_social_message',
                        'description': f"Respond to social message in {item['file']} (age: {item['age_hours']:.1f}h)"
                    })
                    if len(decisions) >= self.max_plans_per_iteration:
                        break

        # Priority 4: High AR % from CEO briefing (optional context)
        if len(decisions) < self.max_plans_per_iteration and state['latest_ceo_briefing']:
            # Parse CEO briefing for AR % (mock: assume >40% if "⚠️" in file)
            try:
                with open(state['latest_ceo_briefing'], 'r', encoding='utf-8') as f:
                    content = f.read()
                    if '⚠️' in content and 'AR' in content:
                        decisions.append({
                            'priority': 'low',
                            'action_type': 'review_ar',
                            'source_file': 'CEO_Briefing',
                            'task_slug': 'review_accounts_receivable',
                            'description': 'Review high AR percentage from CEO briefing'
                        })
            except Exception:
                pass  # Ignore briefing parsing errors

        return decisions

    def _create_plan(self, decision: Dict) -> Tuple[bool, str]:
        """
        Create plan file for a decision using brain_create_plan pattern.

        Args:
            decision: Decision dict with task_slug, description, etc.

        Returns:
            Tuple of (success: bool, plan_filename: str)
        """
        # Generate plan ID
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
        task_slug = decision['task_slug']
        plan_id = f"plan__{task_slug}__{timestamp}"
        plan_filename = f"{plan_id}.md"
        plan_path = self.plans_dir / plan_filename

        # Ensure Plans/ directory exists
        self.plans_dir.mkdir(exist_ok=True)

        # Plan content
        plan_content = f"""---
plan_id: {plan_id}
created_at: {datetime.now(timezone.utc).isoformat()}
created_by: brain_ralph_loop_orchestrator
priority: {decision['priority']}
action_type: {decision['action_type']}
source_file: {decision['source_file']}
status: pending_approval
approval_required: true
---

# Plan: {decision['description']}

## Context

**Generated by**: Ralph Loop Orchestrator (G-M7)
**Source**: {decision['source_file']}
**Priority**: {decision['priority']}

## Task Description

{decision['description']}

## Proposed Actions

1. Review source file: `{decision['source_file']}`
2. Determine appropriate action (response, follow-up, remediation)
3. Execute action via appropriate skill (social executor, Odoo executor, etc.)
4. Log outcome to system_log.md
5. Archive this plan to Plans/completed/

## Approval Requirements

**CRITICAL**: This plan requires human approval before execution.
- [ ] Reviewed by human
- [ ] Actions approved
- [ ] Ready for execution

## Risk Assessment

**Risk Level**: {decision['priority']}
**Blast Radius**: Limited to single action
**Rollback**: Cancel plan if not approved within 48h

## Notes

- Created by autonomous Ralph loop
- Iteration: {self.current_iteration}/{self.max_iterations}
- Plan count this iteration: {len(self.decisions_made) + 1}/{self.max_plans_per_iteration}
"""

        # Write plan file
        try:
            with open(plan_path, 'w', encoding='utf-8') as f:
                f.write(plan_content)
            return True, plan_filename
        except Exception as e:
            print(f"  ❌ Failed to create plan: {e}")
            return False, ""

    def _move_plan_to_pending_approval(self, plan_filename: str) -> bool:
        """
        Move plan to Pending_Approval/ directory.

        Args:
            plan_filename: Name of plan file in Plans/

        Returns:
            True if moved successfully
        """
        plan_path = self.plans_dir / plan_filename
        approval_path = self.pending_approval / plan_filename

        # Ensure Pending_Approval/ exists
        self.pending_approval.mkdir(exist_ok=True)

        try:
            # Copy to Pending_Approval/ (keep original in Plans/ for audit trail)
            with open(plan_path, 'r', encoding='utf-8') as f:
                content = f.read()

            with open(approval_path, 'w', encoding='utf-8') as f:
                f.write(content)

            return True
        except Exception as e:
            print(f"  ❌ Failed to move plan to Pending_Approval: {e}")
            return False

    def run(self):
        """
        Execute Ralph loop with bounded iterations and approval gate respect.

        Returns:
            Dict with execution summary
        """
        print("\n" + "="*70)
        print("Ralph Loop Autonomous Orchestrator (G-M7)")
        print("="*70)
        print(f"Mode: {'DRY-RUN (no plans created)' if self.dry_run else 'EXECUTE (plans will be created)'}")
        print(f"Max Iterations: {self.max_iterations}")
        print(f"Max Plans per Iteration: {self.max_plans_per_iteration}")
        print(f"Timeout per Iteration: {self.iteration_timeout}s ({self.iteration_timeout // 60} min)")
        print("="*70 + "\n")

        # Ensure log directory exists
        self._ensure_logs_dir()

        # Log start
        self._log_to_system(f"Started (dry_run={self.dry_run}, max_iter={self.max_iterations})")
        self._log_to_file(f"=== Ralph Loop Started (dry_run={self.dry_run}) ===", self.ralph_log)

        # Main loop
        for iteration in range(1, self.max_iterations + 1):
            self.current_iteration = iteration
            iteration_start_time = time.time()

            print(f"\n🔄 Iteration {iteration}/{self.max_iterations}")
            print("-" * 70)

            # Step 1: Scan vault state
            print("  📂 Scanning vault state...")
            state = self._scan_vault_state()

            print(f"     - Social Inbox: {len(state['social_inbox'])} items")
            print(f"     - Accounting Intake: {len(state['accounting_intake'])} items")
            print(f"     - Needs Action: {len(state['needs_action'])} items")
            print(f"     - Pending Approval: {len(state['pending_approval'])} items")
            if state['latest_ceo_briefing']:
                print(f"     - Latest CEO Briefing: {Path(state['latest_ceo_briefing']).name}")

            # Step 2: Check for approval gate (CRITICAL: stops loop)
            if state['pending_approval']:
                print(f"\n  ⏸️  HALTED: {len(state['pending_approval'])} approval(s) pending")
                print("     Ralph loop respects HITL gates. Resume after approval.")
                self.halt_reason = f"Approval required ({len(state['pending_approval'])} pending)"
                self._log_iteration(iteration, "HALTED", 0, 0, self.halt_reason)
                self._log_to_system(f"Halted at iteration {iteration}: {self.halt_reason}")
                break

            # Step 3: Decide next actions
            print("  🧠 Deciding next actions...")
            decisions = self._decide_next_actions(state)

            if not decisions:
                print("     ℹ️  No actionable items found")
                self._log_iteration(iteration, "NO_ACTION", 0, 0, "No actionable items")

                # If no actions for 2 consecutive iterations, exit gracefully
                if iteration > 1:
                    print("\n  ✅ No work remaining. Loop complete.")
                    self.halt_reason = "No actionable items remaining"
                    self._log_to_system(f"Completed at iteration {iteration}: {self.halt_reason}")
                    break
                continue

            print(f"     ✓ {len(decisions)} decision(s) made:")
            for idx, decision in enumerate(decisions, 1):
                print(f"       {idx}. [{decision['priority'].upper()}] {decision['description']}")

            self.decisions_made.extend(decisions)

            # Step 4: Create plans (or show dry-run)
            plans_created = 0
            if self.dry_run:
                print(f"\n  🔍 DRY-RUN: Would create {len(decisions)} plan(s)")
                for idx, decision in enumerate(decisions, 1):
                    print(f"     {idx}. Plan slug: {decision['task_slug']}")
                    print(f"        Description: {decision['description']}")
            else:
                print(f"\n  📝 Creating {len(decisions)} plan(s)...")
                for idx, decision in enumerate(decisions, 1):
                    success, plan_filename = self._create_plan(decision)
                    if success:
                        # Move to Pending_Approval
                        if self._move_plan_to_pending_approval(plan_filename):
                            plans_created += 1
                            self.total_plans_created += 1
                            print(f"     ✓ Created: {plan_filename} → Pending_Approval/")
                        else:
                            print(f"     ⚠️  Created {plan_filename} but failed to move to Pending_Approval")
                    else:
                        print(f"     ❌ Failed to create plan for: {decision['description']}")
                        self.halt_reason = "Plan creation failed"
                        self._log_iteration(iteration, "FAILED", len(decisions), plans_created, self.halt_reason)
                        self._log_to_system(f"Failed at iteration {iteration}: {self.halt_reason}")
                        break

                # If plan creation failed, stop loop
                if self.halt_reason:
                    break

                # After creating plans, they go to Pending_Approval → loop will halt on next iteration
                print(f"\n  ⏸️  Plans created and moved to Pending_Approval/")
                print("     Loop will halt on next iteration (respects approval gate)")

            # Log iteration
            self._log_iteration(iteration, "COMPLETED", len(decisions), plans_created)

            # Step 5: Check iteration timeout
            iteration_elapsed = time.time() - iteration_start_time
            if iteration_elapsed > self.iteration_timeout:
                print(f"\n  ⏱️  TIMEOUT: Iteration exceeded {self.iteration_timeout}s limit")
                self.halt_reason = f"Iteration timeout ({iteration_elapsed:.1f}s > {self.iteration_timeout}s)"
                self._log_iteration(iteration, "TIMEOUT", len(decisions), plans_created, self.halt_reason)
                self._log_to_system(f"Timeout at iteration {iteration}: {self.halt_reason}")
                break

            # Step 6: Check max plans limit
            if self.total_plans_created >= self.max_plans_per_iteration:
                print(f"\n  🛑 MAX PLANS REACHED: {self.total_plans_created}/{self.max_plans_per_iteration}")
                self.halt_reason = f"Max plans per iteration reached ({self.total_plans_created})"
                self._log_iteration(iteration, "MAX_PLANS", len(decisions), plans_created, self.halt_reason)
                self._log_to_system(f"Max plans at iteration {iteration}: {self.halt_reason}")
                break

        # Final summary
        print("\n" + "="*70)
        print("Ralph Loop Summary")
        print("="*70)
        print(f"Iterations Completed: {self.current_iteration}/{self.max_iterations}")
        print(f"Total Decisions Made: {len(self.decisions_made)}")
        print(f"Total Plans Created: {self.total_plans_created}")
        print(f"Final Status: {self.halt_reason or 'Max iterations reached'}")
        print("="*70 + "\n")

        # Log final summary
        self._log_to_file(f"=== Ralph Loop Ended: {self.halt_reason or 'Max iterations reached'} ===", self.ralph_log)
        self._log_to_system(f"Ended: {len(self.decisions_made)} decisions, {self.total_plans_created} plans, {self.halt_reason or 'Max iterations reached'}")

        return {
            'iterations': self.current_iteration,
            'decisions': len(self.decisions_made),
            'plans_created': self.total_plans_created,
            'status': self.halt_reason or 'Completed',
            'dry_run': self.dry_run
        }


def main():
    """Main entry point for Ralph loop orchestrator."""
    parser = argparse.ArgumentParser(
        description='Ralph Loop Autonomous Orchestrator (G-M7)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (show decisions without creating plans)
  python3 brain_ralph_loop_orchestrator_skill.py --dry-run

  # Execute (create plans, requires approval before execution)
  python3 brain_ralph_loop_orchestrator_skill.py --execute

  # Custom iteration limits
  python3 brain_ralph_loop_orchestrator_skill.py --execute --max-iterations 5 --max-plans 3

Safety:
  - Never bypasses approval gates
  - Never executes actions directly
  - Stops when approval required
  - Bounded iterations and plans
  - Timeout per iteration: 5 minutes
        """
    )

    # Mode selection
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Show decision reasoning without creating plans'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        help='Create plans (NOT execute actions - requires approval)'
    )

    # Configuration
    parser.add_argument(
        '--max-iterations',
        type=int,
        default=10,
        help='Maximum loop iterations (default: 10)'
    )
    parser.add_argument(
        '--max-plans',
        type=int,
        default=5,
        help='Maximum plans per iteration (default: 5)'
    )

    args = parser.parse_args()

    # Validate limits
    if args.max_iterations < 1 or args.max_iterations > 50:
        print("❌ Error: --max-iterations must be between 1 and 50")
        sys.exit(1)

    if args.max_plans < 1 or args.max_plans > 10:
        print("❌ Error: --max-plans must be between 1 and 10")
        sys.exit(1)

    # Run orchestrator
    orchestrator = RalphLoopOrchestrator(
        dry_run=args.dry_run,
        max_iterations=args.max_iterations,
        max_plans_per_iteration=args.max_plans
    )

    summary = orchestrator.run()

    # Exit code based on status
    if 'Approval required' in summary['status']:
        sys.exit(2)  # Exit code 2: waiting for approval
    elif 'failed' in summary['status'].lower():
        sys.exit(1)  # Exit code 1: error
    else:
        sys.exit(0)  # Exit code 0: success


if __name__ == '__main__':
    main()
