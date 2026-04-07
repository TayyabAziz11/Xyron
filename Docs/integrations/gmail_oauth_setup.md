# Gmail OAuth2 Setup Guide

**Silver Tier - Gmail Watcher**

This guide explains how to set up Gmail API OAuth2 authentication for the Gmail watcher skill (perception-only email intake).

---

## Prerequisites

- Google account with Gmail access
- Python 3.7+ installed
- Gmail API Python libraries installed (see requirements.txt)

---

## Step 1: Create Google Cloud Project

1. **Go to Google Cloud Console:**
   - Visit: https://console.cloud.google.com/
   - Sign in with your Google account

2. **Create New Project:**
   - Click "Select a project" (top-left)
   - Click "New Project"
   - Name: `Personal AI Employee` (or your preferred name)
   - Click "Create"

3. **Wait for Project Creation:**
   - Wait 10-30 seconds for project to be created
   - You'll see a notification when ready

---

## Step 2: Enable Gmail API

1. **Navigate to APIs & Services:**
   - In the Google Cloud Console, go to: "APIs & Services" → "Library"
   - Or visit: https://console.cloud.google.com/apis/library

2. **Search for Gmail API:**
   - In the search box, type "Gmail API"
   - Click on "Gmail API" in results

3. **Enable the API:**
   - Click "Enable" button
   - Wait for API to be enabled

---

## Step 3: Create OAuth2 Credentials

1. **Go to Credentials Page:**
   - Navigate to: "APIs & Services" → "Credentials"
   - Or visit: https://console.cloud.google.com/apis/credentials

2. **Create OAuth Consent Screen (First Time Only):**
   - Click "Configure Consent Screen"
   - Select **"External"** (unless you have Google Workspace)
   - Click "Create"

   **Fill in required fields:**
   - App name: `Personal AI Employee`
   - User support email: (your email)
   - Developer contact: (your email)
   - Click "Save and Continue"

   **Scopes (optional for now):**
   - Click "Save and Continue" (we'll add scopes via code)

   **Test Users (IMPORTANT):**
   - Click "Add Users"
   - Add YOUR email address (the one you'll use for testing)
   - Click "Save and Continue"

   **Summary:**
   - Review settings
   - Click "Back to Dashboard"

3. **Create OAuth2 Credentials:**
   - Go back to: "APIs & Services" → "Credentials"
   - Click "+ Create Credentials" (top)
   - Select "OAuth client ID"

   **Configure OAuth Client:**
   - Application type: **Desktop app**
   - Name: `Gmail Watcher Desktop` (or your preferred name)
   - Click "Create"

4. **Download Credentials:**
   - A dialog appears with Client ID and Client Secret
   - Click "Download JSON"
   - **IMPORTANT:** Rename the downloaded file to: `credentials.json`
   - Save it in your vault root (same directory as `gmail_watcher_skill.py`)

---

## Step 4: Install Python Dependencies

```powershell
# Install Gmail API libraries
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

Or install from `requirements.txt`:

```powershell
pip install -r requirements.txt
```

---

## Step 5: First-Time OAuth Authorization

1. **Run Gmail Watcher (Mock Mode First):**
   ```powershell
   # Test with mock data (no OAuth needed)
   python gmail_watcher_skill.py --mock --once
   ```

2. **Run with Real Gmail (OAuth Flow):**
   ```powershell
   # First run will trigger OAuth consent
   python gmail_watcher_skill.py --once
   ```

3. **OAuth Consent Flow:**
   - Browser window will open automatically
   - You'll see: "Sign in with Google"
   - **Select your Google account**
   - You'll see a warning: "Google hasn't verified this app"
   - Click "Advanced" → "Go to Personal AI Employee (unsafe)"
   - **Review permissions requested:**
     - "Read emails from Gmail" (read-only, perception only)
   - Click "Allow"
   - You'll see: "The authentication flow has completed"
   - **Close the browser window**

4. **Token Saved:**
   - The script automatically saves `token.json` in your vault root
   - Future runs will use this token (no browser popup)
   - Token auto-refreshes when expired

---

## Step 6: Verify Setup

```powershell
# Run in dry-run mode to preview
python gmail_watcher_skill.py --dry-run

# Run once to process emails
python gmail_watcher_skill.py --once

# Check logs
Get-Content Logs/gmail_watcher.log | Select-Object -Last 10
```

---

## Security Best Practices

### DO NOT Commit Secrets

**CRITICAL:** The following files contain secrets and MUST NOT be committed to git:

- ✗ `credentials.json` (OAuth2 client secret)
- ✗ `token.json` (OAuth2 access/refresh tokens)
- ✗ `Logs/gmail_checkpoint.json` (may contain email IDs)

**Verify .gitignore:**

```powershell
# Check that secrets are gitignored
Select-String -Path .gitignore -Pattern "credentials.json|token.json|gmail_checkpoint"
```

All three should be listed in `.gitignore`.

### File Locations

```
personal-ai-employee/
├── credentials.json          ← NEVER commit (in .gitignore)
├── token.json                ← NEVER commit (in .gitignore)
├── gmail_watcher_skill.py    ← Safe to commit
├── Logs/
│   ├── gmail_watcher.log     ← In .gitignore (privacy)
│   └── gmail_checkpoint.json ← In .gitignore (privacy)
└── Needs_Action/
    └── inbox__gmail__*.md    ← In .gitignore (privacy)
```

---

## Troubleshooting

### Issue: "Credentials file not found"

**Solution:**
- Ensure `credentials.json` is in vault root
- Check filename (must be exactly `credentials.json`)
- Re-download from Google Cloud Console if needed

### Issue: "Google hasn't verified this app"

**Solution:**
- This is normal for personal projects
- Click "Advanced" → "Go to [App Name] (unsafe)"
- This is safe for your own app

### Issue: "Access blocked: Authorization Error"

**Solution:**
- Ensure your email is added as a "Test User" in OAuth consent screen
- Go to: Google Cloud Console → APIs & Services → OAuth consent screen → Test users
- Add your email address

### Issue: "Token expired" or "Invalid grant"

**Solution:**
```powershell
# Delete token and re-authorize
Remove-Item token.json
python gmail_watcher_skill.py --once
# Browser will open for new authorization
```

### Issue: "ImportError: No module named google.auth"

**Solution:**
```powershell
# Install Gmail API dependencies
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

---

## Testing with Mock Mode

If you want to test the watcher without setting up OAuth2:

```powershell
# Use mock email fixture (no Gmail API needed)
python gmail_watcher_skill.py --mock --once
```

This uses fake emails from `templates/mock_emails.json` for safe testing.

---

## Privacy & Permissions

### What the Gmail Watcher CAN Do:

- ✓ Read your emails (read-only access)
- ✓ Create intake wrappers in `Needs_Action/`
- ✓ Track processed email IDs (checkpointing)
- ✓ Log watcher activity to `Logs/gmail_watcher.log`

### What the Gmail Watcher CANNOT Do:

- ✗ Send emails (perception-only, no write access)
- ✗ Delete emails
- ✗ Modify emails
- ✗ Access other Google services (only Gmail)

**CRITICAL:** This is a **perception-only** watcher. All email sending requires:
1. Approved plan (brain_create_plan)
2. HITL approval (file moved to Approved/)
3. MCP execution (brain_execute_with_mcp)

---

## Revoking Access (Optional)

To revoke access to your Gmail:

1. Go to: https://myaccount.google.com/permissions
2. Find "Personal AI Employee" (or your app name)
3. Click "Remove Access"
4. Delete `token.json` and `credentials.json` from your vault

---

## Support

If you encounter issues not covered here:

1. Check `Logs/gmail_watcher.log` for error messages
2. Run with `--mock` mode to isolate OAuth issues
3. Review Google Cloud Console quota limits
4. Ensure test user email is added in OAuth consent screen

---

**Last Updated:** 2026-02-11
**Silver Tier:** M3 - Gmail Watcher Implementation
