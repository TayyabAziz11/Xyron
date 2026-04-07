# Odoo MCP Server Setup

**Purpose**: Configure Odoo Community/Enterprise integration for accounting operations (perception + action with approval)

**Tier**: Gold (G-M5)

---

## What is Odoo Used For

In this project, Odoo serves as the **accounting and business management backend**:

- **Accounts Receivable (AR)**: Track unpaid invoices, aging reports
- **Invoice Management**: Create, post, and track customer invoices
- **Payment Processing**: Register and reconcile customer payments
- **Revenue Reporting**: Generate financial summaries and KPIs
- **Customer Management**: Maintain customer records and contact info

**Integration Pattern**:
- **Perception**: `odoo_watcher_skill.py` monitors for overdue invoices and accounting alerts
- **Query**: `brain_odoo_query_with_mcp_skill.py` retrieves read-only financial data (no approval needed)
- **Action**: `brain_execute_odoo_with_mcp_skill.py` performs write operations (approval required)

---

## Prerequisites

- Odoo Community Edition (v14+) or Enterprise Edition installed locally or accessible via network
- Odoo user account with API access permissions
- API key or password authentication enabled

---

## Required Credentials

Odoo integration requires:
1. **Base URL**: Odoo instance URL (e.g., `http://localhost:8069` or `https://your-odoo.com`)
2. **Database Name**: Your Odoo database identifier
3. **Username**: Odoo user login (usually email)
4. **API Key** or **Password**: Authentication token
5. **API Version**: Odoo version (e.g., "14.0", "15.0", "16.0")

---

## Setup Methods

### Method 1: Environment Variables (Development)

Create or update `.env` file in vault root:

```bash
# Odoo API Credentials
ODOO_BASE_URL=http://localhost:8069
ODOO_DATABASE=my_company_db
ODOO_USERNAME=admin@example.com
ODOO_PASSWORD=your_api_key_or_password
ODOO_API_VERSION=16.0
```

**IMPORTANT**: NEVER commit `.env` to git. It's covered by `.gitignore`.

### Method 2: Credentials File (Production Recommended)

Create `.secrets/odoo_credentials.json`:

```json
{
  "base_url": "http://localhost:8069",
  "database": "my_company_db",
  "username": "admin@example.com",
  "password": "your_api_key_or_password",
  "api_version": "16.0",
  "use_ssl": false,
  "verify_ssl": true
}
```

**File permissions**: `chmod 600 .secrets/odoo_credentials.json` (owner read/write only)

---

## Obtaining Credentials

### Step 1: Install Odoo Locally (Optional)

For development/testing, run Odoo Community Edition via Docker:

```bash
# Docker Compose example
docker run -d -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo \
  -e POSTGRES_DB=postgres --name db postgres:13

docker run -d -p 8069:8069 --name odoo --link db:db \
  -e HOST=db -e USER=odoo -e PASSWORD=odoo odoo:16
```

Access Odoo at `http://localhost:8069` and complete initial setup.

### Step 2: Create API User (Recommended)

1. Log into Odoo as administrator
2. Navigate to **Settings → Users & Companies → Users**
3. Click **Create** to add new user:
   - Name: "AI Assistant API"
   - Login: `ai-assistant@example.com`
   - Access Rights:
     - **Accounting**: Billing
     - **Invoicing**: Administrator (if using Invoicing app)
4. Generate API key (if using Odoo 14+):
   - User settings → Account Security → API Keys
   - Click **New API Key**
   - Label: "Personal AI Employee"
   - Copy and save the generated key

### Step 3: Get Database Name

The database name is shown in the URL after login:
- URL: `http://localhost:8069/web?db=my_company_db`
- Database name: `my_company_db`

Or use Odoo's database manager: `http://localhost:8069/web/database/manager`

### Step 4: Configure Credentials File

Place credentials in `.secrets/odoo_credentials.json` using the format above.

---

## MCP Server Configuration

The Odoo MCP server provides these tools:

### QUERY Tools (Perception - No Approval Required)
- `odoo.list_unpaid_invoices` - List invoices with unpaid status
- `odoo.get_invoice` - Get specific invoice details
- `odoo.get_customer` - Get customer information
- `odoo.revenue_summary` - Get revenue totals by period
- `odoo.ar_aging_summary` - Get accounts receivable aging report

### ACTION Tools (Execution - Approval Required)
- `odoo.create_customer` - Create new customer record
- `odoo.create_invoice` - Create new invoice (draft state)
- `odoo.post_invoice` - Post invoice (validate and make official)
- `odoo.register_payment` - Register customer payment
- `odoo.create_credit_note` - Create credit note for returns/adjustments

---

## Running in Mock vs Real Mode

### Mock Mode (Default)

All Odoo skills support `--mode mock` for testing without a real Odoo instance:

```bash
# Watcher in mock mode (uses fixtures)
python3 odoo_watcher_skill.py --mode mock --once

# Query in mock mode
python3 brain_odoo_query_with_mcp_skill.py --mode mock --operation list_unpaid_invoices

# Executor in mock mode (dry-run)
python3 brain_execute_odoo_with_mcp_skill.py --mode mock
```

**Mock mode uses**:
- `templates/mock_odoo_invoices.json` - Sample invoice data
- Simulated responses without real Odoo connection
- Useful for development, testing, and CI/CD

### Real MCP Mode

When Odoo credentials are configured:

```bash
# Watcher in MCP mode (requires Odoo running)
python3 odoo_watcher_skill.py --mode mcp --once

# Query in MCP mode
python3 brain_odoo_query_with_mcp_skill.py --mode mcp --operation revenue_summary

# Executor in MCP mode (still requires --execute for real actions)
python3 brain_execute_odoo_with_mcp_skill.py --mode mcp --execute
```

**Real mode requires**:
- Valid `.secrets/odoo_credentials.json`
- Odoo instance running and accessible
- Network connectivity to Odoo server

---

## Validation Steps

### Test 1: Odoo Connection

```bash
# Test Odoo connectivity (if you have curl)
curl -X POST http://localhost:8069/jsonrpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
      "service": "common",
      "method": "version"
    },
    "id": 1
  }'

# Expected response:
# {"jsonrpc": "2.0", "id": 1, "result": {"server_version": "16.0"}}
```

### Test 2: Odoo Watcher (Mock Mode)

```bash
python3 odoo_watcher_skill.py --mode mock --once --max-results 3

# Expected output:
# ✅ Odoo watcher complete: 3 scanned, X created, Y skipped, 0 errors
```

If successful, intake wrappers appear in `Business/Accounting/`.

### Test 3: MCP Registry Discovery

```bash
# Refresh MCP tool registry for Odoo
python3 brain_mcp_registry_refresh_skill.py --server odoo --mock

# Check snapshot
cat Logs/mcp_tools_snapshot_odoo.json
```

Expected snapshot shows at least 5 tools (query + action).

---

## Troubleshooting

### Error: "Odoo credentials not found"
- **Cause**: Missing `.secrets/odoo_credentials.json`
- **Fix**: Create credentials file using template above
- **Verify**: `ls -la .secrets/odoo_credentials.json`

### Error: "Connection refused" or "Connection timeout"
- **Cause**: Odoo instance not running or wrong URL
- **Fix**:
  1. Verify Odoo is running: `curl http://localhost:8069`
  2. Check base_url in credentials matches Odoo URL
  3. Check firewall/network settings

### Error: "Authentication failed" or "Invalid credentials"
- **Cause**: Wrong username, password, or database name
- **Fix**:
  1. Verify credentials by logging into Odoo web interface
  2. Check database name in URL
  3. Regenerate API key if using key-based auth

### Error: "Access Denied" or "Permission error"
- **Cause**: Odoo user lacks required permissions
- **Fix**: Grant user access rights:
  - Settings → Users → Your API user
  - Access Rights → Accounting/Invoicing permissions

### Error: "Model not found: account.move"
- **Cause**: Accounting module not installed in Odoo
- **Fix**: Install Accounting or Invoicing app:
  - Apps → Search "Accounting" → Install

---

## Security Best Practices

1. **Never commit credentials** to git
2. **Use API keys** instead of passwords when possible
3. **Restrict API user permissions** to minimum required (accounting only)
4. **Rotate credentials** every 90 days
5. **Enable SSL/TLS** for production Odoo instances (`use_ssl: true`)
6. **Use firewall rules** to restrict Odoo access to known IPs
7. **Monitor API usage** via Odoo logs (`/var/log/odoo/`)
8. **Backup Odoo database** regularly before automation

---

## Rate Limits and Performance

### Odoo API Limits

Odoo doesn't have built-in rate limits, but:
- **Default**: Unlimited (local instance)
- **Cloud/SaaS**: May have limits depending on plan
- **Best practice**: Add artificial limits in code (e.g., 100 requests/minute)

### Performance Tips

- **Batch operations**: Use domain filters to reduce query count
- **Limit results**: Use `limit` parameter (default: 50 records)
- **Cache results**: Store invoice summaries to avoid repeated queries
- **Use fields parameter**: Only fetch required fields

---

## API Reference

### Odoo JSON-RPC Patterns

**Authentication**:
```python
# Get user ID
uid = odoo.authenticate(database, username, password, {})
```

**Query Records**:
```python
# Search invoices
invoice_ids = odoo.execute_kw(
    database, uid, password,
    'account.move', 'search',
    [[('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]]
)

# Read invoice details
invoices = odoo.execute_kw(
    database, uid, password,
    'account.move', 'read',
    [invoice_ids], {'fields': ['name', 'partner_id', 'amount_total']}
)
```

**Create Records**:
```python
# Create invoice
invoice_id = odoo.execute_kw(
    database, uid, password,
    'account.move', 'create',
    [{'move_type': 'out_invoice', 'partner_id': 123}]
)
```

---

## References

- [Odoo Documentation](https://www.odoo.com/documentation)
- [Odoo External API](https://www.odoo.com/documentation/16.0/developer/api/external_api.html)
- [Odoo Accounting Module](https://www.odoo.com/documentation/16.0/applications/finance/accounting.html)
- [Odoo Docker Images](https://hub.docker.com/_/odoo)

---

**Last Updated**: 2026-02-16
**Version**: 1.0.0 (Gold Tier - G-M5)
