#!/usr/bin/env python3
"""
LinkedIn API Helper - OAuth2 Authentication + API Operations

Purpose: Provide authenticated LinkedIn API access for watcher (query) and executor (action) operations
Tier: Gold
OAuth Flow: Authorization Code Flow with PKCE (recommended for security)

Security:
- Credentials: .secrets/linkedin_credentials.json (client_id, client_secret, redirect_uri)
- Token: .secrets/linkedin_token.json (access_token, refresh_token, expires_at)
- PII redaction: All logs use redact_pii()
- Rate limiting: Exponential backoff on 429 errors

LinkedIn API Docs: https://learn.microsoft.com/en-us/linkedin/shared/authentication/authentication
"""

import json
import logging
import os
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import requests
from urllib.parse import urlencode, parse_qs, urlparse

# Configure logging
logger = logging.getLogger(__name__)

# Import mcp_helpers for PII redaction and repo root
try:
    from ai_operator.core.mcp_helpers import redact_pii, get_repo_root
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from ai_operator.core.mcp_helpers import redact_pii, get_repo_root


class LinkedInAPIError(Exception):
    """LinkedIn API error"""
    pass


class LinkedInAuthError(Exception):
    """LinkedIn authentication error"""
    pass


class LinkedInAPIHelper:
    """
    LinkedIn API helper with OAuth2 authentication.

    Features:
    - OAuth2 authorization code flow
    - Token refresh (if LinkedIn provides refresh tokens)
    - Rate limiting with exponential backoff
    - PII redaction in all logs
    - Graceful error handling
    """

    # LinkedIn OAuth2 endpoints
    AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
    TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
    USERINFO_URL = "https://api.linkedin.com/v2/userinfo"  # OpenID Connect userinfo endpoint

    # LinkedIn API base URLs
    API_BASE = "https://api.linkedin.com/v2"
    REST_BASE = "https://api.linkedin.com/rest"  # New versioned REST Posts API

    # Rate limiting defaults
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1  # seconds

    # Required API headers (LinkedIn enforces these on every call to api.linkedin.com)
    # "LinkedIn-Version" must be a calendar version string (YYYYMM).
    # Missing it causes: 403 "me.GET.NO_VERSION" / "Unsupported request version".
    # Docs: https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts/versioning
    DEFAULT_LINKEDIN_VERSION = "202601"  # January 2026 — confirmed active LinkedIn API version
    RESTLI_PROTOCOL_VERSION = "2.0.0"

    def __init__(self, secrets_dir: Optional[Path] = None):
        """
        Initialize LinkedIn API helper.

        Args:
            secrets_dir: Path to .secrets directory (default: repo_root/.secrets)
        """
        if secrets_dir is None:
            repo_root = get_repo_root()
            secrets_dir = repo_root / ".secrets"

        self.secrets_dir = Path(secrets_dir)
        self.credentials_file = self.secrets_dir / "linkedin_credentials.json"
        self.token_file = self.secrets_dir / "linkedin_token.json"

        self._credentials = None
        self._token = None

        logger.info(f"LinkedIn API helper initialized (secrets_dir={self.secrets_dir})")

    @staticmethod
    def _normalize_linkedin_version(version: str) -> str:
        """
        Normalize a LinkedIn API version string to the required YYYYMM format.

        LinkedIn requires exactly 6-digit YYYYMM versions. If a user specifies
        YYYYMMDD (8 digits, e.g. "20250201"), silently strip the day portion.
        Any other format is returned as-is and LinkedIn will reject it with 426.

        Examples:
            "20250201" → "202502"  (YYYYMMDD → YYYYMM)
            "202502"   → "202502"  (already correct)
            "202401"   → "202401"  (already correct)
        """
        v = version.strip()
        if len(v) == 8 and v.isdigit():
            return v[:6]
        return v

    def _build_headers(self, access_token: str, content_type: bool = False) -> Dict[str, str]:
        """
        Build the required headers for every LinkedIn API request.

        LinkedIn requires ALL requests to api.linkedin.com to include:
          Authorization: Bearer <token>
          LinkedIn-Version: YYYYMM          ← must be 6-digit YYYYMM (not YYYYMMDD)
          X-Restli-Protocol-Version: 2.0.0

        The ``LinkedIn-Version`` value comes from (in priority order):
          1. ``linkedin_version`` key in .secrets/linkedin_credentials.json
          2. ``api_version`` key in .secrets/linkedin_credentials.json (alias)
          3. Class constant ``DEFAULT_LINKEDIN_VERSION``

        In all cases the version is normalised via ``_normalize_linkedin_version()``
        so that YYYYMMDD (8-digit) values are silently truncated to YYYYMM.

        Args:
            access_token: Valid OAuth2 access token.
            content_type:  If True, adds ``Content-Type: application/json``.

        Returns:
            Dict of headers safe to pass directly to ``requests``.
        """
        # Resolve linkedin_version — allow override from credentials file
        # Supports both "linkedin_version" and "api_version" keys (latter per user docs)
        linkedin_version = self.DEFAULT_LINKEDIN_VERSION
        try:
            creds = self._load_credentials()
            if creds.get('linkedin_version'):
                linkedin_version = str(creds['linkedin_version'])
            elif creds.get('api_version'):
                linkedin_version = str(creds['api_version'])
        except Exception:
            pass  # credentials may not exist yet; use default

        # Normalize: YYYYMMDD → YYYYMM (LinkedIn only accepts 6-digit versions)
        linkedin_version = self._normalize_linkedin_version(linkedin_version)

        headers = {
            'Authorization': f'Bearer {access_token}',
            'LinkedIn-Version': linkedin_version,
            'X-Restli-Protocol-Version': self.RESTLI_PROTOCOL_VERSION,
        }
        if content_type:
            headers['Content-Type'] = 'application/json'
        return headers

    def _load_credentials(self) -> Dict[str, str]:
        """
        Load LinkedIn app credentials from .secrets/linkedin_credentials.json.

        Expected format:
        {
            "client_id": "YOUR_CLIENT_ID",
            "client_secret": "YOUR_CLIENT_SECRET",
            "redirect_uri": "http://localhost:8080/callback",
            "scopes": ["openid", "profile", "email", "w_member_social"]  // Optional
        }

        Returns:
            Dict with client_id, client_secret, redirect_uri, scopes (optional)

        Raises:
            LinkedInAuthError: If credentials file missing or invalid
        """
        if self._credentials is not None:
            return self._credentials

        if not self.credentials_file.exists():
            raise LinkedInAuthError(
                f"Credentials file not found: {self.credentials_file}\n"
                "Create .secrets/linkedin_credentials.json with:\n"
                '{\n'
                '  "client_id": "YOUR_CLIENT_ID",\n'
                '  "client_secret": "YOUR_CLIENT_SECRET",\n'
                '  "redirect_uri": "http://localhost:8080/callback",\n'
                '  "scopes": ["openid", "profile", "email", "w_member_social"]\n'
                '}'
            )

        try:
            with open(self.credentials_file, 'r') as f:
                creds = json.load(f)

            required_keys = ['client_id', 'client_secret', 'redirect_uri']
            missing_keys = [k for k in required_keys if k not in creds]
            if missing_keys:
                raise LinkedInAuthError(
                    f"Missing required keys in credentials file: {missing_keys}"
                )

            self._credentials = creds
            logger.info("LinkedIn credentials loaded successfully")
            return self._credentials

        except json.JSONDecodeError as e:
            raise LinkedInAuthError(f"Invalid JSON in credentials file: {e}")

    def _load_token(self) -> Optional[Dict[str, Any]]:
        """
        Load access token from .secrets/linkedin_token.json.

        Expected format:
        {
            "access_token": "TOKEN",
            "expires_at": "2026-02-18T12:00:00Z",
            "refresh_token": "REFRESH_TOKEN" (optional)
        }

        Returns:
            Dict with access_token, expires_at, refresh_token (if available), or None if not found
        """
        if self._token is not None:
            return self._token

        if not self.token_file.exists():
            logger.warning(f"Token file not found: {self.token_file}")
            return None

        try:
            with open(self.token_file, 'r') as f:
                token = json.load(f)

            self._token = token
            logger.info("LinkedIn token loaded successfully")
            return self._token

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in token file: {e}")
            return None

    def _save_token(self, token: Dict[str, Any]):
        """
        Save access token to .secrets/linkedin_token.json.

        Args:
            token: Dict with access_token, expires_in (or expires_at), refresh_token (optional)
        """
        # Ensure .secrets directory exists
        self.secrets_dir.mkdir(parents=True, exist_ok=True)

        # Calculate expires_at if not provided
        if 'expires_at' not in token and 'expires_in' in token:
            expires_in = int(token['expires_in'])
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            token['expires_at'] = expires_at.strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'

        # Auto-populate granted_scopes from raw 'scope' field if not already parsed.
        # This forward-fixes tokens created before exchange_code_for_token() added this logic.
        if 'scope' in token and 'granted_scopes' not in token:
            scope_str = token['scope']
            if isinstance(scope_str, str) and scope_str.strip():
                token['granted_scopes'] = [
                    s.strip() for s in scope_str.replace(',', ' ').split() if s.strip()
                ]
                logger.info(f"Auto-populated granted_scopes from raw scope field: {token['granted_scopes']}")

        # Save token
        with open(self.token_file, 'w') as f:
            json.dump(token, f, indent=2)

        # Secure file permissions (owner read/write only)
        os.chmod(self.token_file, 0o600)

        self._token = token
        logger.info("LinkedIn token saved successfully")

    def _is_token_expired(self, token: Dict[str, Any]) -> bool:
        """
        Check if access token is expired.

        Args:
            token: Dict with expires_at field

        Returns:
            True if expired or expires in <5 minutes
        """
        if 'expires_at' not in token:
            logger.warning("Token has no expires_at field, assuming expired")
            return True

        expires_at = datetime.fromisoformat(token['expires_at'].replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        # Consider expired if <5 minutes remaining
        time_remaining = (expires_at - now).total_seconds()
        is_expired = time_remaining < 300

        if is_expired:
            logger.info(f"Token expired or expiring soon (time_remaining={time_remaining}s)")

        return is_expired

    def get_authorization_url(self, state: Optional[str] = None, scope: Optional[str] = None) -> str:
        """
        Generate LinkedIn OAuth2 authorization URL.

        Args:
            state: Optional state parameter for CSRF protection
            scope: Optional space-separated scopes
                   Default: "openid profile email w_member_social" (OpenID Connect scopes)
                   Can be overridden via credentials file "scopes" field

        Returns:
            Authorization URL to redirect user to

        Note: User must visit this URL, authorize, and you'll receive a code at redirect_uri

        Scopes:
        - openid: OpenID Connect authentication
        - profile: Read basic profile (name, photo) via OIDC userinfo endpoint
        - email: Read email address via OIDC userinfo endpoint
        - w_member_social: Post content as user (requires "Share on LinkedIn" product)

        Legacy scopes (deprecated for new apps):
        - r_liteprofile: Read profile (replaced by "profile")
        - r_emailaddress: Read email (replaced by "email")
        """
        creds = self._load_credentials()

        if scope is None:
            # Check if scopes defined in credentials file
            if 'scopes' in creds and isinstance(creds['scopes'], list):
                scope = ' '.join(creds['scopes'])
                logger.info(f"Using scopes from credentials file: {creds['scopes']}")
            else:
                # Default to OpenID Connect scopes (recommended for new LinkedIn apps)
                # openid: OpenID Connect authentication
                # profile: Read basic profile via userinfo endpoint
                # email: Read email via userinfo endpoint
                # w_member_social: Post content as user
                scope = "openid profile email w_member_social"
                logger.info("Using default OpenID Connect scopes")

        if state is None:
            state = str(int(time.time()))

        params = {
            'response_type': 'code',
            'client_id': creds['client_id'],
            'redirect_uri': creds['redirect_uri'],
            'state': state,
            'scope': scope
        }

        auth_url = f"{self.AUTH_URL}?{urlencode(params)}"
        logger.info(f"Authorization URL generated (state={state}, scope={redact_pii(scope)})")

        return auth_url

    def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token.

        Args:
            code: Authorization code from callback URL

        Returns:
            Token dict with access_token, expires_in, refresh_token (if provided)

        Raises:
            LinkedInAuthError: If token exchange fails
        """
        creds = self._load_credentials()

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': creds['redirect_uri'],
            'client_id': creds['client_id'],
            'client_secret': creds['client_secret']
        }

        logger.info("Exchanging authorization code for access token...")

        try:
            response = requests.post(self.TOKEN_URL, data=data, timeout=30)
            response.raise_for_status()

            token = response.json()

            if 'access_token' not in token:
                raise LinkedInAuthError(f"No access_token in response: {token}")

            logger.info("Access token obtained successfully")

            # Parse and store granted_scopes from OAuth `scope` field (space/comma-separated)
            scope_str = token.get('scope', '')
            if scope_str:
                token['granted_scopes'] = [
                    s.strip() for s in scope_str.replace(',', ' ').split() if s.strip()
                ]
                logger.info(f"Granted scopes parsed: {token['granted_scopes']}")

            # Save token
            self._save_token(token)

            return token

        except requests.exceptions.RequestException as e:
            logger.error(f"Token exchange failed: {e}")
            raise LinkedInAuthError(f"Failed to exchange code for token: {e}")

    def refresh_access_token(self) -> Dict[str, Any]:
        """
        Refresh access token using refresh token (if LinkedIn provides one).

        Returns:
            New token dict

        Raises:
            LinkedInAuthError: If refresh fails or no refresh token available

        Note: LinkedIn may not provide refresh tokens. Check API docs for current behavior.
        """
        token = self._load_token()

        if token is None or 'refresh_token' not in token:
            raise LinkedInAuthError(
                "No refresh token available. User must re-authorize.\n"
                "LinkedIn may not provide refresh tokens depending on app configuration."
            )

        creds = self._load_credentials()

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': token['refresh_token'],
            'client_id': creds['client_id'],
            'client_secret': creds['client_secret']
        }

        logger.info("Refreshing access token...")

        try:
            response = requests.post(self.TOKEN_URL, data=data, timeout=30)
            response.raise_for_status()

            new_token = response.json()

            if 'access_token' not in new_token:
                raise LinkedInAuthError(f"No access_token in refresh response: {new_token}")

            logger.info("Access token refreshed successfully")

            # Save new token
            self._save_token(new_token)

            return new_token

        except requests.exceptions.RequestException as e:
            logger.error(f"Token refresh failed: {e}")
            raise LinkedInAuthError(f"Failed to refresh token: {e}")

    def get_person_id_v2_me(self) -> Optional[str]:
        """
        Call GET /v2/me and return the numeric LinkedIn member ID string.

        Caches the raw /v2/me response to .secrets/linkedin_profile.json alongside the URN.

        Returns:
            Member ID string (e.g. "ABC123") or None if /v2/me returns 401/403/other error.
        """
        access_token = self.get_access_token()
        headers = self._build_headers(access_token)

        try:
            resp = requests.get(f"{self.API_BASE}/me", headers=headers, timeout=30)
            logger.info(f"GET /v2/me → status={resp.status_code}")

            if resp.status_code == 200:
                me_data = resp.json()
                member_id = me_data.get('id')
                if member_id:
                    # Cache raw /v2/me response alongside any existing profile data
                    self._cache_profile(
                        person_urn=f"urn:li:person:{member_id}",
                        person_id=str(member_id),
                        method='v2_me',
                        raw_me=me_data,
                    )
                    logger.info(f"GET /v2/me → member id obtained: {redact_pii(str(member_id))}")
                    return str(member_id)
                logger.warning("/v2/me 200 but 'id' field missing in response")
            elif resp.status_code in (401, 403):
                logger.warning(
                    f"GET /v2/me → {resp.status_code} (scope not granted). "
                    f"Response: {resp.text[:200]}"
                )
            else:
                logger.warning(
                    f"GET /v2/me → unexpected {resp.status_code}. "
                    f"Response: {resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"GET /v2/me request failed: {e}")

        return None

    def _cache_profile(self, person_urn: str, person_id: str, method: str,
                       raw_me: Optional[Dict] = None):
        """Persist resolved person URN + optional raw /v2/me data to .secrets/linkedin_profile.json."""
        profile_file = self.secrets_dir / "linkedin_profile.json"
        try:
            self.secrets_dir.mkdir(parents=True, exist_ok=True)
            profile_data: Dict[str, Any] = {
                'person_urn': person_urn,
                'person_id': person_id,
                'method': method,
                'cached_at': datetime.now(timezone.utc).isoformat(),
            }
            if raw_me:
                profile_data['raw_me'] = raw_me
            with open(profile_file, 'w') as f:
                json.dump(profile_data, f, indent=2)
            os.chmod(profile_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not cache profile: {e}")

    def _get_granted_scopes(self) -> List[str]:
        """
        Return the union of OAuth scopes from all available sources.

        Sources (all merged — union wins so no configured scope is silently dropped):
        1. token['granted_scopes']  — pre-parsed list (added by exchange_code_for_token)
        2. token['scope']           — raw OAuth scope string returned by LinkedIn
                                      (space or comma-separated; present even on older tokens)
        3. credentials['scopes']    — configured scopes from .secrets/linkedin_credentials.json

        Using the union of all sources is intentional: LinkedIn may not echo back every
        requested scope in the token response's `scope` field (product-approval timing),
        so we never silently drop scopes the user explicitly configured.
        """
        result: set = set()

        token = self._load_token()
        if token:
            # Source 1: pre-parsed list
            gs = token.get('granted_scopes')
            if isinstance(gs, list):
                result.update(gs)

            # Source 2: raw 'scope' string from LinkedIn token response
            scope_str = token.get('scope', '')
            if scope_str and isinstance(scope_str, str):
                result.update(
                    s.strip() for s in scope_str.replace(',', ' ').split() if s.strip()
                )

        # Source 3: credentials configured scopes
        try:
            creds = self._load_credentials()
            scopes = creds.get('scopes', [])
            if isinstance(scopes, list):
                result.update(scopes)
        except Exception:
            pass

        return list(result)

    def get_author_urn(self) -> str:
        """
        Return the LinkedIn URN of the authenticated member for posting/querying.

        Unlike ``get_person_urn()``, this method uses OIDC ``sub`` as a fallback
        when ``/v2/me`` is blocked (HTTP 401/403), enabling real-mode posting without
        the legacy ``r_liteprofile`` scope.

        Resolution order:
        1. In-memory cache (_author_urn).
        2. On-disk cache .secrets/linkedin_profile.json (any method).
        3. Live GET /v2/me call.
        4. OIDC /v2/userinfo ``sub`` field → ``urn:li:person:<sub>``.

        Returns:
            Full person URN string, e.g. "urn:li:person:ABC123xyz"

        Raises:
            LinkedInAuthError: If none of the above sources can produce a URN.
        """
        # In-memory cache
        if getattr(self, '_author_urn', None):
            return self._author_urn

        # On-disk cache (any method)
        profile_file = self.secrets_dir / "linkedin_profile.json"
        if profile_file.exists():
            try:
                with open(profile_file, 'r') as f:
                    cached = json.load(f)
                if cached.get('person_urn'):
                    self._author_urn = cached['person_urn']
                    logger.info(
                        f"Author URN loaded from cache (method={cached.get('method')}): "
                        f"{redact_pii(self._author_urn)}"
                    )
                    return self._author_urn
            except Exception:
                pass

        # Try /v2/me
        member_id = self.get_person_id_v2_me()
        if member_id:
            self._author_urn = f"urn:li:person:{member_id}"
            # get_person_id_v2_me() already caches to disk as method='v2_me'
            logger.info(f"Author URN resolved via /v2/me: {redact_pii(self._author_urn)}")
            return self._author_urn

        # OIDC sub fallback — works when /v2/me is blocked
        logger.info("GET /v2/me unavailable — falling back to OIDC sub for author URN")
        try:
            auth_result = self.check_auth()
            sub = auth_result.get('profile', {}).get('sub')
            if sub:
                self._author_urn = f"urn:li:person:{sub}"
                self._cache_profile(
                    person_urn=self._author_urn,
                    person_id=sub,
                    method='oidc_sub',
                )
                logger.info(f"Author URN derived from OIDC sub: {redact_pii(self._author_urn)}")
                return self._author_urn
        except Exception as exc:
            logger.warning(f"OIDC sub fallback failed: {exc}")

        raise LinkedInAuthError(
            "Cannot resolve author URN: /v2/me blocked and OIDC sub unavailable.\n"
            "Run: python3 scripts/linkedin_oauth_helper.py --init"
        )

    def get_person_urn(self) -> str:
        """
        Return the authenticated member's LinkedIn URN for use with API endpoints.

        **Requires a valid /v2/me response** (numeric member id).  OIDC ``sub`` is
        NOT used here because LinkedIn's sharing/UGC endpoints require the numeric id
        from /v2/me, not the opaque OIDC subject identifier.

        Resolution order:
        1. In-memory cache (only when previously resolved via v2_me).
        2. On-disk cache .secrets/linkedin_profile.json (only when method=v2_me).
        3. Live GET /v2/me call.

        If /v2/me returns 401 or 403 a ``LinkedInAuthError`` is raised with
        actionable instructions — the caller must NOT fall back to OIDC sub.

        Returns:
            Full person URN string, e.g. "urn:li:person:123456789"

        Raises:
            LinkedInAuthError: If /v2/me fails (401/403) or returns no ``id`` field.
        """
        # In-memory cache — only trust it when it came from v2_me
        if (
            hasattr(self, '_person_urn') and self._person_urn
            and getattr(self, '_person_urn_method', None) == 'v2_me'
        ):
            return self._person_urn

        # On-disk cache — only trust when written by /v2/me call
        profile_file = self.secrets_dir / "linkedin_profile.json"
        if profile_file.exists():
            try:
                with open(profile_file, 'r') as f:
                    cached = json.load(f)
                if cached.get('person_urn') and cached.get('method') == 'v2_me':
                    self._person_urn = cached['person_urn']
                    self._person_urn_method = 'v2_me'
                    logger.info(
                        f"Person URN loaded from cache (v2_me): {redact_pii(self._person_urn)}"
                    )
                    return self._person_urn
                if cached.get('method') == 'oidc_sub':
                    logger.info(
                        "Cached URN was derived from OIDC sub — bypassing cache, "
                        "calling /v2/me for strict numeric member id."
                    )
            except Exception:
                pass

        # Live /v2/me call
        member_id = self.get_person_id_v2_me()

        if not member_id:
            # get_person_id_v2_me() already logged the status code and response snippet.
            raise LinkedInAuthError(
                "GET /v2/me did not return a numeric member id.\n"
                "\n"
                "This usually means the token was issued BEFORE the LinkedIn products\n"
                "were enabled, so it lacks the required scopes.\n"
                "\n"
                "FIX — re-run OAuth to get a fresh token with the correct scopes:\n"
                "  python3 scripts/linkedin_oauth_helper.py --init\n"
                "\n"
                "Then confirm products are enabled in LinkedIn Developer Console:\n"
                "  - Sign In with LinkedIn using OpenID Connect\n"
                "  - Share on LinkedIn\n"
                "\n"
                "After re-auth, verify with:\n"
                "  python3 scripts/linkedin_oauth_helper.py --whoami\n"
                "  python3 scripts/linkedin_oauth_helper.py --test-endpoints"
            )

        self._person_urn = f"urn:li:person:{member_id}"
        self._person_urn_method = 'v2_me'
        logger.info(f"Person URN resolved (v2_me): {redact_pii(self._person_urn)}")
        return self._person_urn

    def get_access_token(self) -> str:
        """
        Get valid access token (refresh if expired).

        Returns:
            Valid access token

        Raises:
            LinkedInAuthError: If no token available or refresh fails
        """
        token = self._load_token()

        if token is None:
            raise LinkedInAuthError(
                "No access token found. Run OAuth flow first:\n"
                "1. Get authorization URL: helper.get_authorization_url()\n"
                "2. User authorizes at that URL\n"
                "3. Exchange code: helper.exchange_code_for_token(code)"
            )

        # Check if expired
        if self._is_token_expired(token):
            logger.info("Token expired, attempting refresh...")
            try:
                token = self.refresh_access_token()
            except LinkedInAuthError:
                logger.error("Token refresh failed. User must re-authorize.")
                raise

        return token['access_token']

    def check_auth(self) -> Dict[str, Any]:
        """
        Check authentication status and get user profile.

        Supports both:
        - OpenID Connect (OIDC) tokens: Uses /v2/userinfo endpoint
        - Legacy tokens: Falls back to /v2/me endpoint

        Returns:
            Dict with status, profile (if authenticated), error (if failed)

        Example response:
        {
            "status": "authenticated",
            "profile": {"sub": "...", "name": "...", "email": "..."},  # OIDC
            "auth_method": "oidc"
        }
        OR
        {
            "status": "authenticated",
            "profile": {"id": "...", "localizedFirstName": "...", ...},  # Legacy
            "auth_method": "legacy"
        }
        OR
        {
            "status": "unauthenticated",
            "error": "No token found"
        }
        """
        try:
            access_token = self.get_access_token()
            headers = self._build_headers(access_token)

            # Try OpenID Connect userinfo endpoint first (for OIDC tokens)
            try:
                response = requests.get(
                    self.USERINFO_URL,
                    headers=headers,
                    timeout=30
                )

                if response.status_code == 200:
                    profile = response.json()
                    logger.info(f"OIDC authentication verified (sub={profile.get('sub', 'unknown')})")
                    return {
                        'status': 'authenticated',
                        'profile': profile,
                        'auth_method': 'oidc'
                    }
                elif response.status_code == 401:
                    logger.warning("OIDC userinfo returned 401, trying legacy /me endpoint")
                else:
                    logger.warning(f"OIDC userinfo failed ({response.status_code}), trying legacy /me endpoint")

            except Exception as e:
                logger.warning(f"OIDC userinfo request failed: {e}, trying legacy /me endpoint")

            # Fall back to legacy /me endpoint (for old token scopes)
            # headers already contain all required fields from _build_headers()

            response = requests.get(
                f"{self.API_BASE}/me",
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                profile = response.json()
                logger.info(f"Legacy authentication verified (user_id={profile.get('id', 'unknown')})")
                return {
                    'status': 'authenticated',
                    'profile': profile,
                    'auth_method': 'legacy'
                }
            elif response.status_code == 401:
                logger.error("Token invalid (401 Unauthorized)")
                return {
                    'status': 'unauthenticated',
                    'error': '401 Unauthorized - Token invalid or expired'
                }
            else:
                logger.error(f"Auth check failed: {response.status_code} {response.text}")
                return {
                    'status': 'error',
                    'error': f"{response.status_code}: {response.text}"
                }

        except LinkedInAuthError as e:
            logger.error(f"Auth check failed: {e}")
            return {
                'status': 'unauthenticated',
                'error': str(e)
            }
        except Exception as e:
            logger.error(f"Auth check failed: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }

    def _api_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make authenticated API request with rate limiting and retries.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/me")
            **kwargs: Additional arguments for requests (json, params, etc.)

        Returns:
            Response object

        Raises:
            LinkedInAPIError: If request fails after retries
        """
        access_token = self.get_access_token()
        has_json = 'json' in kwargs
        headers = self._build_headers(access_token, content_type=has_json)
        # Allow caller-supplied header overrides (rare)
        caller_headers = kwargs.pop('headers', {})
        headers.update(caller_headers)

        url = f"{self.API_BASE}{endpoint}" if endpoint.startswith('/') else f"{self.API_BASE}/{endpoint}"

        # Retry loop with exponential backoff
        backoff = self.INITIAL_BACKOFF
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.debug(f"API request: {method} {url} (attempt {attempt + 1}/{self.MAX_RETRIES})")

                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    timeout=30,
                    **kwargs
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', backoff))
                    logger.warning(f"Rate limited (429). Retrying after {retry_after}s...")
                    time.sleep(retry_after)
                    backoff *= 2
                    continue

                # Raise for other errors
                response.raise_for_status()

                return response

            except requests.exceptions.RequestException as e:
                logger.error(f"API request failed (attempt {attempt + 1}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    raise LinkedInAPIError(f"API request failed after {self.MAX_RETRIES} attempts: {e}")

        raise LinkedInAPIError(f"API request failed after {self.MAX_RETRIES} attempts")

    def _api_request_raw(self, method: str, endpoint: str, *, base: Optional[str] = None,
                         **kwargs) -> requests.Response:
        """
        Make an authenticated API request and return the raw response WITHOUT raising.

        Handles 429 rate limiting with backoff.  For 4xx/5xx responses the caller is
        responsible for checking ``response.status_code``; no exception is raised for
        client/server errors (only for network-level failures).

        This exists so that callers that implement endpoint fallback logic can inspect
        the status code before deciding whether to try a secondary endpoint.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path (e.g. "/posts")
            base: Optional base URL override (default: self.API_BASE).
                  Use self.REST_BASE for the new REST Posts API endpoints.
        """
        access_token = self.get_access_token()
        has_json = 'json' in kwargs
        headers = self._build_headers(access_token, content_type=has_json)
        caller_headers = kwargs.pop('headers', {})
        headers.update(caller_headers)

        api_base = base if base is not None else self.API_BASE
        url = f"{api_base}{endpoint}" if endpoint.startswith('/') else f"{api_base}/{endpoint}"

        backoff = self.INITIAL_BACKOFF
        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.request(method, url, headers=headers, timeout=30, **kwargs)

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', backoff))
                    logger.warning(f"Rate limited (429). Retrying after {retry_after}s...")
                    time.sleep(retry_after)
                    backoff *= 2
                    continue

                # Return immediately — caller checks status_code
                return response

            except requests.exceptions.RequestException as e:
                last_exc = e
                logger.warning(f"Network error (attempt {attempt + 1}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= 2

        raise LinkedInAPIError(
            f"Network request failed after {self.MAX_RETRIES} attempts: {last_exc}"
        )

    @staticmethod
    def _normalize_post(post: Dict[str, Any], source: str) -> Dict[str, Any]:
        """
        Normalize a raw API post dict from either ugcPosts or shares into a unified structure.

        Unified fields:
            id, author_urn, text, created_ms, source_endpoint
        """
        if source == 'ugcPosts':
            text = (
                post.get('specificContent', {})
                    .get('com.linkedin.ugc.ShareContent', {})
                    .get('shareCommentary', {})
                    .get('text', '')
            )
            author_urn = post.get('author', '')
            created_ms = post.get('created', {}).get('time', 0)
        else:  # shares
            text = post.get('text', {}).get('text', '') if isinstance(post.get('text'), dict) else post.get('text', '')
            author_urn = post.get('owner', '')
            created_ms = post.get('created', {}).get('time', 0)

        return {
            'id': post.get('id', ''),
            'author_urn': author_urn,
            'text': text,
            'created_ms': created_ms,
            'source_endpoint': source,
            '_raw': post,
        }

    # ============================================================================
    # QUERY METHODS (Perception - Read-Only)
    # ============================================================================

    def list_notifications(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        List recent notifications (if API supports it).

        Note: LinkedIn Social API may not provide notifications endpoint.
        Check current API documentation.

        Args:
            limit: Max number of notifications to return

        Returns:
            List of notification dicts
        """
        logger.warning("LinkedIn notifications endpoint may not be available in current API")

        # Placeholder - actual endpoint depends on LinkedIn API version
        # This is a mock implementation
        return []

    def list_ugc_posts(self, author_urn: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        List recent UGC posts by author (raw API response elements).

        Prefer ``list_posts()`` which transparently falls back to /v2/shares on 404/403.
        """
        params = {'q': 'author', 'author': author_urn, 'count': limit}
        endpoint = '/ugcPosts'

        try:
            response = self._api_request('GET', endpoint, params=params)
            data = response.json()
            posts = data.get('elements', [])
            logger.info(
                f"GET {endpoint} → {response.status_code} | "
                f"{len(posts)} posts for author={redact_pii(author_urn)}"
            )
            if not posts:
                logger.warning(f"GET {endpoint} returned 0 posts. Response: {str(data)[:300]}")
            return posts
        except LinkedInAPIError as e:
            logger.error(f"GET {endpoint} failed for author={redact_pii(author_urn)}: {e}")
            return []

    def list_posts(self, author_urn: str, count: int = 10) -> List[Dict[str, Any]]:
        """
        List recent posts by author with automatic endpoint fallback.

        Tries endpoints in order:
        1. GET /v2/ugcPosts?q=author&author=<urn>&count=<count>
        2. GET /v2/shares?q=owners&owners=<urn>&count=<count>  (if ugcPosts → 404 or 403)

        Both responses are normalised into a unified structure via ``_normalize_post()``.

        Returns:
            List of normalised post dicts (may be empty if both endpoints fail).
        """
        redacted_urn = redact_pii(author_urn)

        # --- Attempt 1: ugcPosts ---
        ugc_params = {'q': 'author', 'author': author_urn, 'count': count}
        try:
            resp = self._api_request_raw('GET', '/ugcPosts', params=ugc_params)
            logger.info(f"GET /ugcPosts → status={resp.status_code} for author={redacted_urn}")

            if resp.status_code == 200:
                elements = resp.json().get('elements', [])
                posts = [self._normalize_post(p, 'ugcPosts') for p in elements]
                logger.info(f"GET /ugcPosts → {len(posts)} posts retrieved (endpoint: ugcPosts)")
                if not posts:
                    logger.warning(
                        f"GET /ugcPosts returned 0 posts. "
                        f"Response snippet: {resp.text[:300]}"
                    )
                return posts

            if resp.status_code in (403, 404):
                logger.warning(
                    f"GET /ugcPosts → {resp.status_code} "
                    f"({resp.text[:200]}). Falling back to /v2/shares."
                )
            else:
                logger.warning(
                    f"GET /ugcPosts → unexpected {resp.status_code} "
                    f"({resp.text[:200]}). Falling back to /v2/shares."
                )

        except LinkedInAPIError as e:
            logger.warning(f"GET /ugcPosts network error: {e}. Falling back to /v2/shares.")

        # --- Attempt 2: shares (fallback) ---
        shares_params = {'q': 'owners', 'owners': author_urn, 'count': count}
        try:
            resp = self._api_request_raw('GET', '/shares', params=shares_params)
            logger.info(f"GET /shares → status={resp.status_code} for author={redacted_urn}")

            if resp.status_code == 200:
                elements = resp.json().get('elements', [])
                posts = [self._normalize_post(p, 'shares') for p in elements]
                logger.info(f"GET /shares → {len(posts)} posts retrieved (endpoint: shares)")
                if not posts:
                    logger.warning(
                        f"GET /shares returned 0 posts. "
                        f"Response snippet: {resp.text[:300]}"
                    )
                return posts

            logger.error(
                f"GET /shares → {resp.status_code} ({resp.text[:300]}). "
                f"Both endpoints failed for author={redacted_urn}."
            )

        except LinkedInAPIError as e:
            logger.error(f"GET /shares network error: {e}. Both endpoints failed.")

        return []

    # ============================================================================
    # ACTION METHODS (Execution - Write Operations)
    # ============================================================================

    def _log_post_action(self, post_urn: str, text: str, visibility: str) -> None:
        """Append a JSON line to Logs/mcp_actions.log on successful post creation."""
        try:
            log_path = get_repo_root() / 'Logs' / 'mcp_actions.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'tool': 'linkedin.create_post',
                'endpoint': 'rest/posts',
                'post_urn': post_urn,
                'visibility': visibility,
                'text_length': len(text),
                'status': 'success',
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as exc:
            logger.warning(f"Could not write to mcp_actions.log: {exc}")

    def create_post(self, text: str, visibility: str = "PUBLIC") -> Dict[str, Any]:
        """
        Create a LinkedIn post using the official REST Posts API.

        Endpoint: POST https://api.linkedin.com/rest/posts

        Required headers (all supplied by _build_headers):
            Authorization: Bearer <token>
            Content-Type: application/json
            LinkedIn-Version: YYYYMM
            X-Restli-Protocol-Version: 2.0.0

        Args:
            text: Post text content (commentary)
            visibility: "PUBLIC" (default) or "CONNECTIONS"

        Returns:
            Dict with id (post URN), author, visibility, endpoint_used

        Raises:
            LinkedInAPIError: If posting fails (HTTP error or network error)
        """
        author_urn = self.get_author_urn()

        payload = {
            "author": author_urn,
            "commentary": text,
            "visibility": visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        logger.info(
            f"Creating LinkedIn post via REST Posts API "
            f"(visibility={visibility}, text_length={len(text)})"
        )

        try:
            resp = self._api_request_raw(
                'POST', '/posts',
                base=self.REST_BASE,
                json=payload,
            )
            logger.info(f"POST rest/posts → status={resp.status_code}")

            if resp.status_code in (200, 201):
                # LinkedIn REST Posts API returns the post URN in x-restli-id header
                post_urn = resp.headers.get('x-restli-id', '')
                if not post_urn:
                    # Fall back to response body
                    try:
                        post_urn = resp.json().get('id', 'unknown')
                    except Exception:
                        post_urn = 'unknown'

                logger.info(f"Post created via REST Posts API (urn={redact_pii(post_urn)})")
                self._log_post_action(post_urn=post_urn, text=text, visibility=visibility)

                return {
                    'id': post_urn,
                    'author': author_urn,
                    'commentary': text,
                    'visibility': visibility,
                    'endpoint_used': 'rest/posts',
                }

            # Non-2xx response — build structured error and raise
            error_msg = (
                f"POST rest/posts → HTTP {resp.status_code}: {resp.text[:300]}"
            )
            logger.error(error_msg)
            raise LinkedInAPIError(error_msg)

        except LinkedInAPIError:
            raise
        except requests.exceptions.RequestException as exc:
            error_msg = f"POST rest/posts → network error: {exc}"
            logger.error(error_msg)
            raise LinkedInAPIError(error_msg)

    def check_read_access(self) -> Dict[str, Any]:
        """
        Probe whether LinkedIn post-read access is available.

        Makes a minimal GET /v2/ugcPosts call.

        Returns:
            {
                'available': bool,
                'status':    int   (HTTP status code, -1 = network error, -2 = URN error),
                'reason':    str   (human-readable explanation when available=False),
            }
        """
        try:
            author_urn = self.get_author_urn()
        except LinkedInAuthError as exc:
            return {'available': False, 'status': -2, 'reason': str(exc)}

        try:
            resp = self._api_request_raw(
                'GET', '/ugcPosts',
                params={'q': 'author', 'author': author_urn, 'count': 1},
            )
            if resp.status_code == 403:
                return {
                    'available': False,
                    'status': 403,
                    'reason': 'r_member_social not granted (LinkedIn requires special access approval)',
                }
            # 200 or 404 (no posts yet) — read access is available
            return {'available': True, 'status': resp.status_code, 'reason': ''}
        except Exception as exc:
            return {'available': False, 'status': -1, 'reason': f'Network error: {exc}'}

    def check_can_post(self) -> Dict[str, Any]:
        """
        Check whether posting (w_member_social) is available.

        Only verifies the scope is present — no network call is made.
        Uses _get_granted_scopes() which reads from token['granted_scopes'] first,
        then falls back to credentials['scopes'].

        Returns:
            {'available': bool, 'reason': str}
        """
        scopes = self._get_granted_scopes()
        if 'w_member_social' not in scopes:
            return {
                'available': False,
                'reason': 'w_member_social scope not in configured scopes',
            }
        return {'available': True, 'reason': ''}

    def upload_image(self, image_bytes: bytes, mime_type: str = 'image/jpeg') -> str:
        """
        Upload an image to LinkedIn via the REST Images API.

        Two-step upload:
        1. POST /rest/images?action=initializeUpload → get uploadUrl + image URN
        2. PUT <uploadUrl> with image bytes → 201

        Args:
            image_bytes: Raw image bytes (JPEG recommended)
            mime_type:   MIME type for the upload (default: image/jpeg)

        Returns:
            LinkedIn image URN (e.g. "urn:li:image:xxx")

        Raises:
            LinkedInAPIError: If initialization or upload fails
        """
        author_urn = self.get_author_urn()

        # Step 1: Initialize upload
        init_payload = {
            "initializeUploadRequest": {
                "owner": author_urn,
            }
        }

        logger.info("Initializing LinkedIn image upload...")
        init_resp = self._api_request_raw(
            'POST',
            '/images?action=initializeUpload',
            base=self.REST_BASE,
            json=init_payload,
        )

        if init_resp.status_code not in (200, 201):
            raise LinkedInAPIError(
                f"Image upload initialization failed: HTTP {init_resp.status_code}: "
                f"{init_resp.text[:300]}"
            )

        init_data = init_resp.json()
        value = init_data.get('value', {})
        upload_url = value.get('uploadUrl', '')
        image_urn = value.get('image', '')

        if not upload_url or not image_urn:
            raise LinkedInAPIError(
                f"Image upload initialization returned unexpected response: {init_data}"
            )

        logger.info(f"Image upload initialized (urn={redact_pii(image_urn)})")

        # Step 2: PUT image bytes to upload URL
        access_token = self.get_access_token()
        put_headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': mime_type,
        }

        put_resp = requests.put(
            upload_url,
            headers=put_headers,
            data=image_bytes,
            timeout=60,
        )

        if put_resp.status_code not in (200, 201):
            raise LinkedInAPIError(
                f"Image PUT upload failed: HTTP {put_resp.status_code}: {put_resp.text[:300]}"
            )

        logger.info(f"Image uploaded successfully (urn={redact_pii(image_urn)})")

        # Log the upload action
        try:
            log_path = get_repo_root() / 'Logs' / 'mcp_actions.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'tool': 'linkedin.upload_image',
                'endpoint': 'rest/images',
                'image_urn': image_urn,
                'mime_type': mime_type,
                'size_bytes': len(image_bytes),
                'status': 'success',
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as exc:
            logger.warning(f"Could not write image upload to mcp_actions.log: {exc}")

        return image_urn

    def create_post_with_image(
        self,
        text: str,
        image_urn: str,
        image_title: str = '',
        visibility: str = 'PUBLIC',
    ) -> Dict[str, Any]:
        """
        Create a LinkedIn post with an embedded image.

        Uses the REST Posts API with a content.media block.

        Args:
            text:        Post commentary text
            image_urn:   LinkedIn image URN (from upload_image())
            image_title: Optional alt-text / title for the image
            visibility:  "PUBLIC" (default) or "CONNECTIONS"

        Returns:
            Dict with id (post URN), author, visibility, image_urn, endpoint_used

        Raises:
            LinkedInAPIError: If posting fails
        """
        author_urn = self.get_author_urn()

        payload = {
            "author": author_urn,
            "commentary": text,
            "visibility": visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {
                "media": {
                    "id": image_urn,
                    "title": image_title,
                }
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        logger.info(
            f"Creating LinkedIn post with image via REST Posts API "
            f"(visibility={visibility}, text_length={len(text)}, image_urn={redact_pii(image_urn)})"
        )

        try:
            resp = self._api_request_raw(
                'POST', '/posts',
                base=self.REST_BASE,
                json=payload,
            )
            logger.info(f"POST rest/posts+image → status={resp.status_code}")

            if resp.status_code in (200, 201):
                post_urn = resp.headers.get('x-restli-id', '')
                if not post_urn:
                    try:
                        post_urn = resp.json().get('id', 'unknown')
                    except Exception:
                        post_urn = 'unknown'

                logger.info(f"Post with image created (urn={redact_pii(post_urn)})")

                # Log action
                try:
                    log_path = get_repo_root() / 'Logs' / 'mcp_actions.log'
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    entry = {
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'tool': 'linkedin.create_post_with_image',
                        'endpoint': 'rest/posts+image',
                        'post_urn': post_urn,
                        'image_urn': image_urn,
                        'visibility': visibility,
                        'text_length': len(text),
                        'status': 'success',
                    }
                    with open(log_path, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(entry) + '\n')
                except Exception as exc:
                    logger.warning(f"Could not write to mcp_actions.log: {exc}")

                return {
                    'id': post_urn,
                    'author': author_urn,
                    'commentary': text,
                    'visibility': visibility,
                    'image_urn': image_urn,
                    'endpoint_used': 'rest/posts+image',
                }

            error_msg = f"POST rest/posts+image → HTTP {resp.status_code}: {resp.text[:300]}"
            logger.error(error_msg)
            raise LinkedInAPIError(error_msg)

        except LinkedInAPIError:
            raise
        except requests.exceptions.RequestException as exc:
            error_msg = f"POST rest/posts+image → network error: {exc}"
            logger.error(error_msg)
            raise LinkedInAPIError(error_msg)

    def reply_comment(self, comment_id: str, text: str) -> Dict[str, Any]:
        """
        Reply to a comment (if API supports it).

        Note: LinkedIn Social API may have limited comment interaction.
        Check current API documentation.

        Args:
            comment_id: Comment URN
            text: Reply text

        Returns:
            Reply dict
        """
        logger.warning("LinkedIn comment reply endpoint may not be available in current API")
        raise LinkedInAPIError("Comment reply not implemented - check LinkedIn API docs for current capabilities")

    def send_message(self, recipient_urn: str, text: str) -> Dict[str, Any]:
        """
        Send a direct message (if API supports it).

        Note: LinkedIn Messaging API may require additional permissions/review.
        Check current API documentation.

        Args:
            recipient_urn: Recipient URN (e.g., "urn:li:person:ABC123")
            text: Message text

        Returns:
            Message dict
        """
        logger.warning("LinkedIn messaging endpoint may require additional permissions")
        raise LinkedInAPIError("Messaging not implemented - check LinkedIn API docs for Messaging API access")


def run_oauth_server_and_get_code(auth_url: str, timeout: int = 300) -> Optional[str]:
    """
    Start local HTTP server on port 8080, open browser, and wait for OAuth callback.

    Args:
        auth_url: Authorization URL to open in browser
        timeout: Max seconds to wait for callback (default 5 minutes)

    Returns:
        Authorization code or None if timeout/error
    """
    import webbrowser
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    code_container = {'code': None, 'error': None}

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        """Handle OAuth callback from LinkedIn"""

        def log_message(self, format, *args):
            """Suppress default logging"""
            pass

        def do_GET(self):
            """Handle GET request (OAuth callback)"""
            try:
                parsed = urlparse(self.path)
                query_params = parse_qs(parsed.query)

                if 'code' in query_params:
                    code_container['code'] = query_params['code'][0]

                    # Send success response
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()

                    success_html = """
                    <html>
                    <head><title>LinkedIn Authorization Success</title></head>
                    <body style="font-family: Arial; text-align: center; padding-top: 100px;">
                        <h1 style="color: green;">✅ Authorization Successful!</h1>
                        <p>You can close this window and return to the terminal.</p>
                    </body>
                    </html>
                    """
                    self.wfile.write(success_html.encode())

                elif 'error' in query_params:
                    code_container['error'] = query_params['error'][0]

                    # Send error response
                    self.send_response(400)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()

                    error_html = f"""
                    <html>
                    <head><title>LinkedIn Authorization Error</title></head>
                    <body style="font-family: Arial; text-align: center; padding-top: 100px;">
                        <h1 style="color: red;">❌ Authorization Failed</h1>
                        <p>Error: {query_params['error'][0]}</p>
                        <p>Please close this window and try again.</p>
                    </body>
                    </html>
                    """
                    self.wfile.write(error_html.encode())

            except Exception as e:
                logger.error(f"Callback handler error: {e}")
                code_container['error'] = str(e)

    try:
        # Start server on port 8080
        server = HTTPServer(('localhost', 8080), OAuthCallbackHandler)
        server.timeout = timeout

        # Run server in background thread
        server_thread = threading.Thread(target=server.handle_request)
        server_thread.daemon = True
        server_thread.start()

        print(f"\n🌐 Opening browser for LinkedIn authorization...")
        print(f"   (Local server listening on http://localhost:8080)")

        # Open browser with authorization URL
        webbrowser.open(auth_url)

        # Wait for callback or timeout
        server_thread.join(timeout)

        # Shutdown server
        server.server_close()

        if code_container['code']:
            return code_container['code']
        elif code_container['error']:
            print(f"\n❌ Authorization error: {code_container['error']}")
            return None
        else:
            print(f"\n⏱️  Timeout waiting for authorization callback ({timeout}s)")
            return None

    except OSError as e:
        if 'Address already in use' in str(e):
            print(f"\n❌ Port 8080 is already in use. Please close other applications using this port.")
        else:
            print(f"\n❌ Server error: {e}")
        return None
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return None


def test_endpoints(execute: bool = False):
    """
    5-step LinkedIn API endpoint diagnostic.

    Step 1: OIDC /v2/userinfo          — verifies token is valid
    Step 2: Author URN resolution      — /v2/me with OIDC sub fallback
    Step 3: Post validation (dry-run)  — confirms w_member_social scope + URN ready
    Step 4: POST rest/posts            — real post (only when execute=True)
    Step 5: Read endpoints             — informational; failure is NOT fatal
    """
    SEP = "=" * 70
    print(SEP)
    print("LinkedIn API Endpoint Diagnostics (--test-endpoints)")
    print(SEP)

    helper = LinkedInAPIHelper()

    # ── Token check ──────────────────────────────────────────────────────────
    try:
        access_token = helper.get_access_token()
    except Exception as e:
        print(f"\n❌ Cannot load access token: {e}")
        print(f"   Run: python3 scripts/linkedin_oauth_helper.py --init")
        print(f"\n{SEP}")
        return

    headers = helper._build_headers(access_token)
    print(f"   LinkedIn-Version          : {headers.get('LinkedIn-Version', 'MISSING')}")
    print(f"   X-Restli-Protocol-Version : {headers.get('X-Restli-Protocol-Version', 'MISSING')}")

    # ── [1/5] OIDC /v2/userinfo ───────────────────────────────────────────
    print("\n[1/5] OIDC /v2/userinfo")
    oidc_sub = None
    try:
        resp = requests.get(helper.USERINFO_URL, headers=headers, timeout=30)
        if resp.status_code == 200:
            profile = resp.json()
            oidc_sub = profile.get('sub', '')
            name = profile.get('name', profile.get('given_name', ''))
            print(f"  ✅ OK — sub={oidc_sub}, name={name}")
        else:
            print(f"  ❌ HTTP {resp.status_code}: {resp.text[:200]}")
            print(f"     Fix: Run --init to refresh token")
    except Exception as e:
        print(f"  ❌ Network error: {e}")

    # ── [2/5] Author URN ──────────────────────────────────────────────────
    print("\n[2/5] Author URN Resolution (/v2/me with OIDC sub fallback)")
    author_urn = None
    try:
        author_urn = helper.get_author_urn()
        profile_file = helper.secrets_dir / "linkedin_profile.json"
        method = 'unknown'
        if profile_file.exists():
            try:
                with open(profile_file) as f:
                    cached = json.load(f)
                method = cached.get('method', 'unknown')
            except Exception:
                pass
        print(f"  ✅ URN={author_urn} (method={method})")
    except Exception as e:
        print(f"  ❌ URN resolution failed: {e}")
        if oidc_sub:
            print(f"     Note: OIDC sub={oidc_sub} is available")

    # ── [3/5] Post validation (dry-run) ──────────────────────────────────
    print("\n[3/5] Post Validation (dry-run — no content is sent)")
    can_post = False
    if author_urn:
        post_result = helper.check_can_post()
        if post_result['available']:
            print(f"  ✅ w_member_social scope confirmed, author URN ready — can post")
            can_post = True
        else:
            print(f"  ❌ Cannot post: {post_result.get('reason', 'unknown')}")
    else:
        print(f"  ⚠️  Skipped — no author URN resolved")

    # ── [4/5] Real post (only with --execute) ─────────────────────────────
    if execute:
        print("\n[4/5] POST rest/posts (--execute)")
        if can_post and author_urn:
            ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            test_text = f"[ENDPOINT TEST] Automated connectivity check | {ts}"
            try:
                result = helper.create_post(test_text)
                print(f"  ✅ Posted! URN={result.get('id')}, endpoint={result.get('endpoint_used')}")
            except Exception as e:
                print(f"  ❌ Post failed: {e}")
        else:
            print(f"  ⚠️  Skipped — posting not available")
    else:
        print(f"\n[4/5] POST rest/posts → skipped (dry-run; use --execute to actually post)")

    # ── [5/5] Read endpoints (informational) ──────────────────────────────
    print("\n[5/5] Read Endpoints (informational — failure is expected for most apps)")
    if author_urn:
        read_result = helper.check_read_access()
        if read_result['available']:
            print(f"  ✅ Read OK (status={read_result['status']})")
        else:
            status = read_result.get('status', -1)
            reason = read_result.get('reason', 'unknown')
            print(f"  ⚠️  Read unavailable (status={status}): {reason}")
            print(f"     Expected — r_member_social requires LinkedIn partner program approval")
            print(f"     Posting is unaffected by this restriction.")
    else:
        print(f"  ⚠️  Skipped — no author URN")

    print(f"\n{SEP}")
    print("Summary:")
    print(f"  OIDC userinfo : {'OK' if oidc_sub else 'FAIL'}")
    print(f"  Author URN    : {'OK (' + author_urn + ')' if author_urn else 'FAIL'}")
    print(f"  Can Post      : {'YES' if can_post else 'NO'}")
    print(f"{SEP}\n")


def post_test(text: str, execute: bool = True):
    """
    Test posting to LinkedIn via REST Posts API.

    Args:
        text:    Post text content (prefixed with [TEST POST] + timestamp when execute=True)
        execute: If True (default), send a real post; if False, dry-run validation only.

    Always validates: auth OK, author URN resolved, w_member_social scope present.
    When execute=True: sends to POST https://api.linkedin.com/rest/posts.
    When execute=False: validates pipeline without sending (use --dry-run CLI flag).
    """
    SEP = "-" * 60
    print(SEP)
    print("LinkedIn Post Test")
    print(SEP)

    helper = LinkedInAPIHelper()

    # Step 1: Auth check
    try:
        auth = helper.check_auth()
        if auth['status'] != 'authenticated':
            print(f"❌ Not authenticated: {auth.get('error')}")
            print(f"   Run: python3 scripts/linkedin_oauth_helper.py --init")
            return
        profile = auth.get('profile', {})
        name = (profile.get('name') or profile.get('sub') or 'unknown')
        print(f"✅ Authenticated as: {name}")
    except Exception as e:
        print(f"❌ Auth check failed: {e}")
        return

    # Step 2: Resolve author URN
    try:
        author_urn = helper.get_author_urn()
        print(f"✅ Author URN: {author_urn}")
    except Exception as e:
        print(f"❌ Author URN resolution failed: {e}")
        return

    # Step 3: Scope check
    post_result = helper.check_can_post()
    if not post_result['available']:
        print(f"❌ Cannot post: {post_result.get('reason')}")
        return
    print(f"✅ w_member_social scope: confirmed")

    # Step 4: Build post text
    if execute:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        post_text = f"[TEST POST] {text} | {ts}"
    else:
        post_text = text

    print(f"\n📝 Post content ({len(post_text)} chars):")
    print(f"   {post_text[:200]}{'...' if len(post_text) > 200 else ''}")

    if not execute:
        print(f"\n✅ Dry-run OK — payload validated, author URN resolved, scope confirmed")
        print(f"   To actually post, add --execute")
        print(SEP)
        return

    # Step 5: Send
    print("\n🚀 Posting to LinkedIn...")
    try:
        result = helper.create_post(post_text)
        print(f"✅ Posted!")
        print(f"   URN      : {result.get('id')}")
        print(f"   Endpoint : {result.get('endpoint_used')}")
        print(f"   Author   : {result.get('author')}")
    except Exception as e:
        print(f"❌ Post failed: {e}")
    print(SEP)


def show_whoami():
    """Show identity details: sub, person_urn, method used, scopes detected."""
    print("=" * 70)
    print("LinkedIn Identity & URN Resolution")
    print("=" * 70)

    helper = LinkedInAPIHelper()

    # Step 1: OIDC userinfo
    try:
        auth_result = helper.check_auth()
    except Exception as e:
        print(f"\n❌ Auth check failed: {e}")
        return

    if auth_result['status'] != 'authenticated':
        print(f"\n❌ Not authenticated: {auth_result.get('error', 'unknown')}")
        print(f"\n   Run: python3 scripts/linkedin_oauth_helper.py --init")
        print("\n" + "=" * 70)
        return

    profile = auth_result.get('profile', {})
    auth_method = auth_result.get('auth_method', 'unknown')

    print(f"\n📋 OIDC Userinfo ({auth_method.upper()}):")
    print(f"   sub     : {profile.get('sub', 'n/a')}")
    print(f"   name    : {profile.get('name', profile.get('given_name', '') + ' ' + profile.get('family_name', ''))}")
    if 'email' in profile:
        print(f"   email   : {profile.get('email')}")

    # Step 2: Person URN resolution
    print(f"\n🔗 Person URN Resolution:")
    try:
        person_urn = helper.get_person_urn()
        # Determine which method was used from cached profile file
        profile_file = helper.secrets_dir / "linkedin_profile.json"
        method = "unknown"
        if profile_file.exists():
            with open(profile_file, 'r') as f:
                cached = json.load(f)
            method = cached.get('method', 'unknown')
        print(f"   person_urn : {person_urn}")
        print(f"   method     : {method}")
    except Exception as e:
        print(f"   ❌ URN resolution failed: {e}")
        print(f"   Tip: /v2/me requires 'profile' scope or legacy 'r_liteprofile' scope")

    # Step 3: Scopes / products detected
    print(f"\n🔐 Scopes / Products Detected:")
    creds_file = helper.credentials_file
    if creds_file.exists():
        try:
            with open(creds_file, 'r') as f:
                creds = json.load(f)
            scopes = creds.get('scopes', ['openid', 'profile', 'email', 'w_member_social'])
            print(f"   Configured scopes: {scopes}")
        except Exception:
            print(f"   Could not read credentials file")
    else:
        print(f"   credentials file not found")

    token_data = helper._load_token()
    if token_data:
        print(f"\n🔑 Token:")
        print(f"   expires_at : {token_data.get('expires_at', 'unknown')}")
        has_refresh = 'refresh_token' in token_data
        print(f"   refresh_token available: {has_refresh}")

    print("\n" + "=" * 70)


def show_capabilities():
    """
    Test and display LinkedIn API capabilities.

    Checks (no content is posted):
    - Authenticated: OIDC userinfo reachable
    - Can Post: w_member_social scope configured + person URN resolvable
    - Can Read Posts: GET /v2/ugcPosts probe (403 → False)

    Output example:
        ----------------------------------------
        LinkedIn Capabilities
        ----------------------------------------
        Authenticated: YES
        Can Post:      YES
        Can Read Posts: NO (r_member_social not granted)
        ----------------------------------------
    """
    SEP = "-" * 40

    print(SEP)
    print("LinkedIn Capabilities")
    print(SEP)

    helper = LinkedInAPIHelper()

    # --- Authenticated ---
    try:
        auth_result = helper.check_auth()
        authenticated = auth_result.get('status') == 'authenticated'
    except Exception:
        authenticated = False

    print(f"Authenticated: {'YES' if authenticated else 'NO'}")

    if not authenticated:
        print("Can Post:       UNKNOWN (not authenticated)")
        print("Can Read Posts: UNKNOWN (not authenticated)")
        print(SEP)
        print(f"Run: python3 scripts/linkedin_oauth_helper.py --init")
        return

    # --- Can Post ---
    post_result = helper.check_can_post()
    can_post = post_result['available']
    post_reason = post_result.get('reason', '')
    if can_post:
        print("Can Post:       YES")
    else:
        suffix = f" ({post_reason})" if post_reason else ""
        print(f"Can Post:       NO{suffix}")

    # --- Can Read Posts ---
    read_result = helper.check_read_access()
    can_read = read_result['available']
    read_reason = read_result.get('reason', '')
    if can_read:
        print("Can Read Posts: YES")
    else:
        suffix = f" ({read_reason})" if read_reason else ""
        print(f"Can Read Posts: NO{suffix}")

    print(SEP)


def show_status():
    """Show current LinkedIn OAuth status"""
    print("=" * 70)
    print("LinkedIn OAuth2 Status")
    print("=" * 70)

    helper = LinkedInAPIHelper()
    auth_result = helper.check_auth()

    if auth_result['status'] == 'authenticated':
        profile = auth_result['profile']
        token_data = helper._load_token()
        auth_method = auth_result.get('auth_method', 'unknown')

        print(f"\n✅ Status: AUTHENTICATED")
        print(f"   Method: {auth_method.upper()}")

        print(f"\n📋 Profile:")
        # Handle both OIDC and legacy profile formats
        if auth_method == 'oidc':
            # OIDC format: sub, name, email, picture
            print(f"   ID: {profile.get('sub', 'unknown')}")
            print(f"   Name: {profile.get('name', profile.get('given_name', '') + ' ' + profile.get('family_name', ''))}")
            if 'email' in profile:
                print(f"   Email: {profile.get('email')}")
        else:
            # Legacy format: id, localizedFirstName, localizedLastName
            print(f"   ID: {profile.get('id', 'unknown')}")
            print(f"   Name: {profile.get('localizedFirstName', 'unknown')} {profile.get('localizedLastName', '')}")

        if token_data:
            print(f"\n🔑 Token:")
            print(f"   File: {helper.token_file}")
            print(f"   Expires at: {token_data.get('expires_at', 'unknown')}")

            # Check expiration
            if 'expires_at' in token_data:
                try:
                    expires_at = datetime.fromisoformat(token_data['expires_at'].replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    seconds_remaining = (expires_at - now).total_seconds()

                    if seconds_remaining > 0:
                        days = int(seconds_remaining // 86400)
                        print(f"   Seconds remaining: {int(seconds_remaining)}")
                        print(f"   Days remaining: {days}")
                    else:
                        print(f"   ⚠️  Token EXPIRED")
                        print(f"   Run: python3 scripts/linkedin_oauth_helper.py --init")
                except Exception as e:
                    logger.debug(f"Failed to parse expiry: {e}")

        print(f"\n✅ LinkedIn API access is ready")
        print(f"\n💡 Test with:")
        print(f"   python3 scripts/linkedin_watcher_skill.py --mode real --once")

    else:
        error = auth_result.get('error', 'Unknown error')
        print(f"\n❌ Status: NOT AUTHENTICATED")
        print(f"   Error: {error}")
        print(f"\n🔧 To authenticate, run:")
        print(f"   python3 scripts/linkedin_oauth_helper.py --init")

    print("\n" + "=" * 70)


def init_oauth():
    """Initialize OAuth flow with browser auto-open and local server"""
    import sys

    print("=" * 70)
    print("LinkedIn OAuth2 Authorization Flow")
    print("=" * 70)

    helper = LinkedInAPIHelper()

    # Step 1: Check if already authenticated
    print("\nStep 1: Checking existing authentication...")
    auth_result = helper.check_auth()

    if auth_result['status'] == 'authenticated':
        print(f"\n✅ Already authenticated!")
        profile = auth_result['profile']
        print(f"   User: {profile.get('localizedFirstName', 'unknown')}")
        print(f"\nTo re-authenticate, delete: {helper.token_file}")
        return

    print(f"⚠️  Not authenticated: {auth_result.get('error', 'No token found')}")

    # Step 2: Generate authorization URL
    print("\nStep 2: Generating authorization URL...")
    auth_url = helper.get_authorization_url()

    print("\n" + "=" * 70)
    print("BROWSER AUTHORIZATION")
    print("=" * 70)
    print("\nYour browser will open automatically in 3 seconds...")
    print("Please authorize the application in the browser window.")
    print("\n💡 WSL Users: If browser doesn't open automatically,")
    print("   copy the URL below and paste it into your Windows browser.")
    print("\nIf browser doesn't open, visit this URL manually:")
    print(f"\n   {auth_url}\n")
    print("=" * 70)

    time.sleep(3)

    # Step 3: Start local server and open browser
    print("\nStep 3: Starting local server and opening browser...")
    code = run_oauth_server_and_get_code(auth_url, timeout=300)

    if not code:
        print(f"\n❌ Failed to obtain authorization code")
        print(f"\nTroubleshooting:")
        print(f"1. Ensure port 8080 is not in use")
        print(f"2. Check redirect URI in LinkedIn app settings: http://localhost:8080/callback")
        print(f"3. If you see 'invalid_scope' error in browser:")
        print(f"   - This usually means required Products are not enabled in your LinkedIn app")
        print(f"   - Enable 'Share on LinkedIn' product for w_member_social scope")
        print(f"   - Enable 'Sign In with LinkedIn using OpenID Connect' for profile/email scopes")
        print(f"   - Go to: https://www.linkedin.com/developers/apps")
        print(f"4. WSL users: If browser didn't open, copy/paste the URL manually into Windows browser")
        print(f"5. Try again with: python3 scripts/linkedin_oauth_helper.py --init")
        sys.exit(1)

    print(f"\n✅ Authorization code received: {code[:20]}...")

    # Step 4: Exchange code for token
    print("\nStep 4: Exchanging code for access token...")
    try:
        token = helper.exchange_code_for_token(code)
        print(f"\n✅ Access token obtained!")
        print(f"   Expires at: {token.get('expires_at', 'unknown')}")
        print(f"   Token saved to: {helper.token_file}")

    except LinkedInAuthError as e:
        print(f"\n❌ Token exchange failed: {e}")
        sys.exit(1)

    # Step 5: Verify authentication
    print("\nStep 5: Verifying authentication...")
    auth_result = helper.check_auth()

    if auth_result['status'] == 'authenticated':
        profile = auth_result['profile']
        auth_method = auth_result.get('auth_method', 'unknown')

        print(f"\n✅ AUTH OK: LinkedIn authentication successful!")
        print(f"   Method: {auth_method.upper()}")

        # Display minimal PII based on auth method
        if auth_method == 'oidc':
            # OIDC format: name, email (minimal)
            name = profile.get('name', profile.get('given_name', '') + ' ' + profile.get('family_name', '')).strip()
            print(f"   Name: {name or 'unknown'}")
            if 'email' in profile:
                print(f"   Email: {profile.get('email')}")
        else:
            # Legacy format: localizedFirstName, localizedLastName
            print(f"   Name: {profile.get('localizedFirstName', 'unknown')} {profile.get('localizedLastName', '')}")

        print(f"\n🎉 LinkedIn OAuth setup complete!")
        print(f"\nNext steps:")
        print(f"1. Test watcher: python3 scripts/linkedin_watcher_skill.py --mode real --once")
        print(f"2. Check status: python3 scripts/linkedin_oauth_helper.py --status")
    else:
        print(f"\n❌ Verification failed: {auth_result.get('error')}")
        print(f"\nTroubleshooting:")
        print(f"1. Check that token was saved correctly: {helper.token_file}")
        print(f"2. Verify LinkedIn app has required Products enabled")
        print(f"3. Try re-authenticating: python3 scripts/linkedin_oauth_helper.py --init")
        sys.exit(1)


def main():
    """
    LinkedIn OAuth2 CLI Helper

    Commands:
        --init                        : Initialize OAuth flow (browser auto-open + local server)
        --status                      : Check current authentication status
        --check-auth                  : Quick authentication check (alias for --status)
        --whoami                      : Show identity details (sub, person_urn, method, scopes)
        --test-endpoints              : 5-step diagnostic (OIDC, URN, dry-run, optional post, read)
        --capabilities                : Show LinkedIn API capabilities (post/read/auth)
        --post-test "TEXT"            : Send a real test post to LinkedIn REST Posts API
        --post-test "TEXT" --dry-run  : Validate pipeline only (no post sent)
        --test-endpoints --execute    : Include a real post in the 5-step diagnostic

    Usage:
        python3 scripts/linkedin_oauth_helper.py --init
        python3 scripts/linkedin_oauth_helper.py --status
        python3 scripts/linkedin_oauth_helper.py --capabilities
        python3 scripts/linkedin_oauth_helper.py --whoami
        python3 scripts/linkedin_oauth_helper.py --test-endpoints
        python3 scripts/linkedin_oauth_helper.py --post-test "Hello from AI"
        python3 scripts/linkedin_oauth_helper.py --post-test "Hello from AI" --dry-run
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='LinkedIn OAuth2 Helper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # First-time setup (opens browser automatically)
  python3 scripts/linkedin_oauth_helper.py --init

  # Check if authenticated and what is possible
  python3 scripts/linkedin_oauth_helper.py --status
  python3 scripts/linkedin_oauth_helper.py --capabilities

  # Show identity details and person URN
  python3 scripts/linkedin_oauth_helper.py --whoami

  # 5-step endpoint diagnostic (dry-run)
  python3 scripts/linkedin_oauth_helper.py --test-endpoints

  # 5-step endpoint diagnostic + real post
  python3 scripts/linkedin_oauth_helper.py --test-endpoints --execute

  # Send a real test post to LinkedIn (always executes)
  python3 scripts/linkedin_oauth_helper.py --post-test "My test post"

  # Validate pipeline without sending (dry-run opt-out)
  python3 scripts/linkedin_oauth_helper.py --post-test "My test post" --dry-run
        """
    )

    parser.add_argument('--init', action='store_true',
                        help='Initialize OAuth flow with browser auto-open')
    parser.add_argument('--status', action='store_true',
                        help='Show current authentication status')
    parser.add_argument('--check-auth', action='store_true',
                        help='Quick authentication check (alias for --status)')
    parser.add_argument('--whoami', action='store_true',
                        help='Show identity details: sub, person_urn, method, scopes')
    parser.add_argument('--test-endpoints', action='store_true',
                        help='5-step diagnostic: OIDC, URN, dry-run post, optional real post, read')
    parser.add_argument('--capabilities', action='store_true',
                        help='Show LinkedIn API capabilities (authenticated/can-post/can-read)')
    parser.add_argument('--post-test', metavar='TEXT',
                        help='Send a real test post to LinkedIn REST Posts API')
    parser.add_argument('--dry-run', action='store_true',
                        help='With --post-test: validate pipeline only, do not send post')
    parser.add_argument('--execute', action='store_true',
                        help='With --test-endpoints: include a real post in the 5-step diagnostic')

    args = parser.parse_args()

    if not (args.init or args.status or args.check_auth or args.whoami
            or args.test_endpoints or args.capabilities or args.post_test):
        parser.print_help()
        return

    if args.init:
        init_oauth()
    elif args.capabilities:
        show_capabilities()
    elif args.post_test:
        # --post-test always executes unless --dry-run is given
        post_test(args.post_test, execute=not args.dry_run)
    elif args.test_endpoints:
        test_endpoints(execute=args.execute)
    elif args.whoami:
        show_whoami()
    elif args.status or args.check_auth:
        show_status()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
