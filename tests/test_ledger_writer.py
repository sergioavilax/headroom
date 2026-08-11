"""The delivery guarantee, tested as a guarantee rather than assumed as a comment.

``LedgerWriter`` is deliberately *at most once, in process, best effort* (H-027): a row
queued when the process dies is lost, and that trade buys a request path with no
database on it. A promise like that is only worth anything if its edges are pinned —
what a graceful stop preserves, what a full queue does, what a failing store does to
the rows behind it. Every one of those is a way the ledger silently becomes an
undercount, which is a cost meter's version of lying.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from headroom.core.ledger import (
    DailyRollup,
    LedgerEntry,
    LedgerQuery,
    LedgerStore,
    UsageBucket,
    UsageTotals,
)
from headroom.db.memory import InMemoryLedgerStore
from headroom.metering.writer import LedgerWriter


def entry(request_id: str = "hr_1") -> LedgerEntry:
    return LedgerEntry(
        request_id=request_id,
        tenant_id="aaaaaaaa-0000-4000-8000-000000000001",
        key_id="aaaaaaaa-0000-4000-8000-00000000000a",
        route="/v1/messages",
        dialect="anthropic",
        model="mock-model-1",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        usd_cost=Decimal("0.0000115"),
        cost_status="priced",
    )


class BrokenStore(LedgerStore):
    """A store that always fails. The database being down is not hypothetical."""

    def __init__(self) -> None:
        self.attempts = 0

    async def record(self, entry: LedgerEntry) -> None:
        self.attempts += 1
        raise RuntimeError("the ledger database is unreachable")

    async def list_entries(self, query: LedgerQuery) -> list[LedgerEntry]:
        return []

    async def totals(self, query: LedgerQuery, *, by_model: bool = False) -> list[UsageTotals]:
        return []

    async def series(self, query: LedgerQuery, *, bucket: str = "hour") -> list[UsageBucket]:
        return []

    async def get(self, request_id: str) -> LedgerEntry | None:
        return None

    async def write_daily_rollup(self, day: date) -> list[DailyRollup]:
        return []

    async def list_rollups(
        self,
        *,
        tenant_id: str | None = None,
        since: date | None = None,
        until: date | None = None,
        limit: int = 90,
    ) -> list[DailyRollup]:
        return []


class BlockedStore(InMemoryLedgerStore):
    """An in-memory store that will not accept a row until it is released."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def record(self, entry: LedgerEntry) -> None:
        await self.gate.wait()
        await super().record(entry)


async def test_a_submitted_row_reaches_the_store() -> None:
    store = InMemoryLedgerStore()
    writer = LedgerWriter(store)

    assert writer.submit(entry()) is True
    await writer.drain()

    assert await store.get("hr_1") is not None
    assert writer.written == 1


async def test_submitting_does_not_wait_for_the_store() -> None:
    """**The property the whole design exists for.**

    The store is held shut, and ``submit`` still returns — synchronously, having
    queued the row. If the write were on the request path, this call could not
    complete at all, and every caller would be waiting on that gate.
    """
    store = BlockedStore()
    writer = LedgerWriter(store)

    assert writer.submit(entry()) is True
    assert writer.pending == 1
    assert await store.get("hr_1") is None

    store.gate.set()
    await writer.drain()
    assert await store.get("hr_1") is not None


async def test_a_graceful_shutdown_drains_the_backlog() -> None:
    """A deploy or a ``compose down`` costs no rows — only a crash does."""
    store = InMemoryLedgerStore()
    writer = LedgerWriter(store)
    for index in range(50):
        writer.submit(entry(f"hr_{index}"))

    await writer.aclose()

    assert writer.written == 50
    assert writer.dropped == 0
    assert len(await store.list_entries(LedgerQuery(limit=100))) == 50


async def test_closing_twice_is_safe() -> None:
    writer = LedgerWriter(InMemoryLedgerStore())
    writer.submit(entry())

    await writer.aclose()
    await writer.aclose()

    assert writer.written == 1


async def test_a_full_queue_drops_and_counts_rather_than_blocking() -> None:
    """Backpressure is a drop, and the drop is a number somebody can alert on.

    An unbounded queue would trade a reporting gap for an out-of-memory kill, which
    takes the gateway down with it — the ledger's job is not worth the gateway's life.
    """
    store = BlockedStore()
    writer = LedgerWriter(store, queue_size=2)

    accepted = [writer.submit(entry(f"hr_{index}")) for index in range(5)]

    assert accepted.count(True) <= 3, "the queue is bounded"
    assert writer.dropped >= 2
    store.gate.set()
    await writer.drain()


async def test_a_failing_store_does_not_kill_the_writer() -> None:
    """The next request's row is still worth having, and the log line has this one."""
    store = BrokenStore()
    writer = LedgerWriter(store)

    writer.submit(entry("hr_1"))
    writer.submit(entry("hr_2"))
    await writer.drain()

    assert store.attempts == 2
    assert writer.failed == 2
    assert writer.written == 0


async def test_the_writer_recovers_when_the_store_comes_back() -> None:
    """A transient database outage costs the rows it swallowed and nothing after."""
    store = InMemoryLedgerStore()
    writer = LedgerWriter(store)
    broken = BrokenStore()

    writer._store = broken
    writer.submit(entry("hr_lost"))
    await writer.drain()

    writer._store = store
    writer.submit(entry("hr_kept"))
    await writer.drain()

    assert await store.get("hr_lost") is None
    assert await store.get("hr_kept") is not None
    assert (writer.failed, writer.written) == (1, 1)


async def test_a_writer_that_was_never_used_closes_cleanly() -> None:
    """No worker was ever started; shutdown must not wait on one that does not exist."""
    writer = LedgerWriter(InMemoryLedgerStore())

    await writer.aclose()

    assert writer.written == 0
