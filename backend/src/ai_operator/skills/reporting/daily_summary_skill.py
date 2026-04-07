"""
Daily Summary Skill — generate a structured activity summary for today.

Public API:
    generate_daily_summary() -> str
        Returns a formatted markdown summary of today's logged activity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json


def generate_daily_summary() -> str:
    """
    Read today's audit log and return a human-readable markdown summary.

    Returns a fallback message if no log exists for today.
    """
    logs_dir = _find_logs_dir()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = logs_dir / f"{today}.json" if logs_dir else None

    entries = []
    if log_file and log_file.exists():
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    if not entries:
        return (
            f"**Daily Summary — {today}**\n\n"
            "No activity logged today yet.\n\n"
            "The system will record actions here as agents execute tasks."
        )

    success_count = sum(1 for e in entries if e.get("result") == "success")
    failure_count = sum(1 for e in entries if e.get("result") != "success")
    action_types = [e.get("action_type", "unknown") for e in entries]

    lines = [
        f"**Daily Summary — {today}**\n",
        f"- Total actions: **{len(entries)}**",
        f"- Successful: **{success_count}**",
        f"- Failed: **{failure_count}**",
        "",
        "**Actions:**",
    ]
    for entry in entries[-10:]:  # Show last 10
        ts = entry.get("timestamp", "")[:16].replace("T", " ")
        action = entry.get("action_type", "unknown").replace("_", " ").title()
        result = entry.get("result", "?")
        icon = "✓" if result == "success" else "✗"
        lines.append(f"  {icon} `{ts}` — {action}")

    return "\n".join(lines)


def _find_logs_dir() -> Path | None:
    current = Path(__file__).parent
    while current != current.parent:
        candidate = current / "Logs"
        if candidate.exists():
            return candidate
        current = current.parent
    return None
