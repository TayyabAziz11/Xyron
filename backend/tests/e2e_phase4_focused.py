"""Focused runner: only the flight-search and PC-cleanup workflows (Phase 4 audit)."""
from __future__ import annotations
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from e2e_phase4_live import (  # noqa: E402
    test3_automation, test3b_cleanup_approve, test4_flight, print_report,
)


async def main() -> None:
    results = []
    for label, fn in [
        ("Test 3: Automation + Approval (deny path)", test3_automation),
        ("Test 3b: Cleanup Approve Path (safe test files)", test3b_cleanup_approve),
        ("Test 4: Flight Search + Booking Safety", test4_flight),
    ]:
        print(f"\n>>> {label} — starting...", flush=True)
        try:
            r = await fn()
            print(f">>> {label} — done: {r.verdict}", flush=True)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            from e2e_phase4_live import TestResult
            r = TestResult(name=label, transcript="ERROR", verdict="FAIL")
            r.extra_notes.append(f"exception: {exc}")
        results.append(r)
    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
