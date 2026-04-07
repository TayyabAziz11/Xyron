#!/usr/bin/env python3
"""
Gmail API Helper - Centralized OAuth2 and API Integration
Silver Tier - Secure credential handling for Gmail API

Purpose: Provide secure Gmail API authentication and API wrapper.
Supports:
- OAuth2 flow with secure token storage
- Credential loading from env var or file
- Gmail API operations (list, search, send, modify)
- Direct API mode (no MCP dependency)
"""

import os
import sys
import json
import base64
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from email.mime.text import MIMEText

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GMAIL_API_AVAILABLE = True
except ImportError:
    GMAIL_API_AVAILABLE = False


class GmailAPIHelper:
    """Gmail API helper with secure OAuth2 handling."""

    # Gmail API scopes
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify'
    ]

    def __init__(self, credentials_path: Optional[str] = None, token_path: Optional[str] = None):
        """
        Initialize Gmail API helper.

        Args:
            credentials_path: Path to OAuth2 credentials JSON (default: .secrets/gmail_credentials.json)
            token_path: Path to token storage (default: .secrets/gmail_token.json)
        """
        if not GMAIL_API_AVAILABLE:
            raise ImportError(
                "Gmail API libraries not installed. "
                "Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client"
            )

        self.base_dir = Path(__file__).parent
        self.credentials_path = Path(credentials_path) if credentials_path else self.base_dir / '.secrets' / 'gmail_credentials.json'
        self.token_path = Path(token_path) if token_path else self.base_dir / '.secrets' / 'gmail_token.json'

        self.service = None
        self.credentials = None
        self.user_email = None

    def load_credentials(self) -> bool:
        """
        Load Gmail API credentials.

        Priority:
        1. Environment variable GMAIL_OAUTH_CREDENTIALS (JSON string)
        2. Credentials file at self.credentials_path

        Returns:
            True if credentials loaded successfully
        """
        # Try environment variable first
        env_creds = os.environ.get('GMAIL_OAUTH_CREDENTIALS')
        if env_creds:
            try:
                creds_data = json.loads(env_creds)
                # Save to temp file for OAuth flow
                temp_creds = self.base_dir / '.secrets' / '.temp_credentials.json'
                temp_creds.parent.mkdir(parents=True, exist_ok=True)
                with open(temp_creds, 'w') as f:
                    json.dump(creds_data, f)
                self.credentials_path = temp_creds
                return True
            except json.JSONDecodeError:
                print("Warning: GMAIL_OAUTH_CREDENTIALS is not valid JSON")

        # Check credentials file
        if not self.credentials_path.exists():
            print()
            print("=" * 70)
            print("  GMAIL CREDENTIALS NOT FOUND")
            print("=" * 70)
            print(f"Expected location: {self.credentials_path}")
            print()
            print("Setup instructions:")
            print("1. Go to: https://console.cloud.google.com/")
            print("2. Enable Gmail API")
            print("3. Create OAuth2 credentials (Desktop app)")
            print("4. Download credentials JSON")
            print(f"5. Save as: {self.credentials_path}")
            print()
            print("OR set environment variable:")
            print("export GMAIL_OAUTH_CREDENTIALS='<credentials-json>'")
            print("=" * 70)
            return False

        return True

    def authenticate(self) -> bool:
        """
        Authenticate with Gmail API using OAuth2.

        Returns:
            True if authentication successful
        """
        if not self.load_credentials():
            return False

        creds = None

        # Load token if exists
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), self.SCOPES)
            except Exception as e:
                print(f"Warning: Failed to load token: {e}")
                creds = None

        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    print("Refreshing expired token...")
                    creds.refresh(Request())
                except Exception as e:
                    print(f"Token refresh failed: {e}")
                    creds = None

            # Run OAuth flow if needed
            if not creds:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_path), self.SCOPES)
                    creds = flow.run_local_server(port=0)
                    print()
                    print("=" * 70)
                    print("  ✓ GMAIL AUTHENTICATION SUCCESSFUL")
                    print("=" * 70)
                except Exception as e:
                    print(f"OAuth flow failed: {e}")
                    return False

            # Save token
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())
            print(f"Token saved to: {self.token_path}")

        self.credentials = creds

        # Build Gmail service
        try:
            self.service = build('gmail', 'v1', credentials=creds)

            # Get user profile
            profile = self.service.users().getProfile(userId='me').execute()
            self.user_email = profile.get('emailAddress', 'unknown')

            return True
        except Exception as e:
            print(f"Failed to build Gmail service: {e}")
            return False

    def check_auth(self) -> Dict:
        """
        Check authentication status.

        Returns:
            Dict with auth status information
        """
        if not self.authenticate():
            return {
                'status': 'failed',
                'error': 'Authentication failed'
            }

        # Get token expiry
        expiry = None
        if self.credentials and hasattr(self.credentials, 'expiry'):
            expiry = self.credentials.expiry.strftime('%Y-%m-%d %H:%M:%S UTC') if self.credentials.expiry else None

        return {
            'status': 'ok',
            'email': self.user_email,
            'token_expiry': expiry,
            'scopes': self.SCOPES
        }

    def list_messages(self, query: str = '', max_results: int = 10) -> Dict:
        """
        List Gmail messages.

        Args:
            query: Gmail query string (e.g., 'is:unread', 'newer_than:7d')
            max_results: Maximum number of messages to return

        Returns:
            Dict with messages list
        """
        if not self.service:
            if not self.authenticate():
                return {'success': False, 'error': 'Authentication failed'}

        try:
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()

            messages = results.get('messages', [])

            return {
                'success': True,
                'messages': messages,
                'count': len(messages),
                'total_estimated': results.get('resultSizeEstimate', 0)
            }
        except HttpError as error:
            return {'success': False, 'error': str(error)}

    def get_message(self, message_id: str, format: str = 'full') -> Dict:
        """
        Get Gmail message by ID.

        Args:
            message_id: Message ID
            format: Message format ('full', 'metadata', 'minimal')

        Returns:
            Dict with message data
        """
        if not self.service:
            if not self.authenticate():
                return {'success': False, 'error': 'Authentication failed'}

        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format=format
            ).execute()

            return {
                'success': True,
                'message': message
            }
        except HttpError as error:
            return {'success': False, 'error': str(error)}

    def send_email(self, to: str, subject: str, body: str, dry_run: bool = True) -> Dict:
        """
        Send email via Gmail API.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (plain text)
            dry_run: If True, don't actually send

        Returns:
            Dict with send result
        """
        if dry_run:
            return {
                'success': True,
                'mode': 'dry-run',
                'message': f'DRY-RUN: Would send email to {to}',
                'preview': {
                    'to': to,
                    'subject': subject,
                    'body': body[:100] + '...' if len(body) > 100 else body
                }
            }

        if not self.service:
            if not self.authenticate():
                return {'success': False, 'error': 'Authentication failed'}

        try:
            message = MIMEText(body, 'plain', 'utf-8')
            message['to'] = to
            message['subject'] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            result = self.service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()

            return {
                'success': True,
                'mode': 'execute',
                'message': f'Email sent to {to}',
                'message_id': result.get('id')
            }
        except HttpError as error:
            return {'success': False, 'error': str(error)}

    def get_labels(self) -> Dict:
        """
        Get Gmail labels.

        Returns:
            Dict with labels list
        """
        if not self.service:
            if not self.authenticate():
                return {'success': False, 'error': 'Authentication failed'}

        try:
            results = self.service.users().labels().list(userId='me').execute()
            labels = results.get('labels', [])

            return {
                'success': True,
                'labels': labels,
                'count': len(labels)
            }
        except HttpError as error:
            return {'success': False, 'error': str(error)}


def main():
    """CLI interface for testing Gmail API helper."""
    import argparse

    parser = argparse.ArgumentParser(description='Gmail API Helper - Test authentication')
    parser.add_argument('--check-auth', action='store_true', help='Check authentication status')
    parser.add_argument('--list', action='store_true', help='List recent messages')
    parser.add_argument('--query', default='', help='Gmail query string')
    parser.add_argument('--max-results', type=int, default=5, help='Max messages to list')

    args = parser.parse_args()

    helper = GmailAPIHelper()

    if args.check_auth:
        auth_status = helper.check_auth()
        print()
        print("=" * 70)
        print("  GMAIL AUTHENTICATION STATUS")
        print("=" * 70)
        if auth_status['status'] == 'ok':
            print(f"Status: ✓ OK")
            print(f"Email: {auth_status['email']}")
            print(f"Token Expiry: {auth_status['token_expiry']}")
            print(f"Scopes: {', '.join(auth_status['scopes'][:2])}...")
        else:
            print(f"Status: ✗ FAILED")
            print(f"Error: {auth_status.get('error', 'Unknown')}")
        print("=" * 70)
        sys.exit(0 if auth_status['status'] == 'ok' else 1)

    if args.list:
        result = helper.list_messages(query=args.query, max_results=args.max_results)
        if result['success']:
            print(f"Found {result['count']} messages")
            for msg in result.get('messages', []):
                print(f"  - {msg.get('id')}")
        else:
            print(f"Error: {result['error']}")
        sys.exit(0 if result['success'] else 1)

    # Default: check auth
    auth_status = helper.check_auth()
    if auth_status['status'] == 'ok':
        print(f"✓ Authenticated as: {auth_status['email']}")
        sys.exit(0)
    else:
        print(f"✗ Authentication failed: {auth_status.get('error')}")
        sys.exit(1)


if __name__ == '__main__':
    main()
