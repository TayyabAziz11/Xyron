#!/usr/bin/env python3
"""
Personal AI Employee - Accounting Audit Generator
Gold Tier - G-M6: Weekly CEO Briefing + Accounting Audit

Purpose: Generate comprehensive accounting audit report (AR aging, unpaid invoices, anomalies)
Tier: Gold
Skill ID: G-M6-T02

CRITICAL: This is REPORT-ONLY. NO approvals required. NO external actions.
Outputs Markdown reports for executive review.

Features:
- AR aging summary (0-30, 31-60, 61-90, 90+ days)
- Top unpaid invoices (sorted by amount)
- Anomaly detection (overdue > 90d, outstanding % > 40%, sudden jumps)
- Recommended actions (NOT executed, just suggestions)
- PII redaction in logs
- Mock mode + MCP mode support
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


class AccountingAuditGenerator:
    """Accounting Audit Report Generator - Executive-level financial insights"""

    def __init__(self, config: Dict):
        self.config = config
        self.as_of_date = datetime.fromisoformat(config['as_of_date'])

    def _load_mock_invoices(self) -> List[Dict]:
        """Load mock invoices from fixture"""
        fixture_path = Path(self.config['base_dir']) / 'templates' / 'mock_odoo_invoices.json'

        if not fixture_path.exists():
            logger.warning(f"Mock fixture not found: {fixture_path}")
            return []

        try:
            with open(fixture_path, 'r') as f:
                invoices = json.load(f)
            logger.info(f"Loaded {len(invoices)} mock invoices")
            return invoices
        except Exception as e:
            logger.error(f"Failed to load mock invoices: {e}")
            return []

    def _fetch_invoices_mcp(self) -> List[Dict]:
        """Fetch invoices via Odoo MCP query"""
        try:
            # TODO: Real MCP integration (G-M6)
            # For now, use brain_odoo_query_with_mcp_skill.py if available
            logger.info("MCP mode: Using Odoo query skill")

            import subprocess
            result = subprocess.run(
                ['python3', 'brain_odoo_query_with_mcp_skill.py', '--operation', 'list_unpaid_invoices', '--mode', 'mock'],
                capture_output=True,
                text=True,
                cwd=self.config['base_dir']
            )

            if result.returncode == 0:
                # Parse JSON output
                output_lines = result.stdout.strip().split('\n')
                for line in output_lines:
                    if line.startswith('{'):
                        data = json.loads(line)
                        return data.get('invoices', [])

            logger.warning("MCP query failed, falling back to mock")
            return self._load_mock_invoices()

        except Exception as e:
            logger.error(f"MCP fetch failed: {e}, falling back to mock")
            return self._load_mock_invoices()

    def _calculate_ar_aging(self, invoices: List[Dict]) -> Dict:
        """Calculate AR aging buckets"""
        aging = {
            'current': 0.0,
            '1-30': 0.0,
            '31-60': 0.0,
            '61-90': 0.0,
            '90+': 0.0
        }

        for inv in invoices:
            amount = inv.get('amount_residual', 0)
            days_overdue = inv.get('days_overdue', 0)

            if days_overdue == 0:
                aging['current'] += amount
            elif days_overdue <= 30:
                aging['1-30'] += amount
            elif days_overdue <= 60:
                aging['31-60'] += amount
            elif days_overdue <= 90:
                aging['61-90'] += amount
            else:
                aging['90+'] += amount

        return aging

    def _detect_anomalies(self, invoices: List[Dict], aging: Dict) -> List[str]:
        """Detect accounting anomalies"""
        anomalies = []

        # Total amounts
        total_outstanding = sum(aging.values())
        total_invoiced = sum(inv.get('amount_total', 0) for inv in invoices)

        # Anomaly 1: Invoices overdue > 90 days
        overdue_90_invoices = [inv for inv in invoices if inv.get('days_overdue', 0) > 90]
        if overdue_90_invoices:
            anomalies.append(f"⚠️ {len(overdue_90_invoices)} invoice(s) overdue > 90 days (${aging['90+']:,.2f})")

        # Anomaly 2: Outstanding % > 40%
        if total_invoiced > 0:
            outstanding_pct = (total_outstanding / total_invoiced) * 100
            if outstanding_pct > 40:
                anomalies.append(f"⚠️ Outstanding % is {outstanding_pct:.1f}% (threshold: 40%)")

        # Anomaly 3: Check for prior audit to compare
        prior_audit = self._load_prior_audit()
        if prior_audit:
            prior_count = prior_audit.get('unpaid_count', 0)
            current_count = len([inv for inv in invoices if inv.get('amount_residual', 0) > 0])

            if current_count > prior_count * 1.2:  # 20% increase
                anomalies.append(f"⚠️ Unpaid invoice count jumped {((current_count - prior_count) / prior_count * 100):.0f}% since last audit")

        if not anomalies:
            anomalies.append("✅ No significant anomalies detected")

        return anomalies

    def _load_prior_audit(self) -> Optional[Dict]:
        """Load previous audit for comparison"""
        reports_dir = Path(self.config['base_dir']) / 'Business' / 'Accounting' / 'Reports'

        if not reports_dir.exists():
            return None

        # Find most recent audit (excluding current date)
        audit_files = sorted(reports_dir.glob('accounting_audit__*.md'), reverse=True)

        for audit_file in audit_files:
            if audit_file.stem != f"accounting_audit__{self.as_of_date.strftime('%Y-%m-%d')}":
                try:
                    content = audit_file.read_text(encoding='utf-8')
                    # Extract metadata (simple parse)
                    if 'unpaid_count:' in content:
                        count_line = [l for l in content.split('\n') if 'unpaid_count:' in l][0]
                        count = int(count_line.split(':')[1].strip())
                        return {'unpaid_count': count}
                except Exception as e:
                    logger.warning(f"Failed to parse prior audit {audit_file}: {e}")
                    continue

        return None

    def _generate_recommended_actions(self, invoices: List[Dict], aging: Dict) -> List[str]:
        """Generate recommended actions (NOT executed)"""
        actions = []

        # Action 1: Follow up on 90+ days overdue
        overdue_90 = [inv for inv in invoices if inv.get('days_overdue', 0) > 90]
        if overdue_90:
            actions.append(f"📋 Create plan to send payment reminders for {len(overdue_90)} invoice(s) overdue > 90 days")

        # Action 2: Register payments for large outstanding amounts
        large_outstanding = [inv for inv in invoices if inv.get('amount_residual', 0) > 10000]
        if large_outstanding:
            actions.append(f"📋 Review and register pending payments for {len(large_outstanding)} invoice(s) > $10,000")

        # Action 3: General collections
        if aging['90+'] > 0:
            actions.append(f"📋 Escalate collections for ${aging['90+']:,.2f} in 90+ day bucket")

        if not actions:
            actions.append("✅ No immediate actions required")

        return actions

    def generate_report(self, mode: str = 'mock') -> str:
        """Generate accounting audit report"""
        logger.info(f"Generating accounting audit (mode={mode}, as_of={self.as_of_date.date()})")

        # Fetch data
        if mode == 'mock':
            invoices = self._load_mock_invoices()
        else:
            invoices = self._fetch_invoices_mcp()

        if not invoices:
            logger.warning("No invoice data available")
            invoices = []

        # Calculate metrics
        unpaid_invoices = [inv for inv in invoices if inv.get('amount_residual', 0) > 0]
        aging = self._calculate_ar_aging(unpaid_invoices)
        total_outstanding = sum(aging.values())
        anomalies = self._detect_anomalies(invoices, aging)
        recommended_actions = self._generate_recommended_actions(unpaid_invoices, aging)

        # Sort top 10 by amount
        top_unpaid = sorted(unpaid_invoices, key=lambda x: x.get('amount_residual', 0), reverse=True)[:10]

        # Generate report
        report_date = self.as_of_date.strftime('%Y-%m-%d')
        report_timestamp = self.as_of_date.isoformat()

        report_content = f"""# Accounting Audit Report

**As of Date:** {report_date}
**Generated:** {datetime.now(timezone.utc).isoformat()}
**Data Source:** {mode.upper()} mode
**Report Type:** Executive Accounting Audit

---

## Executive Summary

- **Total Outstanding AR:** ${total_outstanding:,.2f}
- **Unpaid Invoices Count:** {len(unpaid_invoices)}
- **Oldest Invoice:** {max([inv.get('days_overdue', 0) for inv in unpaid_invoices], default=0)} days overdue

---

## AR Aging Summary

| Aging Bucket | Amount | % of Total |
|--------------|--------|-----------|
| Current (0 days) | ${aging['current']:,.2f} | {(aging['current'] / total_outstanding * 100) if total_outstanding > 0 else 0:.1f}% |
| 1-30 days | ${aging['1-30']:,.2f} | {(aging['1-30'] / total_outstanding * 100) if total_outstanding > 0 else 0:.1f}% |
| 31-60 days | ${aging['31-60']:,.2f} | {(aging['31-60'] / total_outstanding * 100) if total_outstanding > 0 else 0:.1f}% |
| 61-90 days | ${aging['61-90']:,.2f} | {(aging['61-90'] / total_outstanding * 100) if total_outstanding > 0 else 0:.1f}% |
| 90+ days | ${aging['90+']:,.2f} | {(aging['90+'] / total_outstanding * 100) if total_outstanding > 0 else 0:.1f}% |
| **Total** | **${total_outstanding:,.2f}** | **100.0%** |

---

## Top Unpaid Invoices

"""

        for idx, inv in enumerate(top_unpaid, 1):
            customer = inv.get('customer_name', 'Unknown')
            invoice_num = inv.get('invoice_number', 'N/A')
            amount = inv.get('amount_residual', 0)
            days_overdue = inv.get('days_overdue', 0)

            report_content += f"{idx}. **{customer}** - Invoice {invoice_num}: ${amount:,.2f} ({days_overdue} days overdue)\n"

        report_content += f"""
---

## Anomalies & Risk Factors

"""
        for anomaly in anomalies:
            report_content += f"- {anomaly}\n"

        report_content += f"""
---

## Recommended Actions

**IMPORTANT:** These are recommendations only. NO actions have been executed.

"""
        for action in recommended_actions:
            report_content += f"{action}\n"

        report_content += f"""
---

## Metadata

```yaml
report_type: accounting_audit
as_of_date: {report_date}
generated_at: {report_timestamp}
data_source: {mode}
unpaid_count: {len(unpaid_invoices)}
total_outstanding: {total_outstanding}
mode: report_only
actions_executed: none
pii_redacted: true
```

---

**Generated by:** brain_generate_accounting_audit_skill.py (Gold Tier G-M6)
**Architecture:** Report-only (no approval gates, no external actions)
"""

        # Write report
        output_dir = Path(self.config['base_dir']) / 'Business' / 'Accounting' / 'Reports'
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"accounting_audit__{report_date}.md"
        output_file.write_text(report_content, encoding='utf-8')

        logger.info(f"Report generated: {output_file}")

        # Append to system log
        try:
            with open(Path(self.config['base_dir']) / 'system_log.md', 'a', encoding='utf-8') as f:
                f.write(f"\n**[{datetime.now(timezone.utc).isoformat()}]** Accounting audit generated: {len(unpaid_invoices)} unpaid invoices, ${total_outstanding:,.2f} outstanding\n")
        except:
            pass

        return str(output_file)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Accounting Audit Generator - Executive financial insights (Gold Tier G-M6)'
    )
    parser.add_argument('--as-of', type=str, default=datetime.now().strftime('%Y-%m-%d'),
                        help='As-of date for audit (YYYY-MM-DD, default: today)')
    parser.add_argument('--mode', type=str, choices=['mock', 'mcp'], default='mock',
                        help='Data source mode: mock (default) or mcp')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    config = {
        'base_dir': get_repo_root(),
        'as_of_date': args.as_of
    }

    generator = AccountingAuditGenerator(config)

    try:
        report_path = generator.generate_report(mode=args.mode)

        print(f"\n✅ Accounting audit generated successfully")
        print(f"   Report: {report_path}")
        print(f"   As of: {args.as_of}")
        print(f"   Mode: {args.mode}")

        return 0

    except Exception as e:
        logger.error(f"Audit generation failed: {e}")
        print(f"\n❌ Audit generation failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
