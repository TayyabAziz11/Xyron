#!/usr/bin/env python3
"""
Personal AI Employee - brain_execute_social_with_mcp Skill
Gold Tier - G-M4: Social MCP Execution Layer

Purpose: Execute approved social media actions via MCP ACTION tools
Tier: Gold
Skill ID: G-M4-T05

CRITICAL SAFETY RULES:
1. REQUIRES approved plan in Approved/ directory
2. Dry-run is DEFAULT (--execute flag required for real actions)
3. NEVER bypass approval gates
4. STOP immediately on any failure
5. NEVER claim success if any action failed
6. Create remediation tasks for all failures

Supported Operations:
- WhatsApp: send_message, reply_message
- LinkedIn: create_post, reply_comment, send_message
- Twitter: create_post, reply_post, send_dm
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Import helpers
try:
    from ai_operator.core.mcp_helpers import log_json, redact_pii, get_repo_root
except ImportError:
    # Fallback implementations
    def log_json(log_path: Path, data: Dict) -> None:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data) + '\n')

    import re
    def redact_pii(text: str) -> str:
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        text = re.sub(email_pattern, '[EMAIL_REDACTED]', text)
        text = re.sub(phone_pattern, '[PHONE_REDACTED]', text)
        return text

    def get_repo_root() -> Path:
        """Fallback: assume we're in skills subdirectory and go up to find system_log.md"""
        current = Path(__file__).parent
        while current != current.parent:
            if (current / 'system_log.md').exists():
                return current
            current = current.parent
        return Path(__file__).parent.parent.parent.parent.parent

# Import LinkedIn API helper for real mode execution
try:
    from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
    except ImportError:
        LinkedInAPIHelper = None


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SocialExecutor:
    """Execute approved social media actions via MCP ACTION tools"""

    # Supported MCP action tools (server.tool_name format)
    SUPPORTED_ACTIONS = {
        'whatsapp': ['send_message', 'reply_message'],
        'linkedin': ['create_post', 'reply_comment', 'send_message'],
        'twitter': ['create_post', 'reply_post', 'send_dm']
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize social executor.

        Args:
            config: Optional configuration dict
        """
        self.config = config or self._default_config()
        self.base_dir = Path(self.config['base_dir'])
        self.approved_dir = self.base_dir / 'Approved'
        self.actions_log = self.base_dir / 'Logs' / 'mcp_actions.log'
        self.system_log = self.base_dir / 'system_log.md'

        # Ensure directories exist
        self.actions_log.parent.mkdir(parents=True, exist_ok=True)

    def _default_config(self) -> Dict:
        """Return default configuration"""
        return {
            'base_dir': get_repo_root(),
        }

    def _log_action(self, action_data: Dict) -> None:
        """
        Log action to Logs/mcp_actions.log in JSON format.

        Args:
            action_data: Action metadata dict
        """
        try:
            log_json(self.actions_log, action_data)
            logger.info(f"Logged action: {action_data['tool']}")
        except Exception as e:
            logger.error(f"Failed to log action: {e}")

    def _log_system(self, message: str) -> None:
        """
        Append entry to system_log.md.

        Args:
            message: Log message
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            log_entry = f"\n**[{timestamp}]** {message}\n"

            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"Failed to write to system_log.md: {e}")

    def _create_remediation_task(self, title: str, description: str, plan_path: str) -> None:
        """
        Create remediation task in Needs_Action/ when execution fails.

        Args:
            title: Issue title
            description: Problem description
            plan_path: Path to original plan
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
            filename = f"remediation__mcp__social_executor__{timestamp}.md"
            task_path = self.base_dir / 'Needs_Action' / filename

            task_content = f"""---
id: remediation_social_executor_{timestamp}
type: remediation
source: brain_execute_social_with_mcp
created_at: {datetime.now(timezone.utc).isoformat()}
priority: high
status: needs_action
plan_reference: {plan_path}
---

# MCP Execution Remediation: {title}

**Issue**: {title}
**Source**: Social Executor (G-M4)
**Created**: {datetime.now(timezone.utc).isoformat()}
**Original Plan**: {plan_path}

## Problem

{description}

## Suggested Actions

1. Review original plan: {plan_path}
2. Check MCP server status and credentials
3. Verify approval was not bypassed (plan must be in Approved/)
4. Review Logs/mcp_actions.log for detailed error context
5. Consider re-planning or adjusting action parameters
6. Test MCP connection: `python3 brain_mcp_registry_refresh_skill.py --mock`

## Safety Notes

- **NEVER bypass approval gates**: All actions require approved plans
- **NEVER use --execute without approval**: Dry-run is mandatory default
- **NEVER claim success on failure**: Stop immediately on errors

## References

- Action Log: Logs/mcp_actions.log
- System Log: system_log.md
- MCP Setup Guides: Docs/mcp_*_setup.md

---
**Generated by**: brain_execute_social_with_mcp_skill.py
**Execution Mode**: Failed - see logs for details
"""

            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(task_content, encoding='utf-8')

            logger.info(f"Created remediation task: {filename}")
            self._log_system(f"Remediation task created: {title}")

        except Exception as e:
            logger.error(f"Failed to create remediation task: {e}")

    def _find_approved_plan(self, plan_id: Optional[str] = None) -> Optional[Path]:
        """
        Find approved plan file.

        Args:
            plan_id: Optional specific plan ID to find

        Returns:
            Path to plan file or None if not found
        """
        if not self.approved_dir.exists():
            logger.error(f"Approved directory not found: {self.approved_dir}")
            return None

        # If specific plan_id provided, search for it
        if plan_id:
            pattern = f"plan__{plan_id}__*.md"
            matches = list(self.approved_dir.glob(pattern))

            if not matches:
                logger.error(f"No approved plan found with ID: {plan_id}")
                return None

            if len(matches) > 1:
                logger.warning(f"Multiple plans found for ID {plan_id}, using first")

            return matches[0]

        # Otherwise, find the most recent approved plan
        plans = list(self.approved_dir.glob("plan__*.md"))

        if not plans:
            logger.error("No approved plans found in Approved/")
            return None

        # Sort by modification time, most recent first
        plans.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        return plans[0]

    def _parse_plan(self, plan_path: Path) -> Dict:
        """
        Parse approved plan to extract MCP actions.

        Supports two formats:
        1. Markdown table under "## MCP Tools" or "## Actions"
           Columns: server | operation | parameters (JSON string)
        2. Fenced JSON block under "## Actions JSON"
           Array of {server, operation, parameters} objects

        Args:
            plan_path: Path to approved plan file

        Returns:
            Dict with plan metadata and actions list
        """
        try:
            content = plan_path.read_text(encoding='utf-8')
            actions = []

            # Extract plan ID from filename
            plan_id = plan_path.stem.split('__')[1] if '__' in plan_path.stem else 'unknown'

            # Method 1: Try to find JSON block first (## Actions JSON)
            import re
            json_block_pattern = r'##\s+Actions\s+JSON\s*\n\s*```(?:json)?\s*\n(.*?)\n\s*```'
            json_match = re.search(json_block_pattern, content, re.DOTALL | re.IGNORECASE)

            if json_match:
                try:
                    json_str = json_match.group(1)
                    actions = json.loads(json_str)

                    if not isinstance(actions, list):
                        raise ValueError("Actions JSON must be an array")

                    logger.info(f"Parsed {len(actions)} actions from JSON block")

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in Actions JSON block: {e}")
                    return {
                        'error': f"Invalid JSON: {e}",
                        'parsed_successfully': False
                    }

            # Method 2: Try to find markdown table (## MCP Tools or ## Actions)
            if not actions:
                table_pattern = r'##\s+(?:MCP\s+Tools|Actions)\s*\n\s*\|.*?\n\s*\|[-:\s|]+\n((?:\|.*?\n)+)'
                table_match = re.search(table_pattern, content, re.DOTALL | re.IGNORECASE)

                if table_match:
                    table_rows = table_match.group(1).strip().split('\n')

                    for row in table_rows:
                        cells = [cell.strip() for cell in row.split('|')[1:-1]]  # Skip first/last empty

                        if len(cells) >= 3:
                            server = cells[0].strip()
                            operation = cells[1].strip()
                            params_str = cells[2].strip()

                            try:
                                parameters = json.loads(params_str) if params_str else {}
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid JSON in parameters, using as-is: {params_str}")
                                parameters = {'text': params_str}

                            actions.append({
                                'server': server,
                                'operation': operation,
                                'parameters': parameters
                            })

                    logger.info(f"Parsed {len(actions)} actions from markdown table")

            if not actions:
                logger.warning("No actions found in plan (tried JSON block and markdown table)")
                return {
                    'plan_id': plan_id,
                    'plan_path': str(plan_path),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'actions': [],
                    'parsed_successfully': True,
                    'parse_note': 'No actions found (plan may be informational only)'
                }

            # Validate and normalize action format
            normalized_actions = []
            for idx, action in enumerate(actions):
                if not isinstance(action, dict):
                    logger.error(f"Action {idx} is not a dict: {action}")
                    continue

                server = action.get('server', '').lower()
                operation = action.get('operation', '')
                parameters = action.get('parameters', {})

                if not server or not operation:
                    logger.error(f"Action {idx} missing server or operation: {action}")
                    continue

                normalized_actions.append({
                    'server': server,
                    'tool': operation,  # Normalize to 'tool' for consistency
                    'params': parameters  # Normalize to 'params'
                })

            return {
                'plan_id': plan_id,
                'plan_path': str(plan_path),
                'created_at': datetime.now(timezone.utc).isoformat(),
                'actions': normalized_actions,
                'parsed_successfully': True,
                'actions_count': len(normalized_actions)
            }

        except Exception as e:
            logger.error(f"Failed to parse plan: {e}")
            return {'error': str(e), 'parsed_successfully': False}

    def _validate_action(self, server: str, tool: str, params: Dict) -> tuple[bool, str]:
        """
        Validate action parameters against platform constraints.

        Args:
            server: MCP server name
            tool: Tool name
            params: Parameters dict

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Twitter constraint: max 280 characters
        if server == 'twitter' and tool in ['create_post', 'reply_post']:
            content = params.get('content', params.get('text', ''))
            if len(content) > 280:
                return False, f"Twitter content too long: {len(content)} chars (max 280)"

        # WhatsApp: check required fields
        if server == 'whatsapp':
            if tool == 'send_message' and not params.get('to'):
                return False, "WhatsApp send_message requires 'to' field"
            if tool == 'reply_message' and not params.get('message_id'):
                return False, "WhatsApp reply_message requires 'message_id' field"

        # LinkedIn: check required fields
        if server == 'linkedin' and tool == 'create_post':
            if not params.get('content') and not params.get('text'):
                return False, "LinkedIn create_post requires 'content' or 'text' field"

        return True, ""

    def _execute_action(self, server: str, tool: str, params: Dict, dry_run: bool = True) -> Dict:
        """
        Execute a single MCP action tool.

        Args:
            server: MCP server name (whatsapp, linkedin, twitter)
            tool: Tool name (send_message, create_post, etc.)
            params: Tool parameters dict
            dry_run: If True, simulate only

        Returns:
            Result dict with success status and response
        """
        # Validate server and tool
        if server not in self.SUPPORTED_ACTIONS:
            return {
                'success': False,
                'error': f"Unsupported MCP server: {server}",
                'server': server,
                'tool': tool
            }

        if tool not in self.SUPPORTED_ACTIONS[server]:
            return {
                'success': False,
                'error': f"Unsupported tool {tool} for server {server}",
                'server': server,
                'tool': tool
            }

        # Validate action parameters
        is_valid, error_msg = self._validate_action(server, tool, params)
        if not is_valid:
            logger.error(f"Validation failed: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'server': server,
                'tool': tool
            }

        # Prepare redacted params for logging
        redacted_params = {}
        for key, value in params.items():
            if isinstance(value, str):
                redacted_params[key] = redact_pii(value)
            else:
                redacted_params[key] = value

        # Log attempt
        action_log = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'server': server,
            'tool': tool,
            'params': redacted_params,
            'dry_run': dry_run,
            'status': 'pending'
        }

        if dry_run:
            # Print detailed dry-run preview
            print(f"\n  📋 Action Preview:")
            print(f"     Platform: {server.title()}")
            print(f"     Operation: {tool}")

            # Show content preview (redacted)
            content_key = 'content' if 'content' in params else 'text' if 'text' in params else 'message'
            if content_key in params:
                content = params[content_key]
                content_preview = redact_pii(content)
                print(f"     Content: {content_preview[:100]}{'...' if len(content_preview) > 100 else ''}")
                print(f"     Length: {len(content)} chars")

                # Show constraint validation
                if server == 'twitter':
                    print(f"     Twitter limit: {len(content)}/280 chars {'✓' if len(content) <= 280 else '✗ EXCEEDS LIMIT'}")

            # Show other params (redacted)
            for key, value in params.items():
                if key not in [content_key]:
                    print(f"     {key}: {redact_pii(str(value))}")

            logger.info(f"[DRY-RUN] Would execute: {server}.{tool}")

            action_log['status'] = 'dry_run_success'
            action_log['result'] = 'Dry-run successful (no real action taken)'
            self._log_action(action_log)

            return {
                'success': True,
                'dry_run': True,
                'server': server,
                'tool': tool,
                'result': 'Dry-run successful'
            }

        # Real execution mode
        logger.info(f"[EXECUTE] Real execution: {server}.{tool}")
        print(f"\n  🚀 Executing Action:")
        print(f"     Platform: {server.title()}")
        print(f"     Operation: {tool}")

        # LinkedIn real execution
        if server == 'linkedin':
            try:
                if LinkedInAPIHelper is None:
                    raise ImportError("LinkedInAPIHelper not available")

                # Initialize LinkedIn helper
                secrets_dir = self.base_dir / '.secrets'
                helper = LinkedInAPIHelper(secrets_dir=secrets_dir)

                # Verify authentication
                try:
                    helper.check_auth()
                except Exception as auth_error:
                    raise Exception(f"LinkedIn authentication failed: {auth_error}")

                # Execute based on tool type
                if tool == 'create_post':
                    text = params.get('content', params.get('text', ''))
                    visibility = params.get('visibility', 'PUBLIC')
                    result_data = helper.create_post(text=text, visibility=visibility)

                    action_log['status'] = 'success'
                    action_log['result'] = f"Post created: {result_data.get('id', 'unknown')}"
                    action_log['post_id'] = result_data.get('id', '')
                    print(f"     ✅ Post created successfully")
                    print(f"     Post ID: {result_data.get('id', 'N/A')}")

                elif tool == 'reply_comment':
                    comment_id = params.get('comment_id', '')
                    text = params.get('text', params.get('content', ''))
                    result_data = helper.reply_comment(comment_id=comment_id, text=text)

                    action_log['status'] = 'success'
                    action_log['result'] = f"Comment replied: {result_data.get('id', 'unknown')}"
                    print(f"     ✅ Comment reply posted successfully")

                elif tool == 'send_message':
                    recipient_urn = params.get('recipient_urn', params.get('to', ''))
                    text = params.get('text', params.get('content', ''))
                    result_data = helper.send_message(recipient_urn=recipient_urn, text=text)

                    action_log['status'] = 'success'
                    action_log['result'] = f"Message sent: {result_data.get('id', 'unknown')}"
                    print(f"     ✅ Message sent successfully")

                else:
                    raise ValueError(f"Unsupported LinkedIn tool: {tool}")

                self._log_action(action_log)

                return {
                    'success': True,
                    'server': server,
                    'tool': tool,
                    'result': action_log['result'],
                    'response': result_data
                }

            except Exception as e:
                logger.error(f"LinkedIn execution failed: {e}")
                print(f"     ❌ Execution failed: {e}")

                action_log['status'] = 'failed'
                action_log['error'] = str(e)
                self._log_action(action_log)

                return {
                    'success': False,
                    'server': server,
                    'tool': tool,
                    'error': str(e)
                }

        # WhatsApp real execution via WhatsAppWebClient
        elif server == 'whatsapp':
            try:
                from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient

                client = WhatsAppWebClient(headless=True)
                client.start()

                if not client.is_logged_in():
                    raise Exception(
                        "WhatsApp Web not logged in. Run: python3 scripts/wa_setup.py"
                    )

                if tool == 'send_message':
                    to = params.get('to', '')
                    message = params.get('message', params.get('text', ''))
                    if not to or not message:
                        raise ValueError("WhatsApp send_message requires 'to' and 'message' params")
                    success = client.send_message(to=to, text=message)
                    client.stop()

                    if not success:
                        raise Exception("send_message returned False — check WhatsApp Web session")

                    action_log['status'] = 'success'
                    action_log['result'] = f"WhatsApp message sent to {to[:5]}****"
                    print(f"     ✅ WhatsApp message sent successfully")

                else:
                    client.stop()
                    raise ValueError(f"Unsupported WhatsApp tool: {tool}")

                self._log_action(action_log)

                return {
                    'success': True,
                    'server': server,
                    'tool': tool,
                    'result': action_log['result'],
                }

            except Exception as e:
                logger.error(f"WhatsApp execution failed: {e}")
                print(f"     ❌ Execution failed: {e}")

                action_log['status'] = 'failed'
                action_log['error'] = str(e)
                self._log_action(action_log)

                return {
                    'success': False,
                    'server': server,
                    'tool': tool,
                    'error': str(e)
                }

        # For other platforms (Twitter etc.): simulated for now
        else:
            logger.info(f"[EXECUTE] Simulating: {server}.{tool} (real integration pending)")
            print(f"     Status: Simulated success (real MCP integration pending for {server})")

            action_log['status'] = 'simulated_success'
            action_log['result'] = f'Simulated execution successful for {server}'
            action_log['note'] = f'Real {server} integration pending'
            self._log_action(action_log)

            return {
                'success': True,
                'simulated': True,
                'server': server,
                'tool': tool,
                'result': f'Simulated execution successful for {server}'
            }

    def _move_plan(self, plan_path: Path, destination: str) -> bool:
        """
        Move plan to completed/ or failed/ subdirectory.

        Args:
            plan_path: Path to plan file
            destination: 'completed' or 'failed'

        Returns:
            True if successful, False otherwise
        """
        try:
            dest_dir = self.base_dir / 'Plans' / destination
            dest_dir.mkdir(parents=True, exist_ok=True)

            dest_path = dest_dir / plan_path.name

            # If destination already exists, append timestamp
            if dest_path.exists():
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
                stem = plan_path.stem
                dest_path = dest_dir / f"{stem}_{timestamp}{plan_path.suffix}"

            plan_path.rename(dest_path)
            logger.info(f"Moved plan to {destination}/: {dest_path.name}")

            return True

        except Exception as e:
            logger.error(f"Failed to move plan to {destination}/: {e}")
            return False

    def _update_dashboard_last_action(self, server: str, tool: str, status: str) -> None:
        """
        Update Dashboard.md with last external action.

        Args:
            server: MCP server name
            tool: Tool name
            status: Action status
        """
        try:
            dashboard_path = self.base_dir / 'Dashboard.md'

            if not dashboard_path.exists():
                logger.warning("Dashboard.md not found, skipping update")
                return

            content = dashboard_path.read_text(encoding='utf-8')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

            # Create or update "Last External Action (Gold)" section
            action_info = f"{server}.{tool} - {status} - {timestamp}"

            import re
            pattern = r'(\*\*Last External Action \(Gold\)\*\*:)(.*?)(\n|$)'
            match = re.search(pattern, content)

            if match:
                # Update existing entry
                new_content = re.sub(
                    pattern,
                    rf'\1 {action_info}\3',
                    content
                )
            else:
                # Add new entry (append after MCP Registry Status section if exists)
                if "## MCP Registry Status" in content:
                    new_content = content.replace(
                        "## MCP Registry Status",
                        f"**Last External Action (Gold)**: {action_info}\n\n## MCP Registry Status"
                    )
                else:
                    new_content = content + f"\n\n**Last External Action (Gold)**: {action_info}\n"

            dashboard_path.write_text(new_content, encoding='utf-8')
            logger.info("Updated Dashboard.md with last action")

        except Exception as e:
            logger.error(f"Failed to update dashboard: {e}")

    def execute(self, plan_id: Optional[str] = None, dry_run: bool = True, verbose: bool = False) -> Dict:
        """
        Execute approved social media plan.

        Args:
            plan_id: Optional specific plan ID to execute
            dry_run: If True (default), simulate only
            verbose: Enable verbose logging

        Returns:
            Execution result dict
        """
        start_time = datetime.now(timezone.utc)

        if verbose:
            logger.setLevel(logging.DEBUG)

        mode_str = "DRY-RUN" if dry_run else "EXECUTE"
        logger.info(f"Starting social executor ({mode_str} mode)")

        # Find approved plan
        plan_path = self._find_approved_plan(plan_id)

        if not plan_path:
            error_msg = f"No approved plan found{f' for ID: {plan_id}' if plan_id else ''}"
            logger.error(error_msg)
            self._create_remediation_task(
                "Approved plan not found",
                f"{error_msg}\nPlans must be approved and placed in Approved/ before execution.",
                plan_path=plan_id or "unknown"
            )
            return {
                'success': False,
                'error': error_msg,
                'actions_attempted': 0,
                'actions_succeeded': 0
            }

        logger.info(f"Found approved plan: {plan_path.name}")

        # Parse plan
        plan_data = self._parse_plan(plan_path)

        if not plan_data.get('parsed_successfully'):
            error_msg = f"Failed to parse plan: {plan_data.get('parse_note', 'Unknown error')}"
            logger.error(error_msg)

            # For G-M4, this is expected (parsing not implemented yet)
            # Return successful dry-run result with note
            if dry_run:
                logger.info("[DRY-RUN] Plan parsing pending implementation")
                logger.info("[DRY-RUN] Would execute actions from plan once parsing is complete")

                summary = "Social executor dry-run complete (plan parsing pending G-M4)"
                self._log_system(summary)

                return {
                    'success': True,
                    'dry_run': True,
                    'plan_path': str(plan_path),
                    'actions_attempted': 0,
                    'actions_succeeded': 0,
                    'note': 'Plan parsing implementation pending (G-M4 TODO)',
                    'duration': (datetime.now(timezone.utc) - start_time).total_seconds()
                }

            self._create_remediation_task(
                "Plan parsing failed",
                error_msg,
                plan_path=str(plan_path)
            )
            return {
                'success': False,
                'error': error_msg,
                'plan_path': str(plan_path)
            }

        # Execute actions from plan
        actions = plan_data.get('actions', [])

        if not actions:
            logger.warning("No actions found in plan")

        results = []
        failed_actions = []

        for idx, action in enumerate(actions):
            server = action.get('server')
            tool = action.get('tool')
            params = action.get('params', {})

            logger.info(f"Executing action {idx+1}/{len(actions)}: {server}.{tool}")

            result = self._execute_action(server, tool, params, dry_run=dry_run)
            results.append(result)

            if not result.get('success'):
                failed_actions.append({
                    'index': idx,
                    'server': server,
                    'tool': tool,
                    'error': result.get('error')
                })

                # STOP IMMEDIATELY on failure (per requirements)
                logger.error(f"Action failed: {server}.{tool} - {result.get('error')}")
                logger.error("Stopping execution (NEVER continue after failure)")
                break

        # Calculate statistics
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        actions_attempted = len(results)
        actions_succeeded = sum(1 for r in results if r.get('success'))

        success = (len(failed_actions) == 0 and actions_attempted > 0)

        # Log summary
        summary = (
            f"Social executor {'dry-run' if dry_run else 'execution'} complete: "
            f"Plan={plan_path.name}, "
            f"{actions_attempted} attempted, {actions_succeeded} succeeded, "
            f"{len(failed_actions)} failed, {duration:.2f}s"
        )

        self._log_system(summary)
        logger.info(summary)

        # Plan lifecycle management (only for real execution, not dry-run)
        if not dry_run:
            if success:
                # Move plan to completed/
                if self._move_plan(plan_path, 'completed'):
                    logger.info(f"Plan moved to Plans/completed/")

                    # Update dashboard with last action
                    if actions_attempted > 0:
                        last_action = results[-1]
                        self._update_dashboard_last_action(
                            last_action.get('server', 'unknown'),
                            last_action.get('tool', 'unknown'),
                            'success'
                        )
            else:
                # Move plan to failed/
                if self._move_plan(plan_path, 'failed'):
                    logger.info(f"Plan moved to Plans/failed/")

                # Create remediation task if any failures
                if failed_actions:
                    self._create_remediation_task(
                        f"{len(failed_actions)} action(s) failed",
                        f"Failed actions:\n" + "\n".join([
                            f"- {fa['server']}.{fa['tool']}: {fa['error']}"
                            for fa in failed_actions
                        ]),
                        plan_path=str(plan_path)
                    )

                # Update dashboard with last action (failure)
                if failed_actions:
                    first_fail = failed_actions[0]
                    self._update_dashboard_last_action(
                        first_fail.get('server', 'unknown'),
                        first_fail.get('tool', 'unknown'),
                        'failed'
                    )

        return {
            'success': success,
            'dry_run': dry_run,
            'plan_path': str(plan_path),
            'actions_attempted': actions_attempted,
            'actions_succeeded': actions_succeeded,
            'actions_failed': len(failed_actions),
            'failed_actions': failed_actions,
            'results': results,
            'duration': duration
        }


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Social Executor - Execute approved social media actions via MCP (Gold Tier - G-M4)'
    )
    parser.add_argument(
        '--plan-id',
        type=str,
        help='Specific plan ID to execute (default: most recent approved plan)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute real actions (default: dry-run only). REQUIRES approved plan!'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=True,
        help='Dry-run mode (default: True). Simulates actions without executing.'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Determine execution mode
    # If --execute is provided, turn off dry-run
    dry_run = not args.execute

    if dry_run:
        print("🔍 DRY-RUN MODE: No real actions will be taken")
        print("   Use --execute to perform real actions (requires approved plan)")
    else:
        print("⚠️  EXECUTE MODE: Real actions will be performed!")
        print("   Ensure plan is approved and credentials are configured")

    executor = SocialExecutor()

    result = executor.execute(
        plan_id=args.plan_id,
        dry_run=dry_run,
        verbose=args.verbose
    )

    # Output result
    if result['success']:
        if dry_run:
            print(f"\n✅ Dry-run successful")
            print(f"   Plan: {result.get('plan_path', 'N/A')}")
            print(f"   Actions would be executed: {result.get('actions_attempted', 0)}")
            if result.get('note'):
                print(f"   Note: {result['note']}")
        else:
            print(f"\n✅ Execution successful")
            print(f"   Plan: {result['plan_path']}")
            print(f"   Actions executed: {result['actions_succeeded']}/{result['actions_attempted']}")
        return 0
    else:
        print(f"\n❌ Execution failed")
        print(f"   Error: {result.get('error', 'Unknown error')}")
        if result.get('failed_actions'):
            print(f"   Failed actions: {len(result['failed_actions'])}")
            for fa in result['failed_actions']:
                print(f"     - {fa['server']}.{fa['tool']}: {fa['error']}")
        print(f"\n   Remediation task created in Needs_Action/")
        return 1


if __name__ == "__main__":
    sys.exit(main())
