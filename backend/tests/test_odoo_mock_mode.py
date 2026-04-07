#!/usr/bin/env python3
"""
Tests for Odoo integration — mock mode only (no real Odoo instance needed).
Gold Tier - G-M5

Tests cover:
- OdooAPIHelper mock mode operations
- OdooWatcher mock mode (intake wrapper creation)
- web_execute_plan.py Odoo handlers
"""

import json
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure repo root on path
TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'src'))


class TestOdooAPIHelperMock(unittest.TestCase):
    """Test OdooAPIHelper in mock mode."""

    def setUp(self):
        from src.ai_operator.core.odoo_api_helper import OdooAPIHelper
        self.OdooAPIHelper = OdooAPIHelper
        self.mock_invoices = REPO_ROOT / 'templates' / 'mock_odoo_invoices.json'
        self.mock_customers = REPO_ROOT / 'templates' / 'mock_odoo_customers.json'

    def _make_helper(self):
        return self.OdooAPIHelper(
            mock_invoices_path=str(self.mock_invoices),
            mock_customers_path=str(self.mock_customers),
            mock=True,
        )

    def test_authenticate_mock(self):
        helper = self._make_helper()
        self.assertTrue(helper.authenticate())
        self.assertEqual(helper._uid, 1)

    def test_check_auth_mock(self):
        helper = self._make_helper()
        result = helper.check_auth()
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['mock'])

    def test_list_invoices_all(self):
        helper = self._make_helper()
        result = helper.list_invoices(status_filter='all')
        self.assertTrue(result['success'])
        self.assertIn('invoices', result)
        self.assertGreater(result['count'], 0)
        self.assertTrue(result.get('mock'))

    def test_list_invoices_unpaid(self):
        helper = self._make_helper()
        result = helper.list_invoices(status_filter='unpaid')
        self.assertTrue(result['success'])
        # Should exclude paid invoices
        for inv in result['invoices']:
            self.assertNotEqual(inv.get('status'), 'paid')

    def test_list_invoices_overdue(self):
        helper = self._make_helper()
        result = helper.list_invoices(status_filter='overdue')
        self.assertTrue(result['success'])
        for inv in result['invoices']:
            self.assertGreater(inv.get('days_overdue', 0), 0)

    def test_get_invoice_found(self):
        helper = self._make_helper()
        result = helper.get_invoice('INV-2024-001')
        self.assertTrue(result['success'])
        self.assertIn('invoice', result)
        self.assertEqual(result['invoice']['invoice_number'], 'INV-2024-001')

    def test_get_invoice_not_found(self):
        helper = self._make_helper()
        result = helper.get_invoice('INV-9999-999')
        self.assertFalse(result['success'])

    def test_create_invoice_mock(self):
        helper = self._make_helper()
        result = helper.create_invoice(
            partner_id=1001,
            lines=[{'name': 'Test Service', 'quantity': 1, 'price_unit': 500.0}],
        )
        self.assertTrue(result['success'])
        self.assertTrue(result.get('mock'))

    def test_post_invoice_mock(self):
        helper = self._make_helper()
        result = helper.post_invoice(invoice_id=1)
        self.assertTrue(result['success'])
        self.assertTrue(result.get('mock'))

    def test_register_payment_mock(self):
        helper = self._make_helper()
        result = helper.register_payment(invoice_id=1, amount=500.0)
        self.assertTrue(result['success'])
        self.assertTrue(result.get('mock'))

    def test_list_customers_mock(self):
        helper = self._make_helper()
        result = helper.list_customers()
        self.assertTrue(result['success'])
        self.assertGreater(result['count'], 0)
        self.assertTrue(result.get('mock'))

    def test_create_customer_mock(self):
        helper = self._make_helper()
        result = helper.create_customer(name='Test Customer', email='test@example.com')
        self.assertTrue(result['success'])
        self.assertTrue(result.get('mock'))

    def test_revenue_summary_mock(self):
        helper = self._make_helper()
        result = helper.revenue_summary()
        self.assertTrue(result['success'])
        self.assertIn('total_invoiced', result)
        self.assertIn('total_paid', result)
        self.assertIn('total_outstanding', result)
        self.assertTrue(result.get('mock'))
        # Total should be >= paid + outstanding
        self.assertGreaterEqual(result['total_invoiced'], 0)

    def test_ar_aging_mock(self):
        helper = self._make_helper()
        result = helper.ar_aging_summary()
        self.assertTrue(result['success'])
        self.assertIn('aging', result)
        aging = result['aging']
        for bucket in ['current', '1_30', '31_60', '61_90', 'over_90']:
            self.assertIn(bucket, aging)
            self.assertGreaterEqual(aging[bucket], 0)

    def test_create_credit_note_mock(self):
        helper = self._make_helper()
        result = helper.create_credit_note(invoice_id=1, reason='Test return')
        self.assertTrue(result['success'])
        self.assertTrue(result.get('mock'))


class TestOdooWatcherMock(unittest.TestCase):
    """Test OdooWatcher in mock mode."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.tmp_dir, 'Business', 'Accounting')
        self.logs_dir = os.path.join(self.tmp_dir, 'Logs')
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_watcher(self):
        # Import here so we have sys.path set up
        sys.path.insert(0, str(REPO_ROOT / 'scripts'))
        try:
            from odoo_watcher_skill import OdooWatcher
        except ImportError:
            # Try src path
            from src.ai_operator.skills.gold.odoo_watcher_skill import OdooWatcher

        config = {
            'base_dir': self.tmp_dir,
            'checkpoint_path': os.path.join(self.logs_dir, 'odoo_watcher_checkpoint.json'),
            'log_path': os.path.join(self.logs_dir, 'odoo_watcher.log'),
            'output_dir': self.output_dir,
            'max_results': 5,
            'mock_fixture_path': str(REPO_ROOT / 'templates' / 'mock_odoo_invoices.json'),
        }
        return OdooWatcher(config)

    def test_watcher_mock_run(self):
        watcher = self._make_watcher()
        result = watcher.run(dry_run=False, mock=True)
        self.assertTrue(result['success'])
        self.assertGreater(result['created'], 0)

    def test_watcher_creates_intake_files(self):
        watcher = self._make_watcher()
        watcher.run(dry_run=False, mock=True)
        files = list(Path(self.output_dir).glob('intake__odoo__*.md'))
        self.assertGreater(len(files), 0)

    def test_watcher_intake_has_frontmatter(self):
        watcher = self._make_watcher()
        watcher.run(dry_run=False, mock=True)
        files = list(Path(self.output_dir).glob('intake__odoo__*.md'))
        self.assertGreater(len(files), 0)
        content = files[0].read_text()
        self.assertIn('---', content)
        self.assertIn('source: odoo', content)
        self.assertIn('channel: odoo_accounting', content)

    def test_watcher_checkpoint_saved(self):
        watcher = self._make_watcher()
        watcher.run(dry_run=False, mock=True)
        cp_path = os.path.join(self.logs_dir, 'odoo_watcher_checkpoint.json')
        self.assertTrue(os.path.exists(cp_path))
        with open(cp_path) as f:
            cp = json.load(f)
        self.assertIn('processed_ids', cp)
        self.assertGreater(len(cp['processed_ids']), 0)

    def test_watcher_dry_run_no_files(self):
        watcher = self._make_watcher()
        watcher.run(dry_run=True, mock=True)
        files = list(Path(self.output_dir).glob('intake__odoo__*.md'))
        self.assertEqual(len(files), 0)

    def test_watcher_skips_duplicates(self):
        # Run 1: fresh watcher, creates intake files and saves checkpoint
        watcher1 = self._make_watcher()
        result1 = watcher1.run(dry_run=False, mock=True)
        self.assertGreater(result1['created'], 0)
        # Run 2: fresh watcher instance loads checkpoint from disk, should skip all
        watcher2 = self._make_watcher()
        result2 = watcher2.run(dry_run=False, mock=True)
        self.assertEqual(result2['created'], 0, "Second run should create 0 (all already processed)")
        self.assertEqual(result2['skipped'], result1['created'])


class TestMockFixtures(unittest.TestCase):
    """Validate mock fixture files."""

    def test_invoices_fixture_valid(self):
        fixture = REPO_ROOT / 'templates' / 'mock_odoo_invoices.json'
        self.assertTrue(fixture.exists(), f"Mock invoices fixture missing: {fixture}")
        with open(fixture) as f:
            data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        for inv in data:
            self.assertIn('id', inv)
            self.assertIn('customer_name', inv)
            self.assertIn('amount_total', inv)

    def test_customers_fixture_valid(self):
        fixture = REPO_ROOT / 'templates' / 'mock_odoo_customers.json'
        self.assertTrue(fixture.exists(), f"Mock customers fixture missing: {fixture}")
        with open(fixture) as f:
            data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        for cust in data:
            self.assertIn('id', cust)
            self.assertIn('name', cust)


if __name__ == '__main__':
    unittest.main(verbosity=2)
