"""
Weekly Briefing Skill — generate a CEO-level weekly summary.

Public API:
    generate_weekly_briefing() -> str
        Returns a formatted markdown briefing covering the last 7 days.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate_weekly_briefing() -> str:
    """
    Aggregate audit logs from the past 7 days into a structured briefing.

    Returns a formatted markdown string suitable for executive review.
    """
    logs_dir = _find_logs_dir()
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)

    all_entries = []
    if logs_dir and logs_dir.exists():
        for log_file in sorted(logs_dir.glob("*.json"), reverse=True):
            try:
                file_date = datetime.strptime(log_file.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if file_date < week_start:
                break
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            all_entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    period = f"{week_start.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"

    if not all_entries:
        return (
            f"**Weekly Briefing — {period}**\n\n"
            "No activity recorded this week yet.\n\n"
            "Actions taken by AI Operator agents will appear here."
        )

    total = len(all_entries)
    success = sum(1 for e in all_entries if e.get("result") == "success")
    failures = total - success
    action_counts = Counter(e.get("action_type", "unknown") for e in all_entries)
    top_actions = action_counts.most_common(5)

    lines = [
        f"**Weekly Briefing — {period}**\n",
        "## Overview",
        f"- Total agent actions: **{total}**",
        f"- Success rate: **{int(success/total*100)}%** ({success}/{total})",
        f"- Failures requiring attention: **{failures}**",
        "",
        "## Top Actions This Week",
    ]
    for action, count in top_actions:
        label = action.replace("_", " ").title()
        lines.append(f"  - {label}: {count}×")

    lines += [
        "",
        "## Recent Failures",
    ]
    failures_list = [e for e in all_entries if e.get("result") != "success"][-5:]
    if failures_list:
        for e in failures_list:
            ts = e.get("timestamp", "")[:16].replace("T", " ")
            action = e.get("action_type", "unknown").replace("_", " ").title()
            lines.append(f"  - `{ts}` {action}: {e.get('error', 'unknown error')}")
    else:
        lines.append("  - None. All systems operating normally.")

    return "\n".join(lines)


def _find_logs_dir() -> Path | None:
    current = Path(__file__).parent
    while current != current.parent:
        candidate = current / "Logs"
        if candidate.exists():
            return candidate
        current = current.parent
    return None
