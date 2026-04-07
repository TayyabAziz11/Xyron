#!/usr/bin/env python3
"""Wrapper: delegates to the Gold-tier Instagram executor skill."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.skills.gold.brain_execute_instagram_with_mcp_skill import main

if __name__ == "__main__":
    sys.exit(main())
