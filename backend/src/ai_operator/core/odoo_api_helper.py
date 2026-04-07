#!/usr/bin/env python3
"""
Odoo API Helper - Centralized JSON-RPC Integration
Gold Tier - G-M5: Odoo MCP Integration

Purpose: Provide Odoo JSON-RPC authentication and API wrapper.
Supports:
- JSON-RPC authentication (uid via /xmlrpc/2/common)
- execute_kw wrapper for all model operations
- Invoice management (list, get, create, post)
- Customer management
- Payment registration
- Revenue/AR reporting
- Mock mode using JSON fixtures (no real Odoo required)
"""

import os
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

# Maximum retries for transient connection errors (never applied to payment ops)
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds (exponential: 2, 4, 8)


class OdooAPIHelper:
    """Odoo JSON-RPC API helper with mock/real mode support."""

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        mock_invoices_path: Optional[str] = None,
        mock_customers_path: Optional[str] = None,
        mock: bool = False,
    ):
        """
        Initialize Odoo API helper.

        Args:
            credentials_path: Path to .secrets/odoo_credentials.json
            mock_invoices_path: Path to mock invoice fixture
            mock_customers_path: Path to mock customer fixture
            mock: Force mock mode (default: False, auto-detected from credentials)
        """
        self.base_dir = Path(__file__).parent

        # Resolve fixture paths relative to repo root
        repo_root = self._find_repo_root()

        # Resolve credential paths — default to repo root .secrets/
        if credentials_path:
            self.credentials_path = Path(credentials_path)
        else:
            self.credentials_path = repo_root / '.secrets' / 'odoo_credentials.json'
        self.mock_invoices_path = Path(mock_invoices_path) if mock_invoices_path else repo_root / 'templates' / 'mock_odoo_invoices.json'
        self.mock_customers_path = Path(mock_customers_path) if mock_customers_path else repo_root / 'templates' / 'mock_odoo_customers.json'

        self._mock = mock
        self._uid: Optional[int] = None
        self._credentials: Optional[Dict] = None
        self._session_cookie: Optional[str] = None  # Web session cookie for SaaS

    def _find_repo_root(self) -> Path:
        """Find repo root by looking for system_log.md."""
        current = Path(__file__).parent
        while current != current.parent:
            if (current / 'system_log.md').exists():
                return current
            current = current.parent
        return Path(__file__).parent.parent.parent.parent.parent

    @property
    def is_mock(self) -> bool:
        """True if running in mock mode."""
        return self._mock

    # ── Credential loading ────────────────────────────────────────────────────

    def load_credentials(self) -> bool:
        """
        Load Odoo credentials from file or environment.

        Priority:
        1. Environment variables (ODOO_BASE_URL, ODOO_DATABASE, ODOO_USERNAME, ODOO_PASSWORD)
        2. Credentials file at self.credentials_path

        Returns:
            True if credentials loaded successfully
        """
        # Try environment variables first
        base_url = os.environ.get('ODOO_BASE_URL')
        database = os.environ.get('ODOO_DATABASE')
        username = os.environ.get('ODOO_USERNAME')
        password = os.environ.get('ODOO_PASSWORD')

        if all([base_url, database, username, password]):
            self._credentials = {
                'base_url': base_url.rstrip('/'),
                'database': database,
                'username': username,
                'password': password,
                'api_version': os.environ.get('ODOO_API_VERSION', '16.0'),
            }
            return True

        # Try credentials file
        if not self.credentials_path.exists():
            return False

        try:
            with open(self.credentials_path, 'r') as f:
                creds = json.load(f)

            # Validate required fields
            required = ['base_url', 'database', 'username', 'password']
            missing = [k for k in required if not creds.get(k)]
            if missing:
                return False

            # Check for placeholder values
            if creds.get('password', '').startswith('YOUR_'):
                return False

            self._credentials = creds
            self._credentials['base_url'] = self._credentials['base_url'].rstrip('/')
            return True

        except Exception:
            return False

    # ── JSON-RPC transport ───────────────────────────────────────────────────

    def _jsonrpc(self, url: str, method: str, params: Dict, no_retry: bool = False) -> Any:
        """
        Execute a JSON-RPC call with optional retry for transient errors.

        Args:
            url: Odoo endpoint URL
            method: RPC method name
            params: RPC parameters
            no_retry: If True, never retry (required for payment operations)
        """
        payload = {
            'jsonrpc': '2.0',
            'method': 'call',
            'params': params,
            'id': 1,
        }
        data = json.dumps(payload).encode('utf-8')
        max_attempts = 1 if no_retry else _MAX_RETRIES

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                    if 'error' in result:
                        raise RuntimeError(f"Odoo RPC error: {result['error']}")
                    return result.get('result')
            except urllib.error.URLError as e:
                last_error = ConnectionError(f"Cannot reach Odoo at {url}: {e}")
                if no_retry or attempt == max_attempts:
                    break
                wait = _RETRY_BACKOFF_BASE ** attempt
                import logging
                logging.getLogger(__name__).warning(
                    f"Odoo connection failed (attempt {attempt}/{max_attempts}), "
                    f"retrying in {wait}s: {e}"
                )
                time.sleep(wait)
            except Exception as e:
                # Non-transient errors: don't retry
                raise e

        raise last_error or ConnectionError(f"Cannot reach Odoo at {url}")

    def _execute_kw_no_retry(self, model: str, method: str, args: List, kwargs: Optional[Dict] = None) -> Any:
        """execute_kw variant that NEVER retries — for financial operations."""
        return self._web_call(model, method, args, kwargs, no_retry=True)

    # ── Authentication ────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """
        Authenticate with Odoo via web session API (/web/session/authenticate).
        Works on both Odoo SaaS cloud and self-hosted instances.

        Returns:
            True if authentication successful
        """
        if self._mock:
            self._uid = 1  # Mock UID
            return True

        if not self.load_credentials():
            return False

        creds = self._credentials
        try:
            payload = json.dumps({
                'jsonrpc': '2.0',
                'method': 'call',
                'id': 1,
                'params': {
                    'db': creds['database'],
                    'login': creds['username'],
                    'password': creds['password'],
                },
            }).encode('utf-8')
            req = urllib.request.Request(
                f"{creds['base_url']}/web/session/authenticate",
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                # Capture session cookie for subsequent calls
                set_cookie = resp.headers.get('Set-Cookie', '')
                if set_cookie:
                    # Extract session_id value
                    for part in set_cookie.split(';'):
                        part = part.strip()
                        if part.startswith('session_id='):
                            self._session_cookie = part
                            break
                    if not self._session_cookie:
                        self._session_cookie = set_cookie.split(';')[0].strip()
                result = json.loads(resp.read())
                uid = result.get('result', {}).get('uid')
                if not uid:
                    return False
                self._uid = uid
                return True
        except Exception:
            return False

    def _web_call(self, model: str, method: str, args: List, kwargs: Optional[Dict] = None, no_retry: bool = False) -> Any:
        """
        Call an Odoo model method via /web/dataset/call_kw (web session API).
        Works on Odoo SaaS cloud. Uses session cookie from authenticate().
        """
        if not self._uid or not self._session_cookie:
            if not self.authenticate():
                raise RuntimeError("Not authenticated")

        creds = self._credentials
        payload = json.dumps({
            'jsonrpc': '2.0',
            'method': 'call',
            'id': 1,
            'params': {
                'model': model,
                'method': method,
                'args': args,
                'kwargs': kwargs or {},
            },
        }).encode('utf-8')

        max_attempts = 1 if no_retry else _MAX_RETRIES
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            req = urllib.request.Request(
                f"{creds['base_url']}/web/dataset/call_kw",
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'Cookie': self._session_cookie or '',
                },
                method='POST',
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                    if 'error' in result:
                        raise RuntimeError(f"Odoo error: {result['error']}")
                    return result.get('result')
            except urllib.error.URLError as e:
                last_error = ConnectionError(f"Cannot reach Odoo: {e}")
                if no_retry or attempt == max_attempts:
                    break
                wait = _RETRY_BACKOFF_BASE ** attempt
                import logging
                logging.getLogger(__name__).warning(
                    f"Odoo connection failed (attempt {attempt}/{max_attempts}), retrying in {wait}s"
                )
                time.sleep(wait)
            except Exception as e:
                raise e

        raise last_error or ConnectionError("Cannot reach Odoo")

    def check_auth(self) -> Dict:
        """Check authentication status."""
        if not self.authenticate():
            return {'status': 'failed', 'error': 'Authentication failed', 'mock': self._mock}

        return {
            'status': 'ok',
            'uid': self._uid,
            'mock': self._mock,
            'base_url': self._credentials.get('base_url', 'mock') if self._credentials else 'mock',
            'database': self._credentials.get('database', 'mock') if self._credentials else 'mock',
        }

    # ── execute_kw wrapper ────────────────────────────────────────────────────

    def execute_kw(self, model: str, method: str, args: List, kwargs: Optional[Dict] = None) -> Any:
        """
        Execute an Odoo model method via the web session API (/web/dataset/call_kw).
        Works on Odoo SaaS cloud and self-hosted instances.
        """
        return self._web_call(model, method, args, kwargs)

    # ── Invoice operations ────────────────────────────────────────────────────

    def list_invoices(
        self,
        status_filter: Optional[str] = 'unpaid',
        limit: int = 50,
    ) -> Dict:
        """
        List customer invoices.

        Args:
            status_filter: 'unpaid' | 'overdue' | 'all' (default: 'unpaid')
            limit: Max results (default: 50)

        Returns:
            Dict with invoices list
        """
        if self._mock:
            return self._mock_list_invoices(status_filter, limit)

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed', 'degraded': True}

        try:
            domain = [('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]
            if status_filter == 'unpaid':
                domain.append(('payment_state', 'in', ['not_paid', 'partial']))
            elif status_filter == 'overdue':
                today = datetime.now().strftime('%Y-%m-%d')
                domain += [
                    ('payment_state', 'in', ['not_paid', 'partial']),
                    ('invoice_date_due', '<', today),
                ]

            fields = ['name', 'partner_id', 'amount_total', 'amount_residual',
                      'invoice_date_due', 'payment_state', 'invoice_date']
            records = self.execute_kw('account.move', 'search_read', [domain], {'fields': fields, 'limit': limit})

            invoices = []
            for r in records:
                today = datetime.now()
                due = r.get('invoice_date_due')
                days_overdue = 0
                if due:
                    due_dt = datetime.strptime(due, '%Y-%m-%d')
                    days_overdue = max(0, (today - due_dt).days)
                invoices.append({
                    'id': r['name'],
                    'customer_name': r['partner_id'][1] if r.get('partner_id') else 'Unknown',
                    'partner_id': r['partner_id'][0] if r.get('partner_id') else None,
                    'invoice_number': r['name'],
                    'amount_total': r.get('amount_total', 0),
                    'amount_residual': r.get('amount_residual', 0),
                    'due_date': due,
                    'status': r.get('payment_state', 'unknown'),
                    'days_overdue': days_overdue,
                    'invoice_date': r.get('invoice_date'),
                })

            return {'success': True, 'invoices': invoices, 'count': len(invoices)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _mock_list_invoices(self, status_filter: Optional[str], limit: int) -> Dict:
        """Return mock invoice data from fixture."""
        try:
            with open(self.mock_invoices_path, 'r') as f:
                all_invoices = json.load(f)
        except Exception as e:
            return {'success': False, 'error': f'Mock fixture error: {e}'}

        invoices = all_invoices
        if status_filter == 'unpaid':
            invoices = [i for i in all_invoices if i.get('payment_state') != 'paid' and i.get('status') != 'paid']
        elif status_filter == 'overdue':
            invoices = [i for i in all_invoices if i.get('days_overdue', 0) > 0]

        return {'success': True, 'invoices': invoices[:limit], 'count': len(invoices[:limit]), 'mock': True}

    def get_invoice(self, invoice_id: str) -> Dict:
        """Get specific invoice by ID/name."""
        if self._mock:
            try:
                with open(self.mock_invoices_path, 'r') as f:
                    invoices = json.load(f)
                inv = next((i for i in invoices if str(i.get('id')) == str(invoice_id)), None)
                if inv:
                    return {'success': True, 'invoice': inv, 'mock': True}
                return {'success': False, 'error': f'Invoice {invoice_id} not found in mock data'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            domain = [('name', '=', invoice_id), ('move_type', '=', 'out_invoice')]
            records = self.execute_kw('account.move', 'search_read', [domain], {'limit': 1})
            if not records:
                return {'success': False, 'error': f'Invoice {invoice_id} not found'}
            return {'success': True, 'invoice': records[0]}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def create_invoice(self, partner_id: int, lines: List[Dict], due_date: Optional[str] = None) -> Dict:
        """
        Create a new customer invoice (draft state).

        Args:
            partner_id: Customer partner ID
            lines: List of invoice line dicts with keys: name, quantity, price_unit
            due_date: Optional due date (YYYY-MM-DD)

        Returns:
            Dict with new invoice ID
        """
        if self._mock:
            return {
                'success': True,
                'invoice_id': 'INV-MOCK-NEW',
                'mock': True,
                'message': 'Mock: Invoice would be created in draft state',
            }

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            invoice_vals: Dict[str, Any] = {
                'move_type': 'out_invoice',
                'partner_id': partner_id,
                'invoice_line_ids': [
                    (0, 0, {
                        'name': line.get('name', 'Service'),
                        'quantity': line.get('quantity', 1),
                        'price_unit': line.get('price_unit', 0),
                    })
                    for line in lines
                ],
            }
            if due_date:
                invoice_vals['invoice_date_due'] = due_date

            invoice_id = self.execute_kw('account.move', 'create', [invoice_vals])
            return {'success': True, 'invoice_id': invoice_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def post_invoice(self, invoice_id: int) -> Dict:
        """Validate/post a draft invoice (makes it official)."""
        if self._mock:
            return {'success': True, 'mock': True, 'message': f'Mock: Invoice {invoice_id} would be posted/validated'}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            self.execute_kw('account.move', 'action_post', [[invoice_id]])
            return {'success': True, 'invoice_id': invoice_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def register_payment(
        self,
        invoice_id: int,
        amount: float,
        payment_date: Optional[str] = None,
        memo: str = '',
        _no_retry: bool = False,
    ) -> Dict:
        """
        Register a payment for an invoice.

        Args:
            invoice_id: Invoice record ID
            amount: Payment amount
            payment_date: Payment date (YYYY-MM-DD, defaults to today)
            memo: Payment memo / reference
            _no_retry: Internal flag — always True for payment calls (financial idempotency)

        Returns:
            Dict with payment result

        IMPORTANT: This method NEVER auto-retries on failure.
        If Odoo is unreachable, return failure and let the orchestrator create a remediation task.
        """
        if self._mock:
            return {
                'success': True,
                'mock': True,
                'message': f'Mock: Payment of ${amount:.2f} registered for invoice {invoice_id}',
            }

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed — cannot register payment'}

        try:
            if not payment_date:
                payment_date = datetime.now().strftime('%Y-%m-%d')

            # Use account.payment.register wizard — NEVER retry financial operations
            ctx = self._execute_kw_no_retry(
                'account.move', 'action_register_payment', [[invoice_id]]
            )
            vals: Dict[str, Any] = {'amount': amount, 'payment_date': payment_date}
            if memo:
                vals['memo'] = memo
            wizard_id = self._execute_kw_no_retry(
                'account.payment.register', 'create',
                [vals],
                {'context': ctx.get('context', {})},
            )
            self._execute_kw_no_retry(
                'account.payment.register', 'action_create_payments', [[wizard_id]]
            )
            return {'success': True, 'invoice_id': invoice_id, 'amount': amount}
        except ConnectionError as e:
            # Odoo offline — graceful degradation: return structured failure
            return {
                'success': False,
                'error': str(e),
                'degraded': True,
                'remediation': 'Create remediation task; do NOT retry payment automatically',
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ── Customer operations ───────────────────────────────────────────────────

    def find_partner(self, name: Optional[str] = None, partner_id: Optional[int] = None) -> Optional[int]:
        """
        Resolve a partner_id by name search or ID validation.

        Strategy:
          1. If partner_id given: verify it exists → return it, or None if missing.
          2. If name given: search res.partner by name (ilike) → return first match ID.
          3. If neither: return the ID of the first active customer in the DB.

        Returns None if nothing is found.
        """
        if self._mock:
            return 9999  # stable mock ID

        if not self.authenticate():
            return None

        try:
            if partner_id:
                # Validate the given ID actually exists
                existing = self.execute_kw(
                    'res.partner', 'search',
                    [[['id', '=', partner_id], ['active', 'in', [True, False]]]],
                    {'limit': 1},
                )
                if existing:
                    return partner_id
                # ID invalid — fall through to name search

            if name:
                matches = self.execute_kw(
                    'res.partner', 'search',
                    [[['name', 'ilike', name], ['active', '=', True]]],
                    {'limit': 1},
                )
                if matches:
                    return matches[0]

            # Last resort: first active customer
            fallback = self.execute_kw(
                'res.partner', 'search',
                [[['customer_rank', '>', 0], ['active', '=', True]]],
                {'limit': 1},
            )
            if fallback:
                return fallback[0]

        except Exception:
            pass

        return None

    def list_customers(self, limit: int = 50) -> Dict:
        """List customers."""
        if self._mock:
            try:
                with open(self.mock_customers_path, 'r') as f:
                    customers = json.load(f)
                return {'success': True, 'customers': customers[:limit], 'count': len(customers[:limit]), 'mock': True}
            except Exception as e:
                return {'success': False, 'error': f'Mock fixture error: {e}'}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            domain = [('customer_rank', '>', 0), ('active', '=', True)]
            fields = ['name', 'email', 'phone', 'city', 'country_id', 'customer_rank']
            records = self.execute_kw('res.partner', 'search_read', [domain], {'fields': fields, 'limit': limit})
            return {'success': True, 'customers': records, 'count': len(records)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def create_customer(self, name: str, email: Optional[str] = None, phone: Optional[str] = None) -> Dict:
        """Create a new customer record."""
        if self._mock:
            return {'success': True, 'partner_id': 9999, 'mock': True, 'message': f'Mock: Customer "{name}" would be created'}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            vals: Dict[str, Any] = {'name': name, 'customer_rank': 1}
            if email:
                vals['email'] = email
            if phone:
                vals['phone'] = phone
            partner_id = self.execute_kw('res.partner', 'create', [vals])
            return {'success': True, 'partner_id': partner_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ── Reporting ────────────────────────────────────────────────────────────

    def revenue_summary(self, year: Optional[int] = None) -> Dict:
        """
        Get revenue summary (total invoiced, total paid, total outstanding).

        Args:
            year: Year filter (default: current year)

        Returns:
            Dict with revenue metrics
        """
        if self._mock:
            try:
                with open(self.mock_invoices_path, 'r') as f:
                    invoices = json.load(f)
                total_invoiced = sum(i.get('amount_total', 0) for i in invoices)
                total_paid = sum(
                    i.get('amount_total', 0)
                    for i in invoices
                    if i.get('status') == 'paid' or i.get('payment_state') == 'paid'
                )
                total_outstanding = sum(i.get('amount_residual', i.get('amount_total', 0)) for i in invoices if i.get('status') != 'paid')
                return {
                    'success': True,
                    'mock': True,
                    'total_invoiced': total_invoiced,
                    'total_paid': total_paid,
                    'total_outstanding': total_outstanding,
                    'invoice_count': len(invoices),
                    'currency': 'USD',
                }
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            year = year or datetime.now().year
            date_from = f'{year}-01-01'
            date_to = f'{year}-12-31'

            domain = [
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', date_from),
                ('invoice_date', '<=', date_to),
            ]
            fields = ['amount_total', 'amount_residual', 'payment_state']
            records = self.execute_kw('account.move', 'search_read', [domain], {'fields': fields})

            total_invoiced = sum(r.get('amount_total', 0) for r in records)
            total_outstanding = sum(r.get('amount_residual', 0) for r in records)
            total_paid = total_invoiced - total_outstanding

            return {
                'success': True,
                'year': year,
                'total_invoiced': total_invoiced,
                'total_paid': total_paid,
                'total_outstanding': total_outstanding,
                'invoice_count': len(records),
                'currency': 'USD',
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def ar_aging_summary(self) -> Dict:
        """
        Get AR aging summary (0-30, 31-60, 61-90, 90+ days overdue).

        Returns:
            Dict with aging buckets
        """
        if self._mock:
            try:
                with open(self.mock_invoices_path, 'r') as f:
                    invoices = json.load(f)
                buckets: Dict[str, float] = {'current': 0, '1_30': 0, '31_60': 0, '61_90': 0, 'over_90': 0}
                for inv in invoices:
                    days = inv.get('days_overdue', 0)
                    amount = inv.get('amount_residual', inv.get('amount_total', 0))
                    if inv.get('status') == 'paid':
                        continue
                    if days <= 0:
                        buckets['current'] += amount
                    elif days <= 30:
                        buckets['1_30'] += amount
                    elif days <= 60:
                        buckets['31_60'] += amount
                    elif days <= 90:
                        buckets['61_90'] += amount
                    else:
                        buckets['over_90'] += amount
                return {'success': True, 'mock': True, 'aging': buckets, 'currency': 'USD'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            invoices_result = self.list_invoices(status_filter='unpaid', limit=500)
            if not invoices_result['success']:
                return invoices_result

            buckets: Dict[str, float] = {'current': 0, '1_30': 0, '31_60': 0, '61_90': 0, 'over_90': 0}
            for inv in invoices_result['invoices']:
                days = inv.get('days_overdue', 0)
                amount = inv.get('amount_residual', 0)
                if days <= 0:
                    buckets['current'] += amount
                elif days <= 30:
                    buckets['1_30'] += amount
                elif days <= 60:
                    buckets['31_60'] += amount
                elif days <= 90:
                    buckets['61_90'] += amount
                else:
                    buckets['over_90'] += amount

            return {'success': True, 'aging': buckets, 'currency': 'USD'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def create_credit_note(self, invoice_id: int, reason: str = '') -> Dict:
        """Create a credit note for an invoice."""
        if self._mock:
            return {'success': True, 'mock': True, 'message': f'Mock: Credit note for invoice {invoice_id} would be created'}

        if not self.authenticate():
            return {'success': False, 'error': 'Authentication failed'}

        try:
            ctx = self.execute_kw('account.move', 'action_reverse', [[invoice_id]], {'context': {'active_ids': [invoice_id]}})
            vals: Dict[str, Any] = {'reason': reason} if reason else {}
            wizard_id = self.execute_kw('account.move.reversal', 'create', [vals], {'context': ctx.get('context', {})})
            result = self.execute_kw('account.move.reversal', 'reverse_moves', [[wizard_id]])
            return {'success': True, 'result': result}
        except Exception as e:
            return {'success': False, 'error': str(e)}
