#!/usr/bin/env python3
"""
Personal AI Employee - Weekly CEO Briefing Generator
Gold Tier - G-M6: Weekly CEO Briefing + Accounting Audit

Purpose: Generate weekly executive briefing (system health + social + accounting + goals)
Tier: Gold
Skill ID: G-M6-T01

CRITICAL: This is REPORT-ONLY. NO approvals required. NO external actions.
Outputs executive-level Markdown briefing for CEO review.

Features:
- 8 required sections (KPIs, Wins, Risks, Invoices, Social, Priorities, Approvals, Summary)
- Data confidence scoring per section (high/medium/low)
- Graceful degradation if data sources missing
- Mock mode + live mode support
- Follows templates/ceo_briefing_template.md
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Import mcp_helpers for get_repo_root and redact_pii
try:
    from ai_operator.core.mcp_helpers import get_repo_root, redact_pii
except ImportError:
    # Fallback for direct script execution
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    from ai_operator.core.mcp_helpers import get_repo_root, redact_pii

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WeeklyCEOBriefingGenerator:
    """Weekly CEO Briefing Generator - Cross-domain executive summary"""

    def __init__(self, config: Dict):
        self.config = config
        self.week_start = datetime.fromisoformat(config['week_start'])
        self.week_end = self.week_start + timedelta(days=6)

    def _parse_system_log(self) -> Dict:
        """Parse system_log.md for key operations"""
        log_path = Path(self.config['base_dir']) / 'system_log.md'

        if not log_path.exists():
            logger.warning("system_log.md not found")
            return {'entries': [], 'confidence': 'low'}

        try:
            content = log_path.read_text(encoding='utf-8')
            lines = content.split('\n')

            entries = []
            for line in lines:
                if line.startswith('**[') and ']**' in line:
                    # Extract timestamp and message
                    try:
                        timestamp_str = line.split('[')[1].split(']')[0]
                        message = line.split('**', 2)[2].strip()
                        entries.append({'timestamp': timestamp_str, 'message': message})
                    except:
                        continue

            logger.info(f"Parsed {len(entries)} system log entries")
            return {'entries': entries[-50:], 'confidence': 'high'}  # Last 50 entries

        except Exception as e:
            logger.error(f"Failed to parse system_log.md: {e}")
            return {'entries': [], 'confidence': 'low'}

    def _parse_mcp_actions_log(self) -> Dict:
        """Parse Logs/mcp_actions.log for MCP statistics"""
        log_path = Path(self.config['base_dir']) / 'Logs' / 'mcp_actions.log'

        if not log_path.exists():
            logger.warning("mcp_actions.log not found")
            return {'total': 0, 'by_server': {}, 'failures': 0, 'confidence': 'low'}

        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            actions = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # Try to parse as JSON
                if line.startswith('{'):
                    try:
                        action = json.loads(line)
                        actions.append(action)
                    except json.JSONDecodeError:
                        continue

            # Count by server
            by_server = {}
            failures = 0

            for action in actions:
                server = action.get('server', 'unknown')
                by_server[server] = by_server.get(server, 0) + 1

                if action.get('status') in ['error', 'failed', 'simulated_failure']:
                    failures += 1

            logger.info(f"Parsed {len(actions)} MCP actions")
            return {'total': len(actions), 'by_server': by_server, 'failures': failures, 'confidence': 'high'}

        except Exception as e:
            logger.error(f"Failed to parse mcp_actions.log: {e}")
            return {'total': 0, 'by_server': {}, 'failures': 0, 'confidence': 'low'}

    def _get_social_performance(self) -> Dict:
        """Get social media performance metrics"""
        social_inbox = Path(self.config['base_dir']) / 'Social' / 'Inbox'
        social_summaries = Path(self.config['base_dir']) / 'Social' / 'Summaries'

        # Try to find latest summary first
        if social_summaries.exists():
            summaries = sorted(social_summaries.glob('*.md'), reverse=True)
            if summaries:
                try:
                    latest = summaries[0]
                    content = latest.read_text(encoding='utf-8')
                    # Basic metric extraction
                    return {
                        'summary_file': latest.name,
                        'has_data': True,
                        'confidence': 'medium'
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse social summary: {e}")

        # Fallback: count intake wrappers
        if social_inbox.exists():
            whatsapp_count = len(list(social_inbox.glob('inbox__whatsapp__*.md')))
            linkedin_count = len(list(social_inbox.glob('inbox__linkedin__*.md')))
            twitter_count = len(list(social_inbox.glob('inbox__twitter__*.md')))

            total = whatsapp_count + linkedin_count + twitter_count

            return {
                'whatsapp': whatsapp_count,
                'linkedin': linkedin_count,
                'twitter': twitter_count,
                'total': total,
                'confidence': 'medium' if total > 0 else 'low'
            }

        return {'total': 0, 'confidence': 'low'}

    def _get_business_goals(self) -> Dict:
        """Get business goals from Business/Goals/"""
        goals_dir = Path(self.config['base_dir']) / 'Business' / 'Goals'

        if not goals_dir.exists():
            return {'goals': [], 'confidence': 'low'}

        try:
            goal_files = list(goals_dir.glob('*.md'))
            goals = []

            for goal_file in goal_files[:5]:  # Top 5 goals
                try:
                    content = goal_file.read_text(encoding='utf-8')
                    # Extract title (first # heading)
                    for line in content.split('\n'):
                        if line.startswith('# '):
                            goals.append(line[2:].strip())
                            break
                except:
                    continue

            return {'goals': goals, 'confidence': 'high' if goals else 'low'}

        except Exception as e:
            logger.error(f"Failed to parse goals: {e}")
            return {'goals': [], 'confidence': 'low'}

    def _get_odoo_metrics(self, mode: str) -> Dict:
        """Get full Odoo accounting metrics including revenue, AR aging, late invoices."""
        base_dir = Path(self.config['base_dir'])
        reports_dir = base_dir / 'Business' / 'Accounting' / 'Reports'

        # ── Try direct OdooAPIHelper (most accurate) ──────────────────────────
        try:
            import sys
            sys.path.insert(0, str(base_dir / 'src'))
            from ai_operator.core.odoo_api_helper import OdooAPIHelper

            creds_path = base_dir / '.secrets' / 'odoo_credentials.json'
            helper = OdooAPIHelper(
                credentials_path=str(creds_path) if creds_path.exists() else None,
            )
            if not helper.load_credentials():
                helper._mock = True

            rev = helper.revenue_summary()
            aging_result = helper.ar_aging_summary()
            inv_result = helper.list_invoices(status_filter='unpaid', limit=100)

            if rev.get('success'):
                invoices = inv_result.get('invoices', []) if inv_result.get('success') else []
                late_invoices = [i for i in invoices if i.get('days_overdue', 0) > 30]
                aging = aging_result.get('aging', {}) if aging_result.get('success') else {}

                return {
                    'total_invoiced': rev.get('total_invoiced', 0),
                    'total_paid': rev.get('total_paid', 0),
                    'total_outstanding': rev.get('total_outstanding', 0),
                    'invoice_count': rev.get('invoice_count', 0),
                    'unpaid_count': len(invoices),
                    'late_count': len(late_invoices),
                    'late_invoices': late_invoices[:5],  # top 5 for briefing
                    'aging': aging,
                    'source': 'odoo_api_helper',
                    'mock': helper.is_mock,
                    'confidence': 'high',
                }
        except Exception as e:
            logger.warning(f"OdooAPIHelper direct call failed: {e}")

        # ── Fallback: parse latest report file ────────────────────────────────
        if reports_dir.exists():
            audits = sorted(reports_dir.glob('accounting_audit__*.md'), reverse=True)
            revenue_reports = sorted(reports_dir.glob('odoo_query__revenue_summary__*.md'), reverse=True)
            latest = (audits or revenue_reports or [None])[0]

            if latest:
                try:
                    content = latest.read_text(encoding='utf-8')
                    unpaid_count = 0
                    total_outstanding = 0.0
                    for line in content.split('\n'):
                        if 'unpaid_count:' in line.lower():
                            try:
                                unpaid_count = int(line.split(':')[1].strip())
                            except ValueError:
                                pass
                        if 'total_outstanding:' in line.lower():
                            try:
                                total_outstanding = float(line.split(':')[1].strip())
                            except ValueError:
                                pass
                        if 'Total Outstanding' in line and '$' in line:
                            parts = line.split('$')
                            if len(parts) > 1:
                                try:
                                    total_outstanding = float(parts[1].replace(',', '').split()[0])
                                except ValueError:
                                    pass
                    return {
                        'unpaid_count': unpaid_count,
                        'total_outstanding': total_outstanding,
                        'source': latest.name,
                        'confidence': 'medium',
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse Odoo report: {e}")

        return {
            'total_invoiced': 0, 'total_paid': 0, 'total_outstanding': 0,
            'unpaid_count': 0, 'late_count': 0, 'late_invoices': [],
            'aging': {}, 'confidence': 'low',
        }

    def _get_business_goals_data(self) -> Dict:
        """Parse Business_Goals.md for revenue target and MTD progress."""
        base_dir = Path(self.config['base_dir'])

        # Try Business/Goals/Business_Goals.md first, then any .md in Goals/
        candidates = [
            base_dir / 'Business' / 'Goals' / 'Business_Goals.md',
        ]
        goals_dir = base_dir / 'Business' / 'Goals'
        if goals_dir.exists():
            candidates += list(goals_dir.glob('*.md'))

        for path in candidates:
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding='utf-8')
                monthly_goal = 0.0
                current_mtd = 0.0

                for line in content.split('\n'):
                    if 'monthly goal' in line.lower() and '$' in line:
                        try:
                            monthly_goal = float(line.split('$')[1].replace(',', '').split()[0])
                        except (IndexError, ValueError):
                            pass
                    if 'current mtd' in line.lower() and '$' in line:
                        try:
                            current_mtd = float(line.split('$')[1].replace(',', '').split()[0])
                        except (IndexError, ValueError):
                            pass

                if monthly_goal > 0:
                    pct = round((current_mtd / monthly_goal) * 100, 1)
                    return {
                        'monthly_goal': monthly_goal,
                        'current_mtd': current_mtd,
                        'pct_to_target': pct,
                        'source': path.name,
                        'confidence': 'high',
                    }
            except Exception as e:
                logger.warning(f"Failed to parse Business_Goals: {e}")

        return {'monthly_goal': 0, 'current_mtd': 0, 'pct_to_target': 0, 'confidence': 'low'}

    def _get_bottlenecks(self) -> Dict:
        """Find bottlenecks: plans older than 3 days still in Plans/ folder."""
        base_dir = Path(self.config['base_dir'])
        plans_dir = base_dir / 'Business' / 'Plans'

        if not plans_dir.exists():
            # Fallback: check root-level Plans/
            plans_dir = base_dir / 'Plans'

        if not plans_dir.exists():
            return {'bottlenecks': [], 'confidence': 'low'}

        try:
            now = datetime.now(timezone.utc)
            bottlenecks = []

            for plan_file in plans_dir.glob('*.md'):
                try:
                    age_days = (now.timestamp() - plan_file.stat().st_mtime) / 86400
                    if age_days > 3:
                        bottlenecks.append({
                            'file': plan_file.name,
                            'age_days': round(age_days, 1),
                        })
                except Exception:
                    continue

            bottlenecks.sort(key=lambda x: x['age_days'], reverse=True)
            return {'bottlenecks': bottlenecks[:5], 'confidence': 'high' if bottlenecks else 'medium'}
        except Exception as e:
            logger.error(f"Failed to find bottlenecks: {e}")
            return {'bottlenecks': [], 'confidence': 'low'}

    def _get_pending_approvals(self) -> Dict:
        """Get pending approvals count"""
        pending_dir = Path(self.config['base_dir']) / 'Pending_Approval'

        if not pending_dir.exists():
            return {'count': 0, 'confidence': 'high'}

        try:
            pending_files = list(pending_dir.glob('ACTION__*.md'))
            return {'count': len(pending_files), 'confidence': 'high'}
        except Exception as e:
            logger.error(f"Failed to count pending approvals: {e}")
            return {'count': 0, 'confidence': 'low'}

    def generate_briefing(self, mode: str = 'mock') -> str:
        """Generate weekly CEO briefing — hackathon compliant (8 sections + proactive suggestions)"""
        logger.info(f"Generating CEO briefing (mode={mode}, week_start={self.week_start.date()})")

        # Gather data from all sources
        system_log = self._parse_system_log()
        mcp_stats = self._parse_mcp_actions_log()
        social = self._get_social_performance()
        goals = self._get_business_goals()
        goals_data = self._get_business_goals_data()
        odoo = self._get_odoo_metrics(mode)
        approvals = self._get_pending_approvals()
        bottlenecks_data = self._get_bottlenecks()

        # Calculate week number
        week_num = self.week_start.isocalendar()[1]
        year = self.week_start.year

        # Generate briefing
        briefing_content = f"""# Weekly CEO Briefing - Week {week_num}, {year}

**Period:** {self.week_start.strftime('%Y-%m-%d')} to {self.week_end.strftime('%Y-%m-%d')}
**Generated:** {datetime.now(timezone.utc).isoformat()}
**Mode:** {mode.upper()}
**Report Type:** Executive Weekly Briefing

---

## 1. KPIs

**Data Confidence:** {mcp_stats['confidence']}

| Metric | Value | Status |
|--------|-------|--------|
| MCP Actions Executed | {mcp_stats['total']} | {"✅" if mcp_stats['total'] > 0 else "—"} |
| MCP Action Failures | {mcp_stats['failures']} | {"⚠️" if mcp_stats['failures'] > 0 else "✅"} |
| Social Interactions | {social.get('total', 0)} | {"✅" if social.get('total', 0) > 0 else "—"} |
| Pending Approvals | {approvals['count']} | {"⚠️" if approvals['count'] > 3 else "✅"} |
| Outstanding AR | ${odoo.get('total_outstanding', 0):,.2f} | {"⚠️" if odoo.get('total_outstanding', 0) > 10000 else "✅"} |

**MCP Actions by Server:**
"""

        for server, count in mcp_stats['by_server'].items():
            briefing_content += f"- {server.title()}: {count} actions\n"

        if not mcp_stats['by_server']:
            briefing_content += "- No MCP actions recorded\n"

        briefing_content += f"""
---

## 2. Wins

**Data Confidence:** {system_log['confidence']}

"""

        # Extract recent wins from system log
        wins_found = False
        for entry in reversed(system_log['entries'][-10:]):
            msg = entry['message'].lower()
            if any(keyword in msg for keyword in ['complete', 'success', 'operational', '100%', 'passed']):
                briefing_content += f"- {entry['message']}\n"
                wins_found = True

        if not wins_found:
            briefing_content += "- System operational and stable\n"
            briefing_content += f"- {len(system_log['entries'])} system operations logged\n"

        briefing_content += f"""
---

## 3. Risks

**Data Confidence:** {max(odoo['confidence'], mcp_stats['confidence'], key=lambda x: ['low', 'medium', 'high'].index(x))}

"""

        # Identify risks
        risks_found = False

        if mcp_stats['failures'] > 0:
            briefing_content += f"- ⚠️ {mcp_stats['failures']} MCP action failure(s) detected\n"
            risks_found = True

        if odoo.get('total_outstanding', 0) > 20000:
            briefing_content += f"- ⚠️ High AR outstanding: ${odoo.get('total_outstanding', 0):,.2f}\n"
            risks_found = True

        if approvals['count'] > 5:
            briefing_content += f"- ⚠️ {approvals['count']} pending approvals (backlog building)\n"
            risks_found = True

        if not risks_found:
            briefing_content += "- ✅ No significant risks identified\n"

        # Revenue + MTD section
        mtd_pct = goals_data.get('pct_to_target', 0)
        monthly_goal = goals_data.get('monthly_goal', 0)
        current_mtd = goals_data.get('current_mtd', odoo.get('total_paid', 0))
        revenue_this_week = odoo.get('total_paid', 0)  # approximation from mock data

        briefing_content += f"""
---

## 4. Revenue & AR Aging

**Data Confidence:** {odoo['confidence']}

**Revenue Summary:**
| Metric | Value |
|--------|-------|
| Total Invoiced (YTD) | ${odoo.get('total_invoiced', 0):,.2f} |
| Total Paid (YTD) | ${odoo.get('total_paid', revenue_this_week):,.2f} |
| Total Outstanding AR | ${odoo.get('total_outstanding', 0):,.2f} |
| Monthly Target | ${monthly_goal:,.2f} |
| MTD Revenue | ${current_mtd:,.2f} |
| % to Target | {mtd_pct:.1f}% |

**AR Aging Buckets:**
"""
        aging = odoo.get('aging', {})
        if aging:
            for bucket, label in [('current', 'Current (not due)'), ('1_30', '1–30 days'), ('31_60', '31–60 days'), ('61_90', '61–90 days'), ('over_90', '90+ days')]:
                amt = aging.get(bucket, 0)
                flag = ' ⚠️' if bucket in ('61_90', 'over_90') and amt > 0 else ''
                briefing_content += f"- {label}: ${amt:,.2f}{flag}\n"
        else:
            briefing_content += f"- Total Outstanding: ${odoo.get('total_outstanding', 0):,.2f}\n"
            briefing_content += f"- Run `get_aging_report` via Odoo MCP for full breakdown\n"

        # Late invoices > 30 days
        late_invoices = odoo.get('late_invoices', [])
        late_count = odoo.get('late_count', 0)
        if late_count > 0:
            briefing_content += f"\n**Late Invoices (>30 days overdue): {late_count}**\n"
            for inv in late_invoices[:5]:
                briefing_content += f"- {inv.get('invoice_number', '?')} | {inv.get('customer_name', '?')} | ${inv.get('amount_total', 0):,.2f} | {inv.get('days_overdue', 0)} days\n"
        else:
            briefing_content += f"\n**Late Invoices (>30 days):** {late_count} — ✅ None\n"

        briefing_content += f"""
---

## 5. Social Performance

**Data Confidence:** {social['confidence']}

**Activity Summary:**
"""

        if social.get('has_data'):
            briefing_content += f"- Latest Summary: {social.get('summary_file', 'N/A')}\n"
            briefing_content += f"- Review Social/Summaries/ for detailed metrics\n"
        elif social.get('total', 0) > 0:
            briefing_content += f"- WhatsApp: {social.get('whatsapp', 0)} messages\n"
            briefing_content += f"- LinkedIn: {social.get('linkedin', 0)} interactions\n"
            briefing_content += f"- Twitter: {social.get('twitter', 0)} mentions\n"
        else:
            briefing_content += "- No social activity recorded\n"
            briefing_content += "- Social watchers pending full implementation (G-M3)\n"

        briefing_content += f"""
---

## 6. Next Week Priorities & Bottlenecks

**Data Confidence:** {goals['confidence']}

"""
        if goals['goals']:
            briefing_content += "**Active Goals:**\n"
            for goal in goals['goals']:
                briefing_content += f"- {goal}\n"
        else:
            briefing_content += "**Suggested Priorities:**\n"
            briefing_content += "- Review and update Business/Goals/Business_Goals.md\n"
            briefing_content += "- Process pending approvals\n"
            briefing_content += "- Run accounting audit if needed\n"

        # Bottlenecks
        bottlenecks = bottlenecks_data.get('bottlenecks', [])
        if bottlenecks:
            briefing_content += f"\n**Bottlenecks (Plans > 3 days old):**\n"
            for b in bottlenecks:
                briefing_content += f"- ⏳ {b['file']} — {b['age_days']} days old\n"
        else:
            briefing_content += "\n**Bottlenecks:** ✅ None detected\n"

        briefing_content += f"""
---

## 7. Pending Approvals

**Data Confidence:** {approvals['confidence']}

**Status:** {approvals['count']} action(s) awaiting approval

"""

        if approvals['count'] > 0:
            briefing_content += f"- Review Pending_Approval/ folder\n"
            briefing_content += f"- Move approved actions to Approved/\n"
            briefing_content += f"- Move rejected actions to Rejected/\n"
        else:
            briefing_content += "- ✅ No pending approvals\n"

        # ── Proactive Suggestions (subscription anomalies + cost optimization) ─
        briefing_content += "\n---\n\n## 8. Proactive Suggestions\n\n"
        suggestions = []

        # Subscription anomaly detection
        try:
            import sys
            sys.path.insert(0, str(Path(self.config['base_dir']) / 'src'))
            from ai_operator.core.odoo_api_helper import OdooAPIHelper  # type: ignore

            creds_path = Path(self.config['base_dir']) / '.secrets' / 'odoo_credentials.json'
            helper = OdooAPIHelper(
                credentials_path=str(creds_path) if creds_path.exists() else None,
            )
            if not helper.load_credentials():
                helper._mock = True

            inv_all = helper.list_invoices(status_filter='all', limit=200)
            if inv_all.get('success'):
                invoices_all = inv_all.get('invoices', [])
                by_customer: Dict[str, list] = {}
                for inv in invoices_all:
                    cname = inv.get('customer_name', 'Unknown')
                    by_customer.setdefault(cname, []).append(inv)

                for cname, invs in by_customer.items():
                    if len(invs) >= 2:
                        latest_amt = invs[0].get('amount_total', 0)
                        prev_amt = invs[1].get('amount_total', 0)
                        if prev_amt > 0:
                            change_pct = ((latest_amt - prev_amt) / prev_amt) * 100
                            if change_pct > 20:
                                suggestions.append(
                                    f"💡 **Cost increase detected** — {cname}: "
                                    f"${prev_amt:,.2f} → ${latest_amt:,.2f} (+{change_pct:.0f}%). "
                                    f"Review subscription terms. [ACTION] Move to /Pending_Approval if cancellation needed."
                                )
        except Exception as e:
            logger.warning(f"Subscription anomaly check failed: {e}")

        # AR collection suggestions
        if odoo.get('late_count', 0) > 0:
            suggestions.append(
                f"📬 **AR follow-up needed** — {odoo['late_count']} invoice(s) >30 days overdue "
                f"(${odoo.get('total_outstanding', 0):,.2f} total). Send payment reminder emails."
            )

        # Target pace suggestion
        if goals_data.get('monthly_goal', 0) > 0 and goals_data.get('pct_to_target', 100) < 50:
            suggestions.append(
                f"📊 **Revenue pace alert** — MTD at {goals_data['pct_to_target']:.0f}% of "
                f"${goals_data['monthly_goal']:,.2f} monthly target. "
                f"Consider accelerating invoicing or following up on pending deals."
            )

        # Approvals backlog
        if approvals['count'] > 3:
            suggestions.append(
                f"⚡ **Approvals backlog** — {approvals['count']} actions waiting. "
                f"Review Pending_Approval/ to unblock the agent."
            )

        if suggestions:
            for suggestion in suggestions:
                briefing_content += f"{suggestion}\n\n"
        else:
            briefing_content += "✅ No urgent suggestions — system running well.\n\n"

        briefing_content += f"""
---

## 9. Summary

**Overall System Health:** {"✅ Healthy" if mcp_stats['failures'] == 0 else "⚠️ Needs Attention"}

**Key Takeaways:**
- MCP operations: {mcp_stats['total']} actions executed
- Revenue: ${odoo.get('total_paid', 0):,.2f} paid | ${odoo.get('total_outstanding', 0):,.2f} outstanding
- MTD vs target: {goals_data.get('pct_to_target', 0):.1f}% of ${goals_data.get('monthly_goal', 0):,.2f}
- Late invoices: {odoo.get('late_count', 0)} (>30 days)
- Social engagement: {social.get('total', 0)} interactions tracked
- Governance: {approvals['count']} pending approvals

**Recommended Focus:**
"""

        # Priority recommendations
        if approvals['count'] > 0:
            briefing_content += f"1. Process {approvals['count']} pending approvals\n"

        if odoo.get('late_count', 0) > 0:
            briefing_content += f"2. Follow up on {odoo['late_count']} late invoice(s)\n"

        if odoo.get('total_outstanding', 0) > 10000:
            briefing_content += f"3. Review AR aging and collections strategy\n"

        if mcp_stats['failures'] > 0:
            briefing_content += f"4. Investigate {mcp_stats['failures']} MCP failures\n"

        if social.get('total', 0) == 0:
            briefing_content += f"5. Complete social watcher implementation (G-M3)\n"

        briefing_content += f"""
---

## Metadata

```yaml
briefing_type: weekly_ceo_briefing
week_start: {self.week_start.strftime('%Y-%m-%d')}
week_end: {self.week_end.strftime('%Y-%m-%d')}
week_number: {week_num}
year: {year}
generated_at: {datetime.now(timezone.utc).isoformat()}
mode: {mode}
report_only: true
actions_executed: none
data_sources:
  - system_log.md
  - Logs/mcp_actions.log
  - Social/Inbox/ or Social/Summaries/
  - Business/Goals/
  - Business/Accounting/Reports/
  - Pending_Approval/
```

---

**Generated by:** brain_generate_weekly_ceo_briefing_skill.py (Gold Tier G-M6)
**Architecture:** Report-only (no approval gates, no external actions)
**Next Briefing:** {(self.week_start + timedelta(days=7)).strftime('%Y-%m-%d')}
"""

        # Write briefing
        output_dir = Path(self.config['base_dir']) / 'Business' / 'Briefings'
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"CEO_Briefing__{year}-W{week_num:02d}.md"
        output_file.write_text(briefing_content, encoding='utf-8')

        logger.info(f"Briefing generated: {output_file}")

        # Append to system log
        try:
            with open(Path(self.config['base_dir']) / 'system_log.md', 'a', encoding='utf-8') as f:
                f.write(f"\n**[{datetime.now(timezone.utc).isoformat()}]** Weekly CEO briefing generated: Week {week_num}, {year} ({mcp_stats['total']} MCP actions, {approvals['count']} pending approvals)\n")
        except:
            pass

        return str(output_file)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Weekly CEO Briefing Generator - Executive summary (Gold Tier G-M6)'
    )
    parser.add_argument('--week-start', type=str,
                        default=(datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d'),
                        help='Week start date (YYYY-MM-DD, default: current week Monday)')
    parser.add_argument('--mode', type=str, choices=['mock', 'live'], default='mock',
                        help='Data gathering mode: mock (default) or live')
    parser.add_argument('--output', type=str, help='Optional output path override')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    config = {
        'base_dir': get_repo_root(),
        'week_start': args.week_start
    }

    generator = WeeklyCEOBriefingGenerator(config)

    try:
        briefing_path = generator.generate_briefing(mode=args.mode)

        if args.output:
            # Copy to custom output location
            import shutil
            shutil.copy(briefing_path, args.output)
            briefing_path = args.output

        print(f"\n✅ Weekly CEO briefing generated successfully")
        print(f"   Report: {briefing_path}")
        print(f"   Week: {args.week_start}")
        print(f"   Mode: {args.mode}")

        return 0

    except Exception as e:
        logger.error(f"Briefing generation failed: {e}")
        print(f"\n❌ Briefing generation failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
