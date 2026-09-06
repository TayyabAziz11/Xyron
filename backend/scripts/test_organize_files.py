"""
Manual test script for the organize_files / undo_organize_files tools.

Runs the tool registry directly — the backend server does NOT need to be
running for this script.

Usage:
    cd backend
    python3 scripts/test_organize_files.py                 # safe: disposable sandbox with sample files
    python3 scripts/test_organize_files.py --path Desktop   # real: organizes your actual Desktop (asks to confirm)
    python3 scripts/test_organize_files.py --undo           # reverses the most recent organize run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.tools import registry as _tools_pkg  # noqa: F401  (side-effect: registers all tools)
from api.tools.registry import registry


def _make_sandbox() -> Path:
    sandbox = Path(__file__).parent.parent / "data" / "organize_test_sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    samples = {
        "vacation1.jpg": b"A",
        "vacation2.jpg": b"A",  # duplicate of vacation1 — same content
        "Screenshot_2026-08-27.png": b"B",
        "invoice.pdf": b"C",
        "notes.txt": b"D",
        "budget.xlsx": b"E",
        "trailer.mp4": b"F",
        "song.mp3": b"G",
        "app.zip": b"H",
        "mystery.xyz": b"I",  # unknown extension → Other
    }
    for name, content in samples.items():
        (sandbox / name).write_bytes(content)
    (sandbox / "ExistingProject").mkdir()  # must be left untouched
    return sandbox


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None,
                     help="Folder to organize (e.g. Desktop, Downloads). "
                          "Default: disposable sandbox with sample files — nothing real is touched.")
    ap.add_argument("--undo", action="store_true", help="Undo the most recent organize run instead.")
    args = ap.parse_args()

    if args.undo:
        result = registry.execute("undo_organize_files", {}, {})
        print(result.spoken)
        return

    if args.path:
        target = args.path
        print(f"** Testing against a REAL location: {target} **")
    else:
        sandbox = _make_sandbox()
        target = str(sandbox)
        print(f"Created disposable sandbox with sample files at: {sandbox}")

    plan = registry.execute("organize_files", {"path": target}, {})
    print("\n--- PLAN ---")
    print(plan.spoken)
    if plan.error != "confirm_required":
        print("(Nothing pending — plan was empty or failed, nothing more to do.)")
        return

    answer = input("\nProceed? [y/N] ").strip().lower()
    if answer != "y":
        print("Cancelled — nothing moved.")
        return

    result = registry.execute("organize_files", plan.data["params"], {})
    print("\n--- RESULT ---")
    print(result.spoken)
    print("\nData:", result.data)

    if args.path:
        print("\nRun with --undo to revert this real run if needed.")
    else:
        print(f"\nSandbox left at {target} — inspect the folders, then run "
              f"with --undo to revert, or delete the sandbox folder manually.")


if __name__ == "__main__":
    main()
