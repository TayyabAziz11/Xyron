#!/usr/bin/env python3
"""
Odoo MCP Server — JSON-RPC 2.0 over stdin/stdout.

Exposes Odoo Community accounting operations as MCP tools for Claude CLI.
Uses the OdooAPIHelper (JSON-RPC external API, Odoo 19+).

Gold Tier — G2: Odoo Community + JSON-RPC MCP Integration

Tools:
  get_unpaid_invoices(limit, status_filter)   [PERCEPTION — read-only]
  get_revenue_summary(year)                   [PERCEPTION — read-only]
  get_aging_report()                          [PERCEPTION — read-only]
  get_subscriptions()                         [PERCEPTION — read-only]
  create_invoice(partner_id, lines, note)     [ACTION — HITL required]
  register_payment(invoice_id, amount, memo)  [ACTION — HITL required; never auto-retry]

HITL Safety Rules:
  - create_invoice / register_payment: Claude must create a Pending_Approval file FIRST.
    These tools should only be called AFTER the approval file has been moved to /Approved.
  - register_payment is NEVER auto-retried on failure (financial idempotency).

Architecture:
  - Mock mode when .secrets/odoo_credentials.json has placeholder password (safe default).
  - Real mode when valid Odoo credentials are configured.
  - All write actions are logged to Logs/YYYY-MM-DD.json (hackathon audit schema).

Usage:
    python3 mcp_servers/odoo_mcp/server.py

Register in Claude CLI (~/.claude.json mcpServers section):
    {
      "odoo": {
        "command": "python3",
        "args": ["/absolute/path/to/mcp_servers/odoo_mcp/server.py"]
      }
    }
"""

import sys
import os
import json
import signal
import logging
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure repo/src on path
_HERE = Path(__file__).parent.resolve()
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

logging.basicConfig(
    level=logging.WARNING,
    format="[odoo-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("odoo_mcp")


# ── Tool definitions (MCP spec) ───────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_unpaid_invoices",
        "description": (
            "Fetch unpaid or overdue customer invoices from Odoo. "
            "PERCEPTION ONLY — read-only, no side effects. "
            "Returns invoice list with customer, amount, due date, days overdue."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 25,
                    "description": "Max invoices to return (default: 25)",
                },
                "status_filter": {
                    "type": "string",
                    "enum": ["unpaid", "overdue", "all"],
                    "default": "unpaid",
                    "description": "Filter: unpaid (all unpaid), overdue (past due date), all",
                },
            },
        },
    },
    {
        "name": "get_revenue_summary",
        "description": (
            "Get revenue summary from Odoo: total invoiced, total paid, total outstanding. "
            "PERCEPTION ONLY — read-only. Use for weekly CEO briefing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "Year to summarize (default: current year)",
                },
            },
        },
    },
    {
        "name": "get_aging_report",
        "description": (
            "Get accounts receivable aging report from Odoo. "
            "Returns AR buckets: current, 1-30 days, 31-60 days, 61-90 days, 90+ days. "
            "PERCEPTION ONLY — read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_subscriptions",
        "description": (
            "Detect recurring/subscription invoices in Odoo by scanning for recurring patterns. "
            "PERCEPTION ONLY — read-only. Used for subscription anomaly detection in CEO briefing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "flag_cost_increase": {
                    "type": "boolean",
                    "default": True,
                    "description": "Flag subscriptions with cost increase > 20%",
                },
            },
        },
    },
    {
        "name": "create_invoice",
        "description": (
            "Create a draft customer invoice in Odoo. "
            "REQUIRES HUMAN APPROVAL — write action. "
            "Claude must create /Pending_Approval/ODOO_invoice_<client>.md FIRST "
            "and only call this tool AFTER the file has been moved to /Approved. "
            "Creates account.move in draft state (does NOT post/confirm it)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "partner_id": {
                    "type": "integer",
                    "description": "Odoo res.partner ID of the customer",
                },
                "lines": {
                    "type": "array",
                    "description": "Invoice line items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Line description"},
                            "quantity": {"type": "number", "default": 1},
                            "price_unit": {"type": "number", "description": "Unit price"},
                        },
                        "required": ["name", "price_unit"],
                    },
                },
                "note": {
                    "type": "string",
                    "description": "Optional internal note / reference",
                },
                "approval_ref": {
                    "type": "string",
                    "description": "Reference to the approved plan file (for audit trail)",
                },
            },
            "required": ["partner_id", "lines"],
        },
    },
    {
        "name": "register_payment",
        "description": (
            "Register a payment against an existing Odoo invoice. "
            "REQUIRES HUMAN APPROVAL — financial write action. "
            "Claude must create /Pending_Approval/ODOO_payment_<invoice>.md FIRST. "
            "IMPORTANT: This tool is NEVER auto-retried on failure (financial safety). "
            "If it fails, create a remediation task instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "integer",
                    "description": "Odoo account.move ID of the invoice to pay",
                },
                "amount": {
                    "type": "number",
                    "description": "Payment amount (must be > 0)",
                },
                "memo": {
                    "type": "string",
                    "description": "Payment memo / reference (e.g. 'January invoice payment')",
                },
                "approval_ref": {
                    "type": "string",
                    "description": "Reference to the approved plan file (for audit trail)",
                },
            },
            "required": ["invoice_id", "amount"],
        },
    },
]


# ── Lazy helper singleton ─────────────────────────────────────────────────────

_helper: Optional[Any] = None


def _get_helper():
    global _helper
    if _helper is not None:
        return _helper

    from ai_operator.core.odoo_api_helper import OdooAPIHelper

    creds_path = _REPO / ".secrets" / "odoo_credentials.json"
    helper = OdooAPIHelper(
        credentials_path=str(creds_path) if creds_path.exists() else None,
    )

    # Load credentials to determine mock vs real mode
    if not helper.load_credentials():
        logger.warning("Odoo credentials not found or placeholder — running in mock mode")
        helper._mock = True

    _helper = helper
    return _helper


def _get_audit_logger():
    try:
        from ai_operator.core.audit_logger import AuditLogger
        return AuditLogger(str(_REPO / "Logs"))
    except ImportError:
        return None


# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_get_unpaid_invoices(args: Dict) -> str:
    limit = int(args.get("limit", 25))
    status_filter = args.get("status_filter", "unpaid")

    helper = _get_helper()
    result = helper.list_invoices(status_filter=status_filter, limit=limit)
    return json.dumps(result, ensure_ascii=False)


def handle_get_revenue_summary(args: Dict) -> str:
    from datetime import datetime
    year = int(args.get("year", datetime.now().year))
    helper = _get_helper()
    result = helper.revenue_summary(year=year)
    return json.dumps(result, ensure_ascii=False)


def handle_get_aging_report(_args: Dict) -> str:
    helper = _get_helper()
    result = helper.ar_aging_summary()
    return json.dumps(result, ensure_ascii=False)


def handle_get_subscriptions(args: Dict) -> str:
    """
    Detect subscription/recurring invoices by scanning all invoices for recurring patterns.
    Flags subscriptions with cost increase > 20% if requested.
    """
    flag_increase = bool(args.get("flag_cost_increase", True))
    helper = _get_helper()

    # Get all invoices
    result = helper.list_invoices(status_filter="all", limit=200)
    if not result.get("success"):
        return json.dumps(result, ensure_ascii=False)

    invoices = result.get("invoices", [])

    # Subscription keywords (from hackathon audit_logic.py example)
    SUBSCRIPTION_KEYWORDS = [
        "subscription", "monthly", "annual", "recurring", "saas",
        "license", "plan", "membership", "retainer", "service fee",
    ]

    subscriptions = []
    # Group by customer to detect recurring patterns
    by_customer: Dict[str, list] = {}
    for inv in invoices:
        cname = inv.get("customer_name", "Unknown")
        by_customer.setdefault(cname, []).append(inv)

    for customer, invs in by_customer.items():
        # Check if any invoice lines mention subscription keywords
        for inv in invs:
            desc = (inv.get("description") or inv.get("invoice_number") or "").lower()
            is_sub = any(kw in desc for kw in SUBSCRIPTION_KEYWORDS)

            if is_sub or len(invs) >= 2:  # recurring pattern = 2+ invoices same customer
                entry = {
                    "customer_name": customer,
                    "invoice_count": len(invs),
                    "latest_invoice": invs[0].get("invoice_number"),
                    "latest_amount": invs[0].get("amount_total", 0),
                    "is_subscription_keyword": is_sub,
                }

                # Cost increase check — compare latest vs previous
                if flag_increase and len(invs) >= 2:
                    latest_amt = invs[0].get("amount_total", 0)
                    prev_amt = invs[1].get("amount_total", 0)
                    if prev_amt > 0:
                        change_pct = ((latest_amt - prev_amt) / prev_amt) * 100
                        entry["cost_change_pct"] = round(change_pct, 1)
                        entry["cost_increase_flag"] = change_pct > 20

                subscriptions.append(entry)
                break  # one entry per customer

    flagged = [s for s in subscriptions if s.get("cost_increase_flag")]
    return json.dumps({
        "success": True,
        "subscriptions": subscriptions,
        "flagged_cost_increase": flagged,
        "total": len(subscriptions),
        "mock": helper.is_mock,
    }, ensure_ascii=False)


def handle_create_invoice(args: Dict) -> str:
    """Create a draft invoice. HITL: must be called post-approval only."""
    partner_id = args.get("partner_id")
    lines = args.get("lines", [])
    note = args.get("note", "")
    approval_ref = args.get("approval_ref", "")

    if not partner_id:
        return json.dumps({"success": False, "error": "partner_id is required"})
    if not lines:
        return json.dumps({"success": False, "error": "at least one invoice line is required"})

    helper = _get_helper()
    result = helper.create_invoice(
        partner_id=int(partner_id),
        lines=lines,
        note=note,
    )

    # Audit log
    audit = _get_audit_logger()
    if audit:
        audit.log(
            action_type="odoo_invoice_create",
            actor="claude_code",
            parameters={"partner_id": partner_id, "line_count": len(lines), "note": note},
            approval_status="approved",
            approval_ref=approval_ref,
            result="success" if result.get("success") else "failure",
            error=result.get("error"),
        )

    return json.dumps(result, ensure_ascii=False)


def handle_register_payment(args: Dict) -> str:
    """Register a payment. HITL required. NEVER auto-retried on failure."""
    invoice_id = args.get("invoice_id")
    amount = args.get("amount")
    memo = args.get("memo", "")
    approval_ref = args.get("approval_ref", "")

    if not invoice_id or not amount:
        return json.dumps({"success": False, "error": "invoice_id and amount are required"})
    if float(amount) <= 0:
        return json.dumps({"success": False, "error": "amount must be > 0"})

    helper = _get_helper()
    # Note: NO retry for payment operations (financial idempotency)
    result = helper.register_payment(
        invoice_id=int(invoice_id),
        amount=float(amount),
        memo=memo,
        _no_retry=True,  # Signal to helper: never retry this call
    )

    # Audit log
    audit = _get_audit_logger()
    if audit:
        audit.log(
            action_type="odoo_payment_register",
            actor="claude_code",
            parameters={"invoice_id": invoice_id, "amount": amount, "memo": memo},
            approval_status="approved",
            approval_ref=approval_ref,
            result="success" if result.get("success") else "failure",
            error=result.get("error"),
        )

    if not result.get("success"):
        # Write remediation task on payment failure
        _write_payment_remediation(invoice_id, amount, result.get("error", "Unknown error"))

    return json.dumps(result, ensure_ascii=False)


def _write_payment_remediation(invoice_id: Any, amount: Any, error: str):
    """Write a remediation task file when payment registration fails."""
    try:
        from datetime import datetime, timezone
        needs_action_dir = _REPO / "Needs_Action"
        needs_action_dir.mkdir(exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        fname = needs_action_dir / f"remediation__odoo_payment__{ts}.md"
        fname.write_text(
            f"---\nsource: odoo_mcp\ntype: payment_failure\nstatus: needs_action\ncreated: {datetime.now(timezone.utc).isoformat()}\n---\n\n"
            f"# Payment Registration Failed\n\n"
            f"**Invoice ID:** {invoice_id}\n"
            f"**Amount:** ${amount}\n"
            f"**Error:** {error}\n\n"
            f"## Required Action\n"
            f"- Verify the invoice exists in Odoo\n"
            f"- Check Odoo connectivity\n"
            f"- Manually register payment if needed\n"
            f"- NEVER auto-retry payment registrations\n",
            encoding="utf-8",
        )
        logger.warning(f"Payment failure remediation task created: {fname.name}")
    except Exception as e:
        logger.error(f"Failed to write remediation task: {e}")


HANDLERS = {
    "get_unpaid_invoices": handle_get_unpaid_invoices,
    "get_revenue_summary": handle_get_revenue_summary,
    "get_aging_report": handle_get_aging_report,
    "get_subscriptions": handle_get_subscriptions,
    "create_invoice": handle_create_invoice,
    "register_payment": handle_register_payment,
}


# ── JSON-RPC 2.0 server loop ──────────────────────────────────────────────────

def send(obj: Dict):
    """Write a JSON-RPC message to stdout."""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def error_response(req_id: Any, code: int, message: str) -> Dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def ok_response(req_id: Any, result: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def dispatch(msg: Dict) -> Optional[Dict]:
    """Handle a single JSON-RPC message. Return None for notifications."""
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if req_id is None and method not in ("initialize",):
        return None

    # ── MCP handshake ─────────────────────────────────────────────────────────
    if method == "initialize":
        return ok_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "odoo-accounting", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return ok_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if not handler:
            return error_response(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result_text = handler(tool_args)
            return ok_response(req_id, {
                "content": [{"type": "text", "text": result_text}]
            })
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Tool {tool_name} failed: {e}\n{tb}")
            return ok_response(req_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True,
            })

    if method == "ping":
        return ok_response(req_id, {})

    return error_response(req_id, -32601, f"Method not found: {method}")


def _shutdown(sig, frame):
    logger.info("Odoo MCP server shutting down …")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("Odoo MCP server started — listening on stdin")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            send(error_response(None, -32700, f"Parse error: {e}"))
            continue

        try:
            response = dispatch(msg)
        except Exception as e:
            response = error_response(msg.get("id"), -32603, f"Internal error: {e}")

        if response is not None:
            send(response)

    _shutdown(None, None)


if __name__ == "__main__":
    main()
