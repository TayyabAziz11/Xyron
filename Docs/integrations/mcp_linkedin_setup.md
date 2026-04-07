# LinkedIn MCP Server Setup

**Purpose**: Configure LinkedIn API integration for perception (QUERY tools) and action (ACTION tools with approval)

**Tier**: Gold (G-M4)

---

## Prerequisites

- LinkedIn account (personal or company page)
- LinkedIn Developer App created
- API access approved by LinkedIn

---

## Required Credentials

LinkedIn API requires:
1. **Client ID** (from LinkedIn Developer App)
2. **Client Secret** (from LinkedIn Developer App)
3. **Access Token** (OAuth 2.0, refreshable)
4. **Refresh Token** (for token renewal)
5. **Member ID / Organization ID** (your profile or company page)

---

## Setup Methods

### Method 1: Environment Variables (Recommended for Development)

Create or update `.env` file in vault root:

```bash
# LinkedIn API Credentials
LINKEDIN_CLIENT_ID=your_client_id_here
LINKEDIN_CLIENT_SECRET=your_client_secret_here
LINKEDIN_ACCESS_TOKEN=your_access_token_here
LINKEDIN_REFRESH_TOKEN=your_refresh_token_here
LINKEDIN_MEMBER_ID=your_member_id_here
```

**IMPORTANT**: NEVER commit `.env` to git. It's covered by `.gitignore`.

### Method 2: Credentials File (Recommended for Production)

Create `.secrets/linkedin_credentials.json`:

```json
{
  "client_id": "your_client_id_here",
  "client_secret": "your_client_secret_here",
  "redirect_uri": "http://localhost:8080/callback",
  "scopes": ["openid", "profile", "email", "w_member_social"]
}
```

**Note**: The `scopes` field is optional. If omitted, defaults to OpenID Connect scopes above.

**File permissions**: `chmod 600 .secrets/linkedin_credentials.json`

---

## Obtaining Credentials

### Step 1: Create LinkedIn Developer App

1. Go to https://www.linkedin.com/developers/apps
2. Click "Create app"
3. Fill in app details:
   - App name: "Personal AI Employee"
   - LinkedIn Page: Select your company page or personal profile
   - App logo: Optional
4. Click "Create app"

### Step 2: Configure App Products

1. In app settings, go to "Products"
2. Request access to:
   - **Sign In with LinkedIn using OpenID Connect** (required for profile/email scopes)
   - **Share on LinkedIn** (required for w_member_social scope - posting)
   - **Marketing Developer Platform** (optional, for analytics)

3. Wait for approval (usually instant for Sign In, may take 1-2 days for Share on LinkedIn)

**IMPORTANT**: If you see `invalid_scope` error during OAuth flow:
- This usually means required Products are not enabled
- Enable "Share on LinkedIn" product for `w_member_social` scope
- Enable "Sign In with LinkedIn using OpenID Connect" for `profile` and `email` scopes
- Products must be approved before OAuth scopes can be used

### Step 3: Get Client ID and Secret

1. In app settings, go to "Auth"
2. Copy **Client ID** and **Client Secret**
3. Add redirect URL: `http://localhost:8000/callback` (for OAuth flow)

### Step 4: Generate Access Token

**Option A: Use OAuth Helper Script (Recommended - Automated)**

We provide an OAuth helper that automates the entire flow with browser auto-open and local server:

```bash
# Initialize OAuth (browser opens automatically, token saved to .secrets/)
python3 scripts/linkedin_oauth_helper.py --init

# Check authentication status
python3 scripts/linkedin_oauth_helper.py --status

# Quick authentication check
python3 scripts/linkedin_oauth_helper.py --check-auth
```

**What it does:**
1. Starts local HTTP server on port 8080
2. Opens browser automatically to LinkedIn authorization page
3. Captures OAuth callback automatically (no manual copy/paste)
4. Exchanges code for access token
5. Verifies token with OIDC userinfo endpoint
6. Saves token to `.secrets/linkedin_token.json`

**WSL Users**: If browser doesn't open automatically, copy the displayed URL and paste into Windows browser.

**Option B: Manual OAuth 2.0 Authorization Code Flow**

1. Build authorization URL (using OpenID Connect scopes):
   ```
   https://www.linkedin.com/oauth/v2/authorization?
   response_type=code&
   client_id=YOUR_CLIENT_ID&
   redirect_uri=http://localhost:8080/callback&
   scope=openid%20profile%20email%20w_member_social
   ```

2. Visit URL in browser, authorize app
3. Copy authorization code from redirect URL
4. Exchange code for access token:
   ```bash
   curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
     -d grant_type=authorization_code \
     -d code=YOUR_AUTH_CODE \
     -d redirect_uri=http://localhost:8080/callback \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET
   ```

5. Response includes `access_token` and `expires_in` (60 days)

### Step 5: Get Member ID

```bash
# Use access token to get profile
curl -X GET 'https://api.linkedin.com/v2/me' \
  -H 'Authorization: Bearer YOUR_ACCESS_TOKEN'

# Extract "id" from response
```

---

## Authentication Methods: OIDC vs Legacy

LinkedIn supports two authentication methods:

### OpenID Connect (OIDC) - Recommended for New Apps

**Scopes**: `openid`, `profile`, `email`, `w_member_social`

**Endpoints**:
- Authorization: `https://www.linkedin.com/oauth/v2/authorization`
- Token: `https://www.linkedin.com/oauth/v2/accessToken`
- Userinfo: `https://api.linkedin.com/v2/userinfo` (OIDC standard endpoint)

**Benefits**:
- Standard OpenID Connect protocol
- Simpler profile data format
- Better for new LinkedIn apps
- Required "Sign In with LinkedIn using OpenID Connect" product

**Profile response format**:
```json
{
  "sub": "user-id",
  "name": "John Doe",
  "given_name": "John",
  "family_name": "Doe",
  "email": "john@example.com",
  "picture": "https://..."
}
```

### Legacy API - For Existing Apps

**Scopes**: `r_liteprofile`, `r_emailaddress`, `w_member_social`

**Endpoints**:
- Profile: `https://api.linkedin.com/v2/me`

**When to use**:
- Existing apps with legacy scope approvals
- Apps that need legacy profile format

**Profile response format**:
```json
{
  "id": "user-id",
  "localizedFirstName": "John",
  "localizedLastName": "Doe"
}
```

**Note**: Our implementation supports both methods automatically. The `check_auth()` function tries OIDC first, falls back to legacy if needed.

---

## Required Headers: LinkedIn-Version + X-Restli-Protocol-Version

Every request to `api.linkedin.com` must include **two mandatory headers** in addition to
`Authorization`:

| Header | Required Value | Notes |
|---|---|---|
| `LinkedIn-Version` | `YYYYMM` (e.g. `202502`) | Calendar-based API version. **Missing this causes `403 me.GET.NO_VERSION`** |
| `X-Restli-Protocol-Version` | `2.0.0` | Rest.li wire protocol version |
| `Authorization` | `Bearer <token>` | OAuth2 access token |

### Why `me.GET.NO_VERSION` Happens

If `LinkedIn-Version` is absent, LinkedIn's API gateway returns:

```json
{
  "status": 403,
  "code": "ACCESS_DENIED",
  "message": "Not enough permissions to access: me.GET.NO_VERSION"
}
```

This is **not** a permissions error — it's a missing header error. Adding
`LinkedIn-Version: YYYYMM` resolves it immediately.

### Where the Version is Set

Our implementation centralises all header construction in `_build_headers()` inside
`LinkedInAPIHelper`. The version defaults to `DEFAULT_LINKEDIN_VERSION = "202502"` and
can be overridden per-deployment by adding `"linkedin_version": "202502"` to
`.secrets/linkedin_credentials.json`:

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "redirect_uri": "http://localhost:8080/callback",
  "scopes": ["openid", "profile", "email", "w_member_social"],
  "linkedin_version": "202502"
}
```

Every request — `/v2/me`, `/v2/ugcPosts`, `/v2/shares`, `/v2/userinfo` — goes through
`_build_headers()`, so the headers are guaranteed to be present on all calls.

---

## Person URN Requirement for Posting Endpoints

### Why OIDC Userinfo Is Not Enough

LinkedIn's posting and content-query APIs (e.g., `/v2/ugcPosts`) require an **author URN** of the form `urn:li:person:<member_id>`.

The OIDC `/v2/userinfo` endpoint returns a `sub` field that is an **opaque subject identifier** — it is suitable for authentication but is **not reliably usable** as the member ID needed in UGC endpoint queries:

| Source | Field | Format | Usable for UGC? |
|---|---|---|---|
| `/v2/userinfo` (OIDC) | `sub` | opaque string | sometimes, but unreliable |
| `/v2/me` (Legacy) | `id` | numeric member id | ✅ yes |

### Resolution Strategy (`get_person_urn()`)

Our implementation resolves the person URN **strictly** via `/v2/me`:

1. **Check in-memory + disk cache** (only if previously resolved via `/v2/me`)
2. **Call `GET /v2/me`** — extracts numeric `id` → `urn:li:person:<id>`
3. **Raise `LinkedInAuthError`** if `/v2/me` returns 401/403 — OIDC `sub` is NOT used
   as a fallback for API endpoints (it is unreliable for sharing/UGC queries)

> **Why not OIDC sub?** LinkedIn's UGC and shares endpoints require the numeric member
> ID from `/v2/me`.  The OIDC `sub` is an opaque identifier — it may be the same as the
> member ID on some apps but is not guaranteed and has caused 404/403 errors in practice.

### Verification Checklist

After enabling LinkedIn products and re-running OAuth, follow this checklist:

```
□ Step 1 — Enable Products in LinkedIn Developer Console
  Go to: https://www.linkedin.com/developers/apps → Your App → Products
  ✔ Sign In with LinkedIn using OpenID Connect   (for profile scope + /v2/me)
  ✔ Share on LinkedIn                            (for w_member_social + /v2/shares + /v2/ugcPosts)

□ Step 2 — Re-run OAuth to get a fresh token with the enabled scopes
  python3 scripts/linkedin_oauth_helper.py --init
  (Existing tokens may have been issued before products were enabled)

□ Step 3 — Verify /v2/me returns a numeric member id
  python3 scripts/linkedin_oauth_helper.py --whoami
  Expected:
    person_urn : urn:li:person:<numeric_id>    ← must be numeric
    method     : v2_me                         ← must be v2_me (not oidc_sub)

□ Step 4 — Test all three API endpoints
  python3 scripts/linkedin_oauth_helper.py --test-endpoints
  Expected (after products enabled + fresh OAuth):
    ✅ /v2/me          — id=<numeric_id>
    ✅ /v2/shares      — 0 or more elements
    ✅ /v2/ugcPosts    — 0 or more elements

□ Step 5 — Run the real-mode watcher
  python3 scripts/linkedin_watcher_skill.py --mode real --once --max-results 5
  Expected: scanned > 0 OR empty + 0 errors (no posts yet is fine)
```

### Example `--whoami` Output (All Products Enabled)

```bash
python3 scripts/linkedin_oauth_helper.py --whoami
```

```
LinkedIn Identity & URN Resolution
📋 OIDC Userinfo (OIDC):
   sub     : 123456789
   name    : Jane Doe
   email   : jane@example.com

🔗 Person URN Resolution:
   person_urn : urn:li:person:123456789
   method     : v2_me              ← confirms /v2/me succeeded

🔐 Scopes / Products Detected:
   Configured scopes: ['openid', 'profile', 'email', 'w_member_social']
```

### Example `--test-endpoints` Output

```bash
python3 scripts/linkedin_oauth_helper.py --test-endpoints
```

```
======================================================================
LinkedIn API Endpoint Diagnostics (--test-endpoints)
======================================================================

[1/3] GET /v2/me
  ✅ /v2/me: id=123456789  →  URN=urn:li:person:123456789

[2/3] GET /v2/shares?q=owners&owners=<urn>&count=1
  ✅ /v2/shares: 0 element(s) returned

[3/3] GET /v2/ugcPosts?q=author&author=<urn>&count=1
  ✅ /v2/ugcPosts: 0 element(s) returned
```

### If `/v2/me` Returns 403

The token was issued before the **Sign In with LinkedIn using OpenID Connect** product was
enabled and lacks the required `profile` scope.

**Fix (in order):**
1. Go to https://www.linkedin.com/developers/apps → Your App → Products
2. Ensure **Sign In with LinkedIn using OpenID Connect** is approved
3. Re-run OAuth to get a fresh token:
   ```bash
   python3 scripts/linkedin_oauth_helper.py --init
   ```
4. Verify:
   ```bash
   python3 scripts/linkedin_oauth_helper.py --test-endpoints
   ```

---

## OIDC Apps: /v2/me May Be Blocked — This Is Expected

### Background

New LinkedIn apps using **Sign In with LinkedIn using OpenID Connect** authenticate via
`/v2/userinfo` (OIDC standard).  The legacy `/v2/me` endpoint was historically gated behind
the `r_liteprofile` scope, which is only available through the **Sign In with LinkedIn** legacy
product — a different product from the OIDC one.

**Bottom line**: If your app only has the OIDC product enabled, `/v2/me` may return 403.
This is **not a bug** — it is a LinkedIn product boundary.

### What Still Works Without `/v2/me`

| Capability | Status | How |
|---|---|---|
| Authentication | ✅ Works | OIDC `/v2/userinfo` → `sub` |
| Identify who I am | ✅ Works | `get_author_urn()` falls back to `urn:li:person:<sub>` |
| Post content | ✅ Works | `POST rest/posts` with `urn:li:person:<sub>` as author |
| Read my posts | ❌ Not available | Requires `r_member_social` (partner program only) |

### Author URN Resolution (`get_author_urn()`)

Our implementation uses a 4-step fallback chain to resolve the author URN:

1. **In-memory cache** — fastest, reuses previous result
2. **Disk cache** (`.secrets/linkedin_profile.json`) — persists across runs
3. **Live `GET /v2/me`** — numeric member ID when available
4. **OIDC `sub` fallback** — `urn:li:person:<sub>` when `/v2/me` is blocked

The `sub` from OIDC userinfo is accepted by LinkedIn's REST Posts API (`POST rest/posts`)
as a valid author URN for new OIDC apps.

### Verify With Exact Commands (WSL)

Run these in order after OAuth:

```bash
# 1. Confirm token + OIDC identity
python3 scripts/linkedin_oauth_helper.py --status

# 2. Show capabilities (no network to ugcPosts yet)
python3 scripts/linkedin_oauth_helper.py --capabilities
# Expected (OIDC app):
# Authenticated:  YES
# Can Post:       YES
# Can Read Posts: NO (r_member_social not granted — expected)

# 3. 5-step diagnostic (dry-run — no post sent)
python3 scripts/linkedin_oauth_helper.py --test-endpoints
# Expected:
# [1/5] OIDC /v2/userinfo   → ✅ OK — sub=<sub>, name=<name>
# [2/5] Author URN          → ✅ URN=urn:li:person:<sub> (method=oidc_sub)
# [3/5] Post validation     → ✅ w_member_social scope confirmed, author URN ready
# [4/5] POST rest/posts     → skipped (dry-run)
# [5/5] Read endpoints      → ⚠️  Read unavailable (expected — r_member_social)

# 4. Validate post pipeline (dry-run — no post sent)
python3 scripts/linkedin_oauth_helper.py --post-test "Hello from my AI Employee"

# 5. Send a real test post (optional — actually posts to LinkedIn)
python3 scripts/linkedin_oauth_helper.py --post-test "Hello from my AI Employee" --execute

# 6. Watcher real mode (exits cleanly if read is blocked; remediation file created once)
python3 scripts/linkedin_watcher_skill.py --mode real --once
```

### Watcher Graceful Degradation

When the watcher runs in real mode and `r_member_social` is not granted:

- First run: logs warning + creates `Needs_Action/remediation__linkedin_read_permission__YYYYMMDD-HHMM.md`
- Subsequent runs: logs info ("already created") + exits cleanly — **no duplicate files**
- The `read_blocked_since` field in `Logs/linkedin_watcher_checkpoint.json` tracks when the
  block was first detected, preventing remediation file spam.

### Expected `--capabilities` Output (OIDC App)

```
----------------------------------------
LinkedIn Capabilities
----------------------------------------
Authenticated: YES
Can Post:      YES
Can Read Posts: NO (r_member_social not granted (LinkedIn requires special access approval))
----------------------------------------
```

**"Can Post: YES"** confirms the integration is working correctly even without `/v2/me`.

---

## MCP Server Configuration

The LinkedIn MCP server provides these tools:

### QUERY Tools (Perception - No Approval Required)
- `linkedin.list_notifications` - List recent notifications
- `linkedin.get_post` - Get specific post details
- `linkedin.get_post_analytics` - Get post performance metrics
- `linkedin.search_posts` - Search for posts

### ACTION Tools (Execution - Approval Required)
- `linkedin.create_post` - Create new post
- `linkedin.reply_comment` - Reply to comment
- `linkedin.send_message` - Send direct message
- `linkedin.react_to_post` - React (like, celebrate, etc.)

---

## Validation Steps

### Test Query Tools (Perception)

Run the LinkedIn watcher in MCP mode:

```bash
# Test connection with MCP query tools
python linkedin_watcher_skill.py --mode mcp --once --max-results 5

# Expected output:
# ✅ LinkedIn watcher complete: 5 scanned, X created, Y skipped, 0 errors
```

### Test MCP Registry Discovery

```bash
# Refresh MCP tool registry
python brain_mcp_registry_refresh_skill.py --server linkedin

# Check snapshot
cat Logs/mcp_tools_snapshot_linkedin.json
```

---

## Troubleshooting

### Error: "Invalid access token"
- **Cause**: Token expired (LinkedIn access tokens last 60 days)
- **Fix**: Use refresh token to get new access token
- **Refresh command**:
  ```bash
  curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
    -d grant_type=refresh_token \
    -d refresh_token=YOUR_REFRESH_TOKEN \
    -d client_id=YOUR_CLIENT_ID \
    -d client_secret=YOUR_CLIENT_SECRET
  ```

### Error: "invalid_scope" or "Insufficient permissions"
- **Cause**: App doesn't have required Products enabled or using legacy scopes on new app
- **Fix**:
  1. Go to LinkedIn Developer Console → Your App → Products
  2. Enable "Sign In with LinkedIn using OpenID Connect" (for profile/email scopes)
  3. Enable "Share on LinkedIn" (for w_member_social scope)
  4. Wait for approval (instant for OIDC, 1-2 days for Share)
  5. Use OpenID Connect scopes: `openid`, `profile`, `email`, `w_member_social`
- **Legacy scopes** (deprecated for new apps): `r_liteprofile`, `r_emailaddress`

### Error: "Rate limit exceeded"
- **Cause**: Too many API calls
- **Fix**: Wait for rate limit reset (usually 15 minutes)
- **Prevention**: Use `rate_limit_and_backoff` helper

### Error: "Member not found"
- **Cause**: Incorrect member ID
- **Fix**: Re-fetch member ID using `/v2/me` endpoint

---

## Security Best Practices

1. **Never commit credentials** to git
2. **Rotate tokens** every 30 days
3. **Use HTTPS** for all OAuth redirects
4. **Validate redirect URIs** in LinkedIn app settings
5. **Enable webhook signature validation** for incoming events
6. **Monitor API usage** in LinkedIn Developer Console

---

## Rate Limits (LinkedIn API)

- **General API calls**: 100 requests per day (default)
- **Posting**: 150 posts per day per member
- **Messaging**: 100 messages per day
- **Analytics**: 1,000 requests per day

**Note**: Rate limits vary by LinkedIn plan and app approval status.

---

## References

- [LinkedIn API Documentation](https://learn.microsoft.com/en-us/linkedin/)
- [OAuth 2.0 Authorization](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authentication)
- [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin)

---

**Last Updated**: 2026-02-17
**Version**: 1.1.0 (Gold Tier - G-M4: OpenID Connect OAuth)
