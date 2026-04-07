#!/usr/bin/env python3
"""
Local Sync Pull — Platinum Tier helper.
Runs git pull to receive cloud artifacts before local processing.
"""
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "src"))

from ai_operator.core.git_sync import git_pull


def main():
    vault_root = repo_root
    print(f"Pulling latest vault changes from remote (vault: {vault_root})...")
    ok, msg = git_pull(vault_root)
    if ok:
        print(f"OK: {msg}")
        sys.exit(0)
    else:
        print(f"WARNING: git pull reported an issue — {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
