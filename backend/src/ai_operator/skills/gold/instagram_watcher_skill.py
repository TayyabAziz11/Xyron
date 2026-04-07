#!/usr/bin/env python3
"""
Personal AI Employee — Instagram Watcher Skill
Gold Tier — G-M3: Social Watchers

Purpose : Instagram perception-only watcher.
          Polls the Graph API for new media/comments and writes intake wrappers
          to Social/Inbox/ as YAML-fronted markdown files.
Tier    : Gold
Skill ID: G-M3-T06

CRITICAL:  PERCEPTION ONLY.
           NEVER posts, NEVER replies, NEVER sends messages.
           Only creates intake wrappers in Social/Inbox/.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
            return re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                          '[EMAIL]', text)

        def get_repo_root() -> Path:  # type: ignore
            current = Path(__file__).parent
            while current != current.parent:
                if (current / 'system_log.md').exists():
                    return current
                current = current.parent
            return Path(__file__).parent.parent.parent.parent.parent

try:
    from ai_operator.core.instagram_api_helper import (
        InstagramAPIHelper,
        InstagramAPIError,
        InstagramAuthError,
        InstagramPermissionError,
    )
except ImportError:
    InstagramAPIHelper = None  # type: ignore
    InstagramAPIError = Exception  # type: ignore
    InstagramAuthError = Exception  # type: ignore
    InstagramPermissionError = Exception  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [instagram_watcher] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

READY_FILE = Path("/tmp/instagram_watcher.ready")


# ══════════════════════════════════════════════════════════════════════════════
# Mock data — realistic but synthetic
# ══════════════════════════════════════════════════════════════════════════════

MOCK_MEDIA: List[Dict[str, Any]] = [
    {
        "id": "mock_media_001",
        "media_type": "IMAGE",
        "caption": "Excited to announce our new AI-powered product launch! The future is here. #AI #Innovation #TechStartup",
        "timestamp": "2026-02-22T08:00:00+0000",
        "permalink": "https://www.instagram.com/p/mock_media_001/",
        "comments_count": 3,
    },
    {
        "id": "mock_media_002",
        "media_type": "IMAGE",
        "caption": "Behind the scenes at the hackathon — building the future one commit at a time. #Hackathon #BuildInPublic",
        "timestamp": "2026-02-21T14:30:00+0000",
        "permalink": "https://www.instagram.com/p/mock_media_002/",
        "comments_count": 5,
    },
]

MOCK_COMMENTS: Dict[str, List[Dict[str, Any]]] = {
    "mock_media_001": [
        {"id": "mock_cmt_001a", "username": "john_doe",   "text": "This is amazing! When is it launching?", "timestamp": "2026-02-22T08:15:00+0000"},
        {"id": "mock_cmt_001b", "username": "sara_k",     "text": "Can't wait to try this!",                "timestamp": "2026-02-22T08:30:00+0000"},
        {"id": "mock_cmt_001c", "username": "tech_friend","text": "Congrats on the launch 🎉",              "timestamp": "2026-02-22T09:00:00+0000"},
    ],
    "mock_media_002": [
        {"id": "mock_cmt_002a", "username": "hackfan",    "text": "Which hackathon is this?",               "timestamp": "2026-02-21T15:00:00+0000"},
        {"id": "mock_cmt_002b", "username": "ai_builder", "text": "Love the energy!",                       "timestamp": "2026-02-21T16:00:00+0000"},
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# Watcher class
# ══════════════════════════════════════════════════════════════════════════════

class InstagramWatcher:
    """
    Instagram perception watcher.

    Modes:
      mock — uses synthetic data, no API calls (safe for demo / CI)
      real — calls Graph API, requires valid token in .secrets/
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.repo_root = Path(config.get("repo_root") or get_repo_root())
        self.inbox_dir = self.repo_root / "Social" / "Inbox"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_path = config.get(
            "checkpoint_path",
            str(self.repo_root / "Logs" / "instagram_watcher_checkpoint.json"),
        )
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint = self._load_checkpoint()

        self.mode = config.get("mode", "mock")
        self.helper: Optional[InstagramAPIHelper] = None

        if self.mode == "real":
            if InstagramAPIHelper is None:
                raise RuntimeError("InstagramAPIHelper import failed — check your Python path")
            self.helper = InstagramAPIHelper(
                secrets_dir=self.repo_root / ".secrets"
            )

        self.created_count = 0
        self.skipped_count = 0
        self.errors: List[str] = []

    # ── Checkpoint ────────────────────────────────────────────────────────────

    def _load_checkpoint(self) -> Dict[str, Any]:
        if self.checkpoint_path.exists():
            try:
                return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load checkpoint: %s", exc)
        return {"processed_ids": [], "last_run": None}

    def _save_checkpoint(self) -> None:
        self.checkpoint["last_run"] = datetime.now(timezone.utc).isoformat()
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(
            json.dumps(self.checkpoint, indent=2), encoding="utf-8"
        )

    def _already_processed(self, item_id: str) -> bool:
        return item_id in self.checkpoint.get("processed_ids", [])

    def _mark_processed(self, item_id: str) -> None:
        ids: List[str] = self.checkpoint.setdefault("processed_ids", [])
        if item_id not in ids:
            ids.append(item_id)
        # Keep last 500 IDs
        if len(ids) > 500:
            self.checkpoint["processed_ids"] = ids[-500:]

    # ── Intake wrapper creation ────────────────────────────────────────────────

    def _write_intake_wrapper(
        self,
        item_id: str,
        item_type: str,
        payload: Dict[str, Any],
        *,
        parent_id: Optional[str] = None,
    ) -> Path:
        """Create a YAML-fronted markdown intake wrapper in Social/Inbox/."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        filename = f"inbox__instagram__{item_type}__{ts}__{item_id[:12]}.md"
        filepath = self.inbox_dir / filename

        # Build safe-for-log excerpt (redact PII)
        raw_text = payload.get("caption") or payload.get("text") or ""
        excerpt = redact_pii(raw_text[:200])

        yaml_block = f"""---
source: instagram
type: {item_type}
id: {item_id}
{"parent_id: " + parent_id if parent_id else "parent_id: null"}
timestamp: {payload.get("timestamp", "")}
permalink: {payload.get("permalink", "")}
media_type: {payload.get("media_type", "")}
username: {payload.get("username", "")}
created_at: {datetime.now(timezone.utc).isoformat()}
status: unread
---

## Instagram {item_type.replace("_", " ").title()}

**ID**: `{item_id}`
**Time**: {payload.get("timestamp", "")}
{"**Permalink**: " + payload.get("permalink", "")}
{"**Username**: @" + payload.get("username", "") if payload.get("username") else ""}

### Content

{redact_pii(raw_text)}

### Raw Payload

```json
{json.dumps({k: v for k, v in payload.items() if k != "access_token"}, indent=2, default=str)}
```
"""
        filepath.write_text(yaml_block, encoding="utf-8")
        logger.info(
            "Intake wrapper created: %s  (excerpt: %s…)",
            filename, excerpt[:60],
        )
        return filepath

    # ── Fetch helpers ─────────────────────────────────────────────────────────

    def _fetch_media(self) -> List[Dict[str, Any]]:
        if self.mode == "mock":
            return MOCK_MEDIA
        assert self.helper is not None
        try:
            return self.helper.list_recent_media(
                limit=self.config.get("media_limit", 10)
            )
        except InstagramPermissionError as exc:
            self._create_remediation(f"Permission denied listing media: {exc}")
            return []
        except InstagramAuthError as exc:
            self._create_remediation(f"Auth error listing media: {exc}")
            return []
        except InstagramAPIError as exc:
            logger.error("API error listing media: %s", exc)
            self.errors.append(str(exc))
            return []

    def _fetch_comments(self, media_id: str) -> List[Dict[str, Any]]:
        if self.mode == "mock":
            return MOCK_COMMENTS.get(media_id, [])
        assert self.helper is not None
        try:
            return self.helper.list_comments(
                media_id, limit=self.config.get("comment_limit", 20)
            )
        except (InstagramPermissionError, InstagramAuthError, InstagramAPIError) as exc:
            logger.warning("Could not fetch comments for %s: %s", media_id, exc)
            return []

    # ── Remediation ───────────────────────────────────────────────────────────

    def _create_remediation(self, reason: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        needs_action_dir = self.repo_root / "src" / "ai_operator" / "skills" / "gold" / "Needs_Action"
        needs_action_dir.mkdir(parents=True, exist_ok=True)
        path = needs_action_dir / f"remediation__instagram_watcher__{ts}.md"
        content = f"""# Remediation Required — Instagram Watcher

**Time**: {datetime.now(timezone.utc).isoformat()}
**Component**: instagram_watcher_skill
**Reason**: {reason}

## Suggested Actions

1. Verify `.secrets/instagram_credentials.json` exists with valid `access_token` and `ig_user_id`
2. Run: `python3 scripts/instagram_oauth_helper.py --status`
3. If token expired, generate a new long-lived token from Meta Developer Console
4. Re-run: `python3 scripts/instagram_watcher_skill.py --mode real --once`
"""
        path.write_text(content, encoding="utf-8")
        logger.warning("Remediation task created: %s", path.name)
        self.errors.append(reason)

    # ── Main watch cycle ──────────────────────────────────────────────────────

    def watch(self) -> Dict[str, Any]:
        """Run a single watch cycle. Returns summary dict."""
        # Reset per-cycle counters so each call to watch() reports its own stats
        self.created_count = 0
        self.skipped_count = 0
        self.errors = []

        logger.info("Instagram watcher starting (mode=%s)", self.mode)
        start = datetime.now(timezone.utc)

        media_items = self._fetch_media()
        logger.info("Fetched %d media items", len(media_items))

        for media in media_items:
            media_id = media.get("id", "")
            if not media_id:
                continue

            # New media item → intake wrapper
            if not self._already_processed(media_id):
                self._write_intake_wrapper(
                    media_id, "media_post", media
                )
                self._mark_processed(media_id)
                self.created_count += 1
            else:
                self.skipped_count += 1

            # Check comments for new interactions
            comments = self._fetch_comments(media_id)
            for comment in comments:
                cmt_id = comment.get("id", "")
                if not cmt_id:
                    continue
                if not self._already_processed(cmt_id):
                    self._write_intake_wrapper(
                        cmt_id, "comment",
                        {**comment, "permalink": media.get("permalink", "")},
                        parent_id=media_id,
                    )
                    self._mark_processed(cmt_id)
                    self.created_count += 1
                else:
                    self.skipped_count += 1

        self._save_checkpoint()

        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        summary = {
            "mode": self.mode,
            "created": self.created_count,
            "skipped": self.skipped_count,
            "errors": len(self.errors),
            "elapsed_ms": elapsed_ms,
            "status": "ok" if not self.errors else "partial",
        }
        logger.info("Watch cycle complete: %s", summary)
        return summary


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Instagram Watcher — perception-only intake (Gold Tier)"
    )
    parser.add_argument(
        "--mode",
        choices=["mock", "real"],
        default="mock",
        help="mock = synthetic data (default); real = Graph API",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one watch cycle and exit",
    )
    parser.add_argument(
        "--media-limit",
        type=int,
        default=10,
        help="Max media items to fetch per cycle (real mode)",
    )
    args = parser.parse_args()

    repo_root = get_repo_root()
    config: Dict[str, Any] = {
        "mode": args.mode,
        "repo_root": str(repo_root),
        "checkpoint_path": str(repo_root / "Logs" / "instagram_watcher_checkpoint.json"),
        "media_limit": args.media_limit,
        "comment_limit": 20,
    }

    watcher = InstagramWatcher(config)

    print("SERVICE_READY", flush=True)
    try:
        READY_FILE.write_text("ready")
    except Exception:
        pass

    if args.once:
        summary = watcher.watch()
        print(json.dumps(summary, indent=2, default=str))
        return 0 if summary["status"] == "ok" else 1

    # Continuous mode (for PM2 — loops every 5 min)
    import time
    import signal

    running = True

    def _stop(sig, frame):  # type: ignore
        nonlocal running
        logger.info("Shutdown signal — stopping watcher…")
        running = False

    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    POLL_INTERVAL = 300  # 5 minutes

    while running:
        try:
            watcher.watch()
        except Exception as exc:
            logger.error("Watch cycle failed: %s", exc)
        if running:
            logger.info("Next cycle in %ds", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)

    READY_FILE.unlink(missing_ok=True)
    logger.info("Instagram watcher stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
