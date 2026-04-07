#!/usr/bin/env python3
"""
Personal AI Employee - brain_handle_mcp_failure Skill
Gold Tier - G-M2: MCP Registry + Reliability Core

Purpose: Standard MCP failure handling - log, create remediation task, continue operations.
Tier: Gold
Skill ID: G-M2-T06

This skill handles MCP server failures gracefully by:
1. Logging error details to Logs/mcp_failures.log (JSON format)
2. Creating remediation task in Needs_Action/
3. Appending entry to system_log.md
4. Allowing operations to continue (graceful degradation)

CRITICAL: This skill NEVER claims success when MCP failed.
It logs failure and creates tasks for human intervention.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MCPFailureHandler:
    """Handle MCP server failures with logging and remediation task creation."""

    # Supported failure types
    FAILURE_TYPES = ['connection_timeout', 'auth_failure', 'rate_limit', 'server_error']

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize MCP failure handler.

        Args:
            config: Optional configuration dict with paths
        """
        self.config = config or self._default_config()
        self.logs_dir = Path(self.config['logs_dir'])
        self.needs_action_dir = Path(self.config['needs_action_dir'])
        self.system_log = Path(self.config['system_log'])
        self.mcp_failures_log = Path(self.config['mcp_failures_log'])

        # Ensure directories exist
        self.logs_dir.mkdir(exist_ok=True)
        self.needs_action_dir.mkdir(exist_ok=True)

    def _default_config(self) -> Dict:
        """Return default configuration."""
        base_dir = Path(__file__).parent
        return {
            'logs_dir': base_dir / 'Logs',
            'needs_action_dir': base_dir / 'Needs_Action',
            'system_log': base_dir / 'system_log.md',
            'mcp_failures_log': base_dir / 'Logs' / 'mcp_failures.log',
        }

    def log_failure(self, failure_type: str, server_name: str,
                    error_message: str) -> bool:
        """
        Log MCP failure to JSON log file.

        Args:
            failure_type: Type of failure (connection_timeout, auth_failure, etc.)
            server_name: Name of MCP server
            error_message: Error message details

        Returns:
            True if logged successfully, False otherwise
        """
        try:
            log_entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'failure_type': failure_type,
                'server_name': server_name,
                'error_message': error_message,
                'remediation_status': 'pending'
            }

            # Append to JSON log file (one JSON object per line)
            with open(self.mcp_failures_log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry) + '\n')

            logger.info(f"Failure logged to {self.mcp_failures_log}")
            return True

        except Exception as e:
            logger.error(f"Failed to log MCP failure: {e}")
            return False

    def create_remediation_task(self, failure_type: str, server_name: str,
                                 error_message: str) -> Optional[Path]:
        """
        Create remediation task in Needs_Action/.

        Filename format: remediation__mcp__<server>__YYYYMMDD-HHMM.md

        Args:
            failure_type: Type of failure
            server_name: Name of MCP server
            error_message: Error message details

        Returns:
            Path to created task file, or None if failed
        """
        try:
            timestamp = datetime.now(timezone.utc)
            timestamp_str = timestamp.strftime('%Y%m%d-%H%M')

            filename = f"remediation__mcp__{server_name}__{timestamp_str}.md"
            task_path = self.needs_action_dir / filename

            # Generate task content
            task_content = f"""---
id: remediation_mcp_{server_name}_{timestamp_str}
source: mcp_failure_handler
created_at: {timestamp.isoformat()}
failure_type: {failure_type}
server_name: {server_name}
status: pending
priority: high
plan_required: false
---

# MCP Server Remediation Required

## Server
**Name**: {server_name}

## Failure Details
**Type**: {failure_type}
**Timestamp**: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
**Error**: {error_message}

## Recommended Actions

"""

            # Add type-specific recommendations
            if failure_type == 'connection_timeout':
                task_content += """### Connection Timeout
1. Verify MCP server is running
2. Check network connectivity
3. Review firewall settings
4. Check server logs for issues
5. Verify server URL/port configuration
"""
            elif failure_type == 'auth_failure':
                task_content += """### Authentication Failure
1. Verify API credentials are current
2. Check if token has expired
3. Verify API key permissions
4. Review secrets in .secrets/ directory
5. Regenerate credentials if needed
"""
            elif failure_type == 'rate_limit':
                task_content += """### Rate Limit Exceeded
1. Review rate_limit_and_backoff configuration
2. Check if retry delays are sufficient
3. Consider reducing request frequency
4. Verify API tier/quota limits
5. Contact provider if limits too restrictive
"""
            elif failure_type == 'server_error':
                task_content += """### Server Error (5xx)
1. Check MCP server status/health endpoint
2. Review server logs for errors
3. Verify server configuration
4. Check for recent server updates
5. Contact server administrator if persistent
"""

            task_content += f"""
## Next Steps
1. Investigate root cause using recommendations above
2. Document findings in this file
3. Apply fix
4. Test with: `python brain_mcp_registry_refresh_skill.py --server {server_name}`
5. Move to Done/ when resolved

## Resolution Notes
(Document resolution here before moving to Done/)

---
**Generated by**: brain_handle_mcp_failure
**Skill Version**: 1.0.0
"""

            task_path.write_text(task_content, encoding='utf-8')
            logger.info(f"Remediation task created: {task_path}")
            return task_path

        except Exception as e:
            logger.error(f"Failed to create remediation task: {e}")
            return None

    def log_to_system(self, failure_type: str, server_name: str,
                     error_message: str) -> None:
        """
        Append entry to system_log.md.

        Args:
            failure_type: Type of failure
            server_name: Name of MCP server
            error_message: Error message details
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            log_entry = (
                f"\n**[{timestamp}]** ⚠️ MCP Failure - {server_name}: "
                f"{failure_type} - {error_message}\n"
            )

            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)

        except Exception as e:
            logger.error(f"Failed to write to system_log.md: {e}")

    def handle_failure(self, failure_type: str, server_name: str,
                      error_message: str) -> Dict:
        """
        Handle MCP failure with full logging and task creation.

        Args:
            failure_type: Type of failure (must be in FAILURE_TYPES)
            server_name: Name of MCP server
            error_message: Error message details

        Returns:
            Dict with handling results
        """
        if failure_type not in self.FAILURE_TYPES:
            logger.error(f"Invalid failure type: {failure_type}")
            return {
                'success': False,
                'error': f"Invalid failure type. Must be one of: {', '.join(self.FAILURE_TYPES)}"
            }

        logger.warning(
            f"Handling MCP failure - Server: {server_name}, "
            f"Type: {failure_type}, Error: {error_message}"
        )

        # Log failure
        log_success = self.log_failure(failure_type, server_name, error_message)

        # Create remediation task
        task_path = self.create_remediation_task(failure_type, server_name, error_message)

        # Log to system_log.md
        self.log_to_system(failure_type, server_name, error_message)

        result = {
            'success': True,
            'failure_logged': log_success,
            'remediation_task_created': task_path is not None,
            'remediation_task_path': str(task_path) if task_path else None,
            'server_name': server_name,
            'failure_type': failure_type
        }

        if log_success and task_path:
            logger.info("✅ MCP failure handled successfully")
        else:
            logger.warning("⚠️ MCP failure handling incomplete")

        return result


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Handle MCP server failures with logging and remediation task creation'
    )
    parser.add_argument(
        '--failure-type',
        type=str,
        required=True,
        choices=MCPFailureHandler.FAILURE_TYPES,
        help='Type of failure'
    )
    parser.add_argument(
        '--server-name',
        type=str,
        required=True,
        help='Name of MCP server that failed'
    )
    parser.add_argument(
        '--error-message',
        type=str,
        required=True,
        help='Error message details'
    )

    args = parser.parse_args()

    handler = MCPFailureHandler()

    result = handler.handle_failure(
        failure_type=args.failure_type,
        server_name=args.server_name,
        error_message=args.error_message
    )

    if result['success']:
        print(f"\n✅ MCP Failure Handled")
        print(f"   Server: {result['server_name']}")
        print(f"   Type: {result['failure_type']}")
        print(f"   Failure logged: {result['failure_logged']}")
        print(f"   Remediation task: {result['remediation_task_path']}")
        return 0
    else:
        print(f"\n❌ Failed to handle MCP failure: {result.get('error')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
