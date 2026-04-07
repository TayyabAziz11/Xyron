#!/usr/bin/env python3
"""
Personal AI Employee - Odoo Action Executor Skill
Gold Tier - G-M5: Odoo MCP Integration (Action Operations)

Purpose: Execute approved Odoo actions via MCP or mock (APPROVAL REQUIRED)
Tier: Gold
Skill ID: G-M5-T04

CRITICAL: This is ACTION layer. Approval MANDATORY. Dry-run DEFAULT.
Supports: create_invoice, post_invoice, register_payment, create_customer, create_credit_note

Features:
- Requires Approved/ plan with HITL approval
- Dry-run default (--execute required for real actions)
- Mock mode with simulated responses
- Real mode with MCP action tools (optional)
- PII redaction using mcp_helpers
- Plan lifecycle: Approved/ → Plans/completed/ or Plans/failed/
- Remediation task creation on failure
- Full audit logging
"""

import os
import sys
import json
import argparse
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Import helpers
try:
    from ai_operator.core.mcp_helpers import redact_pii, get_repo_root
except ImportError:
    import re

    def redact_pii(text: str) -> str:
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        text = re.sub(email_pattern, '[EMAIL_REDACTED]', text)
        text = re.sub(phone_pattern, '[PHONE_REDACTED]', text)
        return text

    def get_repo_root() -> Path:
        """Fallback: find repo root"""
        current = Path(__file__).parent
        while current != current.parent:
            if (current / 'system_log.md').exists():
                return current
            current = current.parent
        return Path(__file__).parent.parent.parent.parent.parent

# Simple rate limiting with time.sleep
import time


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OdooActionExecutor:
    """Odoo Action Executor - Write operations with approval"""

    SUPPORTED_ACTIONS = [
        'create_customer',
        'create_invoice',
        'post_invoice',
        'register_payment',
        'create_credit_note'
    ]

    def __init__(self, config: Dict):
        self.config = config
        self.actions_executed = 0
        self.actions_failed = 0

    def _find_approved_plan(self) -> Optional[Path]:
        """Find the first approved Odoo plan in Approved/"""
        approved_dir = Path(self.config['base_dir']) / 'Approved'

        if not approved_dir.exists():
            logger.error("Approved/ directory not found")
            return None

        # Look for Odoo-related plans
        for plan_path in sorted(approved_dir.glob('*.md')):
            try:
                content = plan_path.read_text(encoding='utf-8')
                # Check if plan is for Odoo actions
                if 'odoo' in content.lower() or 'invoice' in content.lower() or 'payment' in content.lower():
                    logger.info(f"Found approved Odoo plan: {plan_path.name}")
                    return plan_path
            except Exception as e:
                logger.warning(f"Could not read {plan_path}: {e}")
                continue

        return None

    def _parse_plan(self, plan_path: Path) -> List[Dict]:
        """Parse actions from approved plan (JSON block or markdown table)"""
        try:
            content = plan_path.read_text(encoding='utf-8')

            # Method 1: Look for JSON block pattern
            import re
            json_block_pattern = r'##\s+Actions\s+JSON\s*\n\s*```(?:json)?\s*\n(.*?)\n\s*```'
            json_match = re.search(json_block_pattern, content, re.DOTALL | re.IGNORECASE)

            if json_match:
                actions_json = json_match.group(1)
                actions = json.loads(actions_json)
                logger.info(f"Parsed {len(actions)} actions from JSON block")
                return actions

            # Method 2: Look for markdown table pattern
            table_pattern = r'##\s+(?:MCP\s+Tools|Actions|Odoo\s+Actions)\s*\n\s*\|.*?\n\s*\|[-:\s|]+\n((?:\|.*?\n)+)'
            table_match = re.search(table_pattern, content, re.DOTALL | re.IGNORECASE)

            if table_match:
                table_rows = table_match.group(1).strip().split('\n')
                actions = []

                for row in table_rows:
                    cells = [cell.strip() for cell in row.split('|')[1:-1]]  # Remove outer pipes
                    if len(cells) >= 3:
                        server = cells[0].strip()
                        operation = cells[1].strip()
                        params_str = cells[2].strip()

                        # Only parse Odoo actions
                        if server.lower() != 'odoo':
                            continue

                        try:
                            parameters = json.loads(params_str) if params_str else {}
                        except json.JSONDecodeError:
                            logger.warning(f"Could not parse parameters: {params_str}")
                            parameters = {}

                        actions.append({
                            'server': server,
                            'operation': operation,
                            'parameters': parameters
                        })

                logger.info(f"Parsed {len(actions)} actions from markdown table")
                return actions

            logger.warning("No actions found in plan (expected ## Actions JSON or ## Actions table)")
            return []

        except Exception as e:
            logger.error(f"Failed to parse plan: {e}")
            return []

    def _validate_action(self, action: Dict) -> Tuple[bool, str]:
        """Validate Odoo action before execution"""
        server = action.get('server', '').lower()
        operation = action.get('operation', '')
        params = action.get('parameters', {})

        if server != 'odoo':
            return False, f"Invalid server: {server} (expected 'odoo')"

        if operation not in self.SUPPORTED_ACTIONS:
            return False, f"Unsupported operation: {operation}"

        # Validate required fields per operation
        if operation == 'create_customer':
            if not params.get('name'):
                return False, "create_customer requires 'name' parameter"

        elif operation == 'create_invoice':
            if not params.get('partner_id') and not params.get('customer_name'):
                return False, "create_invoice requires 'partner_id' or 'customer_name' parameter"

        elif operation == 'post_invoice':
            if not params.get('invoice_id'):
                return False, "post_invoice requires 'invoice_id' parameter"

        elif operation == 'register_payment':
            if not params.get('invoice_id'):
                return False, "register_payment requires 'invoice_id' parameter"
            if not params.get('amount'):
                return False, "register_payment requires 'amount' parameter"

        elif operation == 'create_credit_note':
            if not params.get('invoice_id'):
                return False, "create_credit_note requires 'invoice_id' parameter"

        return True, "Validation passed"

    def _execute_action(self, action: Dict, dry_run: bool = True, mock: bool = True) -> Dict:
        """Execute a single Odoo action"""
        # Rate limiting
        time.sleep(0.1)

        server = action['server']
        operation = action['operation']
        params = action['parameters']

        if dry_run:
            # Dry-run preview
            print(f"\n  📋 Action Preview:")
            print(f"     Platform: {server.title()}")
            print(f"     Operation: {operation}")

            # Show key parameters (PII-safe)
            for key, value in params.items():
                safe_value = redact_pii(str(value))
                print(f"     {key}: {safe_value}")

            return {
                'status': 'dry_run_success',
                'message': 'Dry-run successful (no real action taken)'
            }

        # Execute mode
        print(f"\n  🚀 Executing Action {'(SIMULATED)' if mock else ''}:")
        print(f"     Platform: {server.title()}")
        print(f"     Operation: {operation}")

        if mock:
            # Simulated execution
            result = {
                'status': 'simulated_success',
                'message': f'Simulated execution successful',
                'note': 'Real MCP client integration pending (G-M5 TODO)'
            }
            print(f"     Status: {result['status']}")
            return result

        else:
            # Real MCP execution
            try:
                secrets_path = Path(self.config['base_dir']) / '.secrets' / 'odoo_credentials.json'

                if not secrets_path.exists():
                    raise FileNotFoundError("Odoo MCP credentials not found")

                # TODO: Real MCP client integration (G-M5)
                logger.info(f"Real Odoo MCP action integration pending: {operation}")
                return {
                    'status': 'error',
                    'message': 'MCP integration pending'
                }

            except Exception as e:
                logger.error(f"MCP action failed: {e}")
                return {
                    'status': 'error',
                    'message': str(e)
                }

    def _log_action(self, action: Dict, result: Dict, dry_run: bool):
        """Log action to mcp_actions.log"""
        log_path = self.config['log_path']

        log_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'server': action['server'],
            'tool': action['operation'],
            'params': {k: redact_pii(str(v)) for k, v in action['parameters'].items()},
            'dry_run': dry_run,
            'status': result['status'],
            'result': result.get('message', '')
        }

        if 'note' in result:
            log_entry['note'] = result['note']

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            logger.warning(f"Could not write to log: {e}")

    def _move_plan(self, plan_path: Path, success: bool):
        """Move plan to completed/ or failed/"""
        base_dir = Path(self.config['base_dir'])
        target_dir = base_dir / 'Plans' / ('completed' if success else 'failed')
        target_dir.mkdir(parents=True, exist_ok=True)

        target_path = target_dir / plan_path.name

        try:
            shutil.move(str(plan_path), str(target_path))
            logger.info(f"Moved plan to {target_dir.name}/")
        except Exception as e:
            logger.error(f"Failed to move plan: {e}")

    def _create_remediation_task(self, plan_path: Path, error_msg: str):
        """Create remediation task in Needs_Action/ when execution fails"""
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
            filename = f"remediation__odoo_executor__{timestamp}.md"
            task_path = Path(self.config['base_dir']) / 'Needs_Action' / filename

            task_content = f"""---
id: remediation_odoo_executor_{timestamp}
type: remediation
source: odoo_executor
created_at: {datetime.now(timezone.utc).isoformat()}
priority: high
status: needs_action
---

# Odoo Execution Failed: {plan_path.name}

**Issue**: Odoo action execution failed
**Source**: Odoo Executor (G-M5)
**Created**: {datetime.now(timezone.utc).isoformat()}

## Problem

{error_msg}

## Failed Plan

- Plan: {plan_path.name}
- Location: Plans/failed/{plan_path.name}

## Suggested Actions

1. Review failed plan in Plans/failed/{plan_path.name}
2. Check Logs/mcp_actions.log for detailed error
3. Verify Odoo MCP server status and credentials
4. Fix plan or data issues
5. Re-approve fixed plan if needed
6. Re-run executor

## References

- Setup Guide: Docs/mcp_odoo_setup.md
- Credentials: .secrets/odoo_credentials.json
- Logs: Logs/mcp_actions.log

---
**Generated by**: brain_execute_odoo_with_mcp_skill.py
**Plan moved to**: Plans/failed/
"""

            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(task_content, encoding='utf-8')

            logger.info(f"Created remediation task: {filename}")

        except Exception as e:
            logger.error(f"Failed to create remediation task: {e}")

    def _update_dashboard_last_action(self, action: Dict, status: str):
        """Update Dashboard.md with last external action"""
        dashboard_path = Path(self.config['base_dir']) / 'Dashboard.md'

        if not dashboard_path.exists():
            logger.warning("Dashboard.md not found")
            return

        try:
            content = dashboard_path.read_text(encoding='utf-8')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            last_action_line = f"**Last External Action (Gold)**: odoo.{action['operation']} - {status} - {timestamp}"

            # Replace existing line or append
            import re
            pattern = r'\*\*Last External Action \(Gold\)\*\*:.*'
            if re.search(pattern, content):
                content = re.sub(pattern, last_action_line, content)
            else:
                content += f"\n\n{last_action_line}\n"

            dashboard_path.write_text(content, encoding='utf-8')
            logger.info("Updated Dashboard.md")

        except Exception as e:
            logger.warning(f"Could not update Dashboard: {e}")

    def execute(self, dry_run: bool = True, mock: bool = True) -> Dict:
        """Execute approved Odoo plan"""
        # Find approved plan
        plan_path = self._find_approved_plan()
        if not plan_path:
            return {
                'success': False,
                'error': 'No approved Odoo plan found in Approved/'
            }

        print(f"\n{'🔍 DRY-RUN MODE' if dry_run else '⚠️  EXECUTE MODE'}: {'No real actions will be taken' if dry_run else 'Real actions will be performed!'}")
        print(f"Plan: {plan_path}")

        # Parse actions
        actions = self._parse_plan(plan_path)
        if not actions:
            return {
                'success': False,
                'error': 'No actions found in plan'
            }

        print(f"\nFound {len(actions)} action(s) to execute\n")

        # Execute each action
        for idx, action in enumerate(actions, 1):
            print(f"Action {idx}/{len(actions)}:")

            # Validate
            valid, validation_msg = self._validate_action(action)
            if not valid:
                error_msg = f"Validation failed for action {idx}: {validation_msg}"
                logger.error(error_msg)
                print(f"\n❌ {error_msg}")

                # Stop on first failure
                if not dry_run:
                    self._move_plan(plan_path, success=False)
                    self._create_remediation_task(plan_path, error_msg)

                return {
                    'success': False,
                    'error': error_msg,
                    'plan': str(plan_path),
                    'actions_executed': self.actions_executed,
                    'actions_failed': self.actions_failed + 1
                }

            # Execute
            try:
                result = self._execute_action(action, dry_run=dry_run, mock=mock)
                self._log_action(action, result, dry_run)

                if result['status'] in ['dry_run_success', 'simulated_success', 'success']:
                    self.actions_executed += 1
                else:
                    self.actions_failed += 1
                    error_msg = f"Action {idx} failed: {result.get('message', 'Unknown error')}"

                    # Stop on first failure
                    if not dry_run:
                        self._move_plan(plan_path, success=False)
                        self._create_remediation_task(plan_path, error_msg)

                    return {
                        'success': False,
                        'error': error_msg,
                        'plan': str(plan_path),
                        'actions_executed': self.actions_executed,
                        'actions_failed': self.actions_failed
                    }

            except Exception as e:
                error_msg = f"Action {idx} raised exception: {e}"
                logger.error(error_msg)
                self.actions_failed += 1

                # Stop on first failure
                if not dry_run:
                    self._move_plan(plan_path, success=False)
                    self._create_remediation_task(plan_path, error_msg)

                return {
                    'success': False,
                    'error': error_msg,
                    'plan': str(plan_path),
                    'actions_executed': self.actions_executed,
                    'actions_failed': self.actions_failed
                }

        # All actions succeeded
        print(f"\n{'✅ Dry-run successful' if dry_run else '✅ Execution successful'}")
        print(f"   Plan: {plan_path}")
        print(f"   Actions {'would be executed' if dry_run else 'executed'}: {self.actions_executed}/{len(actions)}")

        if not dry_run:
            # Move plan to completed
            self._move_plan(plan_path, success=True)

            # Update dashboard
            if actions:
                self._update_dashboard_last_action(actions[-1], 'success')

            # Append to system_log.md
            try:
                with open('system_log.md', 'a', encoding='utf-8') as f:
                    timestamp = datetime.now(timezone.utc).isoformat()
                    f.write(f"\n**[{timestamp}]** Odoo execution complete: {self.actions_executed} actions from {plan_path.name}\n")
            except:
                pass

        return {
            'success': True,
            'plan': str(plan_path),
            'actions_executed': self.actions_executed,
            'actions_failed': self.actions_failed,
            'dry_run': dry_run
        }


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Odoo Action Executor - Execute approved Odoo plans (Gold Tier - G-M5)'
    )
    parser.add_argument('--execute', action='store_true',
                        help='Execute actions (default is dry-run)')
    parser.add_argument('--mode', type=str, choices=['mock', 'mcp'], default='mock',
                        help='Execution mode: mock (default) or mcp')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    config = {
        'base_dir': get_repo_root(),
        'log_path': 'Logs/mcp_actions.log'
    }

    executor = OdooActionExecutor(config)

    dry_run = not args.execute
    use_mock = (args.mode == 'mock')

    result = executor.execute(dry_run=dry_run, mock=use_mock)

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
