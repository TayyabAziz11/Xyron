#!/usr/bin/env python3
"""daily_cycle.py — Daily orchestration run at 03:00 UTC via PM2 cron_restart."""
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR  = REPO_ROOT / "Logs"
LOG_FILE  = LOGS_DIR / "daily_cycle.log"
PY        = sys.executable
SKILLS    = [
    "scripts/instagram_watcher_skill.py",
    "scripts/brain_generate_weekly_ceo_briefing_skill.py",
    "scripts/brain_generate_accounting_audit_skill.py",
    "scripts/brain_ralph_loop_orchestrator_skill.py",
]


def log(msg: str) -> None:
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


print("DAILY_CYCLE_STARTED", flush=True)
log("=== daily_cycle started ===")

for skill_path in SKILLS:
    log(f"▶ {skill_path}")
    start = time.time()
    try:
        result = subprocess.run(
            [PY, str(REPO_ROOT / skill_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        if result.returncode == 0:
            log(f"✓ {skill_path} ({elapsed_ms} ms)")
        else:
            stderr = result.stderr.strip()[:400]
            log(f"✗ {skill_path} exit={result.returncode} ({elapsed_ms} ms) — {stderr}")
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.time() - start) * 1000)
        log(f"⏱ {skill_path} timeout ({elapsed_ms} ms)")
    except FileNotFoundError:
        log(f"💥 {skill_path} not found — skipping")
    except Exception as exc:
        log(f"💥 {skill_path} unexpected error: {exc}")

log("=== daily_cycle completed ===")
print("DAILY_CYCLE_COMPLETED", flush=True)
sys.exit(0)
