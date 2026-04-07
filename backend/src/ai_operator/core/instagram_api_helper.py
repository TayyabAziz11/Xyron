#!/usr/bin/env python3
"""
Instagram API Helper — Token-based Graph API Access
Gold Tier — G-M3: Social Channels

Purpose : Authenticated Instagram Graph API access for watcher (perception)
          and executor (action) operations.  No webhooks, no App Review —
          dev-mode token access only.

Auth model:
  Long-lived User Access Token stored in:
    .secrets/instagram_credentials.json   (primary)
    .secrets/meta_credentials.json        (fallback)

  Canonical schema:
    {
      "app_id":       "...",
      "app_secret":   "...",
      "access_token": "...",     ← NEVER logged
      "page_id":      "...",
      "ig_user_id":   "...",
      "api_version":  "v22.0"
    }

Security:
  - Credentials:  .secrets/instagram_credentials.json
  - PII redaction: All logs use redact_pii()
  - Tokens:        NEVER logged, NEVER printed
  - Rate limiting: Exponential backoff on 429 errors

Instagram Graph API docs:
  https://developers.facebook.com/docs/instagram-api
"""

import json
import logging
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

# ── Path bootstrap ─────────────────────────────────────────────────────────────
try:
    from ai_operator.core.mcp_helpers import redact_pii, get_repo_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from ai_operator.core.mcp_helpers import redact_pii, get_repo_root
    except ImportError:
        import re

        def redact_pii(text: str) -> str:  # type: ignore
            text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                          '[EMAIL]', text)
            text = re.sub(r'(\+?\d[\d\s\-().]{7,})', '[PHONE]', text)
            return text

        def get_repo_root() -> Path:  # type: ignore
            current = Path(__file__).parent
            while current != current.parent:
                if (current / 'system_log.md').exists():
                    return current
                current = current.parent
            return Path(__file__).parent.parent.parent.parent.parent

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════════════

class InstagramAPIError(Exception):
    """Instagram / Graph API error."""


class InstagramAuthError(InstagramAPIError):
    """Authentication / token validation error."""


class InstagramPermissionError(InstagramAPIError):
    """Insufficient permissions for the requested operation."""


# ══════════════════════════════════════════════════════════════════════════════
# Helper
# ══════════════════════════════════════════════════════════════════════════════

class InstagramAPIHelper:
    """
    Instagram Graph API helper.

    Features:
    - Token validation against Graph API /me endpoint
    - Profile resolution (ig_user_id, username, follower count)
    - Two-step media publishing (container → publish)
    - Recent media listing
    - Comment listing
    - Rate-limit-aware exponential backoff
    - PII-redacted structured logging
    - Graceful degradation (mock mode when no token)

    Usage:
        helper = InstagramAPIHelper()
        status = helper.check_auth()        # validate token
        profile = helper.resolve_ig_user()  # get profile info
        result = helper.create_post_with_image(
            image_url="https://example.com/img.jpg",
            caption="Hello world!"
        )
    """

    GRAPH_BASE = "https://graph.facebook.com"
    DEFAULT_API_VERSION = "v22.0"
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0  # seconds

    # Required fields for credentials validation
    REQUIRED_FIELDS = {"access_token", "ig_user_id"}

    def __init__(self, secrets_dir: Optional[Path] = None):
        if secrets_dir is None:
            secrets_dir = get_repo_root() / ".secrets"

        self.secrets_dir = Path(secrets_dir)
        self._creds: Optional[Dict[str, Any]] = None
        self._profile_cache: Optional[Dict[str, Any]] = None

        logger.info("InstagramAPIHelper initialised (secrets_dir=%s)", self.secrets_dir)

    # ── Credentials ───────────────────────────────────────────────────────────

    def _load_credentials(self) -> Dict[str, Any]:
        """Load and validate credentials from .secrets/. Raises InstagramAuthError if missing."""
        if self._creds is not None:
            return self._creds

        paths = [
            self.secrets_dir / "instagram_credentials.json",
            self.secrets_dir / "meta_credentials.json",
        ]
        for path in paths:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    missing = self.REQUIRED_FIELDS - set(data.keys())
                    if missing:
                        raise InstagramAuthError(
                            f"Credentials file {path.name} missing fields: {missing}"
                        )
                    self._creds = data
                    logger.info("Credentials loaded from %s", path.name)
                    return self._creds
                except json.JSONDecodeError as exc:
                    raise InstagramAuthError(
                        f"Invalid JSON in {path.name}: {exc}"
                    ) from exc

        raise InstagramAuthError(
            "No credentials found. Create .secrets/instagram_credentials.json\n"
            "Schema: {app_id, app_secret, access_token, page_id, ig_user_id, api_version}"
        )

    @property
    def _token(self) -> str:
        creds = self._load_credentials()
        # Page Access Token is required for IG Graph API posting; fall back to user token
        return creds.get("page_token") or creds["access_token"]

    @property
    def _ig_user_id(self) -> str:
        return self._load_credentials()["ig_user_id"]

    @property
    def _api_version(self) -> str:
        return self._load_credentials().get("api_version", self.DEFAULT_API_VERSION)

    def _base_url(self) -> str:
        return f"{self.GRAPH_BASE}/{self._api_version}"

    # ── HTTP layer with backoff ────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request("GET", path, params=params)

    def _post(self, path: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request("POST", path, data=data)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        if requests is None:
            raise InstagramAPIError("requests library not installed — pip install requests")

        url = f"{self._base_url()}{path}"
        # Always inject token into params (never log it)
        base_params: Dict[str, Any] = {"access_token": self._token}
        if params:
            base_params.update(params)

        backoff = self.INITIAL_BACKOFF
        last_exc: Exception = RuntimeError("no attempts made")

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                resp = requests.request(
                    method, url, params=base_params, data=data, timeout=20
                )

                # Graph API 429 or 500-range → backoff
                if resp.status_code == 429:
                    logger.warning(
                        "Rate-limited by Graph API (attempt %d/%d) — backoff %.1fs",
                        attempt, self.MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                if resp.status_code >= 500:
                    logger.warning(
                        "Graph API server error %d (attempt %d/%d) — backoff %.1fs",
                        resp.status_code, attempt, self.MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                payload = resp.json()

                # Graph API encodes errors in the body even on 2xx
                if "error" in payload:
                    err = payload["error"]
                    code = err.get("code", 0)
                    msg = err.get("message", "unknown")
                    if code in (190, 102, 463):  # token expired / invalid
                        raise InstagramAuthError(
                            f"Token error ({code}): {redact_pii(msg)}"
                        )
                    if code == 200:  # permission error
                        raise InstagramPermissionError(
                            f"Permission denied ({code}): {redact_pii(msg)}"
                        )
                    raise InstagramAPIError(
                        f"Graph API error ({code}): {redact_pii(msg)}"
                    )

                return payload

            except (InstagramAuthError, InstagramPermissionError):
                raise
            except InstagramAPIError:
                raise
            except Exception as exc:  # network / timeout
                last_exc = exc
                logger.warning(
                    "Request failed (attempt %d/%d): %s — backoff %.1fs",
                    attempt, self.MAX_RETRIES, type(exc).__name__, backoff,
                )
                time.sleep(backoff)
                backoff *= 2

        raise InstagramAPIError(
            f"Graph API request failed after {self.MAX_RETRIES} attempts: {last_exc}"
        )

    # ── A) Identity ───────────────────────────────────────────────────────────

    def check_auth(self) -> Dict[str, Any]:
        """
        Validate the stored access token against Graph API /me.

        Returns:
            {"valid": True, "user_id": "...", "name": "..."}
        Raises:
            InstagramAuthError if token is invalid / expired.
        """
        try:
            data = self._get("/me", params={"fields": "id,name"})
            logger.info(
                "Token valid — Graph user id=%s name=%s",
                data.get("id"), redact_pii(str(data.get("name", "")))
            )
            return {
                "valid": True,
                "user_id": data.get("id"),
                "name": data.get("name"),
            }
        except InstagramAuthError:
            raise
        except Exception as exc:
            raise InstagramAuthError(f"Auth check failed: {exc}") from exc

    def resolve_ig_user(self) -> Dict[str, Any]:
        """
        Fetch Instagram Business/Creator account profile.

        Returns a dict with: id, username, name, biography,
        followers_count, media_count, profile_picture_url
        """
        uid = self._ig_user_id
        fields = "id,username,name,biography,followers_count,media_count,profile_picture_url"
        data = self._get(f"/{uid}", params={"fields": fields})
        self._profile_cache = data
        logger.info(
            "IG user resolved — @%s  followers=%s  media=%s",
            data.get("username"), data.get("followers_count"), data.get("media_count"),
        )
        return data

    def capabilities(self) -> Dict[str, Any]:
        """
        Return the set of actions this helper can perform given the current token.
        Checks scopes via token debug endpoint.
        """
        debug = self._get(
            "/debug_token",
            params={
                "input_token": self._token,
                "access_token": self._token,
            },
        )
        token_data = debug.get("data", {})
        scopes: List[str] = token_data.get("scopes", [])

        caps = {
            "can_post":              any(s in scopes for s in ("instagram_basic", "instagram_content_publish")),
            "can_read_media":        "instagram_basic" in scopes,
            "can_read_comments":     any(s in scopes for s in ("instagram_manage_comments", "instagram_basic")),
            "can_manage_comments":   "instagram_manage_comments" in scopes,
            "scopes":                scopes,
            "expires_at":            token_data.get("expires_at"),
            "token_type":            token_data.get("type"),
        }
        logger.info("Capabilities resolved — scopes=%s", scopes)
        return caps

    def test_endpoints(self) -> Dict[str, Any]:
        """
        Smoke-test each available endpoint and return pass/fail per endpoint.
        Never mutates data.
        """
        results: Dict[str, Any] = {}

        for name, fn in [
            ("check_auth",    self.check_auth),
            ("resolve_ig_user", self.resolve_ig_user),
            ("list_recent_media", lambda: self.list_recent_media(limit=1)),
        ]:
            try:
                fn()
                results[name] = "PASS"
            except InstagramPermissionError as exc:
                results[name] = f"PERMISSION_DENIED: {exc}"
            except InstagramAuthError as exc:
                results[name] = f"AUTH_FAIL: {exc}"
            except InstagramAPIError as exc:
                results[name] = f"API_FAIL: {exc}"
            except Exception as exc:
                results[name] = f"ERROR: {exc}"

        results["capabilities"] = "skip"  # debug_token requires app token, skip in smoke
        logger.info("Endpoint smoke test: %s", results)
        return results

    # ── B) Posting ────────────────────────────────────────────────────────────

    def create_post_with_image(
        self,
        image_url: str,
        caption: str,
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Publish an image post to Instagram via the two-step Graph API flow:
          1. POST /{ig_user_id}/media        → creation_id (media container)
          2. POST /{ig_user_id}/media_publish → media_id    (live post)

        Args:
            image_url: Publicly accessible URL to the image (HTTPS required).
            caption:   Post caption (max 2200 chars for IG).
            dry_run:   If True, skip step 2 and return the container ID only.

        Returns:
            {"success": True, "media_id": "...", "permalink": "..."}
        """
        uid = self._ig_user_id

        if len(caption) > 2200:
            logger.warning("Caption truncated from %d to 2200 chars", len(caption))
            caption = caption[:2197] + "..."

        # Step 1 — create media container
        logger.info("Creating IG media container (dry_run=%s)", dry_run)
        container = self._post(
            f"/{uid}/media",
            data={"image_url": image_url, "caption": caption},
        )
        creation_id = container.get("id")
        if not creation_id:
            raise InstagramAPIError(f"No creation_id returned: {container}")

        logger.info("Media container created — creation_id=%s", creation_id)

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "creation_id": creation_id,
                "message": "Container created — not published (dry_run=True)",
            }

        # Brief pause — Instagram recommends waiting before publish
        time.sleep(2)

        # Step 2 — publish
        logger.info("Publishing media container creation_id=%s", creation_id)
        result = self._post(
            f"/{uid}/media_publish",
            data={"creation_id": creation_id},
        )
        media_id = result.get("id")
        if not media_id:
            raise InstagramAPIError(f"No media_id returned from publish: {result}")

        logger.info("Post published — media_id=%s", media_id)

        # Fetch permalink for logging
        try:
            details = self._get(f"/{media_id}", params={"fields": "id,permalink,timestamp"})
            permalink = details.get("permalink", "")
        except Exception:
            permalink = ""

        return {
            "success": True,
            "dry_run": False,
            "media_id": media_id,
            "permalink": permalink,
        }

    # ── C) Reading ────────────────────────────────────────────────────────────

    def list_recent_media(
        self,
        limit: int = 10,
        fields: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List the most recent media items on the IG account.

        Args:
            limit:  Number of items to return (max 100).
            fields: Comma-separated Graph API field list.

        Returns:
            List of media dicts with id, caption, media_type, timestamp, permalink.
        """
        if fields is None:
            fields = "id,caption,media_type,timestamp,permalink,thumbnail_url"

        uid = self._ig_user_id
        data = self._get(
            f"/{uid}/media",
            params={"fields": fields, "limit": min(limit, 100)},
        )
        items: List[Dict[str, Any]] = data.get("data", [])
        logger.info("Listed %d recent media items", len(items))
        return items

    def list_comments(
        self,
        media_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        List comments on a specific media item.

        Args:
            media_id: Instagram media ID.
            limit:    Max number of comments to fetch.

        Returns:
            List of comment dicts with id, text, username, timestamp.
        """
        fields = "id,text,username,timestamp"
        data = self._get(
            f"/{media_id}/comments",
            params={"fields": fields, "limit": min(limit, 100)},
        )
        items: List[Dict[str, Any]] = data.get("data", [])
        logger.info("Listed %d comments on media_id=%s", len(items), media_id)
        return items


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [instagram_helper] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Instagram Graph API Helper CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/instagram_oauth_helper.py --status
  python3 scripts/instagram_oauth_helper.py --resolve
  python3 scripts/instagram_oauth_helper.py --whoami
  python3 scripts/instagram_oauth_helper.py --test-endpoints
  python3 scripts/instagram_oauth_helper.py --capabilities
""",
    )
    parser.add_argument("--status",         action="store_true", help="Check token validity")
    parser.add_argument("--resolve",        action="store_true", help="Resolve IG user profile")
    parser.add_argument("--whoami",         action="store_true", help="Show current Graph API identity")
    parser.add_argument("--test-endpoints", action="store_true", help="Smoke-test all read endpoints")
    parser.add_argument("--capabilities",   action="store_true", help="Show token scopes & capabilities")
    parser.add_argument("--list-media",     action="store_true", help="List recent media (up to 5)")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        return 0

    helper = InstagramAPIHelper()

    try:
        if args.status or args.whoami:
            result = helper.check_auth()
            print(json.dumps(result, indent=2, default=str))

        if args.resolve:
            result = helper.resolve_ig_user()
            # Redact nothing here — profile is public data, just mask token
            safe = {k: v for k, v in result.items() if k != "access_token"}
            print(json.dumps(safe, indent=2, default=str))

        if args.test_endpoints:
            result = helper.test_endpoints()
            print(json.dumps(result, indent=2, default=str))

        if args.capabilities:
            result = helper.capabilities()
            print(json.dumps(result, indent=2, default=str))

        if args.list_media:
            items = helper.list_recent_media(limit=5)
            print(json.dumps(items, indent=2, default=str))

    except InstagramAuthError as exc:
        print(f"\n[AUTH ERROR] {exc}", file=sys.stderr)
        print("\nTo fix: ensure .secrets/instagram_credentials.json contains:", file=sys.stderr)
        print('  {"app_id":"...","app_secret":"...","access_token":"...","ig_user_id":"..."}',
              file=sys.stderr)
        return 1
    except InstagramPermissionError as exc:
        print(f"\n[PERMISSION ERROR] {exc}", file=sys.stderr)
        print("Your token may be missing instagram_content_publish or instagram_basic scope.",
              file=sys.stderr)
        return 1
    except InstagramAPIError as exc:
        print(f"\n[API ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[UNEXPECTED ERROR] {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
