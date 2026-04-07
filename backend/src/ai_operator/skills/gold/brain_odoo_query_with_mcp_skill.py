#!/usr/bin/env python3
"""
Personal AI Employee - Odoo Query Skill
Gold Tier - G-M5: Odoo MCP Integration (Query Operations)

Purpose: Read-only Odoo queries via MCP or mock fallback (NO approval required)
Tier: Gold
Skill ID: G-M5-T03

CRITICAL: This is QUERY-ONLY. NO approval required. NO write operations.
Supports: list_unpaid_invoices, revenue_summary, ar_aging_summary, list_customers

Features:
- Mock mode with templates/mock_odoo_invoices.json
- Real mode with MCP query tools (optional)
- PII redaction using mcp_helpers
- JSON logging to Logs/mcp_actions.log
- Optional report generation to Business/Accounting/Reports/
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Import PII redaction helper
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


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OdooQueryExecutor:
    """Odoo Query Executor - Read-only operations (no approval required)"""

    SUPPORTED_OPERATIONS = [
        'list_unpaid_invoices',
        'revenue_summary',
        'ar_aging_summary',
        'list_customers'
    ]

    def __init__(self, config: Dict):
        self.config = config
        self.results = {}

    def _load_mock_invoices(self) -> List[Dict]:
        """Load mock invoices from fixture file"""
        fixture_path = self.config['mock_fixture_path']

        if not os.path.exists(fixture_path):
            raise FileNotFoundError(f"Mock fixture not found: {fixture_path}")

        try:
            with open(fixture_path, 'r') as f:
                invoices = json.load(f)

            if not isinstance(invoices, list):
                raise ValueError("Mock fixture must be a JSON array")

            logger.info(f"Loaded {len(invoices)} mock invoices")
            return invoices

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in mock fixture: {e}")

    def _query_unpaid_invoices_mock(self) -> Dict:
        """Mock: List unpaid invoices"""
        invoices = self._load_mock_invoices()
        unpaid = [inv for inv in invoices if inv['amount_residual'] > 0]

        return {
            'operation': 'list_unpaid_invoices',
            'count': len(unpaid),
            'total_amount_due': sum(inv['amount_residual'] for inv in unpaid),
            'invoices': unpaid
        }

    def _query_revenue_summary_mock(self) -> Dict:
        """Mock: Revenue summary"""
        invoices = self._load_mock_invoices()

        total_invoiced = sum(inv['amount_total'] for inv in invoices)
        total_paid = sum(inv['amount_total'] - inv['amount_residual'] for inv in invoices)
        total_outstanding = sum(inv['amount_residual'] for inv in invoices)

        return {
            'operation': 'revenue_summary',
            'total_invoiced': total_invoiced,
            'total_paid': total_paid,
            'total_outstanding': total_outstanding,
            'outstanding_percentage': (total_outstanding / total_invoiced * 100) if total_invoiced > 0 else 0
        }

    def _query_ar_aging_summary_mock(self) -> Dict:
        """Mock: AR aging summary"""
        invoices = self._load_mock_invoices()
        unpaid = [inv for inv in invoices if inv['amount_residual'] > 0]

        current = sum(inv['amount_residual'] for inv in unpaid if inv['days_overdue'] == 0)
        days_1_30 = sum(inv['amount_residual'] for inv in unpaid if 1 <= inv['days_overdue'] <= 30)
        days_31_60 = sum(inv['amount_residual'] for inv in unpaid if 31 <= inv['days_overdue'] <= 60)
        days_61_90 = sum(inv['amount_residual'] for inv in unpaid if 61 <= inv['days_overdue'] <= 90)
        over_90 = sum(inv['amount_residual'] for inv in unpaid if inv['days_overdue'] > 90)

        total = current + days_1_30 + days_31_60 + days_61_90 + over_90
        return {
            'operation': 'ar_aging_summary',
            'aging': {'current': current, '1_30': days_1_30, '31_60': days_31_60, '61_90': days_61_90, 'over_90': over_90},
            'current': current,
            '1-30_days': days_1_30,
            '31-60_days': days_31_60,
            '61-90_days': days_61_90,
            'over_90_days': over_90,
            'total_outstanding': total,
        }

    def _query_customers_mock(self) -> Dict:
        """Mock: List customers"""
        invoices = self._load_mock_invoices()

        customers = {}
        for inv in invoices:
            customer_id = inv['partner_id']
            if customer_id not in customers:
                customers[customer_id] = {
                    'id': customer_id,
                    'name': inv['customer_name'],
                    'invoice_count': 0,
                    'total_invoiced': 0.0,
                    'total_outstanding': 0.0
                }

            customers[customer_id]['invoice_count'] += 1
            customers[customer_id]['total_invoiced'] += inv['amount_total']
            customers[customer_id]['total_outstanding'] += inv['amount_residual']

        return {
            'operation': 'list_customers',
            'count': len(customers),
            'customers': list(customers.values())
        }

    def _query_real(self, operation: str) -> Dict:
        """
        Execute query against real Odoo via OdooAPIHelper (JSON-RPC).

        IMPORTANT: This ONLY calls QUERY methods (read-only).
        NEVER calls ACTION methods (create_invoice, post_invoice, register_payment).

        Returns:
            Query result dict
        """
        try:
            from ai_operator.core.odoo_api_helper import OdooAPIHelper

            base_dir = Path(self.config['base_dir'])
            credentials_path = str(base_dir / '.secrets' / 'odoo_credentials.json')

            helper = OdooAPIHelper(credentials_path=credentials_path, mock=False)

            if not helper.load_credentials():
                logger.warning("Odoo credentials not found or invalid at .secrets/odoo_credentials.json")
                return {'error': 'Credentials not found or invalid. Check .secrets/odoo_credentials.json', 'operation': operation}

            if operation == 'list_unpaid_invoices':
                result = helper.list_invoices(status_filter='unpaid', limit=50)
                if not result.get('success'):
                    return {'error': result.get('error', 'Query failed'), 'operation': operation}
                invoices = result['invoices']
                return {
                    'operation': 'list_unpaid_invoices',
                    'count': len(invoices),
                    'total_amount_due': sum(inv.get('amount_residual', 0) for inv in invoices),
                    'invoices': invoices,
                }

            elif operation == 'revenue_summary':
                result = helper.revenue_summary()
                if not result.get('success'):
                    return {'error': result.get('error', 'Query failed'), 'operation': operation}
                total_invoiced = result.get('total_invoiced', 0)
                total_outstanding = result.get('total_outstanding', 0)
                return {
                    'operation': 'revenue_summary',
                    'total_invoiced': total_invoiced,
                    'total_paid': result.get('total_paid', 0),
                    'total_outstanding': total_outstanding,
                    'outstanding_percentage': (total_outstanding / total_invoiced * 100) if total_invoiced else 0,
                }

            elif operation == 'ar_aging_summary':
                result = helper.ar_aging_summary()
                if not result.get('success'):
                    return {'error': result.get('error', 'Query failed'), 'operation': operation}
                aging = result.get('aging', {})
                return {
                    'operation': 'ar_aging_summary',
                    'aging': aging,
                    'current': aging.get('current', 0),
                    '1-30_days': aging.get('1_30', 0),
                    '31-60_days': aging.get('31_60', 0),
                    '61-90_days': aging.get('61_90', 0),
                    'over_90_days': aging.get('over_90', 0),
                    'total_outstanding': sum(aging.values()),
                }

            elif operation == 'list_customers':
                result = helper.list_customers(limit=50)
                if not result.get('success'):
                    return {'error': result.get('error', 'Query failed'), 'operation': operation}
                customers = result['customers']
                return {
                    'operation': 'list_customers',
                    'count': len(customers),
                    'customers': customers,
                }

            else:
                return {'error': f'Unknown operation: {operation}', 'operation': operation}

        except Exception as e:
            logger.error(f"Failed to query Odoo: {e}")
            return {'error': str(e), 'operation': operation}

    def _log_query(self, operation: str, result: Dict, mode: str):
        """Log query to mcp_actions.log"""
        log_path = self.config['log_path']

        log_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'operation': operation,
            'mode': mode,
            'status': 'success' if 'error' not in result else 'error',
            'result_summary': redact_pii(str(result)[:200])  # Truncated, PII-safe
        }

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            logger.warning(f"Could not write to log: {e}")

    def _generate_report(self, operation: str, result: Dict) -> Optional[str]:
        """Generate optional report markdown"""
        if not self.config.get('generate_report', False):
            return None

        timestamp = datetime.now(timezone.utc)
        report_dir = Path(self.config['base_dir']) / 'Business' / 'Accounting' / 'Reports'
        report_dir.mkdir(parents=True, exist_ok=True)

        report_name = f"odoo_query__{operation}__{timestamp.strftime('%Y%m%d-%H%M')}.md"
        report_path = report_dir / report_name

        # Build report content based on operation
        if operation == 'list_unpaid_invoices':
            content = f"""# Odoo Query Report - Unpaid Invoices

**Generated**: {timestamp.isoformat()}
**Operation**: {operation}
**Source**: Odoo Query Skill (G-M5)

## Summary

- **Total Unpaid Invoices**: {result.get('count', 0)}
- **Total Amount Due**: ${result.get('total_amount_due', 0):,.2f}

## Invoices

"""
            for inv in result.get('invoices', [])[:10]:  # Limit to 10 for readability
                content += f"- **{inv['invoice_number']}**: {inv['customer_name']} - ${inv['amount_residual']:,.2f} (Due: {inv['due_date']}, Overdue: {inv['days_overdue']} days)\n"

        elif operation == 'revenue_summary':
            content = f"""# Odoo Query Report - Revenue Summary

**Generated**: {timestamp.isoformat()}
**Operation**: {operation}
**Source**: Odoo Query Skill (G-M5)

## Summary

- **Total Invoiced**: ${result.get('total_invoiced', 0):,.2f}
- **Total Paid**: ${result.get('total_paid', 0):,.2f}
- **Total Outstanding**: ${result.get('total_outstanding', 0):,.2f}
- **Outstanding %**: {result.get('outstanding_percentage', 0):.1f}%
"""

        elif operation == 'ar_aging_summary':
            content = f"""# Odoo Query Report - AR Aging Summary

**Generated**: {timestamp.isoformat()}
**Operation**: {operation}
**Source**: Odoo Query Skill (G-M5)

## Aging Breakdown

| Period | Amount |
|--------|--------|
| Current | ${result.get('current', 0):,.2f} |
| 1-30 days | ${result.get('1-30_days', 0):,.2f} |
| 31-60 days | ${result.get('31-60_days', 0):,.2f} |
| 61-90 days | ${result.get('61-90_days', 0):,.2f} |
| Over 90 days | ${result.get('over_90_days', 0):,.2f} |
| **Total Outstanding** | **${result.get('total_outstanding', 0):,.2f}** |
"""

        elif operation == 'list_customers':
            content = f"""# Odoo Query Report - Customers

**Generated**: {timestamp.isoformat()}
**Operation**: {operation}
**Source**: Odoo Query Skill (G-M5)

## Summary

- **Total Customers**: {result.get('count', 0)}

## Customer List

"""
            for cust in result.get('customers', [])[:20]:  # Limit to 20
                content += f"- **{cust['name']}**: {cust['invoice_count']} invoices, ${cust['total_invoiced']:,.2f} invoiced, ${cust['total_outstanding']:,.2f} outstanding\n"

        else:
            content = f"""# Odoo Query Report

**Generated**: {timestamp.isoformat()}
**Operation**: {operation}
**Source**: Odoo Query Skill (G-M5)

## Result

```json
{json.dumps(result, indent=2)}
```
"""

        try:
            report_path.write_text(content, encoding='utf-8')
            logger.info(f"Generated report: {report_name}")
            return str(report_path)
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return None

    def execute_query(self, operation: str, mock: bool = True) -> Dict:
        """Execute a single query operation"""
        if operation not in self.SUPPORTED_OPERATIONS:
            raise ValueError(f"Unsupported operation: {operation}. Supported: {self.SUPPORTED_OPERATIONS}")

        logger.info(f"Executing query: {operation} (mock={mock})")

        # Execute query
        if mock:
            if operation == 'list_unpaid_invoices':
                result = self._query_unpaid_invoices_mock()
            elif operation == 'revenue_summary':
                result = self._query_revenue_summary_mock()
            elif operation == 'ar_aging_summary':
                result = self._query_ar_aging_summary_mock()
            elif operation == 'list_customers':
                result = self._query_customers_mock()
            else:
                raise ValueError(f"Mock not implemented for: {operation}")
        else:
            result = self._query_real(operation)

        # Log query
        self._log_query(operation, result, mode='mock' if mock else 'mcp')

        # Generate report if requested
        report_path = self._generate_report(operation, result)

        self.results[operation] = {
            'result': result,
            'report_path': report_path
        }

        return result


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Odoo Query Skill - Read-only operations (Gold Tier - G-M5)'
    )
    parser.add_argument('--operation', type=str, required=True,
                        choices=OdooQueryExecutor.SUPPORTED_OPERATIONS,
                        help='Query operation to execute')
    parser.add_argument('--mode', type=str, choices=['mock', 'mcp'], default='mock',
                        help='Data source mode: mock (default) or mcp')
    parser.add_argument('--report', action='store_true',
                        help='Generate markdown report in Business/Accounting/Reports/')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    config = {
        'base_dir': get_repo_root(),
        'log_path': 'Logs/mcp_actions.log',
        'mock_fixture_path': 'templates/mock_odoo_invoices.json',
        'generate_report': args.report
    }

    executor = OdooQueryExecutor(config)

    try:
        use_mock = (args.mode == 'mock')
        result = executor.execute_query(args.operation, mock=use_mock)

        if args.report and executor.results[args.operation]['report_path']:
            print(f"📄 Report generated: {executor.results[args.operation]['report_path']}", file=sys.stderr)

        # Compact JSON on last line — required by API route parser
        print(json.dumps(result))
        return 0

    except Exception as e:
        logger.error(f"Query failed: {e}")
        print(json.dumps({'error': str(e), 'operation': args.operation}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
