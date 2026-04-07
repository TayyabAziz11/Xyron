#!/usr/bin/env python3
"""
Personal AI Employee - brain_mcp_registry_refresh Skill
Gold Tier - G-M2: MCP Registry + Reliability Core

Purpose: Discover and cache MCP server tool lists for reliability and offline reference.
Tier: Gold
Skill ID: G-M2-T04

This skill queries all configured MCP servers for their available tools and saves
snapshots to Logs/mcp_tools_snapshot_<server>.json. It updates Dashboard.md with
server reachability status.

IMPORTANT: This skill ONLY queries tools, it never calls action tools.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MCPRegistryRefresh:
    """Discover and cache MCP server tool registries."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize MCP registry refresh handler.

        Args:
            config: Optional configuration dict with paths
        """
        self.config = config or self._default_config()
        self.logs_dir = Path(self.config['logs_dir'])
        self.dashboard = Path(self.config['dashboard'])
        self.system_log = Path(self.config['system_log'])
        self.mcp_registry_log = Path(self.config['mcp_registry_log'])

        # Ensure logs directory exists
        self.logs_dir.mkdir(exist_ok=True)

    def _default_config(self) -> Dict:
        """Return default configuration."""
        base_dir = Path(__file__).parent
        return {
            'logs_dir': base_dir / 'Logs',
            'dashboard': base_dir / 'Dashboard.md',
            'system_log': base_dir / 'system_log.md',
            'mcp_registry_log': base_dir / 'Logs' / 'mcp_registry.log',
            'mcp_servers': ['whatsapp', 'linkedin', 'twitter', 'odoo'],
        }

    def get_mock_tools(self, server_name: str) -> List[Dict]:
        """
        Get mock tool list for a server (for testing when MCP not configured).

        Args:
            server_name: Server name (whatsapp, linkedin, twitter, odoo)

        Returns:
            List of mock tools
        """
        mock_tools = {
            'whatsapp': [
                {
                    'name': 'whatsapp.send_message',
                    'description': 'Send a WhatsApp message',
                    'parameters': {
                        'to': {'type': 'string', 'required': True},
                        'message': {'type': 'string', 'required': True}
                    },
                    'returns': 'message_id'
                },
                {
                    'name': 'whatsapp.list_messages',
                    'description': 'List recent WhatsApp messages',
                    'parameters': {
                        'limit': {'type': 'integer', 'required': False, 'default': 10}
                    },
                    'returns': 'message_list'
                }
            ],
            'linkedin': [
                {
                    'name': 'linkedin.create_post',
                    'description': 'Create a LinkedIn post',
                    'parameters': {
                        'content': {'type': 'string', 'required': True},
                        'visibility': {'type': 'string', 'required': False, 'default': 'public'}
                    },
                    'returns': 'post_id'
                },
                {
                    'name': 'linkedin.get_post_analytics',
                    'description': 'Get analytics for a LinkedIn post',
                    'parameters': {
                        'post_id': {'type': 'string', 'required': True}
                    },
                    'returns': 'analytics_data'
                }
            ],
            'twitter': [
                {
                    'name': 'twitter.create_post',
                    'description': 'Create a tweet',
                    'parameters': {
                        'content': {'type': 'string', 'required': True},
                        'media_ids': {'type': 'array', 'required': False}
                    },
                    'returns': 'tweet_id'
                },
                {
                    'name': 'twitter.search_mentions',
                    'description': 'Search for mentions',
                    'parameters': {
                        'query': {'type': 'string', 'required': True},
                        'max_results': {'type': 'integer', 'required': False, 'default': 10}
                    },
                    'returns': 'mention_list'
                }
            ],
            'odoo': [
                {
                    'name': 'odoo.list_unpaid_invoices',
                    'description': 'List unpaid invoices',
                    'parameters': {
                        'limit': {'type': 'integer', 'required': False, 'default': 50}
                    },
                    'returns': 'invoice_list'
                },
                {
                    'name': 'odoo.revenue_summary',
                    'description': 'Get revenue summary',
                    'parameters': {
                        'period': {'type': 'string', 'required': False, 'default': 'month'}
                    },
                    'returns': 'revenue_data'
                }
            ]
        }

        return mock_tools.get(server_name, [])

    def query_server_tools(self, server_name: str, mock: bool = True) -> Optional[Dict]:
        """
        Query MCP server for available tools.

        Args:
            server_name: Name of MCP server
            mock: Use mock data (True for now, False when MCP configured)

        Returns:
            Server info dict with tools, or None if unreachable
        """
        try:
            if mock:
                logger.info(f"Using mock data for server: {server_name}")
                tools = self.get_mock_tools(server_name)

                return {
                    'server_name': server_name,
                    'status': 'reachable',
                    'snapshot_timestamp': datetime.now(timezone.utc).isoformat(),
                    'version': '1.0',
                    'tool_count': len(tools),
                    'tools': tools
                }

            # TODO: Real MCP server query (G-M4)
            # For now, return None to simulate unreachable
            logger.warning(f"Real MCP query not implemented yet for: {server_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to query {server_name}: {e}")
            return None

    def save_snapshot(self, server_name: str, snapshot: Dict, dry_run: bool = False) -> bool:
        """
        Save tool snapshot to JSON file.

        Args:
            server_name: Server name
            snapshot: Snapshot data dict
            dry_run: If True, don't write file

        Returns:
            True if successful, False otherwise
        """
        if dry_run:
            logger.info(f"[DRY-RUN] Would save snapshot for {server_name}")
            return True

        try:
            snapshot_path = self.logs_dir / f"mcp_tools_snapshot_{server_name}.json"

            with open(snapshot_path, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)

            logger.info(f"Snapshot saved: {snapshot_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save snapshot for {server_name}: {e}")
            return False

    def update_dashboard(self, servers_status: List[Dict], dry_run: bool = False) -> bool:
        """
        Update Dashboard.md with MCP Registry Status section.

        Args:
            servers_status: List of server status dicts
            dry_run: If True, don't write file

        Returns:
            True if successful, False otherwise
        """
        if dry_run:
            logger.info("[DRY-RUN] Would update Dashboard.md")
            return True

        try:
            if not self.dashboard.exists():
                logger.warning(f"Dashboard not found: {self.dashboard}")
                return False

            content = self.dashboard.read_text(encoding='utf-8')

            # Generate MCP Registry Status table
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

            table_rows = []
            if not servers_status:
                status_table = "**Status**: No servers configured\n"
            else:
                for server in servers_status:
                    status_icon = "✅" if server['status'] == 'reachable' else "❌"
                    table_rows.append(
                        f"| {server['name']} | {status_icon} {server['status']} | "
                        f"{server.get('last_refresh', 'Never')} | {server.get('tool_count', 0)} |"
                    )

                status_table = f"""| Server | Status | Last Refresh | Tool Count |
|--------|--------|--------------|------------|
{chr(10).join(table_rows)}

**Last Updated**: {timestamp}
"""

            # Find and replace MCP Registry Status section
            mcp_section_pattern = r'(## MCP Registry Status.*?\n\n)(.*?)(\n\n##|\Z)'

            import re
            match = re.search(mcp_section_pattern, content, re.DOTALL)

            if match:
                # Replace existing section
                new_content = re.sub(
                    mcp_section_pattern,
                    r'\1' + status_table + r'\3',
                    content,
                    flags=re.DOTALL
                )
            else:
                # Section doesn't exist, add before "## Gold Tier Vault Structure" or at end
                if "## Gold Tier Vault Structure" in content:
                    new_section = f"\n## MCP Registry Status\n\n{status_table}\n"
                    new_content = content.replace(
                        "## Gold Tier Vault Structure",
                        new_section + "## Gold Tier Vault Structure"
                    )
                else:
                    new_content = content + f"\n\n## MCP Registry Status\n\n{status_table}\n"

            self.dashboard.write_text(new_content, encoding='utf-8')
            logger.info("Dashboard.md updated with MCP Registry Status")
            return True

        except Exception as e:
            logger.error(f"Failed to update dashboard: {e}")
            return False

    def log_to_system(self, message: str, dry_run: bool = False) -> None:
        """
        Append entry to system_log.md.

        Args:
            message: Log message
            dry_run: If True, don't write file
        """
        if dry_run:
            logger.info(f"[DRY-RUN] Would log: {message}")
            return

        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            log_entry = f"\n**[{timestamp}]** {message}\n"

            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)

        except Exception as e:
            logger.error(f"Failed to write to system_log.md: {e}")

    def refresh(self, server_name: Optional[str] = None, dry_run: bool = False,
                once: bool = False, mock: bool = True) -> Dict:
        """
        Refresh MCP registry for all servers or a specific server.

        Args:
            server_name: Specific server to refresh (None = all servers)
            dry_run: Don't write files, only simulate
            once: Run once and exit (vs. continuous monitoring)
            mock: Use mock data for testing

        Returns:
            Dict with refresh results
        """
        servers = [server_name] if server_name else self.config['mcp_servers']

        results = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'servers_queried': len(servers),
            'servers_reachable': 0,
            'servers_unreachable': 0,
            'snapshots_saved': 0,
            'servers': []
        }

        logger.info(f"Starting MCP registry refresh (mock={mock}, dry_run={dry_run})")

        for server in servers:
            logger.info(f"Querying server: {server}")

            snapshot = self.query_server_tools(server, mock=mock)

            if snapshot and snapshot['status'] == 'reachable':
                results['servers_reachable'] += 1

                if self.save_snapshot(server, snapshot, dry_run=dry_run):
                    results['snapshots_saved'] += 1

                results['servers'].append({
                    'name': server,
                    'status': 'reachable',
                    'last_refresh': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                    'tool_count': snapshot['tool_count']
                })
            else:
                results['servers_unreachable'] += 1
                results['servers'].append({
                    'name': server,
                    'status': 'unreachable',
                    'last_refresh': 'Never',
                    'tool_count': 0
                })
                logger.warning(f"Server unreachable: {server}")

        # Update Dashboard
        self.update_dashboard(results['servers'], dry_run=dry_run)

        # Log to system_log.md
        summary = (
            f"MCP Registry Refresh: {results['servers_reachable']} reachable, "
            f"{results['servers_unreachable']} unreachable, "
            f"{results['snapshots_saved']} snapshots saved"
        )
        self.log_to_system(summary, dry_run=dry_run)

        logger.info(f"Refresh complete: {summary}")
        return results


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Refresh MCP tool registry from all configured servers'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate refresh without writing files'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (default behavior for now)'
    )
    parser.add_argument(
        '--list-servers',
        action='store_true',
        help='List configured MCP servers and exit'
    )
    parser.add_argument(
        '--server',
        type=str,
        help='Refresh specific server only'
    )
    parser.add_argument(
        '--mock',
        action='store_true',
        default=True,
        help='Use mock data for testing (default: True)'
    )

    args = parser.parse_args()

    refresher = MCPRegistryRefresh()

    if args.list_servers:
        print("Configured MCP servers:")
        for server in refresher.config['mcp_servers']:
            print(f"  - {server}")
        return 0

    results = refresher.refresh(
        server_name=args.server,
        dry_run=args.dry_run,
        once=args.once,
        mock=args.mock
    )

    print(f"\n✅ MCP Registry Refresh Complete")
    print(f"   Servers queried: {results['servers_queried']}")
    print(f"   Reachable: {results['servers_reachable']}")
    print(f"   Unreachable: {results['servers_unreachable']}")
    print(f"   Snapshots saved: {results['snapshots_saved']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
