# Instagram Integration Setup Guide

**Tier**: Gold — G-M3 (Watcher) + G-M4 (Executor)
**Skill IDs**: G-M3-T06, G-M4-T06
**Mode default**: `mock` (safe for demo / CI — no API calls)

---

## Overview

The Instagram integration follows the standard Perception → Plan → Approval → Action loop:

1. **Watcher** (`instagram_watcher_skill.py`) — polls the Graph API every 10 min, creates
   intake wrappers in `Social/Inbox/` for new media and comments.  Perception-only: never posts.
2. **Executor** (`brain_execute_instagram_with_mcp_skill.py`) — reads an approved plan from
   `Approved/`, publishes the post via Graph API, logs to `Logs/mcp_actions.log`.

---

## Prerequisites

- A **Meta Developer App** with `instagram_basic`, `instagram_content_publish`, `pages_show_list`
  permissions (and `pages_read_engagement` for reading comments).
- An **Instagram Business or Creator** account linked to a Facebook Page.
- A **long-lived User Access Token** (valid 60 days; refresh before expiry).

---

## 1. Credentials File

Create `.secrets/instagram_credentials.json`:

```json
{
  "access_token": "EAAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "ig_user_id": "17841400000000000"
}
```

> **Never commit this file.**  It is listed in `.gitignore`.

### Getting a long-lived token

1. Generate a short-lived token at [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer)
2. Exchange for long-lived (60 days):

```bash
curl "https://graph.facebook.com/v20.0/oauth/access_token?\
  grant_type=fb_exchange_token\
  &client_id=YOUR_APP_ID\
  &client_secret=YOUR_APP_SECRET\
  &fb_exchange_token=SHORT_LIVED_TOKEN"
```

3. Get your Instagram Business Account ID:

```bash
python3 scripts/instagram_oauth_helper.py --whoami
```

4. Paste the `id` value as `ig_user_id` in the credentials file.

---

## 2. Verify Setup

```bash
# Check token is valid and ig_user_id resolves
python3 scripts/instagram_oauth_helper.py --status

# See which API endpoints are reachable
python3 scripts/instagram_oauth_helper.py --test-endpoints

# List your recent media
python3 scripts/instagram_oauth_helper.py --list-media
```

---

## 3. Run the Watcher

### Mock mode (no API calls — safe for dev/CI)

```bash
python3 scripts/instagram_watcher_skill.py --mode mock --once
```

### Real mode (requires valid credentials)

```bash
python3 scripts/instagram_watcher_skill.py --mode real --once
```

Intake wrappers appear in `Social/Inbox/` as:

```
inbox__instagram__media_post__20260222120000__mock_med.md
inbox__instagram__comment__20260222120001__mock_cmt0.md
```

### Continuous mode (managed by PM2 via `instagram-watcher` process)

```bash
pm2 start ecosystem.config.js --only instagram-watcher
pm2 logs instagram-watcher
```

---

## 4. Create and Execute a Post

### Step 1 — Create an approved plan

Place a file in `Approved/` with an `## Actions JSON` block:

```markdown
---
title: Instagram Post — AI launch
channel: instagram
operation: create_post_image
---

## Actions JSON

```json
[
  {
    "server": "instagram",
    "operation": "create_post_image",
    "parameters": {
      "caption": "Exciting news! Our AI assistant just went live. 🚀 #AI #Innovation",
      "image_url": "https://yourcdn.com/launch-image.jpg"
    }
  }
]
```

### Step 2 — Dry-run (always do this first)

```bash
python3 scripts/brain_execute_instagram_with_mcp_skill.py
# Output: 🔍 DRY-RUN MODE — shows what would be posted
```

### Step 3 — Execute (real API call)

```bash
python3 scripts/brain_execute_instagram_with_mcp_skill.py --execute
```

The post ID is logged to `Logs/mcp_actions.log` and `Dashboard.md` is updated.

---

## 5. Limitations & Fail-Safes

| Limitation | Behaviour |
|---|---|
| Text-only posts | Not supported by Graph API. Executor returns a clear error; use `create_post_image` instead. |
| Rate limits (429) | `InstagramAPIHelper._request()` retries with exponential back-off (up to 3 attempts) |
| Expired token | `InstagramAuthError` raised; remediation task created in `Needs_Action/` |
| Missing permissions | `InstagramPermissionError` raised; remediation task created |
| Caption > 2200 chars | Validation fails before any API call |

---

## 6. PM2 Process

The `instagram-watcher` PM2 process runs the watcher in continuous mode (5-min poll internally):

```
pm2 start ecosystem.config.js   # starts all processes including instagram-watcher
pm2 logs instagram-watcher       # tail watcher logs
pm2 restart instagram-watcher    # restart after credential update
```

Logs:
- `Logs/pm2_instagram_watcher_out.log`
- `Logs/pm2_instagram_watcher_err.log`
- `Logs/instagram_watcher_checkpoint.json` — processed item IDs (max 500)

---

## 7. Switching to Real Mode

The PM2 wrapper at `/home/tayyab/pm2_instagram_watcher.sh` defaults to `--mode mock`.
After setting up credentials, change the last line to:

```bash
exec python3 scripts/instagram_watcher_skill.py --mode real
```

Then:

```bash
pm2 restart instagram-watcher
```
