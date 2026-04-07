#!/usr/bin/env python3
"""Backwards compatibility wrapper for brain_create_plan_skill.py"""
import sys
from pathlib import Path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / 'src'))
from ai_operator.skills.silver.brain_create_plan_skill import main
if __name__ == '__main__':
    main()
