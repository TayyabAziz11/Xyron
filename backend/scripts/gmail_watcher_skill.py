#!/usr/bin/env python3
"""
Backwards compatibility wrapper for gmail_watcher_skill.py

This wrapper maintains compatibility with existing commands and scheduled tasks.
The actual implementation is in src/ai_operator/skills/silver/
"""
import sys
from pathlib import Path

# Add src to path for imports
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / 'src'))

from ai_operator.skills.silver.gmail_watcher_skill import main

if __name__ == '__main__':
    main()
