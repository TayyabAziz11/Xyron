#!/usr/bin/env python3
"""
Bronze Tier Filesystem Watcher Skill
Monitors Inbox/ for new items and creates structured intake markdown files.
Bronze safe mode: Non-destructive, preserves all original files.
"""

import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path


# ============================================================================
# ANSI COLOR SUPPORT (Windows-compatible, standard library only)
# ============================================================================

class ANSIColors:
    """ANSI color codes with auto-detection and Windows support."""

    # ANSI escape codes
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    # Colors
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    GRAY = '\033[90m'

    _enabled = True
    _initialized = False

    @classmethod
    def init(cls):
        """Initialize ANSI support (Windows compatibility)."""
        if cls._initialized:
            return

        cls._initialized = True

        # Check if colors should be disabled
        if os.environ.get('NO_COLOR'):
            cls._enabled = False
            return

        # Enable ANSI on Windows
        if sys.platform == 'win32':
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                # Enable ANSI escape code processing
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                # If we can't enable ANSI, disable colors
                cls._enabled = False

    @classmethod
    def colorize(cls, text, color_code):
        """Apply color to text if colors are enabled."""
        if not cls._enabled:
            return text
        return f"{color_code}{text}{cls.RESET}"

    @classmethod
    def disable(cls):
        """Disable color output."""
        cls._enabled = False


# ============================================================================
# OUTPUT MANAGER (Professional CLI UX)
# ============================================================================

class OutputManager:
    """Manages all console output with professional formatting."""

    def __init__(self, quiet=False, verbose=False, no_banner=False):
        self.quiet = quiet
        self.verbose = verbose
        self.no_banner = no_banner
        self.start_time = time.time()

        # Stats tracking
        self.stats = {
            'scanned': 0,
            'new_items': 0,
            'created': 0,
            'skipped': 0,
            'errors': 0,
        }

        ANSIColors.init()

    def print_banner(self, mode, vault_root, interval=None):
        """Print tool banner/header."""
        if self.no_banner or self.quiet:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print()
        print(ANSIColors.colorize("=" * 70, ANSIColors.CYAN))
        print(ANSIColors.colorize("  Personal AI Employee — Filesystem Watcher (Bronze)", ANSIColors.CYAN + ANSIColors.BOLD))
        print(ANSIColors.colorize("=" * 70, ANSIColors.CYAN))
        print()
        print(f"  Mode:      {ANSIColors.colorize(mode, ANSIColors.YELLOW + ANSIColors.BOLD)}")
        print(f"  Started:   {timestamp}")
        print(f"  Vault:     {vault_root}")
        if interval:
            print(f"  Interval:  {interval}s")
        print()

    def print_config(self, inbox_path, needs_action_path, logs_path, system_log_path):
        """Print configuration block."""
        if self.quiet:
            return

        print(ANSIColors.colorize("Configuration:", ANSIColors.CYAN))
        print(f"  Inbox:         {inbox_path}")
        print(f"  Needs_Action:  {needs_action_path}")
        print(f"  Logs:          {logs_path}")
        print(f"  System Log:    {system_log_path}")
        print()

    def print_status_line(self):
        """Print current status line."""
        if self.quiet:
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        status = (
            f"[{ANSIColors.colorize(timestamp, ANSIColors.GRAY)}] "
            f"WATCHING | "
            f"Inbox: {ANSIColors.colorize(str(self.stats['new_items']), ANSIColors.CYAN)} new | "
            f"Created: {ANSIColors.colorize(str(self.stats['created']), ANSIColors.GREEN)} | "
            f"Skipped: {ANSIColors.colorize(str(self.stats['skipped']), ANSIColors.YELLOW)} | "
            f"Errors: {ANSIColors.colorize(str(self.stats['errors']), ANSIColors.RED)}"
        )
        print(status)

    def print_event(self, event_type, filename, details=None):
        """Print an event row."""
        if self.quiet:
            return

        timestamp = datetime.now().strftime("%H:%M:%S")

        # Icon and color based on event type
        icons = {
            'new': ('●', ANSIColors.CYAN),
            'created': ('✓', ANSIColors.GREEN),
            'wrapped': ('↻', ANSIColors.GREEN),
            'skipped': ('○', ANSIColors.YELLOW),
            'error': ('✗', ANSIColors.RED),
            'verified': ('✓', ANSIColors.GRAY),
        }

        icon, color = icons.get(event_type, ('•', ANSIColors.WHITE))
        icon_colored = ANSIColors.colorize(icon, color)

        # Format the message
        msg = f"  {icon_colored} {filename}"
        if details and self.verbose:
            msg += f" {ANSIColors.colorize(f'({details})', ANSIColors.GRAY)}"

        print(msg)

    def print_separator(self):
        """Print a separator line."""
        if self.quiet:
            return
        print(ANSIColors.colorize("-" * 70, ANSIColors.GRAY))

    def print_summary(self, execution_time=None):
        """Print summary table."""
        if self.quiet:
            return

        if execution_time is None:
            execution_time = time.time() - self.start_time

        print()
        self.print_separator()
        print(ANSIColors.colorize("Summary:", ANSIColors.CYAN + ANSIColors.BOLD))
        print()

        # Format execution time
        if execution_time < 1:
            time_str = f"{execution_time * 1000:.0f}ms"
        else:
            time_str = f"{execution_time:.2f}s"

        # Print stats table
        stats_data = [
            ("Scanned", self.stats['scanned'], ANSIColors.CYAN),
            ("New Items", self.stats['new_items'], ANSIColors.CYAN),
            ("Created", self.stats['created'], ANSIColors.GREEN),
            ("Skipped", self.stats['skipped'], ANSIColors.YELLOW),
            ("Errors", self.stats['errors'], ANSIColors.RED if self.stats['errors'] > 0 else ANSIColors.GRAY),
        ]

        for label, value, color in stats_data:
            value_str = ANSIColors.colorize(str(value), color + ANSIColors.BOLD)
            print(f"  {label:<15} {value_str}")

        print()
        print(f"  Execution Time: {ANSIColors.colorize(time_str, ANSIColors.GRAY)}")
        self.print_separator()
        print()

    def print_message(self, msg, level='info'):
        """Print a general message."""
        if self.quiet and level != 'error':
            return

        colors = {
            'info': ANSIColors.WHITE,
            'success': ANSIColors.GREEN,
            'warning': ANSIColors.YELLOW,
            'error': ANSIColors.RED,
        }

        color = colors.get(level, ANSIColors.WHITE)
        print(ANSIColors.colorize(msg, color))

    def print_verbose(self, msg):
        """Print verbose message (only if verbose mode enabled)."""
        if self.verbose and not self.quiet:
            print(ANSIColors.colorize(f"  [VERBOSE] {msg}", ANSIColors.GRAY))


# ============================================================================
# BRONZE WATCHER (Enhanced with OutputManager)
# ============================================================================

class BronzeWatcher:
    """Filesystem watcher for Bronze Tier AI Employee system."""

    def __init__(self, vault_root=None, dry_run=False, output_mgr=None):
        self.vault_root = Path(vault_root) if vault_root else Path(__file__).parent
        self.inbox_path = self.vault_root / "Inbox"
        self.needs_action_path = self.vault_root / "Needs_Action"
        self.logs_path = self.vault_root / "Logs"
        self.watcher_log = self.logs_path / "watcher.log"
        self.system_log = self.vault_root / "system_log.md"
        self.dry_run = dry_run
        self.processed_files = set()
        self.output = output_mgr or OutputManager()

        # Load previously processed files from watcher log
        self._load_processed_files()

    def _load_processed_files(self):
        """Load list of already-processed files from watcher log."""
        if self.watcher_log.exists():
            try:
                with open(self.watcher_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if 'Processed:' in line or 'Created intake:' in line:
                            # Extract filename from log line
                            parts = line.split('Processed:')
                            if len(parts) > 1:
                                filename = parts[1].strip().split()[0]
                                self.processed_files.add(filename)
                self.output.print_verbose(f"Loaded {len(self.processed_files)} previously processed files")
            except Exception as e:
                self.output.print_message(f"Warning: Could not load processed files: {e}", 'warning')

    def _log_to_watcher(self, message):
        """Append message to watcher log."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"

        if self.dry_run:
            self.output.print_verbose(f"Would log: {message}")
            return

        try:
            with open(self.watcher_log, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            self.output.print_message(f"Error writing to watcher log: {e}", 'error')

    def _append_to_system_log(self, skill, files_touched, outcome):
        """Append operation summary to system_log.md."""
        timestamp = datetime.now().strftime("%H:%M UTC")
        date_header = datetime.now().strftime("## %Y-%m-%d")

        entry = f"\n### {timestamp} - watcher_skill\n"
        entry += f"**Skill:** {skill}\n"
        entry += "**Files Touched:**\n"
        for file in files_touched:
            entry += f"- {file}\n"
        entry += f"\n**Outcome:** {outcome}\n\n---\n"

        if self.dry_run:
            self.output.print_verbose("Would append to system_log.md")
            return

        try:
            # Check if date header exists, add if not
            content = ""
            if self.system_log.exists():
                with open(self.system_log, 'r', encoding='utf-8') as f:
                    content = f.read()

            if date_header not in content:
                content += f"\n{date_header}\n"

            content += entry

            with open(self.system_log, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            self.output.print_message(f"Error writing to system log: {e}", 'error')

    def _is_intake_markdown(self, filepath):
        """Check if a markdown file is already in intake format."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                # Check for intake markers
                return all(marker in content for marker in [
                    '**Source:**', '**Received:**', '**Type:**'
                ])
        except Exception:
            return False

    def _create_intake_for_file(self, filepath):
        """Create intake markdown for a non-markdown file."""
        filename = filepath.name
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_code = datetime.now().strftime("%Y%m%d-%H%M")

        # Generate intake filename
        intake_filename = f"inbox__{filename}__{date_code}.md"
        intake_path = self.inbox_path / intake_filename

        # Determine file type
        ext = filepath.suffix.lower()
        type_map = {
            '.pdf': 'document',
            '.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.gif': 'image',
            '.docx': 'document', '.doc': 'document',
            '.xlsx': 'document', '.xls': 'document',
            '.txt': 'document',
            '.zip': 'archive',
        }
        file_type = type_map.get(ext, 'file')

        intake_content = f"""# New File: {filename}

**Source:** file_drop
**Received:** {timestamp}
**Type:** {file_type}
**Urgency:** low
**Original File:** {filename}
**Relative Path:** Inbox/{filename}

## Description

A {file_type} file was dropped into the Inbox folder.

## Action Needed

*Please describe what you want done with this file. Examples:*
- Extract text and summarize
- Review and file appropriately
- Process data
- Archive for reference

## Initial Assessment

- Next Step: Awaiting user instructions
- Estimated Effort: Unknown (depends on requested action)

## Audit Trail

- [{timestamp}] File detected by watcher_skill
- [{timestamp}] Intake markdown created: {intake_filename}

---

*Original file preserved at: Inbox/{filename}*
"""

        if self.dry_run:
            self.output.print_verbose(f"Would create: {intake_path}")
            return intake_filename

        try:
            with open(intake_path, 'w', encoding='utf-8') as f:
                f.write(intake_content)
            return intake_filename
        except Exception as e:
            self.output.print_message(f"Error creating intake file: {e}", 'error')
            return None

    def _wrap_raw_markdown(self, filepath):
        """Wrap a raw markdown file into intake format."""
        filename = filepath.name
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw_content = f.read()
        except Exception as e:
            self.output.print_message(f"Error reading {filepath}: {e}", 'error')
            return False

        # Extract title from first line or filename
        lines = raw_content.strip().split('\n')
        title = lines[0].lstrip('#').strip() if lines else filename.replace('.md', '')

        wrapped_content = f"""# {title}

**Source:** user
**Received:** {timestamp}
**Type:** task
**Urgency:** medium

## Raw Content

{raw_content}

## Initial Assessment

- Next Step: Review and triage
- Estimated Effort: To be determined

## Audit Trail

- [{timestamp}] User-created markdown detected
- [{timestamp}] Wrapped into intake format by watcher_skill

"""

        if self.dry_run:
            self.output.print_verbose(f"Would wrap: {filepath}")
            return True

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(wrapped_content)
            return True
        except Exception as e:
            self.output.print_message(f"Error wrapping markdown: {e}", 'error')
            return False

    def scan_inbox(self):
        """Scan Inbox folder for new items and process them."""
        if not self.inbox_path.exists():
            self.output.print_message(f"Error: Inbox folder not found at {self.inbox_path}", 'error')
            self.output.stats['errors'] += 1
            return

        files_touched = []

        # Reset stats for this scan
        scan_stats = {'new': 0, 'created': 0, 'skipped': 0, 'errors': 0}

        self.output.print_verbose(f"Scanning: {self.inbox_path}")

        try:
            items = list(self.inbox_path.iterdir())
            self.output.stats['scanned'] = len(items)

            if len(items) == 0:
                self.output.print_verbose("Inbox is empty")
            else:
                self.output.print_verbose(f"Found {len(items)} items in Inbox")

            for item in items:
                if item.is_dir():
                    continue  # Skip subdirectories

                # Skip files we've already processed
                if item.name in self.processed_files:
                    scan_stats['skipped'] += 1
                    continue

                # Skip intake files we've created (pattern: inbox__*)
                if item.name.startswith('inbox__'):
                    self.processed_files.add(item.name)
                    scan_stats['skipped'] += 1
                    continue

                # New item detected
                scan_stats['new'] += 1
                self.output.print_event('new', item.name, 'new file detected')

                if item.suffix.lower() == '.md':
                    # Check if already in intake format
                    if self._is_intake_markdown(item):
                        self.output.print_event('verified', item.name, 'already in intake format')
                        self.processed_files.add(item.name)
                        self._log_to_watcher(f"Verified intake format: {item.name}")
                        scan_stats['skipped'] += 1
                    else:
                        self.output.print_event('wrapped', item.name, 'wrapping to intake format')
                        if self._wrap_raw_markdown(item):
                            files_touched.append(f"Wrapped: {item.name}")
                            self._log_to_watcher(f"Wrapped markdown: {item.name}")
                            scan_stats['created'] += 1
                        else:
                            scan_stats['errors'] += 1
                        self.processed_files.add(item.name)
                else:
                    # Non-markdown file: create intake wrapper
                    self.output.print_event('created', item.name, f'creating intake wrapper')
                    intake_file = self._create_intake_for_file(item)
                    if intake_file:
                        files_touched.append(f"Created intake: {intake_file}")
                        self._log_to_watcher(f"Processed: {item.name} → {intake_file}")
                        scan_stats['created'] += 1
                    else:
                        scan_stats['errors'] += 1
                    self.processed_files.add(item.name)

        except Exception as e:
            self.output.print_message(f"Error scanning inbox: {e}", 'error')
            outcome = f"✗ ERROR - {str(e)}"
            self._append_to_system_log("watcher_skill", files_touched, outcome)
            self.output.stats['errors'] += 1
            return

        # Update global stats
        self.output.stats['new_items'] += scan_stats['new']
        self.output.stats['created'] += scan_stats['created']
        self.output.stats['skipped'] += scan_stats['skipped']
        self.output.stats['errors'] += scan_stats['errors']

        # Log results
        if scan_stats['created'] > 0:
            outcome = f"✓ OK - Processed {scan_stats['created']} new items"
            self.output.print_verbose(outcome)
        else:
            outcome = "✓ OK - No new items to process"
            self.output.print_verbose(outcome)

        if files_touched or scan_stats['created'] > 0:
            self._append_to_system_log("watcher_skill", files_touched, outcome)


# ============================================================================
# MAIN CLI ENTRYPOINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Personal AI Employee — Filesystem Watcher (Bronze Tier)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python watcher_skill.py                    # Run once (default)
  python watcher_skill.py --dry-run          # Preview without changes
  python watcher_skill.py --loop             # Continuous monitoring
  python watcher_skill.py --quiet            # Minimal output
  python watcher_skill.py --verbose          # Detailed output
        """
    )

    # Execution modes
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (default behavior)'
    )
    parser.add_argument(
        '--loop',
        action='store_true',
        help='Run continuously in a loop'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=10,
        help='Interval in seconds for loop mode (default: 10)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--vault',
        type=str,
        help='Path to vault root (default: script directory)'
    )

    # Output control
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Minimal output (errors only)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Detailed output with debug information'
    )
    parser.add_argument(
        '--no-banner',
        action='store_true',
        help='Skip banner/header (for automation/scripts)'
    )
    parser.add_argument(
        '--no-color',
        action='store_true',
        help='Disable ANSI color output'
    )

    args = parser.parse_args()

    # Disable colors if requested
    if args.no_color:
        ANSIColors.disable()

    # Determine mode
    if args.dry_run:
        mode = "DRY-RUN"
    elif args.loop:
        mode = "LOOP"
    else:
        mode = "ONCE"

    # Create output manager
    output = OutputManager(quiet=args.quiet, verbose=args.verbose, no_banner=args.no_banner)

    # Create watcher instance
    vault_root = args.vault if args.vault else str(Path(__file__).parent)
    watcher = BronzeWatcher(vault_root=vault_root, dry_run=args.dry_run, output_mgr=output)

    # Print banner
    output.print_banner(mode, vault_root, interval=args.interval if args.loop else None)

    # Print configuration
    output.print_config(
        inbox_path=watcher.inbox_path,
        needs_action_path=watcher.needs_action_path,
        logs_path=watcher.logs_path,
        system_log_path=watcher.system_log
    )

    if args.loop:
        output.print_message(f"Starting continuous monitoring (interval: {args.interval}s)", 'info')
        output.print_message("Press Ctrl+C to stop", 'info')
        print()

        try:
            cycle = 0
            while True:
                cycle += 1
                if not args.quiet:
                    output.print_separator()
                    print(f"Scan Cycle #{cycle}")
                    output.print_separator()

                watcher.scan_inbox()
                output.print_status_line()

                if not args.quiet:
                    print()

                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()
            output.print_message("Watcher stopped by user", 'warning')
            output.print_summary()
    else:
        # Default: run once
        if not args.quiet:
            output.print_separator()
            print("Scanning Inbox...")
            output.print_separator()
            print()

        watcher.scan_inbox()
        output.print_summary()


if __name__ == "__main__":
    main()
