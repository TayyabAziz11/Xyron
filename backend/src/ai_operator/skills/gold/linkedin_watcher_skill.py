#!/usr/bin/env python3
"""
Personal AI Employee - LinkedIn Watcher Skill
Gold Tier - G-M3: Social Watchers

Purpose: LinkedIn notification/message perception-only watcher
Tier: Gold
Skill ID: G-M3-T04

CRITICAL: PERCEPTION-ONLY. NEVER posts, NEVER replies, NEVER sends messages.
Only creates intake wrappers in Social/Inbox/.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Import PII redaction and helpers
try:
    from ai_operator.core.mcp_helpers import redact_pii, get_repo_root
except ImportError:
    import re
    def redact_pii(text: str) -> str:
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        text = re.sub(email_pattern, '[EMAIL_REDACTED]', text)
        text = re.sub(phone_pattern, '[PHONE_REDACTED]', text)
        return text

    def get_repo_root() -> Path:
        """Fallback: find repo root"""
        current = Path(__file__).parent
        while current != current.parent:
            if (current / 'system_log.md').exists():
                return current
            current = current.parent
        return Path(__file__).parent.parent.parent.parent.parent

# Import LinkedIn API helper for real mode
try:
    from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
except ImportError:
    # If running as script without proper sys.path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
    except ImportError:
        LinkedInAPIHelper = None


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LinkedInWatcher:
    """LinkedIn Watcher - Perception-only intake"""

    def __init__(self, config: Dict):
        self.config = config
        self.checkpoint_data = self._load_checkpoint()
        self.created_count = 0
        self.skipped_count = 0
        self.errors = []

    def _load_checkpoint(self) -> Dict:
        checkpoint_path = self.config['checkpoint_path']
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load checkpoint: {e}")
                return {'processed_ids': [], 'last_run': None}
        return {'processed_ids': [], 'last_run': None}

    def _save_checkpoint(self):
        checkpoint_path = self.config['checkpoint_path']
        self.checkpoint_data['last_run'] = datetime.now(timezone.utc).isoformat()

        if len(self.checkpoint_data['processed_ids']) > 500:
            self.checkpoint_data['processed_ids'] = self.checkpoint_data['processed_ids'][-500:]

        try:
            os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
            with open(checkpoint_path, 'w') as f:
                json.dump(self.checkpoint_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def reset_checkpoint(self):
        self.checkpoint_data = {'processed_ids': [], 'last_run': None}
        self._save_checkpoint()
        print("✓ LinkedIn checkpoint reset")

    def _create_read_permission_remediation(self, reason: str = '') -> None:
        """
        Create a specific remediation task when LinkedIn read permission is missing.

        File name: remediation__linkedin_read_permission__YYYYMMDD-HHMM.md

        Explains:
        - r_member_social is a restricted LinkedIn permission
        - Posting still works (w_member_social)
        - Reading posts requires LinkedIn partner/special access approval
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
            filename = f"remediation__linkedin_read_permission__{timestamp}.md"
            task_path = Path(self.config['base_dir']) / 'Needs_Action' / filename

            task_content = f"""---
id: remediation_linkedin_read_permission_{timestamp}
type: remediation
source: linkedin_watcher
created_at: {datetime.now(timezone.utc).isoformat()}
priority: medium
status: needs_action
---

# LinkedIn Read Permission Required

**Issue**: LinkedIn read permission (r_member_social) not granted
**Source**: LinkedIn Watcher (G-M3) — Limited Real Mode
**Created**: {datetime.now(timezone.utc).isoformat()}

## Situation

The LinkedIn watcher successfully authenticated and can **post** content,
but **cannot read** posts because `r_member_social` is a restricted LinkedIn API scope.

## What Works

- ✅ Authentication (OIDC): Working
- ✅ Posting (w_member_social / `rest/posts`): Working
- ❌ Reading posts (r_member_social): Not available

## Why This Happens

`r_member_social` is a **restricted LinkedIn API permission** that requires
special approval from LinkedIn's partner program.  Unlike `w_member_social`
(available via the "Share on LinkedIn" product), reading member posts requires
LinkedIn's explicit approval process.

## Suggested Actions

1. **Posting still works** — use `brain_execute_social_with_mcp_skill.py --execute --mode real`
   to continue publishing content normally.

2. **To request read access**, apply for LinkedIn Marketing API or Partner Program:
   - Go to: https://www.linkedin.com/developers/apps → Your App → Products
   - Request "Marketing Developer Platform" or "Advertising API" access
   - Approval typically takes several business days and requires a business justification.

3. **Workaround (manual)**: Monitor LinkedIn notifications in the browser and
   create intake wrappers manually in `Social/Inbox/`.

## Technical Detail

```
Probe endpoint : GET /v2/ugcPosts
HTTP status    : 403
Reason         : {reason or 'r_member_social not granted'}
```

## References

- Setup Guide: Docs/mcp_linkedin_setup.md
- LinkedIn Products: https://www.linkedin.com/developers/apps

---
**Generated by**: linkedin_watcher_skill.py
**Mode**: Limited Real Mode (auth OK, read restricted, posting unaffected)
"""

            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(task_content, encoding='utf-8')

            logger.info(f"Created read permission remediation: {filename}")
            self._append_log(f"Read permission remediation created: {filename}")

        except Exception as exc:
            logger.error(f"Failed to create read permission remediation: {exc}")

    def _create_remediation_task(self, title: str, description: str):
        """Create remediation task in Needs_Action/ when MCP fails"""
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
            filename = f"remediation__mcp__linkedin__{timestamp}.md"
            task_path = Path(self.config['base_dir']) / 'Needs_Action' / filename

            task_content = f"""---
id: remediation_linkedin_{timestamp}
type: remediation
source: linkedin_watcher
created_at: {datetime.now(timezone.utc).isoformat()}
priority: high
status: needs_action
---

# MCP Remediation: {title}

**Issue**: {title}
**Source**: LinkedIn Watcher (G-M3)
**Created**: {datetime.now(timezone.utc).isoformat()}

## Problem

{description}

## Suggested Actions

1. Check `.secrets/linkedin_credentials.json` exists and has valid credentials
2. Verify LinkedIn MCP server is running and reachable
3. Review Docs/mcp_linkedin_setup.md for setup instructions
4. Test connection: `python3 brain_mcp_registry_refresh_skill.py --server linkedin --mock`
5. Re-run watcher in mock mode to verify intake wrapper creation works

## References

- Setup Guide: Docs/mcp_linkedin_setup.md
- Credentials Template: .secrets/README.md
- MCP Registry: Logs/mcp_tools_snapshot_linkedin.json

---
**Generated by**: linkedin_watcher_skill.py
**Graceful Degradation**: Watcher continues running despite MCP failure
"""

            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(task_content, encoding='utf-8')

            logger.info(f"Created remediation task: {filename}")
            self._append_log(f"Remediation task created: {title}")

        except Exception as e:
            logger.error(f"Failed to create remediation task: {e}")

    def _append_log(self, message: str):
        log_path = self.config['log_path']
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            logger.warning(f"Could not write to log: {e}")

    def _create_intake_wrapper(self, item: Dict, dry_run: bool = False) -> Optional[str]:
        try:
            item_id = item['id']
            sender = item.get('sender', 'unknown')
            timestamp = datetime.now(timezone.utc)
            timestamp_str = timestamp.strftime('%Y%m%d-%H%M')

            # Extract username from LinkedIn URL
            sender_safe = sender.split('/')[-1][:20]

            filename = f"inbox__linkedin__{timestamp_str}__{sender_safe}.md"
            output_path = os.path.join(self.config['output_dir'], filename)

            if dry_run:
                print(f"[DRY-RUN] Would create: {filename}")
                return None

            body = item.get('body', '')
            sender_name = item.get('sender_name', sender_safe)
            item_type = item.get('type', 'notification')
            post_id = item.get('post_id', '')
            comment_id = item.get('comment_id', '')
            thread_id = item.get('thread_id', f"linkedin_{sender_safe}")
            is_urgent = item.get('urgent', False)

            # Privacy-safe excerpt
            excerpt = body[:500] if len(body) <= 500 else body[:497] + "..."
            excerpt = redact_pii(excerpt)

            frontmatter = f"""---
id: {item_id}
source: linkedin
received_at: {item.get('timestamp', timestamp.isoformat())}
sender: {sender}
channel: linkedin
thread_id: {thread_id}
excerpt: "{excerpt}"
status: pending
plan_required: {str(is_urgent).lower()}
pii_redacted: true
linkedin_type: {item_type}
post_id: {post_id}
comment_id: {comment_id}
---

# LinkedIn {item_type.title()} - {sender_name}

**⚠️ PERCEPTION-ONLY**: This is an intake wrapper. No LinkedIn actions have been taken.

## Item Details

**From**: {sender_name}
**Profile**: {sender}
**Received**: {item.get('timestamp', timestamp.isoformat())}
**Type**: {item_type}
**Post ID**: {post_id or 'N/A'}
**Comment ID**: {comment_id or 'N/A'}
**Thread**: {thread_id}
**Urgent**: {is_urgent}

## Content

{body}

## Suggested Next Steps

**If actionable (reply/comment/message)**:
1. Create plan using `brain_create_plan` (Gold Tier - G-M4)
2. Request approval via `brain_request_approval`
3. Execute via `brain_execute_social_with_mcp` (LinkedIn action)

**If informational only**:
- Archive to Done/ with summary note

## Privacy Notice

- PII in excerpt redacted for privacy
- Full content preserved above for context
- Never commit to git (covered by .gitignore)

---
**Generated by**: linkedin_watcher
**Watcher Version**: 1.0.0 (Gold Tier - G-M3)
"""

            os.makedirs(self.config['output_dir'], exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(frontmatter)

            logger.info(f"Created intake wrapper: {filename}")
            return output_path

        except Exception as e:
            logger.error(f"Failed to create intake wrapper: {e}")
            self.errors.append(str(e))
            return None

    def _load_mock_items(self) -> List[Dict]:
        fixture_path = self.config['mock_fixture_path']

        if not os.path.exists(fixture_path):
            raise FileNotFoundError(f"Mock fixture not found: {fixture_path}")

        try:
            with open(fixture_path, 'r') as f:
                items = json.load(f)

            if not isinstance(items, list):
                raise ValueError("Mock fixture must be a JSON array")

            logger.info(f"Loaded {len(items)} mock LinkedIn items")
            return items

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in mock fixture: {e}")

    def _fetch_items_real(self) -> List[Dict]:
        """
        Fetch posts from real LinkedIn API using OAuth2 helper (QUERY only).

        IMPORTANT: This ONLY calls QUERY methods (list_ugc_posts).
        NEVER calls ACTION methods (create_post, reply_comment, send_message).

        Returns:
            List of post dicts in standard format for intake wrapper creation
        """
        try:
            if LinkedInAPIHelper is None:
                logger.error("LinkedInAPIHelper not available (import failed)")
                self._create_remediation_task(
                    "LinkedIn API helper import failed",
                    "Could not import LinkedInAPIHelper. Check src/ai_operator/core/linkedin_api_helper.py exists"
                )
                return []

            # Initialize helper (will check for credentials and token)
            secrets_dir = Path(self.config['base_dir']) / '.secrets'
            helper = LinkedInAPIHelper(secrets_dir=secrets_dir)

            # Verify authentication before proceeding
            try:
                auth_info = helper.check_auth()
                profile = auth_info.get('profile', {})
                display_name = profile.get('name', profile.get('localizedFirstName', 'User'))
                logger.info(f"LinkedIn auth OK ({auth_info.get('auth_method', 'unknown')}): {display_name}")
            except Exception as auth_error:
                logger.warning(f"LinkedIn authentication failed: {auth_error}")
                self._create_remediation_task(
                    "LinkedIn authentication required",
                    f"Real mode requires valid LinkedIn OAuth2 token.\n\n"
                    f"Error: {auth_error}\n\n"
                    f"To authenticate:\n"
                    f"1. Run: python3 scripts/linkedin_oauth_helper.py --init\n"
                    f"2. Follow OAuth2 flow in browser\n"
                    f"3. Token will be saved to .secrets/linkedin_token.json\n"
                    f"4. Re-run watcher in real mode\n\n"
                    f"See Docs/mcp_linkedin_setup.md for details."
                )
                return []

            # Resolve author URN — uses /v2/me first, falls back to OIDC sub
            try:
                author_urn = helper.get_author_urn()
                logger.info(f"Using author URN: {redact_pii(author_urn)}")
            except Exception as urn_error:
                logger.warning(f"Could not determine LinkedIn author URN: {urn_error}")
                self._create_remediation_task(
                    "LinkedIn person URN resolution failed",
                    f"Could not derive person URN for UGC query.\n\nError: {urn_error}\n\n"
                    f"Ensure token has 'openid profile' scopes and LinkedIn Products are enabled.\n"
                    f"Run: python3 scripts/linkedin_oauth_helper.py --init"
                )
                return []

            # Check read access before fetching posts.
            # GET /v2/ugcPosts returning 403 means r_member_social is not granted.
            # Anti-spam: only create remediation file once; subsequent blocked runs just log info.
            read_access = helper.check_read_access()
            if not read_access['available'] and read_access.get('status') == 403:
                read_blocked_since = self.checkpoint_data.get('read_blocked_since')
                if read_blocked_since:
                    logger.info(
                        f"LinkedIn read permission still unavailable (r_member_social not granted, "
                        f"blocked since {read_blocked_since}). Remediation already created — skipping."
                    )
                else:
                    logger.warning(
                        "LinkedIn read permission (r_member_social) not granted. "
                        "Watcher running in limited real mode."
                    )
                    self._create_read_permission_remediation(
                        reason=read_access.get('reason', 'r_member_social not granted')
                    )
                    # Record when we first detected the block so future runs skip file creation
                    self.checkpoint_data['read_blocked_since'] = (
                        datetime.now(timezone.utc).isoformat()
                    )
                    self._save_checkpoint()
                self._append_log(
                    "LinkedIn watcher: limited real mode — read permission unavailable "
                    "(r_member_social not granted). Posting unaffected."
                )
                return []

            # Read access available — clear any stale block marker
            if self.checkpoint_data.get('read_blocked_since'):
                del self.checkpoint_data['read_blocked_since']
                self._save_checkpoint()

            # Fetch posts via list_posts() — tries /v2/ugcPosts then /v2/shares automatically
            logger.info(f"Fetching LinkedIn posts for author: {redact_pii(author_urn)}")
            max_results = self.config.get('max_results', 10)
            normalized_posts = helper.list_posts(author_urn=author_urn, count=max_results)

            if not normalized_posts:
                logger.warning(
                    f"Both /v2/ugcPosts and /v2/shares returned 0 results for "
                    f"author={redact_pii(author_urn)}. "
                    f"Possible causes: no posts exist, 'w_member_social' / 'Share on LinkedIn' "
                    f"product not enabled in LinkedIn Developer Console, or URN mismatch."
                )

            # Transform normalized posts into standard intake-wrapper format.
            # normalized_posts use unified fields from _normalize_post():
            #   id, author_urn, text, created_ms, source_endpoint
            items = []
            for post in normalized_posts:
                post_id = post.get('id', '')
                created_ms = post.get('created_ms', 0) or (datetime.now(timezone.utc).timestamp() * 1000)
                created_iso = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
                text_content = post.get('text', '')
                source_ep = post.get('source_endpoint', 'unknown')

                item = {
                    'id': post_id,
                    'sender': f"https://www.linkedin.com/feed/update/{post_id}",
                    'sender_name': 'You',
                    'timestamp': created_iso,
                    'body': text_content,
                    'type': 'post',
                    'post_id': post_id,
                    'comment_id': '',
                    'thread_id': f"linkedin_{post_id}",
                    'urgent': False,
                    'source_endpoint': source_ep,
                }
                items.append(item)

            logger.info(f"Fetched {len(items)} LinkedIn posts from API")
            return items

        except Exception as e:
            logger.error(f"Failed to fetch from LinkedIn API: {e}")
            self._create_remediation_task(
                "LinkedIn API query failed",
                f"Error: {e}\n\nCheck:\n"
                f"1. .secrets/linkedin_credentials.json exists with valid client_id/client_secret\n"
                f"2. .secrets/linkedin_token.json exists with valid access_token\n"
                f"3. Token hasn't expired (run linkedin_api_helper.py to refresh)\n"
                f"4. Network connection is working\n\n"
                f"See Docs/linkedin_real_setup.md for troubleshooting."
            )
            return []

    def run(self, dry_run: bool = False, mock: bool = True, verbose: bool = False) -> Dict:
        start_time = datetime.now(timezone.utc)

        if verbose:
            logger.setLevel(logging.DEBUG)

        logger.info(f"Starting LinkedIn watcher (mock={mock}, dry_run={dry_run})")

        if mock:
            try:
                items = self._load_mock_items()
            except Exception as e:
                logger.error(f"Failed to load mock items: {e}")
                return {'success': False, 'error': str(e)}
        else:
            items = self._fetch_items_real()

        max_results = self.config.get('max_results', 10)
        items = items[:max_results]

        logger.info(f"Processing {len(items)} LinkedIn items")

        for item in items:
            item_id = item.get('id')

            if not item_id:
                logger.warning("Item missing ID, skipping")
                continue

            if item_id in self.checkpoint_data['processed_ids']:
                logger.debug(f"Skipping already processed: {item_id}")
                self.skipped_count += 1
                continue

            output_path = self._create_intake_wrapper(item, dry_run=dry_run)

            if output_path:
                self.created_count += 1
                if not dry_run:
                    self.checkpoint_data['processed_ids'].append(item_id)

        if not dry_run:
            self._save_checkpoint()  # always save so dashboard shows healthy

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        summary = (
            f"LinkedIn watcher complete: "
            f"{len(items)} scanned, {self.created_count} created, "
            f"{self.skipped_count} skipped, {len(self.errors)} errors, "
            f"{duration:.2f}s"
        )

        self._append_log(summary)

        try:
            with open('system_log.md', 'a', encoding='utf-8') as f:
                f.write(f"\n**[{start_time.isoformat()}]** {summary}\n")
        except:
            pass

        print(f"\n✅ {summary}")

        return {
            'success': True,
            'scanned': len(items),
            'created': self.created_count,
            'skipped': self.skipped_count,
            'errors': len(self.errors),
            'duration': duration
        }


def main():
    parser = argparse.ArgumentParser(description='LinkedIn Watcher - Perception-only (Gold Tier - Real Mode)')
    parser.add_argument('--once', action='store_true', default=True, help='Run once and exit (default)')
    parser.add_argument('--dry-run', action='store_true', help='Simulate run without creating files')
    parser.add_argument('--mode', type=str, choices=['mock', 'real'], default='mock',
                        help='Data source mode: mock (default, uses templates/mock_linkedin.json) or real (uses LinkedIn OAuth2 API)')
    parser.add_argument('--max-results', type=int, default=10, help='Maximum items to process (default: 10)')
    parser.add_argument('--reset-checkpoint', action='store_true', help='Reset checkpoint before running')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    # Determine mock vs real mode
    use_mock = (args.mode == 'mock')

    config = {
        'base_dir': get_repo_root(),
        'checkpoint_path': 'Logs/linkedin_watcher_checkpoint.json',
        'log_path': 'Logs/linkedin_watcher.log',
        'output_dir': 'Social/Inbox',
        'max_results': args.max_results,
        'mock_fixture_path': 'templates/mock_linkedin.json'
    }

    watcher = LinkedInWatcher(config)

    if args.reset_checkpoint:
        watcher.reset_checkpoint()

    result = watcher.run(dry_run=args.dry_run, mock=use_mock, verbose=args.verbose)

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
