#!/usr/bin/env python3
"""
Gmail Watcher Skill (Silver Tier - Perception Only)

CRITICAL: This is a PERCEPTION-ONLY watcher. It NEVER sends emails, NEVER calls MCP,
NEVER completes tasks. It only creates intake wrappers in Needs_Action/.

Features:
- OAuth2 authentication with Gmail API
- Email fetching with configurable queries
- Intake wrapper creation (privacy-safe)
- Checkpointing to avoid duplicates
- PII redaction (emails, phone numbers)
- Mock mode for testing
- Comprehensive logging

Usage:
    python gmail_watcher_skill.py --dry-run
    python gmail_watcher_skill.py --once
    python gmail_watcher_skill.py --mock --once
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Gmail API imports (optional - only required for real mode)
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GMAIL_API_AVAILABLE = True
except ImportError:
    GMAIL_API_AVAILABLE = False

# Gmail API scopes (read-only for perception)
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Default configuration
DEFAULT_CONFIG = {
    'credentials_path': 'credentials.json',
    'token_path': 'token.json',
    'checkpoint_path': 'Logs/gmail_checkpoint.json',
    'log_path': 'Logs/gmail_watcher.log',
    'output_dir': 'Needs_Action',
    'max_results': 10,
    'query': 'is:unread newer_than:7d',
    'include_body': False,  # Privacy: OFF by default
    'mock_fixture_path': 'templates/mock_emails.json'
}


class GmailWatcherSkill:
    """Gmail Watcher - Perception-only email intake"""

    def __init__(self, config: Dict):
        self.config = config
        self.checkpoint_data = self._load_checkpoint()
        self.processed_count = 0
        self.errors = []

    def _load_checkpoint(self) -> Dict:
        """Load checkpoint to avoid re-processing emails"""
        checkpoint_path = self.config['checkpoint_path']
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                self._log_error(f"Failed to load checkpoint: {e}")
                return {'processed_ids': [], 'last_run': None}
        return {'processed_ids': [], 'last_run': None}

    def _save_checkpoint(self):
        """Save checkpoint with processed email IDs"""
        checkpoint_path = self.config['checkpoint_path']
        self.checkpoint_data['last_run'] = datetime.now(timezone.utc).isoformat()

        # Keep only last 1000 IDs to prevent file bloat
        if len(self.checkpoint_data['processed_ids']) > 1000:
            self.checkpoint_data['processed_ids'] = self.checkpoint_data['processed_ids'][-1000:]

        try:
            os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
            with open(checkpoint_path, 'w') as f:
                json.dump(self.checkpoint_data, f, indent=2)
        except Exception as e:
            self._log_error(f"Failed to save checkpoint: {e}")

    def reset_checkpoint(self):
        """Reset checkpoint (manual operation)"""
        self.checkpoint_data = {'processed_ids': [], 'last_run': None}
        self._save_checkpoint()
        print("✓ Checkpoint reset successfully")

    def _redact_email(self, email: str) -> str:
        """Redact email addresses (privacy)"""
        if '@' in email:
            local, domain = email.split('@', 1)
            if len(local) > 2:
                return f"{local[0]}***@{domain}"
            else:
                return f"***@{domain}"
        return email

    def _redact_phone(self, text: str) -> str:
        """Redact phone numbers (privacy)"""
        # Simple pattern for common phone formats
        phone_pattern = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'
        return re.sub(phone_pattern, '[PHONE-REDACTED]', text)

    def _redact_pii(self, text: str) -> str:
        """Redact PII from text"""
        if not text:
            return text

        # Redact email addresses
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        text = re.sub(email_pattern, lambda m: self._redact_email(m.group()), text)

        # Redact phone numbers
        text = self._redact_phone(text)

        return text

    def _log_error(self, message: str):
        """Log error message"""
        self.errors.append(message)
        print(f"✗ ERROR: {message}", file=sys.stderr)

    def _append_log(self, message: str):
        """Append to gmail_watcher.log"""
        log_path = self.config['log_path']
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"{message}\n")
        except Exception as e:
            print(f"Warning: Could not write to log: {e}", file=sys.stderr)

    def _create_intake_wrapper(self, email_data: Dict, dry_run: bool = False) -> Optional[str]:
        """Create intake wrapper in Needs_Action/"""
        try:
            # Generate unique filename
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
            short_id = email_data['id'][:8]
            filename = f"inbox__gmail__{timestamp}__{short_id}.md"
            filepath = os.path.join(self.config['output_dir'], filename)

            # Redact sender for privacy
            sender_raw = email_data.get('from', 'unknown@unknown.com')
            sender_redacted = self._redact_email(sender_raw)

            # Extract safe subject
            subject = email_data.get('subject', '(no subject)')[:200]  # Truncate long subjects

            # Prepare excerpt (truncated and redacted)
            excerpt = email_data.get('snippet', '')[:500]  # First 500 chars
            excerpt_redacted = self._redact_pii(excerpt)

            # Build YAML frontmatter
            frontmatter = f"""---
id: {email_data['id']}
source: gmail
received_at: {email_data.get('received_at', datetime.now(timezone.utc).isoformat())}
from: {sender_redacted}
subject: {subject}
status: needs_triage
plan_required: true
---

# Email from {sender_redacted.split('@')[1] if '@' in sender_redacted else 'Unknown'}

**Subject:** {subject}

**Received:** {email_data.get('received_at', 'Unknown')}

## Summary

New email received via Gmail watcher (Silver Tier perception layer).

## Raw Excerpt (Redacted)

{excerpt_redacted}

{f'... (truncated at 500 chars)' if len(email_data.get('snippet', '')) > 500 else ''}

## Suggested Next Step

1. Review email content
2. Determine if action required
3. If external action needed (reply, forward): Create plan with brain_create_plan
4. All email replies require approved plan + MCP execution

**IMPORTANT:** This watcher is perception-only. No emails have been sent. All external actions require plan approval.

---

**Metadata:**
- Email ID: {email_data['id']}
- Processed: {datetime.now(timezone.utc).isoformat()}
- Watcher Version: 1.0.0 (Silver Tier)

"""

            if dry_run:
                print(f"  [DRY-RUN] Would create: {filename}")
                return filename
            else:
                # Create file
                os.makedirs(self.config['output_dir'], exist_ok=True)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(frontmatter)

                print(f"  ✓ Created: {filename}")
                return filename

        except Exception as e:
            self._log_error(f"Failed to create intake wrapper: {e}")
            return None

    def fetch_emails_mock(self) -> List[Dict]:
        """Fetch emails from mock fixture (for testing)"""
        fixture_path = self.config['mock_fixture_path']

        if not os.path.exists(fixture_path):
            print(f"✗ Mock fixture not found: {fixture_path}")
            print("Creating default mock fixture...")
            self._create_default_mock_fixture(fixture_path)

        try:
            with open(fixture_path, 'r', encoding='utf-8') as f:
                mock_data = json.load(f)

            emails = mock_data.get('emails', [])
            print(f"Loaded {len(emails)} mock emails from {fixture_path}")
            return emails
        except Exception as e:
            self._log_error(f"Failed to load mock fixture: {e}")
            return []

    def _create_default_mock_fixture(self, fixture_path: str):
        """Create default mock email fixture"""
        mock_data = {
            "emails": [
                {
                    "id": "mock001a2b3c4d5e6f7g",
                    "from": "alice@example.com",
                    "subject": "Project Update - Q1 Hackathon",
                    "snippet": "Hi team, I wanted to share an update on our Q1 hackathon progress. We've completed the Bronze tier implementation...",
                    "received_at": "2026-02-11T10:30:00Z"
                },
                {
                    "id": "mock002h3i4j5k6l7m8n",
                    "from": "bob@company.org",
                    "subject": "Meeting Request: Silver Tier Review",
                    "snippet": "Could we schedule a meeting to review the Silver tier implementation? I have some questions about the MCP governance...",
                    "received_at": "2026-02-11T11:45:00Z"
                },
                {
                    "id": "mock003p4q5r6s7t8u9v",
                    "from": "notifications@github.com",
                    "subject": "[GitHub] Pull Request Review Requested",
                    "snippet": "You have been requested to review pull request #42: Add Gmail watcher with OAuth2 authentication...",
                    "received_at": "2026-02-11T14:20:00Z"
                }
            ]
        }

        try:
            os.makedirs(os.path.dirname(fixture_path), exist_ok=True)
            with open(fixture_path, 'w', encoding='utf-8') as f:
                json.dump(mock_data, f, indent=2)
            print(f"✓ Created mock fixture: {fixture_path}")
        except Exception as e:
            print(f"✗ Failed to create mock fixture: {e}", file=sys.stderr)

    def fetch_emails_real(self) -> List[Dict]:
        """Fetch emails from Gmail API (requires OAuth2)"""
        if not GMAIL_API_AVAILABLE:
            self._log_error("Gmail API not available. Install: pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")
            return []

        try:
            # Authenticate
            creds = None
            token_path = self.config['token_path']
            creds_path = self.config['credentials_path']

            # Load existing token
            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)

            # Refresh or get new token
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    print("Refreshing expired OAuth2 token...")
                    creds.refresh(Request())
                else:
                    if not os.path.exists(creds_path):
                        self._log_error(f"Credentials file not found: {creds_path}")
                        print("\nTo set up Gmail OAuth2:")
                        print("1. Go to: https://console.cloud.google.com/")
                        print("2. Create project, enable Gmail API")
                        print("3. Create OAuth2 credentials (Desktop app)")
                        print("4. Download as credentials.json")
                        print("5. Run this script again\n")
                        return []

                    print("Starting OAuth2 flow (browser will open)...")
                    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                    creds = flow.run_local_server(port=0)

                # Save token for future use
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())
                print("✓ OAuth2 token saved")

            # Build Gmail service
            service = build('gmail', 'v1', credentials=creds)

            # Fetch messages
            query = self.config['query']
            max_results = self.config['max_results']

            print(f"Fetching emails (query: '{query}', max: {max_results})...")

            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                print("No messages found matching query")
                return []

            # Fetch full message details
            emails = []
            for msg in messages:
                try:
                    full_msg = service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='full'
                    ).execute()

                    # Extract headers
                    headers = full_msg['payload'].get('headers', [])
                    from_header = next((h['value'] for h in headers if h['name'] == 'From'), 'unknown')
                    subject_header = next((h['value'] for h in headers if h['name'] == 'Subject'), '(no subject)')
                    date_header = next((h['value'] for h in headers if h['name'] == 'Date'), '')

                    email_data = {
                        'id': full_msg['id'],
                        'from': from_header,
                        'subject': subject_header,
                        'snippet': full_msg.get('snippet', ''),
                        'received_at': date_header or datetime.now(timezone.utc).isoformat()
                    }
                    emails.append(email_data)

                except HttpError as e:
                    self._log_error(f"Failed to fetch message {msg['id']}: {e}")

            print(f"✓ Fetched {len(emails)} emails")
            return emails

        except Exception as e:
            self._log_error(f"Gmail API error: {e}")
            return []

    def run(self, dry_run: bool = False, mock: bool = False, use_checkpoint: bool = True):
        """Main execution"""
        start_time = datetime.now(timezone.utc)

        # Fetch emails
        if mock:
            emails = self.fetch_emails_mock()
        else:
            emails = self.fetch_emails_real()

        if not emails:
            print("No emails to process")
            log_msg = f"[{start_time.isoformat()}] No emails found | Mode: {'mock' if mock else 'real'} | Query: {self.config['query']}"
            self._append_log(log_msg)
            if not dry_run and use_checkpoint:
                self._save_checkpoint()  # always save so dashboard shows healthy
            return

        # Filter out already processed (if checkpointing enabled)
        if use_checkpoint:
            processed_ids = set(self.checkpoint_data.get('processed_ids', []))
            emails_to_process = [e for e in emails if e['id'] not in processed_ids]
            skipped = len(emails) - len(emails_to_process)
            if skipped > 0:
                print(f"Skipped {skipped} already processed emails (checkpointing)")
        else:
            emails_to_process = emails

        if not emails_to_process:
            print("All emails already processed (checkpoint)")
            if not dry_run and use_checkpoint:
                self._save_checkpoint()  # always save so dashboard shows healthy
            return

        # Process emails
        print(f"\nProcessing {len(emails_to_process)} emails...")
        for email in emails_to_process:
            filename = self._create_intake_wrapper(email, dry_run=dry_run)
            if filename and not dry_run:
                self.processed_count += 1
                self.checkpoint_data['processed_ids'].append(email['id'])

        # Save checkpoint
        if not dry_run and use_checkpoint:
            self._save_checkpoint()

        # Calculate duration
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        # Log results
        mode = "dry-run" if dry_run else ("mock" if mock else "real")
        log_msg = f"[{start_time.isoformat()}] Gmail Watcher Run | Mode: {mode} | Query: {self.config['query']} | Found: {len(emails)} | Processed: {self.processed_count} | Skipped: {len(emails) - len(emails_to_process)} | Errors: {len(self.errors)} | Duration: {duration:.2f}s"
        self._append_log(log_msg)

        # Summary
        print(f"\n{'='*60}")
        print(f"Gmail Watcher Summary")
        print(f"{'='*60}")
        print(f"Mode: {mode}")
        print(f"Emails found: {len(emails)}")
        print(f"Emails processed: {self.processed_count}")
        print(f"Emails skipped: {len(emails) - len(emails_to_process)} (checkpoint)")
        print(f"Errors: {len(self.errors)}")
        print(f"Duration: {duration:.2f}s")
        print(f"{'='*60}")

        if self.errors:
            print("\nErrors encountered:")
            for error in self.errors:
                print(f"  - {error}")


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Gmail Watcher Skill (Silver Tier - Perception Only)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python gmail_watcher_skill.py --dry-run
  python gmail_watcher_skill.py --once
  python gmail_watcher_skill.py --mock --once
  python gmail_watcher_skill.py --max-results 20 --query "is:unread is:important"
  python gmail_watcher_skill.py --reset-checkpoint
        """
    )

    parser.add_argument('--dry-run', action='store_true',
                        help='Preview what would be processed (no files written)')
    parser.add_argument('--once', action='store_true',
                        help='Process once and exit (default behavior)')
    parser.add_argument('--mock', action='store_true',
                        help='Use mock email fixture instead of real Gmail API')
    parser.add_argument('--max-results', type=int, default=DEFAULT_CONFIG['max_results'],
                        help=f"Max emails to fetch (default: {DEFAULT_CONFIG['max_results']})")
    parser.add_argument('--query', default=DEFAULT_CONFIG['query'],
                        help=f"Gmail search query (default: '{DEFAULT_CONFIG['query']}')")
    parser.add_argument('--label', help='Gmail label to apply (not implemented yet)')
    parser.add_argument('--since-checkpoint', action='store_true', dest='use_checkpoint',
                        help='Use checkpoint to skip already processed emails (default)')
    parser.add_argument('--no-checkpoint', action='store_false', dest='use_checkpoint',
                        help='Ignore checkpoint (process all emails)')
    parser.add_argument('--reset-checkpoint', action='store_true',
                        help='Reset checkpoint and exit')
    parser.add_argument('--include-body', action='store_true',
                        help='Include full email body (PRIVACY WARNING: disabled by default)')

    parser.set_defaults(use_checkpoint=True, once=True)

    args = parser.parse_args()

    # Display privacy warning if --include-body enabled
    if args.include_body:
        print("⚠️  WARNING: --include-body enabled. Full email bodies will be stored.")
        print("   This may contain PII. Use only for testing.\n")

    # Build config
    config = DEFAULT_CONFIG.copy()
    config['max_results'] = args.max_results
    config['query'] = args.query
    config['include_body'] = args.include_body

    # Create watcher
    watcher = GmailWatcherSkill(config)

    # Reset checkpoint if requested
    if args.reset_checkpoint:
        watcher.reset_checkpoint()
        return

    # Display banner
    print("=" * 60)
    print("Gmail Watcher Skill (Silver Tier - Perception Only)")
    print("=" * 60)
    print(f"Mode: {'DRY-RUN' if args.dry_run else ('MOCK' if args.mock else 'REAL')}")
    print(f"Query: {args.query}")
    print(f"Max Results: {args.max_results}")
    print(f"Checkpointing: {'ON' if args.use_checkpoint else 'OFF'}")
    print("=" * 60)
    print()

    # Run watcher
    try:
        watcher.run(dry_run=args.dry_run, mock=args.mock, use_checkpoint=args.use_checkpoint)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ FATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
