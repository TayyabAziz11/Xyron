#!/usr/bin/env python3
"""Wrapper for cloud_worker_orchestrator Platinum skill."""
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "src"))

from ai_operator.skills.platinum.cloud_worker_orchestrator import main

if __name__ == "__main__":
    main()
