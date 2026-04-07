#!/usr/bin/env python3
"""Backwards compatibility wrapper for gmail_api_helper.py"""
import sys
from pathlib import Path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / 'src'))
from ai_operator.core.gmail_api_helper import main
if __name__ == '__main__':
    main()
