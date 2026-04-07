#!/usr/bin/env python3
"""
Personal AI Employee — 24/7 Agent Daemon

Runs every watcher, the Ralph orchestrator, and scheduled summary/briefing tasks
in a continuous loop. Never exits unless told to (SIGTERM / SIGINT / --stop).

Safety guarantees are UNCHANGED from the original design:
  - Watchers are perception-only (never post, never send)
  - Ralph only CREATES plans — never executes them directly
  - All send/post actions still require human approval in the web UI
  - Approval gates cannot be bypassed by this daemon

Task schedule
─────────────────────────────────────────────────────────────────
  approval_monitor       every   3 min   (most important — watches for approved plans)
  gmail_watcher          every   5 min   (perception: new emails → intake wrappers)
  linkedin_watcher       every  10 min   (perception: new notifications → intake wrappers)
  filesystem_watcher     every  15 min   (perception: new vault files)
  ralph_orchestrator     every  15 min   (planning: decides and queues plans for approval)
  daily_summary          once / day  at 20:00 UTC
  weekly_briefing        once / week on Monday at 08:00 UTC

Files written
─────────────────────────────────────────────────────────────────
  Logs/agent_daemon.log  — append-only activity log
  Logs/agent_daemon.pid  — PID while running

Usage
─────────────────────────────────────────────────────────────────
  python3 scripts/agent_daemon.py           # run in foreground (use start_agent.sh instead)
  python3 scripts/agent_daemon.py --status  # check running / not running
"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent.resolve()
SCRIPTS_DIR = REPO_ROOT / "scripts"
LOGS_DIR    = REPO_ROOT / "Logs"
PID_FILE    = LOGS_DIR / "agent_daemon.pid"
LOG_FILE    = LOGS_DIR / "agent_daemon.log"
PY          = sys.executable   # same python that launched this script
READY_FILE  = Path("/tmp/agent_daemon.ready")
atexit.register(lambda: READY_FILE.unlink(missing_ok=True))

# ── Load env vars from apps/web/.env.local if available ───────────────────────
_env_file = REPO_ROOT / "apps" / "web" / ".env.local"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)   # don't clobber already-set vars
    except ImportError:
        # dotenv not installed — parse manually (key=value, skip comments)
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k = _k.strip()
                _v = _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

# ── Interval task definitions ──────────────────────────────────────────────────
#   last_run  = unix timestamp of last successful launch
#   running   = True while the background thread is alive
INTERVAL_TASKS: list[dict] = [
    {
        "name": "approval_monitor",
        "cmd":  [PY, str(SCRIPTS_DIR / "brain_monitor_approvals_skill.py")],
        "interval": 180,    # 3 min
        "timeout":  120,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name": "gmail_watcher",
        "cmd":  [PY, str(SCRIPTS_DIR / "gmail_watcher_skill.py"), "--since-checkpoint"],
        "interval": 300,    # 5 min
        "timeout":  150,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name": "linkedin_watcher",
        "cmd":  [PY, str(SCRIPTS_DIR / "linkedin_watcher_skill.py"), "--mode", "real"],
        "interval": 600,    # 10 min
        "timeout":  180,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name": "instagram_watcher",
        "cmd":  [PY, str(SCRIPTS_DIR / "instagram_watcher_skill.py"), "--once"],
        "interval": 600,    # 10 min
        "timeout":  120,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name": "odoo_watcher",
        "cmd":  [PY, str(SCRIPTS_DIR / "odoo_watcher_skill.py"), "--mode", "mock"],
        "interval": 900,    # 15 min
        "timeout":  120,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name": "whatsapp_watcher",
        "cmd":  [PY, str(SCRIPTS_DIR / "whatsapp_watcher_skill.py"), "--mode", "mock"],
        "interval": 900,    # 15 min
        "timeout":  120,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name": "filesystem_watcher",
        "cmd":  [PY, str(SCRIPTS_DIR / "watcher_skill.py")],
        "interval": 900,    # 15 min
        "timeout":  180,
        "last_run": 0.0,
        "running":  False,
    },
    {
        "name":     "ralph_orchestrator",
        "cmd":      [PY, str(SCRIPTS_DIR / "brain_ralph_loop_orchestrator_skill.py"), "--execute"],
        "interval": 900,    # 15 min
        "timeout":  310,    # ralph can run up to 5 min internally
        "last_run": 0.0,
        "running":  False,
        # Exit code 2 = "waiting for approval" — not an error
        "ok_codes": {0, 2},
    },
]

# ── Timed task definitions (once per day / once per week) ─────────────────────
#   hour_utc      = UTC hour to fire
#   weekday       = 0–6 (Mon–Sun), or None for every day
#   last_run_date = date() of last fire; None = never
TIMED_TASKS: list[dict] = [
    {
        "name":          "daily_summary",
        "cmd":           [PY, str(SCRIPTS_DIR / "brain_generate_daily_summary_skill.py")],
        "hour_utc":      20,
        "weekday":       None,   # every day
        "timeout":       120,
        "last_run_date": None,
    },
    {
        "name":          "weekly_briefing",
        "cmd":           [PY, str(SCRIPTS_DIR / "brain_generate_weekly_ceo_briefing_skill.py")],
        "hour_utc":      8,
        "weekday":       0,      # Monday
        "timeout":       180,
        "last_run_date": None,
    },
]

# ── Shutdown event ─────────────────────────────────────────────────────────────
_shutdown = threading.Event()


def _handle_signal(sig, _frame):
    _log(f"Signal {sig} received — shutting down after current tasks finish…")
    _shutdown.set()


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── Logging ────────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()


def _log(msg: str, task: str = "daemon") -> None:
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [{task:<22}] {msg}\n"
    with _log_lock:
        sys.stdout.write(line)
        sys.stdout.flush()
        # Only write directly to file when stdout is a terminal.
        # When launched via nohup (stdout already redirected to LOG_FILE),
        # writing here too would duplicate every line.
        if sys.stdout.isatty():
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except Exception:
                pass   # never crash on logging


# ── Subprocess runner (runs in its own thread) ─────────────────────────────────
def _run_task_thread(task: dict) -> None:
    """Execute a task as a subprocess, log the result, release the running flag."""
    name  = task["name"]
    start = time.time()
    _log("▶ start", task=name)
    try:
        result = subprocess.run(
            task["cmd"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=task.get("timeout", 300),
            env={**os.environ},   # pass current env (includes .env.local vars)
        )
        ms = int((time.time() - start) * 1000)
        ok_codes = task.get("ok_codes", {0})
        if result.returncode in ok_codes:
            note = " (approval pending)" if result.returncode == 2 else ""
            _log(f"✓ done ({ms} ms){note}", task=name)
        else:
            stderr = result.stderr.strip()[:400]
            _log(f"✗ exit {result.returncode} ({ms} ms) — {stderr}", task=name)
    except subprocess.TimeoutExpired:
        ms = int((time.time() - start) * 1000)
        _log(f"⏱ timeout ({ms} ms) — process killed", task=name)
    except FileNotFoundError:
        _log(f"💥 script not found: {task['cmd'][1]}", task=name)
    except Exception as exc:
        _log(f"💥 unexpected error: {exc}", task=name)
    finally:
        task["running"] = False


def _maybe_spawn(task: dict) -> None:
    """Spawn the task if it's not already running."""
    if task["running"]:
        _log("⟳ still running — skipping cycle", task=task["name"])
        return
    task["running"]  = True
    task["last_run"] = time.time()
    threading.Thread(target=_run_task_thread, args=(task,), daemon=True).start()


# ── Timed task runner ──────────────────────────────────────────────────────────
def _maybe_spawn_timed(task: dict, utc_now: datetime) -> None:
    """Fire a timed task if the correct hour (and weekday) has arrived today."""
    if utc_now.hour != task["hour_utc"]:
        return
    if task["weekday"] is not None and utc_now.weekday() != task["weekday"]:
        return
    today = utc_now.date()
    if task["last_run_date"] == today:
        return   # already ran today

    task["last_run_date"] = today
    name = task["name"]
    _log("▶ start (scheduled)", task=name)

    def _run() -> None:
        start = time.time()
        try:
            result = subprocess.run(
                task["cmd"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=task.get("timeout", 300),
                env={**os.environ},
            )
            ms = int((time.time() - start) * 1000)
            ok = result.returncode == 0
            _log(f"{'✓' if ok else '✗'} done ({ms} ms)", task=name)
        except Exception as exc:
            _log(f"💥 {exc}", task=name)

    threading.Thread(target=_run, daemon=True).start()


# ── PID helpers ────────────────────────────────────────────────────────────────
def _write_pid() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Main event loop ────────────────────────────────────────────────────────────
def run_daemon() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _write_pid()
    _log(f"=== Personal AI Employee — daemon started (PID {os.getpid()}) ===")
    _log(f"REPO_ROOT  = {REPO_ROOT}")
    _log(f"Python     = {PY}")
    _log(f"Log file   = {LOG_FILE}")
    _log("Tasks: " + ", ".join(t["name"] for t in INTERVAL_TASKS + TIMED_TASKS))

    print("SERVICE_READY")
    try:
        READY_FILE.write_text("ready")
    except Exception:
        pass

    last_heartbeat = time.time()

    try:
        while not _shutdown.is_set():
            now     = time.time()
            utc_now = datetime.now(timezone.utc)

            # Interval-based tasks
            for task in INTERVAL_TASKS:
                if (now - task["last_run"]) >= task["interval"]:
                    _maybe_spawn(task)

            # Time-based tasks (daily / weekly)
            for task in TIMED_TASKS:
                _maybe_spawn_timed(task, utc_now)

            # Heartbeat every 30 minutes
            if now - last_heartbeat >= 1800:
                _log(f"♥ alive — tasks running: {sum(1 for t in INTERVAL_TASKS if t['running'])}")
                last_heartbeat = now

            _shutdown.wait(30)   # poll every 30 s; wakes immediately on signal

    finally:
        _log("=== daemon stopped ===")
        _remove_pid()
        READY_FILE.unlink(missing_ok=True)


# ── Outer restart wrapper ──────────────────────────────────────────────────────
def run_with_restart() -> None:
    """
    Wrap the daemon loop so that an unexpected Python exception
    causes an automatic restart after a 10-second back-off.
    SIGTERM/SIGINT still causes a clean exit.
    """
    while True:
        try:
            run_daemon()
        except Exception as exc:
            if _shutdown.is_set():
                break
            _log(f"💥 daemon crashed ({exc}) — restarting in 10 s")
            time.sleep(10)

        if _shutdown.is_set():
            break

    _log("=== daemon exited cleanly ===")


# ── CLI ────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Personal AI Employee — 24/7 Agent Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/agent_daemon.py           # run (use start_agent.sh for background)
  python3 scripts/agent_daemon.py --status  # check if running
""",
    )
    parser.add_argument("--status", action="store_true", help="Check whether the daemon is running")
    args = parser.parse_args()

    if args.status:
        if PID_FILE.exists():
            pid_str = PID_FILE.read_text().strip()
            try:
                os.kill(int(pid_str), 0)
                print(f"✓ Daemon is running (PID {pid_str})")
            except (ProcessLookupError, ValueError):
                print("✗ Daemon is NOT running (stale PID file)")
        else:
            print("✗ Daemon is NOT running")
        return

    run_with_restart()


if __name__ == "__main__":
    main()
