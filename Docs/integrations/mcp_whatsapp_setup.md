# WhatsApp MCP Server Setup

**Purpose**: Configure WhatsApp Business API integration for perception (QUERY tools) and action (ACTION tools with approval)

**Tier**: Gold (G-M4)

---

## Prerequisites

- WhatsApp Business API account (not personal WhatsApp)
- Business phone number verified with WhatsApp
- Access to WhatsApp Business Platform (cloud API or on-premises)

---

## Required Credentials

WhatsApp Business API requires:
1. **Access Token** (temporary, refreshable)
2. **Phone Number ID** (your WhatsApp Business phone number ID)
3. **Business Account ID** (your WhatsApp Business Account ID)
4. **Webhook Verify Token** (for incoming message notifications, optional for perception)

---

## Setup Methods

### Method 1: Environment Variables (Recommended for Development)

Create or update `.env` file in vault root:

```bash
# WhatsApp Business API Credentials
WHATSAPP_ACCESS_TOKEN=your_access_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id_here
WHATSAPP_BUSINESS_ACCOUNT_ID=your_account_id_here
WHATSAPP_API_VERSION=v18.0
WHATSAPP_BASE_URL=https://graph.facebook.com
```

**IMPORTANT**: NEVER commit `.env` to git. It's covered by `.gitignore`.

### Method 2: Credentials File (Recommended for Production)

Create `.secrets/whatsapp_credentials.json`:

```json
{
  "access_token": "your_access_token_here",
  "phone_number_id": "your_phone_number_id_here",
  "business_account_id": "your_account_id_here",
  "api_version": "v18.0",
  "base_url": "https://graph.facebook.com"
}
```

**File permissions**: `chmod 600 .secrets/whatsapp_credentials.json` (owner read/write only)

---

## Obtaining Credentials

### Step 1: Create WhatsApp Business Account

1. Go to https://business.facebook.com/
2. Create a Business Account or use existing
3. Navigate to WhatsApp > API Setup

### Step 2: Get Access Token

1. In WhatsApp API Setup, click "Generate Access Token"
2. Copy the temporary access token (valid 24 hours)
3. For permanent tokens, create a System User in Business Manager:
   - Business Settings > Users > System Users
   - Create system user with WhatsApp permissions
   - Generate permanent access token

### Step 3: Get Phone Number ID

1. WhatsApp API Setup > Phone Numbers
2. Copy the "Phone Number ID" (not the actual phone number)
3. Example: `123456789012345`

### Step 4: Get Business Account ID

1. Business Settings > Business Info
2. Copy "Business ID"
3. Example: `987654321098765`

---

## MCP Server Configuration

The WhatsApp MCP server provides these tools:

### QUERY Tools (Perception - No Approval Required)
- `whatsapp.list_messages` - List recent messages
- `whatsapp.get_message` - Get specific message details
- `whatsapp.get_media` - Download media from message

### ACTION Tools (Execution - Approval Required)
- `whatsapp.send_message` - Send text message
- `whatsapp.reply_message` - Reply to specific message
- `whatsapp.send_media` - Send image/video/document
- `whatsapp.broadcast_message` - Send to multiple recipients

---

## Validation Steps

### Test Query Tools (Perception)

Run the WhatsApp watcher in MCP mode:

```bash
# Test connection with MCP query tools
python whatsapp_watcher_skill.py --mode mcp --once --max-results 5

# Expected output:
# ✅ WhatsApp watcher complete: 5 scanned, X created, Y skipped, 0 errors
```

If successful, intake wrappers will be created in `Social/Inbox/`.

### Test MCP Registry Discovery

```bash
# Refresh MCP tool registry
python brain_mcp_registry_refresh_skill.py --server whatsapp

# Check snapshot
cat Logs/mcp_tools_snapshot_whatsapp.json
```

Expected snapshot should show at least 5 tools (2 query, 3+ action).

---

## Troubleshooting

### Error: "Invalid access token"
- **Cause**: Token expired (temporary tokens last 24 hours)
- **Fix**: Generate new access token or use permanent token from System User

### Error: "Phone number not verified"
- **Cause**: WhatsApp Business phone number not verified
- **Fix**: Complete phone number verification in WhatsApp Business Platform

### Error: "Rate limit exceeded"
- **Cause**: Too many API calls in short time
- **Fix**: Wait 1 hour or contact WhatsApp support to increase limits
- **Prevention**: Use `rate_limit_and_backoff` helper (already implemented)

### Error: "MCP server unreachable"
- **Cause**: MCP server not running or network issue
- **Fix**: Check MCP server logs, verify network connectivity
- **Graceful degradation**: Watcher falls back to mock mode with remediation task

---

## Security Best Practices

1. **Never commit credentials** to git
2. **Use environment variables** for local development
3. **Use .secrets/ files** for production with strict permissions (600)
4. **Rotate tokens** every 90 days minimum
5. **Use webhook signature validation** for incoming messages
6. **Enable 2FA** on Facebook Business Account
7. **Monitor API usage** in WhatsApp Business Platform dashboard

---

## Rate Limits (WhatsApp Business API)

- **Messaging**: 1,000 messages per 24 hours (default tier)
- **Media**: 100 MB per media file
- **Requests**: 80 requests per second (burst), 200 per minute (sustained)

**Tier upgrade**: Request higher limits through WhatsApp Business Platform.

---

## References

- [WhatsApp Business Platform Documentation](https://developers.facebook.com/docs/whatsapp/cloud-api)
- [WhatsApp API Quick Start](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started)
- [Rate Limits](https://developers.facebook.com/docs/whatsapp/cloud-api/overview/rate-limits)

---

**Last Updated**: 2026-02-15
**Version**: 1.0.0 (Gold Tier - G-M4)
