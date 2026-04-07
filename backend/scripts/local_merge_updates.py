#!/usr/bin/env python3
"""Wrapper for local_merge_updates Platinum skill."""
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "src"))

from ai_operator.skills.platinum.local_merge_updates import main

if __name__ == "__main__":
    main()
