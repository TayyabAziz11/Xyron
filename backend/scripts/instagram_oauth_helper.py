#!/usr/bin/env python3
"""Wrapper: delegates to the Instagram API helper CLI (status / whoami / test)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.core.instagram_api_helper import main

if __name__ == "__main__":
    sys.exit(main())
