# Twitter/X MCP Server Setup

**Purpose**: Configure Twitter (X) API integration for perception (QUERY tools) and action (ACTION tools with approval)

**Tier**: Gold (G-M4)

---

## Prerequisites

- Twitter/X account (personal or business)
- Twitter Developer account (elevated access recommended)
- Twitter Developer App created

---

## Required Credentials

Twitter API v2 requires:
1. **API Key** (Consumer Key)
2. **API Secret** (Consumer Secret)
3. **Access Token** (OAuth 1.0a User Token)
4. **Access Token Secret**
5. **Bearer Token** (for App-only authentication, optional)

---

## Setup Methods

### Method 1: Environment Variables (Recommended for Development)

Create or update `.env` file in vault root:

```bash
# Twitter API Credentials
TWITTER_API_KEY=your_api_key_here
TWITTER_API_SECRET=your_api_secret_here
TWITTER_ACCESS_TOKEN=your_access_token_here
TWITTER_ACCESS_TOKEN_SECRET=your_access_token_secret_here
TWITTER_BEARER_TOKEN=your_bearer_token_here
```

**IMPORTANT**: NEVER commit `.env` to git. It's covered by `.gitignore`.

### Method 2: Credentials File (Recommended for Production)

Create `.secrets/twitter_credentials.json`:

```json
{
  "api_key": "your_api_key_here",
  "api_secret": "your_api_secret_here",
  "access_token": "your_access_token_here",
  "access_token_secret": "your_access_token_secret_here",
  "bearer_token": "your_bearer_token_here",
  "api_version": "2",
  "base_url": "https://api.twitter.com"
}
```

**File permissions**: `chmod 600 .secrets/twitter_credentials.json`

---

## Obtaining Credentials

### Step 1: Create Twitter Developer Account

1. Go to https://developer.twitter.com/
2. Click "Sign up" (if not already a developer)
3. Complete application:
   - Use case: "Making a bot for personal automation"
   - Will you make Twitter content available to government entities?: Usually "No"
4. Wait for approval (usually instant for basic access)

### Step 2: Create Twitter App

1. Go to https://developer.twitter.com/en/portal/dashboard
2. Click "Create Project" or "Create App"
3. Fill in app details:
   - App name: "Personal AI Employee"
   - Description: "Personal AI assistant for social media automation"
   - Website URL: Your website or `https://localhost`

### Step 3: Get API Keys

1. In your app dashboard, go to "Keys and tokens"
2. Copy **API Key** and **API Key Secret** (Consumer keys)
3. Regenerate if needed

### Step 4: Enable OAuth 1.0a and Get Access Tokens

1. In app settings, go to "User authentication settings"
2. Click "Set up"
3. Configure:
   - App permissions: "Read and write" (for posting)
   - Type of App: "Web App" or "Automated App"
   - Callback URL: `http://localhost:8000/callback`
   - Website URL: Your website

4. Save settings
5. Go back to "Keys and tokens"
6. Under "Authentication Tokens", click "Generate" for Access Token and Secret
7. Copy **Access Token** and **Access Token Secret**

### Step 5: Get Bearer Token (Optional)

1. In "Keys and tokens", copy **Bearer Token**
2. Bearer Token is for app-only auth (read-only, no user context)

---

## MCP Server Configuration

The Twitter MCP server provides these tools:

### QUERY Tools (Perception - No Approval Required)
- `twitter.search_mentions` - Search for mentions of your account
- `twitter.get_tweet` - Get specific tweet details
- `twitter.get_post_metrics` - Get tweet analytics
- `twitter.list_dms` - List direct messages

### ACTION Tools (Execution - Approval Required)
- `twitter.create_post` - Create new tweet
- `twitter.reply_post` - Reply to tweet
- `twitter.send_dm` - Send direct message
- `twitter.retweet` - Retweet a tweet
- `twitter.like_tweet` - Like a tweet

---

## Validation Steps

### Test Query Tools (Perception)

Run the Twitter watcher in MCP mode:

```bash
# Test connection with MCP query tools
python twitter_watcher_skill.py --mode mcp --once --max-results 5

# Expected output:
# ✅ Twitter watcher complete: 5 scanned, X created, Y skipped, 0 errors
```

### Test MCP Registry Discovery

```bash
# Refresh MCP tool registry
python brain_mcp_registry_refresh_skill.py --server twitter

# Check snapshot
cat Logs/mcp_tools_snapshot_twitter.json
```

---

## Troubleshooting

### Error: "Invalid credentials"
- **Cause**: Wrong API keys or tokens
- **Fix**: Regenerate keys in Twitter Developer Portal
- **Note**: Regenerating keys invalidates old ones

### Error: "403 Forbidden - Your app is not allowed to access this endpoint"
- **Cause**: App permissions insufficient or API access level too low
- **Fix**:
  1. Check app permissions (Settings > User authentication settings)
  2. Apply for Elevated access if using Basic (limited to 500,000 tweets/month)
  3. Go to Developer Portal > Products > Elevated

### Error: "429 Too Many Requests"
- **Cause**: Rate limit exceeded
- **Fix**: Wait for rate limit reset (15-minute windows)
- **Prevention**: Use `rate_limit_and_backoff` helper
- **Check limits**: Twitter Developer Portal > Dashboard > Your app > Usage

### Error: "Read-only application cannot POST"
- **Cause**: App permissions set to "Read-only"
- **Fix**: Change to "Read and write" in User authentication settings

---

## Security Best Practices

1. **Never commit credentials** to git
2. **Use OAuth 1.0a** for user-context actions (posting, DMing)
3. **Rotate keys** every 90 days
4. **Monitor API usage** in Developer Portal
5. **Enable webhook signature validation** for Account Activity API
6. **Use IP whitelisting** if available for your plan

---

## Rate Limits (Twitter API v2)

### Basic Access (Free)
- **Tweet creation**: 100 tweets per 24 hours
- **Mentions search**: 25 requests per 15 minutes
- **User tweet timeline**: 1,500 tweets per month
- **DM sending**: 15 requests per 15 minutes

### Elevated Access (Free, requires approval)
- **Tweet creation**: 2,400 tweets per 24 hours
- **Mentions search**: 180 requests per 15 minutes
- **User tweet timeline**: 2,000,000 tweets per month

### Enterprise (Paid)
- Custom rate limits

**Note**: Rate limits vary by endpoint. Check [Twitter Rate Limits](https://developer.twitter.com/en/docs/twitter-api/rate-limits) for details.

---

## References

- [Twitter API v2 Documentation](https://developer.twitter.com/en/docs/twitter-api)
- [Authentication Guide](https://developer.twitter.com/en/docs/authentication/overview)
- [Rate Limits](https://developer.twitter.com/en/docs/twitter-api/rate-limits)
- [Manage Tweets](https://developer.twitter.com/en/docs/twitter-api/tweets/manage-tweets/introduction)

---

**Last Updated**: 2026-02-15
**Version**: 1.0.0 (Gold Tier - G-M4)
