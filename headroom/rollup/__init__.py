"""The nightly cost rollup: one day of the ledger, aggregated once.

BUILD_PLAN §P9 asks for exactly one Lambda and says what it must be: *"the nightly
cost-rollup (EventBridge schedule → aggregate the day's ledger into ``daily_rollups`` →
the dashboard's history view reads it) — a genuine, small, defensible Lambda, not
decoration."* This package is that Lambda, and the same code run from a terminal.

**The aggregation is not here.** It is :meth:`headroom.core.ledger.LedgerStore.
write_daily_rollup`, implemented on both stores and asserted by one contract suite, for
the reason H-054 gives about the dashboard: ``usage_ledger`` has forty columns and five
``cost_status`` values whose distinctions are the whole point of Phase 3, and a second
reader with its own ``GROUP BY`` re-decides every one of them silently — in a runtime
nobody runs the test suite in. What lives here is the part that is genuinely the
Lambda's: *which* days to roll up, in what order, and what to say afterwards.

**The same code local and in prod.** ``python -m headroom.rollup`` runs the identical
path against ``DATABASE_URL``; the handler differs only in where it reads the URL from.
That is the migration runner's rule (BUILD_PLAN §P9: "migrations run by the same runner
as everywhere") applied to the one other thing that touches the database from outside
the gateway.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from headroom.core.ledger import DailyRollup, LedgerStore, format_usd

__all__ = [
    "DEFAULT_ROLLUP_DAYS",
    "MAX_ROLLUP_WINDOW",
    "RollupResult",
    "RollupSummary",
    "resolve_days",
    "run_rollup",
]

#: Days a scheduled run covers: **today and yesterday**, ending at "now" in UTC.
#:
#: Not just yesterday, which is what "nightly rollup" first suggests. Two reasons, and
#: both are about the table being *derived* rather than accumulated (H-073), which makes
#: recomputing a day free of consequences:
#:
#: * **Late rows.** The ledger writer is fire-and-forget with a drain queue (H-027), so a
#:   request that arrived at 23:59:59 can land its row a moment after midnight. A rollup
#:   that only ever looked backwards one day would miss it forever.
#: * **A history view that is current.** Rolling up the day in progress means the newest
#:   point on the chart is today rather than yesterday — and ``computed_at`` says exactly
#:   how complete that point is, so nothing is claimed that is not true.
DEFAULT_ROLLUP_DAYS = 2

#: A backfill may ask for a year; it may not ask for the whole table in one invocation.
#: Each day is one aggregate query, so this is a bound on how long one run can hold the
#: database rather than on how much history may exist.
MAX_ROLLUP_WINDOW = 366


@dataclass(frozen=True, slots=True)
class RollupResult:
    """What one day's rollup wrote."""

    day: date
    tenants: int
    requests: int
    usd_cost: Decimal

    @classmethod
    def of(cls, day: date, rows: Sequence[DailyRollup]) -> RollupResult:
        return cls(
            day=day,
            tenants=len(rows),
            requests=sum(row.requests for row in rows),
            usd_cost=sum((row.usd_cost for row in rows), Decimal(0)),
        )

    def as_dict(self) -> dict[str, Any]:
        # Money as a string, here as everywhere else it leaves the process: this dict
        # becomes the Lambda's JSON return value and a CloudWatch Logs line, and JSON's
        # only numeric type is a double (H-024's rule at the last mile).
        return {
            "day": self.day.isoformat(),
            "tenants": self.tenants,
            "requests": self.requests,
            "usd_cost": format_usd(self.usd_cost),
        }


@dataclass(frozen=True, slots=True)
class RollupSummary:
    """The whole run: every day it touched, and how long it took."""

    days: tuple[RollupResult, ...]
    duration_ms: float

    @property
    def requests(self) -> int:
        return sum(day.requests for day in self.days)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": "daily_rollup",
            "days": [day.as_dict() for day in self.days],
            "requests": self.requests,
            "duration_ms": round(self.duration_ms, 3),
        }


def resolve_days(event: dict[str, Any] | None, now: datetime) -> list[date]:
    """Which UTC days this invocation rolls up, oldest first.

    Three shapes, and the first two exist so that a backfill and the Phase 9 gate's
    manual fire need no code change:

    * ``{"day": "2026-08-11"}`` — exactly that day. One day, stated.
    * ``{"days": 7}`` — the last seven days ending today (UTC).
    * anything else, including EventBridge's own scheduled event — the last
      :data:`DEFAULT_ROLLUP_DAYS`.

    ``now`` is passed in rather than read, so the boundary behaviour is testable by
    moving the clock instead of by waiting for midnight.
    """
    event = event or {}
    stated = event.get("day")
    if stated is not None:
        # `date.fromisoformat` rather than a permissive parser: a schedule that
        # mistyped a day should fail loudly on the invocation rather than roll up
        # something plausible and adjacent.
        return [date.fromisoformat(str(stated))]
    count = event.get("days", DEFAULT_ROLLUP_DAYS)
    span = int(count)
    if span < 1 or span > MAX_ROLLUP_WINDOW:
        raise ValueError(f"days must be between 1 and {MAX_ROLLUP_WINDOW}, got {span}")
    today = now.astimezone(UTC).date()
    return [today - timedelta(days=offset) for offset in range(span - 1, -1, -1)]


async def run_rollup(store: LedgerStore, days: Iterable[date]) -> RollupSummary:
    """Roll up each day in turn, oldest first, and report what landed.

    One transaction per day rather than one across all of them: a backfill that failed
    on day five should leave days one to four written, and a nightly run's two days are
    two independent facts. There is nothing to be consistent *between* them — each day's
    rollup is a pure function of that day's ledger rows.
    """
    started = time.perf_counter()
    results = [RollupResult.of(day, await store.write_daily_rollup(day)) for day in days]
    return RollupSummary(days=tuple(results), duration_ms=(time.perf_counter() - started) * 1000.0)
