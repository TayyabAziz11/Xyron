#!/usr/bin/env python3
"""
Personal AI Employee — Instagram Executor Skill
Gold Tier — G-M4: Instagram MCP Execution Layer

Purpose: Execute approved Instagram actions via the Graph API.
         Reads approved plans from Approved/, executes the action,
         logs to Logs/mcp_actions.log, updates Dashboard.md.
Tier    : Gold
Skill ID: G-M4-T06

CRITICAL SAFETY RULES:
1. REQUIRES an approved plan in Approved/ directory
2. Dry-run is DEFAULT (--execute flag required for real actions)
3. NEVER bypass approval gates
4. STOP immediately on any failure
5. NEVER claim success if any action failed
6. Create remediation tasks for all failures
7. Fail safe on Instagram API limitations (rate limits, permissions)

Supported Operations:
- create_post_text: Text-only Instagram post
- create_post_image: Image post (image_url + caption)
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Path bootstrap ─────────────────────────────────────────────────────────────
try:
    from ai_operator.core.mcp_helpers import log_json, redact_pii, get_repo_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from ai_operator.core.mcp_helpers import log_json, redact_pii, get_repo_root
    except ImportError:
        def log_json(log_path: Path, data: Dict) -> None:  # type: ignore
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(data) + "\n")

        def redact_pii(text: str) -> str:  # type: ignore
            return re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                          "[EMAIL]", text)

        def get_repo_root() -> Path:  # type: ignore
            current = Path(__file__).parent
            while current != current.parent:
                if (current / "system_log.md").exists():
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
    format="%(asctime)s [ig_executor] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Supported operations
SUPPORTED_OPS = {"create_post_text", "create_post_image"}


class InstagramExecutor:
    """
    Execute approved Instagram plans via the Graph API.

    Safety guarantees:
    - dry_run=True is the default; --execute is required for real calls
    - Parses the plan's ## Actions JSON block (same format as social executor)
    - Stops immediately on first failure
    - Creates a remediation task on any failure
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.repo_root  = Path(config.get("repo_root") or get_repo_root()) if config else Path(get_repo_root())
        self.approved_dir = self.repo_root / "Approved"
        self.actions_log  = self.repo_root / "Logs" / "mcp_actions.log"
        self.system_log   = self.repo_root / "system_log.md"
        self.actions_log.parent.mkdir(parents=True, exist_ok=True)

        self.helper: Optional[InstagramAPIHelper] = None

    # ── Logging helpers ────────────────────────────────────────────────────────

    def _log_action(self, data: Dict[str, Any]) -> None:
        try:
            log_json(self.actions_log, data)
        except Exception as exc:
            logger.error("Failed to log action: %s", exc)

    def _log_system(self, msg: str) -> None:
        try:
            ts  = datetime.now(timezone.utc).isoformat()
            entry = f"\n**[{ts}]** {msg}\n"
            with open(self.system_log, "a", encoding="utf-8") as fh:
                fh.write(entry)
        except Exception as exc:
            logger.error("Failed to write system_log: %s", exc)

    # ── Remediation ────────────────────────────────────────────────────────────

    def _create_remediation(self, title: str, description: str, plan_ref: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        needs_dir = self.repo_root / "src" / "ai_operator" / "skills" / "gold" / "Needs_Action"
        needs_dir.mkdir(parents=True, exist_ok=True)
        path = needs_dir / f"remediation__instagram_executor__{ts}.md"
        content = f"""# Remediation Required — Instagram Executor

**Time**: {datetime.now(timezone.utc).isoformat()}
**Component**: brain_execute_instagram_with_mcp_skill
**Title**: {title}
**Plan**: {plan_ref}

## Problem

{description}

## Suggested Actions

1. Verify `.secrets/instagram_credentials.json` has valid `access_token` and `ig_user_id`
2. Run: `python3 scripts/instagram_oauth_helper.py --status`
3. Check rate limits in `Logs/mcp_actions.log`
4. Re-approve plan and re-run: `python3 scripts/brain_execute_instagram_with_mcp_skill.py --execute`

## Safety Notes

- NEVER bypass approval gates
- NEVER use --execute without an approved plan
"""
        path.write_text(content, encoding="utf-8")
        logger.warning("Remediation task created: %s", path.name)
        self._log_system(f"Remediation task created: {title}")

    # ── Plan helpers ───────────────────────────────────────────────────────────

    def _find_approved_plan(self, plan_id: Optional[str] = None) -> Optional[Path]:
        if not self.approved_dir.exists():
            logger.error("Approved/ directory not found: %s", self.approved_dir)
            return None

        if plan_id:
            # Accept both WEBPLAN_* and plan__* naming
            candidates = list(self.approved_dir.glob(f"*{plan_id}*.md"))
            if not candidates:
                logger.error("No approved plan found with ID: %s", plan_id)
                return None
            return candidates[0]

        # Most recent WEBPLAN_*_Instagram_* plan
        ig_plans = sorted(
            list(self.approved_dir.glob("WEBPLAN_*_Instagram_*.md"))
            + list(self.approved_dir.glob("WEBPLAN_*_instagram_*.md")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if ig_plans:
            return ig_plans[0]

        # Fallback: any most-recent plan
        all_plans = sorted(
            self.approved_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return all_plans[0] if all_plans else None

    def _parse_plan(self, plan_path: Path) -> Dict[str, Any]:
        """
        Parse an approved plan for Instagram actions.

        Supports:
          1. ## Actions JSON  block — array of {operation, parameters}
          2. YAML front-matter fields: operation, caption, image_url
        """
        try:
            content = plan_path.read_text(encoding="utf-8")
            actions: List[Dict[str, Any]] = []

            # 1. Try Actions JSON block
            json_match = re.search(
                r"##\s+Actions\s+JSON\s*\n\s*```(?:json)?\s*\n(.*?)\n\s*```",
                content, re.DOTALL | re.IGNORECASE,
            )
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1))
                    actions = parsed if isinstance(parsed, list) else [parsed]
                    logger.info("Parsed %d action(s) from JSON block", len(actions))
                except json.JSONDecodeError as exc:
                    return {"error": f"Invalid JSON block: {exc}", "parsed_successfully": False}

            # 2. Fallback: YAML front-matter (operation + parameters)
            if not actions:
                fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
                if fm_match:
                    fm_text = fm_match.group(1)
                    operation = ""
                    caption   = ""
                    image_url = ""
                    for line in fm_text.splitlines():
                        k, _, v = line.partition(":")
                        k = k.strip().lower()
                        v = v.strip().strip('"').strip("'")
                        if k == "operation":
                            operation = v
                        elif k == "caption":
                            caption = v
                        elif k in ("image_url", "imageurl", "media_url"):
                            image_url = v
                    if operation:
                        params: Dict[str, Any] = {"caption": caption}
                        if image_url:
                            params["image_url"] = image_url
                        actions = [{"server": "instagram", "operation": operation, "parameters": params}]
                        logger.info("Parsed 1 action from YAML front-matter")

            if not actions:
                logger.warning("No actions found in plan")
                return {
                    "plan_path": str(plan_path),
                    "actions": [],
                    "parsed_successfully": True,
                    "parse_note": "No actions found",
                }

            # Normalise
            normalised = []
            for idx, a in enumerate(actions):
                op     = a.get("operation") or a.get("tool") or ""
                params = a.get("parameters") or a.get("params") or {}
                server = a.get("server", "instagram").lower()
                if not op:
                    logger.warning("Action %d missing operation — skipped", idx)
                    continue
                normalised.append({"server": server, "operation": op, "params": params})

            return {
                "plan_path": str(plan_path),
                "actions": normalised,
                "parsed_successfully": True,
                "actions_count": len(normalised),
            }

        except Exception as exc:
            logger.error("Failed to parse plan: %s", exc)
            return {"error": str(exc), "parsed_successfully": False}

    # ── Validation ─────────────────────────────────────────────────────────────

    def _validate(self, operation: str, params: Dict[str, Any]) -> Tuple[bool, str]:
        if operation not in SUPPORTED_OPS:
            return False, f"Unsupported operation: {operation}"
        caption = params.get("caption", "")
        if not caption:
            return False, "Missing 'caption' parameter"
        if len(caption) > 2200:
            return False, f"Caption too long: {len(caption)} chars (limit 2200)"
        if operation == "create_post_image" and not params.get("image_url"):
            return False, "create_post_image requires 'image_url' parameter"
        return True, ""

    # ── Action execution ───────────────────────────────────────────────────────

    def _execute_action(
        self,
        operation: str,
        params: Dict[str, Any],
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        ok, err = self._validate(operation, params)
        if not ok:
            logger.error("Validation failed: %s", err)
            return {"success": False, "error": err, "operation": operation}

        caption   = params.get("caption", "")
        image_url = params.get("image_url", "")

        action_log: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server":    "instagram",
            "tool":      operation,
            "params": {
                "caption_preview": redact_pii(caption[:100]),
                "image_url":       image_url or "(text-only)",
            },
            "dry_run": dry_run,
            "status":  "pending",
        }

        if dry_run:
            print(f"\n  📋 Action Preview (DRY-RUN):")
            print(f"     Operation : {operation}")
            print(f"     Caption   : {redact_pii(caption[:120])}{'…' if len(caption) > 120 else ''}")
            print(f"     Length    : {len(caption)}/2200 chars")
            if image_url:
                print(f"     Image URL : {image_url[:80]}")
            action_log["status"] = "dry_run_success"
            action_log["result"] = "Dry-run only — no API call made"
            self._log_action(action_log)
            return {"success": True, "dry_run": True, "operation": operation, "result": "Dry-run successful"}

        # Real execution
        if InstagramAPIHelper is None:
            err = "InstagramAPIHelper not available — check Python path"
            action_log["status"] = "failed"
            action_log["error"]  = err
            self._log_action(action_log)
            return {"success": False, "error": err, "operation": operation}

        if self.helper is None:
            try:
                self.helper = InstagramAPIHelper(secrets_dir=self.repo_root / ".secrets")
                self.helper.check_auth()
            except InstagramAuthError as exc:
                err = f"Instagram auth failed: {exc}"
                self._create_remediation(err, str(exc), "(auth)")
                action_log["status"] = "failed"
                action_log["error"]  = err
                self._log_action(action_log)
                return {"success": False, "error": err}
            except Exception as exc:
                err = f"Instagram init failed: {exc}"
                action_log["status"] = "failed"
                action_log["error"]  = err
                self._log_action(action_log)
                return {"success": False, "error": err}

        print(f"\n  🚀 Executing: {operation}")
        try:
            result_data: Dict[str, Any] = {}

            if operation == "create_post_text":
                # Text-only = create container with no media_type (carousel-placeholder pattern)
                # Instagram doesn't support purely text posts via Graph API (requires image).
                # Best practice: upload a 1x1 blank image or use a branded image.
                # For now we raise a clear fail-safe instead of silently creating broken posts.
                action_log["status"] = "failed"
                action_log["error"]  = (
                    "Instagram Graph API does not support text-only posts. "
                    "Please provide an image_url and use create_post_image."
                )
                self._log_action(action_log)
                return {"success": False, "error": action_log["error"], "operation": operation}

            elif operation == "create_post_image":
                result_data = self.helper.create_post_with_image(  # type: ignore[union-attr]
                    image_url=image_url,
                    caption=caption,
                    dry_run=False,
                )

            post_id = result_data.get("id", "unknown")
            print(f"     ✅ Post published — ID: {post_id}")
            action_log["status"] = "success"
            action_log["result"] = f"Post created: {post_id}"
            action_log["post_id"] = post_id
            self._log_action(action_log)
            return {"success": True, "operation": operation, "post_id": post_id, "result": result_data}

        except InstagramPermissionError as exc:
            err = f"Permission denied: {exc}"
        except InstagramAuthError as exc:
            err = f"Auth error: {exc}"
        except InstagramAPIError as exc:
            err = f"API error: {exc}"
        except Exception as exc:
            err = f"Unexpected error: {exc}"

        print(f"     ❌ Failed: {err}")
        action_log["status"] = "failed"
        action_log["error"]  = err
        self._log_action(action_log)
        return {"success": False, "error": err, "operation": operation}

    # ── Dashboard update ───────────────────────────────────────────────────────

    def _update_dashboard(self, operation: str, status: str) -> None:
        try:
            dashboard = self.repo_root / "Dashboard.md"
            if not dashboard.exists():
                return
            content = dashboard.read_text(encoding="utf-8")
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            entry = f"instagram.{operation} - {status} - {ts}"
            pattern = r"(\*\*Last External Action \(Gold\)\*\*:)(.*?)(\n|$)"
            if re.search(pattern, content):
                content = re.sub(pattern, rf"\1 {entry}\3", content)
            else:
                content += f"\n\n**Last External Action (Gold)**: {entry}\n"
            dashboard.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to update Dashboard.md: %s", exc)

    # ── Main execute ───────────────────────────────────────────────────────────

    def execute(
        self,
        plan_id: Optional[str] = None,
        dry_run: bool = True,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        if verbose:
            logger.setLevel(logging.DEBUG)

        mode = "DRY-RUN" if dry_run else "EXECUTE"
        logger.info("Instagram executor starting (%s mode)", mode)
        start = datetime.now(timezone.utc)

        plan_path = self._find_approved_plan(plan_id)
        if not plan_path:
            err = f"No approved Instagram plan found{f' (ID: {plan_id})' if plan_id else ''}"
            logger.error(err)
            self._create_remediation("Approved plan not found", err, plan_id or "unknown")
            return {"success": False, "error": err, "actions_attempted": 0, "actions_succeeded": 0}

        logger.info("Found plan: %s", plan_path.name)

        plan_data = self._parse_plan(plan_path)
        if not plan_data.get("parsed_successfully"):
            err = f"Failed to parse plan: {plan_data.get('error', 'unknown')}"
            logger.error(err)
            self._create_remediation("Plan parse failed", err, str(plan_path))
            return {"success": False, "error": err, "plan_path": str(plan_path)}

        actions = plan_data.get("actions", [])
        if not actions:
            logger.warning("No actions in plan — nothing to do")
            return {
                "success": True,
                "dry_run": dry_run,
                "plan_path": str(plan_path),
                "actions_attempted": 0,
                "actions_succeeded": 0,
                "note": "No actions found in plan",
            }

        results: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []

        for idx, action in enumerate(actions):
            op     = action.get("operation", "")
            params = action.get("params", {})
            logger.info("Action %d/%d: %s", idx + 1, len(actions), op)

            result = self._execute_action(op, params, dry_run=dry_run)
            results.append(result)

            if not result.get("success"):
                failed.append({"index": idx, "operation": op, "error": result.get("error")})
                logger.error("Action failed — stopping immediately (safety)")
                break

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        succeeded = sum(1 for r in results if r.get("success"))
        overall_ok = len(failed) == 0 and len(results) > 0

        summary = (
            f"Instagram executor {'dry-run' if dry_run else 'execution'} complete: "
            f"plan={plan_path.name}, {len(results)} attempted, {succeeded} succeeded, "
            f"{len(failed)} failed, {duration:.2f}s"
        )
        self._log_system(summary)
        logger.info(summary)

        if not dry_run:
            if overall_ok:
                last = results[-1]
                self._update_dashboard(last.get("operation", "unknown"), "success")
            else:
                if failed:
                    self._create_remediation(
                        f"{len(failed)} action(s) failed",
                        "\n".join(f"- {fa['operation']}: {fa['error']}" for fa in failed),
                        str(plan_path),
                    )
                    self._update_dashboard(failed[0]["operation"], "failed")

        return {
            "success":           overall_ok,
            "dry_run":           dry_run,
            "plan_path":         str(plan_path),
            "actions_attempted": len(results),
            "actions_succeeded": succeeded,
            "actions_failed":    len(failed),
            "failed_actions":    failed,
            "results":           results,
            "duration":          duration,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Instagram Executor — execute approved plans via Graph API (Gold Tier G-M4)"
    )
    parser.add_argument("--plan-id", help="Specific plan ID to execute")
    parser.add_argument("--execute", action="store_true",
                        help="Perform real API calls (default: dry-run). REQUIRES approved plan!")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    dry_run = not args.execute

    if dry_run:
        print("🔍 DRY-RUN MODE: No real API calls will be made")
        print("   Use --execute to post (requires approved plan in Approved/)")
    else:
        print("⚠️  EXECUTE MODE: Real Instagram API calls will be made!")

    executor = InstagramExecutor()
    result = executor.execute(plan_id=args.plan_id, dry_run=dry_run, verbose=args.verbose)

    if result["success"]:
        if dry_run:
            print(f"\n✅ Dry-run successful")
            print(f"   Plan: {result.get('plan_path', 'N/A')}")
            print(f"   Actions found: {result.get('actions_attempted', 0)}")
        else:
            print(f"\n✅ Execution successful")
            print(f"   Actions executed: {result['actions_succeeded']}/{result['actions_attempted']}")
        return 0
    else:
        print(f"\n❌ Execution failed: {result.get('error', 'unknown')}")
        if result.get("failed_actions"):
            for fa in result["failed_actions"]:
                print(f"   - {fa['operation']}: {fa['error']}")
        print("   Remediation task created in Needs_Action/")
        return 1


if __name__ == "__main__":
    sys.exit(main())
