# Gmail MCP Setup Guide

**Purpose:** Configure Gmail MCP server integration for Personal AI Employee (Silver Tier)

**Tier:** Silver
**Created:** 2026-02-14
**Status:** Operational

---

## Overview

The Personal AI Employee uses the **Gmail MCP Server** to interact with Gmail for:
- **Query Operations** (read-only): list, search, read emails, get labels
- **Action Operations** (approval-gated): send, draft, reply to emails

**MCP Server:** `@modelcontextprotocol/server-gmail` (official MCP Gmail server)

---

## Architecture

```
Claude Code CLI
    ↓ (MCP Protocol)
Gmail MCP Server (@modelcontextprotocol/server-gmail)
    ↓ (Gmail API)
Gmail Account (OAuth2 authenticated)
```

**Key Components:**
1. **Claude Code CLI** - Connects to MCP server via stdio
2. **Gmail MCP Server** - Node.js server exposing Gmail operations
3. **Gmail API** - Google's official API (OAuth2 credentials required)

---

## Prerequisites

### 1. Node.js and npm

```bash
# Check if installed
node --version  # Should be v18+ or v20+
npm --version

# If not installed, download from: https://nodejs.org/
```

### 2. Gmail API Credentials

You need OAuth2 credentials from Google Cloud Console:

1. Go to: https://console.cloud.google.com/
2. Create a new project (or use existing)
3. Enable "Gmail API"
4. Create OAuth2 credentials:
   - Application type: Desktop app
   - Download JSON credentials file
5. Save as: `gmail_credentials.json` (gitignored)

**Required OAuth Scopes:**
- `https://www.googleapis.com/auth/gmail.readonly` (for queries)
- `https://www.googleapis.com/auth/gmail.send` (for sending)
- `https://www.googleapis.com/auth/gmail.compose` (for drafts)

---

## Installation

### Step 1: Install Gmail MCP Server

```bash
# Install globally
npm install -g @modelcontextprotocol/server-gmail

# Verify installation
which mcp-server-gmail
```

### Step 2: Configure Claude Code CLI

Add Gmail MCP server to Claude Code CLI configuration.

**Config Location:** `~/.claude/claude_desktop_config.json` (or similar)

**Add MCP Server Entry:**

```json
{
  "mcpServers": {
    "gmail": {
      "command": "mcp-server-gmail",
      "args": [],
      "env": {
        "GOOGLE_CLIENT_ID": "YOUR_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET": "YOUR_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI": "http://localhost:3000/oauth2callback"
      }
    }
  }
}
```

**Alternative (using credentials file):**

```json
{
  "mcpServers": {
    "gmail": {
      "command": "mcp-server-gmail",
      "args": ["--credentials", "/absolute/path/to/gmail_credentials.json"]
    }
  }
}
```

### Step 3: Authenticate

```bash
# First run triggers OAuth flow
# Browser will open for Google account authorization
# Token saved to: ~/.gmail-mcp-token (or similar)
```

---

## Verification

### 1. List Available MCP Tools

```bash
python brain_execute_with_mcp_skill.py --list-tools
```

**Expected Output:**

```json
{
  "server": "gmail",
  "tools": [
    {
      "name": "list_messages",
      "description": "List Gmail messages with optional filters"
    },
    {
      "name": "search_messages",
      "description": "Search messages using Gmail query syntax"
    },
    {
      "name": "get_message",
      "description": "Get full message content by ID"
    },
    {
      "name": "send_email",
      "description": "Send an email via Gmail"
    },
    {
      "name": "create_draft",
      "description": "Create a draft email"
    },
    {
      "name": "list_labels",
      "description": "List all Gmail labels"
    }
  ],
  "status": "available",
  "cached_at": "2026-02-14 00:00:00 UTC"
}
```

### 2. Test Query Operation (Safe - No Side Effects)

```bash
# List recent emails
python brain_email_query_with_mcp_skill.py --query "newer_than:7d" --max-results 3
```

**Expected:** Returns list of recent emails (redacted) and logs to `Logs/mcp_actions.log`

### 3. Test Dry-Run Send (Safe - No Real Email Sent)

Create a test plan with send_email operation, then:

```bash
python brain_execute_with_mcp_skill.py --plan Plans/PLAN_test_send.md
# (runs in dry-run mode by default)
```

**Expected:** Logs preview of email, no real send

---

## Security & Privacy

### Gitignore Patterns (CRITICAL)

**Already Configured in `.gitignore`:**

```gitignore
# Gmail MCP credentials and tokens
gmail_credentials.json
.gmail-mcp-token
**/gmail_credentials.json
**/.gmail-mcp-token

# MCP logs (may contain email addresses)
Logs/mcp_actions.log
Logs/mcp_tools_snapshot.json

# Environment variables
.env
.env.local
```

### PII Redaction

All logging automatically redacts:
- Email addresses (shown as `<REDACTED_EMAIL>`)
- Phone numbers (shown as `<REDACTED_PHONE>`)
- Message IDs (truncated to first 8 chars)

**Example Log Entry:**

```json
{
  "timestamp": "2026-02-14 00:15:30 UTC",
  "plan_id": "ad-hoc-query",
  "tool": "gmail",
  "operation": "search_messages",
  "parameters": {"query": "newer_than:7d"},
  "mode": "query",
  "success": true,
  "duration_ms": 245,
  "response_summary": "Found 3 messages (emails redacted)"
}
```

---

## Troubleshooting

### Issue: "MCP Gmail server not available"

**Causes:**
1. Gmail MCP server not installed
2. Claude Code CLI not configured
3. OAuth token expired

**Solutions:**

```bash
# 1. Check if server is installed
which mcp-server-gmail

# 2. Re-authenticate (deletes old token)
rm ~/.gmail-mcp-token
# Then run any MCP command to trigger OAuth flow

# 3. Verify Claude CLI config
cat ~/.claude/claude_desktop_config.json | grep -A 10 gmail
```

### Issue: "Authentication failed"

**Causes:**
1. Invalid OAuth credentials
2. Insufficient scopes
3. Token revoked

**Solutions:**

```bash
# Delete token and re-authenticate
rm ~/.gmail-mcp-token

# Verify credentials file has all required fields:
cat gmail_credentials.json | jq '.installed | keys'
# Should have: client_id, client_secret, redirect_uris
```

### Issue: "No tools found"

**Causes:**
1. MCP server not running
2. Wrong server name in config
3. Claude CLI not restarted after config change

**Solutions:**

```bash
# 1. Test server directly
echo '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}' | mcp-server-gmail

# 2. Restart Claude CLI
# (Close all Claude Code windows and re-open)

# 3. Check server name matches config
cat ~/.claude/claude_desktop_config.json | jq '.mcpServers | keys'
```

### Issue: "Rate limit exceeded"

**Solution:**
Gmail API has quota limits. Wait 1 minute between requests or reduce frequency.

```python
# Built into brain_email_query_with_mcp_skill.py:
# Automatic rate limiting: max 10 requests/minute
```

---

## Operation Categories

### Query Operations (No Approval Required)

These operations are **read-only** and do NOT require plan approval:

- `list_messages` - List emails with filters
- `search_messages` - Search using Gmail query syntax
- `get_message` - Read full email content
- `list_labels` - Get Gmail labels/folders
- `get_thread` - Read email thread

**Usage:**

```bash
python brain_email_query_with_mcp_skill.py --operation list_messages --params '{"maxResults": 5}'
```

### Action Operations (Approval Required)

These operations have **side effects** and REQUIRE approved plan:

- `send_email` - Send email (REQUIRES Approved plan)
- `create_draft` - Create draft (REQUIRES Approved plan)
- `reply_email` - Reply to email (REQUIRES Approved plan)
- `modify_labels` - Change labels (REQUIRES Approved plan)

**Usage:**

```bash
# 1. Create plan with send_email operation
python brain_create_plan_skill.py --task Tasks/send_email.md --objective "Send status update"

# 2. Request approval
python brain_request_approval_skill.py --plan Plans/PLAN_send_email.md

# 3. User manually moves ACTION_*.md to Approved/

# 4. Execute (dry-run first)
python brain_execute_with_mcp_skill.py --plan Plans/PLAN_send_email.md
# (preview only)

# 5. Real execution (if dry-run looks good)
python brain_execute_with_mcp_skill.py --plan Plans/PLAN_send_email.md --execute
```

---

## Fallback Mode

If Gmail MCP server is not available, the system automatically falls back to **simulation mode**:

- Query operations: Return mock data
- Action operations: Log intent but do NOT execute

**Simulation Log Entry:**

```
[2026-02-14 00:20:00 UTC] MCP SIMULATION
- Reason: Gmail MCP server not available
- Operation: send_email
- Mode: dry-run
- Status: Simulated (no real action taken)
```

---

## Best Practices

1. **Always Test with Dry-Run First**
   - Run `--dry-run` (default) before `--execute`
   - Review preview in logs

2. **Use Query Operations Freely**
   - Read-only operations are safe
   - Automatically logged for audit

3. **Gate All Actions**
   - Never bypass approval for send/draft/modify
   - Always create plan → request approval → execute

4. **Monitor Logs**
   - Check `Logs/mcp_actions.log` regularly
   - Watch for failures or unexpected behavior

5. **Rotate Tokens**
   - Delete `~/.gmail-mcp-token` monthly
   - Re-authenticate to refresh

---

## References

- **Gmail MCP Server:** https://github.com/modelcontextprotocol/servers/tree/main/src/gmail
- **Gmail API Docs:** https://developers.google.com/gmail/api
- **MCP Protocol:** https://modelcontextprotocol.io/
- **OAuth2 Setup:** https://developers.google.com/identity/protocols/oauth2

---

**Support:** See `Company_Handbook.md` or `README.md` for Silver Tier skill documentation.

**Last Updated:** 2026-02-14
