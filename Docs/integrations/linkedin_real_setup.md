# LinkedIn Real Mode Setup Guide

**Feature**: LinkedIn OAuth2 Real Mode Integration
**Tier**: Gold
**Status**: Production Ready
**Last Updated**: 2026-02-17

---

## Overview

This guide explains how to configure LinkedIn OAuth2 authentication for real-mode LinkedIn integration in the Personal AI Employee system. Real mode enables:

- **Perception**: Fetch your actual LinkedIn posts via watcher (read-only)
- **Execution**: Create posts, reply to comments, send messages via approved plans (write operations)

## Prerequisites

1. **LinkedIn Developer Account**: https://www.linkedin.com/developers/
2. **Python 3.10+** with dependencies installed
3. **Git repository** with Personal AI Employee code (Gold Tier)
4. **.secrets/** directory (gitignored, not committed)

---

## Step 1: Create LinkedIn App

### 1.1 Register Application

1. Go to https://www.linkedin.com/developers/apps
2. Click **"Create app"**
3. Fill in required fields:
   - **App name**: "Personal AI Employee" (or your choice)
   - **LinkedIn Page**: Select your company/personal page
   - **Privacy policy URL**: (provide if required)
   - **App logo**: (optional)
4. Check **"I have read and agree to these terms"**
5. Click **"Create app"**

### 1.2 Configure OAuth 2.0 Settings

1. In your app dashboard, go to **"Auth"** tab
2. Under **"OAuth 2.0 settings"**, add:
   - **Authorized redirect URLs**:
     ```
     http://localhost:8080/callback
     ```
   - **IMPORTANT**: Match this exactly in your credentials file
3. Note your **Client ID** and **Client Secret** (click "Show" to reveal)

### 1.3 Request API Access

1. Go to **"Products"** tab
2. Request access to:
   - **Sign In with LinkedIn** (for authentication)
   - **Share on LinkedIn** (for creating posts)
   - **Messaging API** (optional, for DMs - may require additional approval)
3. Wait for approval (usually instant for Sign In + Share)

---

## Step 2: Configure Credentials

### 2.1 Create Credentials File

Create `.secrets/linkedin_credentials.json`:

```json
{
  "client_id": "YOUR_CLIENT_ID_HERE",
  "client_secret": "YOUR_CLIENT_SECRET_HERE",
  "redirect_uri": "http://localhost:8080/callback"
}
```

**Replace**:
- `YOUR_CLIENT_ID_HERE`: From LinkedIn app Auth tab
- `YOUR_CLIENT_SECRET_HERE`: From LinkedIn app Auth tab (click "Show")

**Security**:
- ✅ File is in `.secrets/` (automatically gitignored)
- ✅ Never commit this file to git
- ✅ File permissions will be set to 600 (owner read/write only) automatically

### 2.2 Verify File Structure

```bash
cd /mnt/e/Certified\ Cloud\ Native\ Generative\ and\ Agentic\ AI\ Engineer/Q4\ part\ 2/Q4\ part\ 2/Hackathon-0/Personal\ AI\ Employee

# Check credentials file exists and is valid JSON
python3 -c "import json; print('Valid JSON' if json.load(open('.secrets/linkedin_credentials.json')) else 'Invalid')"
```

Expected output: `Valid JSON`

---

## Step 3: Obtain Access Token (OAuth2 Flow)

### 3.1 Run Interactive OAuth Flow

```bash
# Run the LinkedIn API helper in interactive mode
python3 src/personal_ai_employee/core/linkedin_api_helper.py
```

### 3.2 Authorize Application

1. Script will output:
   ```
   🔐 LinkedIn OAuth2 Authorization

   Please visit this URL to authorize the application:
   https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=...

   After authorizing, paste the full callback URL here:
   ```

2. **Copy the URL** and open in your browser
3. **Log in to LinkedIn** (if not already logged in)
4. **Authorize the application** by clicking "Allow"
5. LinkedIn will redirect to `http://localhost:8080/callback?code=...&state=...`
   - Browser will show "Unable to connect" (expected - no server running)
   - **Copy the entire URL from browser address bar**

6. **Paste the callback URL** into the terminal prompt

### 3.3 Token Exchange

Script will:
1. Extract authorization code from callback URL
2. Exchange code for access token
3. Save token to `.secrets/linkedin_token.json`
4. Set file permissions to 600 (secure)
5. Verify authentication by calling `/me` endpoint

Expected output:
```
✅ Token saved successfully
✅ Authentication verified: John Doe (john.doe@example.com)
```

### 3.4 Verify Token File

```bash
# Check token file exists
ls -la .secrets/linkedin_token.json

# Expected: -rw------- 1 user group ... linkedin_token.json
```

**Token format** (.secrets/linkedin_token.json):
```json
{
  "access_token": "AQV...",
  "expires_at": "2026-02-18T12:00:00Z",
  "refresh_token": "optional_if_provided"
}
```

---

## Step 4: Test Real Mode

### 4.1 Test LinkedIn Watcher (Perception)

```bash
# Dry-run: preview what would be fetched
python3 scripts/linkedin_watcher_skill.py --mode real --dry-run

# Real run: fetch actual posts and create intake wrappers
python3 scripts/linkedin_watcher_skill.py --mode real --once --max-results 5
```

**Expected behavior**:
- ✅ Authenticates with LinkedIn API
- ✅ Fetches your recent UGC posts
- ✅ Creates intake wrappers in `Social/Inbox/`
- ✅ PII redacted in excerpts
- ✅ Checkpoints IDs to avoid duplicates

**Verify output**:
```bash
# Check intake wrappers created
ls -l Social/Inbox/inbox__linkedin__*.md

# Check logs
tail -20 Logs/linkedin_watcher.log
```

### 4.2 Test Social Executor (Execution) - Dry Run

```bash
# Create a test plan in Approved/ directory
# (Use brain_create_plan or manually create plan file)

# Dry-run: preview LinkedIn action without executing
python3 scripts/brain_execute_social_with_mcp_skill.py --dry-run
```

**Expected output**:
```
📋 Action Preview:
   Platform: Linkedin
   Operation: create_post
   Content: [your post text, PII redacted]
   Length: 150 chars

✅ Dry-run successful
```

### 4.3 Test Real Execution (OPTIONAL - Only with Approval)

**WARNING**: This will create a REAL LinkedIn post on your profile.

```bash
# Ensure plan is approved and in Approved/ directory
# Execute with --execute flag
python3 scripts/brain_execute_social_with_mcp_skill.py --execute

# Expected output:
# 🚀 Executing Action:
#    Platform: Linkedin
#    Operation: create_post
#    ✅ Post created successfully
#    Post ID: urn:li:ugcPost:123456...
```

**Verify**:
1. Check your LinkedIn profile for the new post
2. Review `Logs/mcp_actions.log` for action record (JSON)
3. Check `system_log.md` for system entry

---

## Step 5: Scheduling (Automation)

### 5.1 Update Scheduled Task

Update `.scheduled/linkedin_watcher.xml` to use real mode:

```xml
<Task>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT15M</Interval> <!-- Every 15 minutes -->
      </Repetition>
    </CalendarTrigger>
  </Triggers>
  <Actions>
    <Exec>
      <Command>python3</Command>
      <Arguments>scripts/linkedin_watcher_skill.py --mode real --once --max-results 10</Arguments>
      <WorkingDirectory>E:\...\Personal AI Employee</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

**Note**: Change `--mode mock` to `--mode real` when ready for production.

---

## Troubleshooting

### Issue: "Authentication failed"

**Symptoms**:
- Error: `LinkedIn authentication failed: 401 Unauthorized`
- Watcher/executor cannot connect to API

**Solutions**:
1. **Token expired**: Re-run OAuth flow (Step 3.1)
2. **Invalid credentials**: Verify client_id/client_secret in `.secrets/linkedin_credentials.json`
3. **Network issues**: Check internet connection

**Quick fix**:
```bash
# Re-authenticate
python3 src/personal_ai_employee/core/linkedin_api_helper.py

# Then retry watcher/executor
```

---

### Issue: "Insufficient permissions"

**Symptoms**:
- Error: `403 Forbidden` or `Missing required scope`
- Cannot create posts or send messages

**Solutions**:
1. **Check app products**: Ensure "Share on LinkedIn" is approved (Step 1.3)
2. **Re-authorize**: May need to re-run OAuth flow with updated scopes
3. **Token scopes**: Current scopes requested:
   - `r_liteprofile` (read profile)
   - `r_emailaddress` (read email)
   - `w_member_social` (write posts)

**Add scopes** (if needed):
- Edit `src/personal_ai_employee/core/linkedin_api_helper.py`:
  ```python
  DEFAULT_SCOPE = "r_liteprofile r_emailaddress w_member_social w_organization_social"
  ```
- Re-run OAuth flow

---

### Issue: "Rate limit exceeded"

**Symptoms**:
- Error: `429 Too Many Requests`
- API calls blocked temporarily

**Solutions**:
1. **Wait**: LinkedIn enforces rate limits (varies by endpoint)
2. **Reduce frequency**: Increase watcher interval (e.g., 30 min instead of 15 min)
3. **Automatic retry**: Helper already implements exponential backoff

**Best practices**:
- Don't poll API more than once every 10 minutes
- Batch operations when possible
- Monitor `Logs/linkedin_watcher.log` for rate limit warnings

---

### Issue: "Token refresh failed"

**Symptoms**:
- Error: `Token expired and refresh failed`
- Watcher stops working after 60 days

**Solution**:
1. **LinkedIn limitation**: Refresh tokens may not be provided by default
2. **Manual re-auth required**: Re-run OAuth flow every 60 days
3. **Automation** (future): Set up cron job to notify when token expires

**Check expiry**:
```bash
# View token expiry date
python3 -c "import json; print(json.load(open('.secrets/linkedin_token.json'))['expires_at'])"
```

---

### Issue: "File permissions error"

**Symptoms**:
- Error: `Permission denied: .secrets/linkedin_token.json`

**Solution**:
```bash
# Fix file permissions
chmod 600 .secrets/linkedin_credentials.json
chmod 600 .secrets/linkedin_token.json

# Verify
ls -la .secrets/linkedin_*.json
# Expected: -rw------- (owner read/write only)
```

---

### Issue: "No posts fetched"

**Symptoms**:
- Watcher runs successfully but creates no intake wrappers
- Log shows "Fetched 0 LinkedIn posts from API"

**Causes**:
1. **No recent posts**: You haven't posted on LinkedIn recently
2. **Privacy settings**: Posts may be private/hidden
3. **API permissions**: May not have access to UGC posts

**Solutions**:
1. **Create a test post** on LinkedIn manually
2. **Verify author URN**: Check LinkedIn profile ID
3. **Review API response**: Add `--verbose` flag to see raw API data

---

## Security Best Practices

### ✅ DO:
- Store credentials in `.secrets/` (gitignored)
- Set file permissions to 600 (owner-only)
- Rotate tokens every 60 days
- Use dry-run mode for testing
- Require approvals for all write operations
- Redact PII in logs and excerpts
- Monitor `Logs/mcp_actions.log` for suspicious activity

### ❌ DON'T:
- Commit credentials/tokens to git
- Share `.secrets/` files publicly
- Bypass approval gates (always use HITL)
- Execute without testing in dry-run first
- Store tokens in environment variables (use files)
- Disable PII redaction
- Auto-approve all plans (risk of spam/errors)

---

## API Scopes Reference

| Scope | Permission | Required For |
|-------|------------|--------------|
| `r_liteprofile` | Read profile (name, ID) | Authentication, author URN |
| `r_emailaddress` | Read email address | Authentication verification |
| `w_member_social` | Write posts as user | create_post (LinkedIn UGC) |
| `r_organization_social` | Read org content | Fetch company posts (optional) |
| `w_organization_social` | Write org posts | Post as company (optional) |

**Current implementation uses**: `r_liteprofile r_emailaddress w_member_social`

---

## LinkedIn API Endpoints Used

| Endpoint | Method | Purpose | Tool |
|----------|--------|---------|------|
| `/v2/me` | GET | Verify authentication | check_auth() |
| `/v2/ugcPosts?q=authors` | GET | Fetch user posts | list_ugc_posts() |
| `/v2/ugcPosts` | POST | Create new post | create_post() |

**API Base URL**: `https://api.linkedin.com/v2`

---

## Next Steps

After completing LinkedIn real mode setup:

1. **Test end-to-end workflow**:
   - Watcher creates intake → Plan created → Approval requested → Executor posts

2. **Enable scheduling**:
   - Update `.scheduled/linkedin_watcher.xml` to use `--mode real`
   - Register scheduled task (see Docs/scheduling_setup.md)

3. **Monitor logs**:
   - `Logs/linkedin_watcher.log` (watcher activity)
   - `Logs/mcp_actions.log` (execution actions, JSON)
   - `system_log.md` (system-wide events)

4. **Update Dashboard**:
   - Dashboard.md will auto-update with "LinkedIn Real Mode: ✅ Connected"

5. **Add other platforms** (optional):
   - WhatsApp real mode (separate setup)
   - Twitter/X real mode (separate setup)

---

## Support

**Documentation**:
- Architecture: `Docs/architecture_gold.md`
- Demo Script: `Docs/gold_demo_script.md`
- Lessons Learned: `Docs/lessons_learned_gold.md`

**Logs**:
- Watcher: `Logs/linkedin_watcher.log`
- Actions: `Logs/mcp_actions.log`
- System: `system_log.md`

**Code**:
- OAuth Helper: `src/personal_ai_employee/core/linkedin_api_helper.py`
- Watcher: `src/personal_ai_employee/skills/gold/linkedin_watcher_skill.py`
- Executor: `src/personal_ai_employee/skills/gold/brain_execute_social_with_mcp_skill.py`

---

**Document Version**: 1.0
**Author**: Personal AI Employee (Claude Sonnet 4.5)
**Last Updated**: 2026-02-17
