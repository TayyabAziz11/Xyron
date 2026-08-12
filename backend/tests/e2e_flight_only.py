from __future__ import annotations
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from e2e_phase4_live import test4_flight, print_report  # noqa: E402


async def main() -> None:
    r = await test4_flight()
    print_report([r])


if __name__ == "__main__":
    asyncio.run(main())
