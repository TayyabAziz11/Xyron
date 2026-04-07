#!/usr/bin/env python3
"""
Personal AI Employee - brain_email_query_with_mcp Skill
Silver Tier - M6.2: Gmail MCP Query Operations (Read-Only)

Purpose: List, search, and read emails via Gmail MCP (no approval required).
Tier: Silver
Skill ID: 19

CRITICAL: This skill is for READ-ONLY operations. NO side effects.
NO approval required, but ALL operations are logged.
"""

import os
import sys
import re
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any


class GmailMCPQuery:
    """Query Gmail via MCP (read-only operations)."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize Gmail MCP query handler.

        Args:
            config: Optional configuration dict with paths
        """
        self.config = config or self._default_config()
        self.mcp_log = Path(self.config['mcp_log'])
        self.system_log = Path(self.config['system_log'])
        self.tools_snapshot = Path(self.config['tools_snapshot'])

        # Ensure directories exist
        self.mcp_log.parent.mkdir(parents=True, exist_ok=True)

        # Query operations (read-only, no approval needed)
        self.query_operations = {
            'list_messages', 'list_emails', 'listMessages',
            'search_messages', 'search_emails', 'findMessage', 'searchMessages',
            'get_message', 'read_email', 'getMessage', 'readEmail',
            'list_labels', 'listLabels', 'getLabels',
            'get_thread', 'read_thread', 'getThread', 'readThread'
        }

    def _default_config(self) -> Dict:
        """Return default configuration."""
        base_dir = get_repo_root()
        return {
            'mcp_log': base_dir / 'Logs' / 'mcp_actions.log',
            'system_log': base_dir / 'system_log.md',
            'tools_snapshot': base_dir / 'Logs' / 'mcp_tools_snapshot.json',
        }

    def _redact_pii(self, text: str) -> str:
        """
        Redact PII from text (emails, phones).

        Args:
            text: Text to redact

        Returns:
            Redacted text
        """
        if not text:
            return text

        # Redact email addresses
        text = re.sub(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            '<REDACTED_EMAIL>',
            text
        )

        # Redact phone numbers (various formats)
        text = re.sub(
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
            '<REDACTED_PHONE>',
            text
        )
        text = re.sub(
            r'\+\d{1,3}[-.]?\d{1,4}[-.]?\d{1,4}[-.]?\d{1,9}',
            '<REDACTED_PHONE>',
            text
        )

        return text

    def _call_mcp(
        self,
        operation: str,
        parameters: Optional[Dict] = None
    ) -> Dict:
        """
        Call Gmail MCP server (or simulate if not available).

        Args:
            operation: Gmail operation (list_messages, search_messages, etc.)
            parameters: Operation parameters

        Returns:
            Dict with operation result
        """
        parameters = parameters or {}
        start_time = time.time()

        # Check if MCP Gmail server is available
        # In real implementation, this would check MCP server connectivity
        # For now, we simulate since real MCP may not be configured
        mcp_available = False  # TODO: Replace with real MCP connectivity check

        if mcp_available:
            # Real MCP call would go here
            # result = mcp_client.call_tool('gmail', operation, parameters)
            result = {
                'success': False,
                'mode': 'mcp-unavailable',
                'error': 'Real MCP integration pending - simulation mode active'
            }
        else:
            # Simulation mode
            result = self._simulate_query(operation, parameters)

        duration = time.time() - start_time
        result['duration_ms'] = int(duration * 1000)

        return result

    def _simulate_query(self, operation: str, parameters: Dict) -> Dict:
        """
        Simulate Gmail query (fallback when MCP unavailable).

        Args:
            operation: Query operation
            parameters: Query parameters

        Returns:
            Simulated result
        """
        # Simulate processing time
        time.sleep(0.1)

        if operation in ['list_messages', 'list_emails', 'listMessages']:
            # Simulate list operation
            max_results = parameters.get('maxResults', parameters.get('max_results', 5))
            return {
                'success': True,
                'mode': 'simulation',
                'operation': operation,
                'message': f'SIMULATED: Listed {max_results} messages',
                'data': {
                    'messages': [
                        {
                            'id': f'msg_{i}',
                            'subject': f'<REDACTED_EMAIL> - Message {i}',
                            'from': '<REDACTED_EMAIL>',
                            'date': '2026-02-14'
                        }
                        for i in range(min(max_results, 3))
                    ],
                    'count': min(max_results, 3)
                }
            }

        elif operation in ['search_messages', 'search_emails', 'findMessage', 'searchMessages']:
            # Simulate search operation
            query = parameters.get('query', parameters.get('q', ''))
            return {
                'success': True,
                'mode': 'simulation',
                'operation': operation,
                'message': f'SIMULATED: Searched for "{query}"',
                'data': {
                    'messages': [
                        {
                            'id': 'msg_search_001',
                            'subject': '<REDACTED_EMAIL> - Search result',
                            'snippet': 'Matching email content...',
                            'from': '<REDACTED_EMAIL>'
                        }
                    ],
                    'count': 1
                }
            }

        elif operation in ['get_message', 'read_email', 'getMessage', 'readEmail']:
            # Simulate get message operation
            message_id = parameters.get('messageId', parameters.get('message_id', 'unknown'))
            return {
                'success': True,
                'mode': 'simulation',
                'operation': operation,
                'message': f'SIMULATED: Retrieved message {message_id[:8]}...',
                'data': {
                    'id': message_id,
                    'subject': '<REDACTED_EMAIL> - Email subject',
                    'from': '<REDACTED_EMAIL>',
                    'to': '<REDACTED_EMAIL>',
                    'body': 'Email body content (redacted)...',
                    'date': '2026-02-14 00:00:00 UTC'
                }
            }

        elif operation in ['list_labels', 'listLabels', 'getLabels']:
            # Simulate list labels operation
            return {
                'success': True,
                'mode': 'simulation',
                'operation': operation,
                'message': 'SIMULATED: Listed Gmail labels',
                'data': {
                    'labels': [
                        {'id': 'INBOX', 'name': 'INBOX'},
                        {'id': 'SENT', 'name': 'SENT'},
                        {'id': 'DRAFT', 'name': 'DRAFT'},
                        {'id': 'SPAM', 'name': 'SPAM'}
                    ],
                    'count': 4
                }
            }

        else:
            return {
                'success': False,
                'mode': 'simulation',
                'error': f'Unsupported query operation: {operation}'
            }

    def query(
        self,
        operation: str,
        parameters: Optional[Dict] = None,
        verbose: bool = False
    ) -> Dict:
        """
        Execute a Gmail query operation.

        Args:
            operation: Query operation name
            parameters: Operation parameters
            verbose: Show detailed output

        Returns:
            Dict with query results
        """
        parameters = parameters or {}

        # Validate operation is read-only
        if operation not in self.query_operations:
            raise ValueError(
                f"Operation '{operation}' not allowed. "
                f"Use brain_execute_with_mcp for action operations."
            )

        # Execute query
        result = self._call_mcp(operation, parameters)

        # Log query
        self._log_query(operation, parameters, result)

        # Display results if verbose
        if verbose:
            self._display_results(operation, result)

        return result

    def _log_query(
        self,
        operation: str,
        parameters: Dict,
        result: Dict
    ):
        """
        Log query to mcp_actions.log (JSON format).

        Args:
            operation: Query operation
            parameters: Query parameters
            result: Query result
        """
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        # Redact parameters
        redacted_params = {
            k: self._redact_pii(str(v)) if isinstance(v, str) else v
            for k, v in parameters.items()
        }

        # Create JSON log entry
        log_entry = {
            'timestamp': timestamp,
            'plan_id': 'ad-hoc-query',
            'tool': 'gmail',
            'operation': operation,
            'parameters': redacted_params,
            'mode': 'query',
            'success': result.get('success', False),
            'duration_ms': result.get('duration_ms', 0),
            'response_summary': self._redact_pii(result.get('message', 'No message'))
        }

        if not result.get('success'):
            log_entry['error'] = result.get('error', 'Unknown error')

        # Append JSON line to log
        with open(self.mcp_log, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry) + '\n')

        # Also log to system_log.md
        self._log_to_system(operation, result)

    def _log_to_system(self, operation: str, result: Dict):
        """
        Log query to system_log.md.

        Args:
            operation: Query operation
            result: Query result
        """
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        status = "OK" if result.get('success') else "FAILED"

        log_entry = f"""
[{timestamp}] GMAIL QUERY
- Operation: {operation}
- Mode: query (read-only)
- Duration: {result.get('duration_ms', 0)}ms
- Status: {status}
- Skill: brain_email_query_with_mcp (M6.2)
- Outcome: {status}

"""

        # Append to system_log.md
        if self.system_log.exists():
            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)

    def _display_results(self, operation: str, result: Dict):
        """
        Display query results to console.

        Args:
            operation: Query operation
            result: Query result
        """
        print()
        print("=" * 70)
        print("  GMAIL QUERY RESULTS")
        print("=" * 70)
        print(f"Operation: {operation}")
        print(f"Mode: {result.get('mode', 'unknown')}")
        print(f"Success: {result.get('success', False)}")
        print(f"Duration: {result.get('duration_ms', 0)}ms")
        print()

        if result.get('success'):
            print(f"Message: {result.get('message', 'No message')}")

            if 'data' in result:
                print()
                print("Data:")
                data = result['data']

                # Display messages
                if 'messages' in data:
                    print(f"  Found {data.get('count', 0)} message(s):")
                    for msg in data['messages'][:5]:  # Limit to first 5
                        print(f"    - {msg.get('subject', 'No subject')} (from {msg.get('from', 'unknown')})")

                # Display labels
                elif 'labels' in data:
                    print(f"  Found {data.get('count', 0)} label(s):")
                    for label in data['labels'][:10]:  # Limit to first 10
                        print(f"    - {label.get('name', 'unknown')}")

                # Display single message
                elif 'id' in data:
                    print(f"  ID: {data.get('id', 'unknown')[:8]}...")
                    print(f"  Subject: {data.get('subject', 'No subject')}")
                    print(f"  From: {data.get('from', 'unknown')}")
                    print(f"  Date: {data.get('date', 'unknown')}")
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")

        print("=" * 70)
        print()


def main():
    """CLI interface for brain_email_query_with_mcp skill."""
    parser = argparse.ArgumentParser(
        description='Brain Email Query with MCP - Read-only Gmail operations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List recent emails
  python brain_email_query_with_mcp_skill.py --operation list_messages --params '{"maxResults": 5}'

  # Search emails
  python brain_email_query_with_mcp_skill.py --operation search_messages --params '{"query": "newer_than:7d"}'

  # Get specific message
  python brain_email_query_with_mcp_skill.py --operation get_message --params '{"messageId": "abc123"}'

  # List labels
  python brain_email_query_with_mcp_skill.py --operation list_labels

  # Shorthand for common operations
  python brain_email_query_with_mcp_skill.py --query "newer_than:7d" --max-results 3
        """
    )

    parser.add_argument(
        '--operation',
        help='Query operation (list_messages, search_messages, get_message, list_labels)'
    )
    parser.add_argument(
        '--params',
        help='Operation parameters (JSON string)'
    )
    parser.add_argument(
        '--query',
        help='Shorthand for search_messages query parameter'
    )
    parser.add_argument(
        '--max-results',
        type=int,
        help='Shorthand for maxResults parameter'
    )
    parser.add_argument(
        '--message-id',
        help='Shorthand for get_message messageId parameter'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    args = parser.parse_args()

    # Initialize query handler
    handler = GmailMCPQuery()

    try:
        # Determine operation and parameters
        operation = args.operation
        parameters = {}

        # Parse params JSON if provided
        if args.params:
            parameters = json.loads(args.params)

        # Handle shorthand arguments
        if args.query and not operation:
            operation = 'search_messages'
            parameters['query'] = args.query

        if args.max_results:
            parameters['maxResults'] = args.max_results

        if args.message_id:
            operation = 'get_message'
            parameters['messageId'] = args.message_id

        # Default operation if none specified
        if not operation:
            operation = 'list_messages'
            parameters['maxResults'] = parameters.get('maxResults', 5)

        # Execute query
        result = handler.query(operation, parameters, verbose=args.verbose)

        # Print summary if not verbose
        if not args.verbose:
            status = "✓" if result.get('success') else "✗"
            print(f"{status} Query completed: {operation}")
            print(f"  Mode: {result.get('mode', 'unknown')}")
            print(f"  Duration: {result.get('duration_ms', 0)}ms")
            if result.get('success'):
                print(f"  Message: {result.get('message', 'No message')}")
            else:
                print(f"  Error: {result.get('error', 'Unknown error')}")

        # Exit with appropriate code
        sys.exit(0 if result.get('success') else 1)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
