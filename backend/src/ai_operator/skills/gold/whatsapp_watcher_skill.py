#!/usr/bin/env python3
"""
Personal AI Employee - WhatsApp Watcher Skill
Gold Tier - G-M3: Social Watchers

Purpose: WhatsApp message perception-only watcher (creates intake wrappers, NEVER sends/replies)
Tier: Gold
Skill ID: G-M3-T02

CRITICAL: This is PERCEPTION-ONLY. It NEVER sends WhatsApp messages, NEVER replies,
NEVER calls ACTION MCP tools. It only creates intake wrappers in Social/Inbox/.

Features:
- Mock mode with templates/mock_whatsapp.json
- Real mode with MCP query tools (optional, not implemented yet)
- PII redaction using mcp_helpers
- Checkpointing to avoid duplicates
- Privacy-safe intake wrappers
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Ensure the repo src/ directory is on sys.path so that
# `ai_operator` is importable regardless of how this script is invoked.
_SKILLS_DIR = Path(__file__).parent.resolve()        # .../skills/gold
_SRC_DIR    = _SKILLS_DIR.parent.parent.parent       # .../src
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Import PII redaction helper
try:
    from ai_operator.core.mcp_helpers import redact_pii, get_repo_root
except ImportError:
    # Fallback if mcp_helpers not available
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


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WhatsAppWatcher:
    """WhatsApp Watcher - Perception-only message intake"""

    def __init__(self, config: Dict):
        self.config = config
        self.checkpoint_data = self._load_checkpoint()
        self.created_count = 0
        self.skipped_count = 0
        self.errors = []

    def _load_checkpoint(self) -> Dict:
        """Load checkpoint to avoid re-processing messages"""
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
        """Save checkpoint with processed message IDs"""
        checkpoint_path = self.config['checkpoint_path']
        self.checkpoint_data['last_run'] = datetime.now(timezone.utc).isoformat()

        # Keep only last 500 IDs
        if len(self.checkpoint_data['processed_ids']) > 500:
            self.checkpoint_data['processed_ids'] = self.checkpoint_data['processed_ids'][-500:]

        try:
            os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
            with open(checkpoint_path, 'w') as f:
                json.dump(self.checkpoint_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def reset_checkpoint(self):
        """Reset checkpoint"""
        self.checkpoint_data = {'processed_ids': [], 'last_run': None}
        self._save_checkpoint()
        print("✓ WhatsApp checkpoint reset")

    def _create_remediation_task(self, title: str, description: str):
        """Create remediation task in Needs_Action/ when MCP fails"""
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
            filename = f"remediation__mcp__whatsapp__{timestamp}.md"
            task_path = Path(self.config['base_dir']) / 'Needs_Action' / filename

            task_content = f"""---
id: remediation_whatsapp_{timestamp}
type: remediation
source: whatsapp_watcher
created_at: {datetime.now(timezone.utc).isoformat()}
priority: high
status: needs_action
---

# MCP Remediation: {title}

**Issue**: {title}
**Source**: WhatsApp Watcher (G-M3)
**Created**: {datetime.now(timezone.utc).isoformat()}

## Problem

{description}

## Suggested Actions

1. Check `.secrets/whatsapp_credentials.json` exists and has valid credentials
2. Verify WhatsApp MCP server is running and reachable
3. Review Docs/mcp_whatsapp_setup.md for setup instructions
4. Test connection: `python3 brain_mcp_registry_refresh_skill.py --server whatsapp --mock`
5. Re-run watcher in mock mode to verify intake wrapper creation works

## References

- Setup Guide: Docs/mcp_whatsapp_setup.md
- Credentials Template: .secrets/README.md
- MCP Registry: Logs/mcp_tools_snapshot_whatsapp.json

---
**Generated by**: whatsapp_watcher_skill.py
**Graceful Degradation**: Watcher continues running despite MCP failure
"""

            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(task_content, encoding='utf-8')

            logger.info(f"Created remediation task: {filename}")
            self._append_log(f"Remediation task created: {title}")

        except Exception as e:
            logger.error(f"Failed to create remediation task: {e}")

    def _append_log(self, message: str):
        """Append to whatsapp_watcher.log"""
        log_path = self.config['log_path']
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            logger.warning(f"Could not write to log: {e}")

    def _create_intake_wrapper(self, message: Dict, dry_run: bool = False) -> Optional[str]:
        """Create intake wrapper in Social/Inbox/"""
        try:
            # Generate unique ID and filename
            msg_id = message['id']
            sender = message.get('sender', 'unknown')
            timestamp = datetime.now(timezone.utc)
            timestamp_str = timestamp.strftime('%Y%m%d-%H%M')

            # Sanitize sender for filename (remove special chars)
            sender_safe = sender.replace('+', '').replace('-', '').replace(' ', '')[:15]

            filename = f"inbox__whatsapp__{timestamp_str}__{sender_safe}.md"
            output_path = os.path.join(self.config['output_dir'], filename)

            if dry_run:
                print(f"[DRY-RUN] Would create: {filename}")
                return None

            # Extract message data
            body = message.get('body', '')
            sender_name = message.get('sender_name', sender)
            thread_id = message.get('thread_id', f"thread_{sender_safe}")
            is_urgent = message.get('urgent', False)

            # Create privacy-safe excerpt (max 500 chars, PII redacted)
            excerpt = body[:500] if len(body) <= 500 else body[:497] + "..."
            excerpt = redact_pii(excerpt)

            # YAML frontmatter
            frontmatter = f"""---
id: {msg_id}
source: whatsapp
received_at: {message.get('timestamp', timestamp.isoformat())}
sender: {sender}
channel: whatsapp
thread_id: {thread_id}
excerpt: "{excerpt}"
status: pending
plan_required: {str(is_urgent).lower()}
pii_redacted: true
---

# WhatsApp Message - {sender_name}

**⚠️ PERCEPTION-ONLY**: This is an intake wrapper. No WhatsApp actions have been taken.

## Message Details

**From**: {sender_name} ({sender})
**Received**: {message.get('timestamp', timestamp.isoformat())}
**Thread**: {thread_id}
**Type**: {message.get('type', 'incoming')}
**Urgent**: {is_urgent}

## Message Content

{body}

## Suggested Next Steps

**If actionable**:
1. Create plan using `brain_create_plan` (Gold Tier - G-M4)
2. Request approval via `brain_request_approval`
3. Execute via `brain_execute_social_with_mcp` (WhatsApp reply/send)

**If informational only**:
- Archive to Done/ with note

## Privacy Notice

- PII in excerpt has been redacted for privacy
- Full message content preserved above for context
- Never commit this file to git (covered by .gitignore)

---
**Generated by**: whatsapp_watcher
**Watcher Version**: 1.0.0 (Gold Tier - G-M3)
**Checkpoint ID**: {self.checkpoint_data.get('last_run', 'initial')}
"""

            # Write file
            os.makedirs(self.config['output_dir'], exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(frontmatter)

            logger.info(f"Created intake wrapper: {filename}")
            return output_path

        except Exception as e:
            logger.error(f"Failed to create intake wrapper: {e}")
            self.errors.append(str(e))
            return None

    def _load_mock_messages(self) -> List[Dict]:
        """Load mock messages from fixture file"""
        fixture_path = self.config['mock_fixture_path']

        if not os.path.exists(fixture_path):
            raise FileNotFoundError(f"Mock fixture not found: {fixture_path}")

        try:
            with open(fixture_path, 'r') as f:
                messages = json.load(f)

            if not isinstance(messages, list):
                raise ValueError("Mock fixture must be a JSON array")

            logger.info(f"Loaded {len(messages)} mock messages from {fixture_path}")
            return messages

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in mock fixture: {e}")

    def _fetch_messages_real(self) -> List[Dict]:
        """
        Fetch unread messages via WhatsApp Web (Playwright).

        PERCEPTION-ONLY: uses WhatsAppWebClient read methods only.
        Never calls send_message or any action tools.

        Returns:
            List of message dicts in standard intake format
        """
        try:
            from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient

            client = WhatsAppWebClient(headless=True)
            client.start()

            if not client.is_logged_in():
                logger.warning("WhatsApp Web not logged in — run: python3 scripts/wa_setup.py")
                self._create_remediation_task(
                    "WhatsApp Web not logged in",
                    "Session not found or expired.\n"
                    "Run: python3 scripts/wa_setup.py to pair your device.\n"
                    "Requires Playwright: pip install playwright && playwright install chromium"
                )
                client.stop()
                return []

            limit = self.config.get('max_results', 10)
            raw = client.get_unread_messages(limit=limit)
            client.stop()

            messages = []
            for m in raw:
                chat_id = m.get('chat_id', '')
                msg_id = m.get('id') or chat_id or f"wa_{len(messages)}"
                messages.append({
                    'id': msg_id,
                    'sender': chat_id,
                    'sender_name': m.get('from_name', 'Unknown'),
                    'body': m.get('text', ''),
                    'timestamp': m.get('timestamp', datetime.now(timezone.utc).isoformat()),
                    'thread_id': chat_id,
                    'type': 'incoming',
                    'urgent': False,
                })

            logger.info(f"Fetched {len(messages)} unread messages via WhatsApp Web")
            return messages

        except ImportError as e:
            logger.error(f"WhatsAppWebClient not available: {e}")
            self._create_remediation_task(
                "WhatsApp Web helper not importable",
                f"ImportError: {e}\n"
                "Ensure Playwright is installed: pip install playwright && playwright install chromium"
            )
            return []
        except Exception as e:
            logger.error(f"Failed to fetch from WhatsApp Web: {e}")
            self._create_remediation_task(
                "WhatsApp Web fetch failed",
                f"Error: {e}\n"
                "Ensure session is paired: python3 scripts/wa_setup.py\n"
                "Check Playwright: pip install playwright && playwright install chromium"
            )
            return []

    def run(self, dry_run: bool = False, mock: bool = True, verbose: bool = False) -> Dict:
        """Run WhatsApp watcher"""
        start_time = datetime.now(timezone.utc)

        if verbose:
            logger.setLevel(logging.DEBUG)

        logger.info(f"Starting WhatsApp watcher (mock={mock}, dry_run={dry_run})")

        # Fetch messages
        if mock:
            try:
                messages = self._load_mock_messages()
            except Exception as e:
                logger.error(f"Failed to load mock messages: {e}")
                return {'success': False, 'error': str(e)}
        else:
            messages = self._fetch_messages_real()

        # Apply max_results limit
        max_results = self.config.get('max_results', 10)
        messages = messages[:max_results]

        logger.info(f"Processing {len(messages)} messages")

        # Process each message
        for message in messages:
            msg_id = message.get('id')

            if not msg_id:
                logger.warning("Message missing ID, skipping")
                continue

            # Check if already processed
            if msg_id in self.checkpoint_data['processed_ids']:
                logger.debug(f"Skipping already processed message: {msg_id}")
                self.skipped_count += 1
                continue

            # Create intake wrapper
            output_path = self._create_intake_wrapper(message, dry_run=dry_run)

            if output_path:
                self.created_count += 1
                if not dry_run:
                    self.checkpoint_data['processed_ids'].append(msg_id)

        # Save checkpoint
        if not dry_run:
            self._save_checkpoint()  # always save so dashboard shows healthy

        # Log summary
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        summary = (
            f"WhatsApp watcher complete: "
            f"{len(messages)} scanned, {self.created_count} created, "
            f"{self.skipped_count} skipped, {len(self.errors)} errors, "
            f"{duration:.2f}s"
        )

        self._append_log(summary)

        # Append to system_log.md
        try:
            with open('system_log.md', 'a', encoding='utf-8') as f:
                f.write(f"\n**[{start_time.isoformat()}]** {summary}\n")
        except:
            pass

        print(f"\n✅ {summary}")

        return {
            'success': True,
            'scanned': len(messages),
            'created': self.created_count,
            'skipped': self.skipped_count,
            'errors': len(self.errors),
            'duration': duration
        }


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='WhatsApp Watcher - Perception-only message intake (Gold Tier - G-M4)'
    )
    parser.add_argument('--once', action='store_true', default=True,
                        help='Run once and exit (default)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate run without creating files')
    parser.add_argument('--mode', type=str, choices=['mock', 'real', 'mcp'], default='mock',
                        help='Data source mode: mock (default, uses templates/mock_whatsapp.json) or real/mcp (uses WhatsApp Web via Playwright)')
    parser.add_argument('--max-results', type=int, default=10,
                        help='Maximum messages to process (default: 10)')
    parser.add_argument('--reset-checkpoint', action='store_true',
                        help='Reset checkpoint before running')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    # Determine mock vs MCP mode
    use_mock = (args.mode == 'mock')

    config = {
        'base_dir': get_repo_root(),
        'checkpoint_path': 'Logs/whatsapp_watcher_checkpoint.json',
        'log_path': 'Logs/whatsapp_watcher.log',
        'output_dir': 'Social/Inbox',
        'max_results': args.max_results,
        'mock_fixture_path': 'templates/mock_whatsapp.json'
    }

    watcher = WhatsAppWatcher(config)

    if args.reset_checkpoint:
        watcher.reset_checkpoint()

    result = watcher.run(dry_run=args.dry_run, mock=use_mock, verbose=args.verbose)

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
