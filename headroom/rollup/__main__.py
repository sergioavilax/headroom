"""``python -m headroom.rollup`` — the nightly rollup, run by hand.

The same code the Lambda runs, against ``DATABASE_URL``: the aggregation is one store
method and the day arithmetic is one function, so a local run and a scheduled one differ
only in where the connection string came from. That matters twice — it is how the rollup
is demonstrated on the compose stack before a dollar of AWS is spent, and it is the
fallback if the schedule ever needs to be replayed from a laptop.

    python -m headroom.rollup                  # today and yesterday, UTC
    python -m headroom.rollup --day 2026-08-11 # exactly that day
    python -m headroom.rollup --days 7         # the last week, oldest first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from headroom.rollup.handler import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m headroom.rollup", description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--day", help="roll up exactly this UTC day (YYYY-MM-DD)")
    group.add_argument("--days", type=int, help="roll up the last N UTC days, ending today")
    args = parser.parse_args(argv)

    # Built as the event the Lambda would receive, so this path exercises
    # `resolve_days` rather than a second copy of its rules.
    event: dict[str, Any] = {}
    if args.day:
        event["day"] = args.day
    if args.days:
        event["days"] = args.days

    summary = asyncio.run(run(event))
    print(json.dumps(summary.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
