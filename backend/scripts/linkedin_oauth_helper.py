#!/usr/bin/env python3
"""
LinkedIn OAuth Helper - Wrapper Script
Gold Tier - OAuth2 Authorization

Purpose: Wrapper for src/ai_operator/core/linkedin_api_helper.py

Usage:
    # Initialize OAuth (browser auto-open + local server)
    python3 scripts/linkedin_oauth_helper.py --init

    # Check authentication status
    python3 scripts/linkedin_oauth_helper.py --status

    # Quick auth check
    python3 scripts/linkedin_oauth_helper.py --check-auth
"""

import sys
from pathlib import Path

# Add src to path to enable imports
repo_root = Path(__file__).parent.parent
src_dir = repo_root / "src"
sys.path.insert(0, str(src_dir))

# Import and run main
from ai_operator.core.linkedin_api_helper import main

if __name__ == "__main__":
    sys.exit(main())
