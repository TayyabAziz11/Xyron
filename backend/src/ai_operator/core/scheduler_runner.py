#!/usr/bin/env python3
"""
Personal AI Employee - Scheduler Runner
Silver Tier - M7: Scheduled Task Automation

Purpose: Wrapper script for Windows Task Scheduler to run skills safely.
Features:
- Execution timing and logging
- Exception capture and crash loop prevention
- Append-only logging to Logs/scheduler.log
- Safe exit codes

CRITICAL: This wrapper ONLY runs scheduled tasks. It does NOT bypass approval gates.
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class SchedulerRunner:
    """Safe wrapper for scheduled task execution."""

    def __init__(self):
        """Initialize scheduler runner."""
        self.base_dir = Path(__file__).parent
        self.log_file = self.base_dir / 'Logs' / 'scheduler.log'
        self.system_log = self.base_dir / 'system_log.md'

        # Ensure Logs/ directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def run_task(self, task_name: str, command: str) -> Dict:
        """
        Run a scheduled task with timing and error handling.

        Args:
            task_name: Human-readable task name
            command: Command to execute (skill script + args)

        Returns:
            Dict with execution results
        """
        start_time = datetime.now(timezone.utc)
        start_timestamp = start_time.strftime('%Y-%m-%d %H:%M:%S UTC')

        result = {
            'task_name': task_name,
            'command': command,
            'start_time': start_timestamp,
            'success': False,
            'duration_ms': 0,
            'error': None,
            'output': ''
        }

        try:
            # Execute command in base directory
            process = subprocess.run(
                command,
                shell=True,
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            # Calculate duration
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds() * 1000
            result['duration_ms'] = int(duration)

            # Check exit code
            if process.returncode == 0:
                result['success'] = True
                result['output'] = process.stdout.strip()
            else:
                result['success'] = False
                result['error'] = f"Exit code {process.returncode}: {process.stderr.strip()}"
                result['output'] = process.stdout.strip()

        except subprocess.TimeoutExpired:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds() * 1000
            result['duration_ms'] = int(duration)
            result['error'] = "Command timeout (5 minutes exceeded)"

        except Exception as e:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds() * 1000
            result['duration_ms'] = int(duration)
            result['error'] = f"Exception: {str(e)}"

        # Log result
        self._log_execution(result)
        self._log_to_system(result)

        return result

    def _log_execution(self, result: Dict):
        """
        Log execution to Logs/scheduler.log.

        Args:
            result: Execution result dict
        """
        log_entry = f"""
[{result['start_time']}] SCHEDULED TASK
Task: {result['task_name']}
Command: {result['command']}
Duration: {result['duration_ms']}ms
Success: {result['success']}
"""

        if result['error']:
            log_entry += f"Error: {result['error']}\n"

        if result['output']:
            # Truncate output if too long
            output = result['output']
            if len(output) > 500:
                output = output[:500] + "... (truncated)"
            log_entry += f"Output: {output}\n"

        log_entry += "\n" + "=" * 70 + "\n"

        # Append to log file
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    def _log_to_system(self, result: Dict):
        """
        Log execution to system_log.md.

        Args:
            result: Execution result dict
        """
        status = "OK" if result['success'] else "FAILED"

        log_entry = f"""
[{result['start_time']}] SCHEDULED TASK
- Task: {result['task_name']}
- Command: {result['command']}
- Duration: {result['duration_ms']}ms
- Status: {status}
"""

        if result['error']:
            log_entry += f"- Error: {result['error']}\n"

        log_entry += "- Skill: scheduler_runner (M7)\n"
        log_entry += f"- Outcome: {status}\n\n"

        # Append to system_log.md
        if self.system_log.exists():
            with open(self.system_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        else:
            print(f"Warning: system_log.md not found at {self.system_log}")


def main():
    """CLI interface for scheduler runner."""
    parser = argparse.ArgumentParser(
        description='Scheduler Runner - Safe wrapper for scheduled tasks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run filesystem watcher
  python scheduler_runner.py --task filesystem_watcher --command "brain_watch_filesystem_skill.py --once"

  # Run gmail watcher
  python scheduler_runner.py --task gmail_watcher --command "brain_watch_gmail_skill.py --since-checkpoint --once"

  # Run approval monitor
  python scheduler_runner.py --task approval_monitor --command "brain_monitor_approvals_skill.py"

  # Run daily summary
  python scheduler_runner.py --task daily_summary --command "brain_daily_summary_skill.py"
        """
    )

    parser.add_argument(
        '--task',
        required=True,
        help='Task name for logging'
    )
    parser.add_argument(
        '--command',
        required=True,
        help='Command to execute (skill script + args)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    args = parser.parse_args()

    # Initialize runner
    runner = SchedulerRunner()

    # Run task
    result = runner.run_task(args.task, args.command)

    # Display result
    if args.verbose:
        print(f"Task: {result['task_name']}")
        print(f"Command: {result['command']}")
        print(f"Duration: {result['duration_ms']}ms")
        print(f"Success: {result['success']}")

        if result['error']:
            print(f"Error: {result['error']}")

        if result['output']:
            print(f"Output: {result['output']}")

    # Exit with appropriate code
    sys.exit(0 if result['success'] else 1)


if __name__ == '__main__':
    main()
