"""One contract, two ledger implementations — the Phase 2 shape, applied to money.

``InMemoryLedgerStore`` exists so the reporting logic (filters, ordering, aggregation,
the NULL-vs-zero distinction) can be tested keylessly and without a container. The
hazard is the same one H-021 was written about: a second implementation that quietly
disagrees with the first turns every green assertion above into a claim about nothing.

So every behaviour is asserted here once, parametrised over **both** stores. The
Postgres parameter follows H-012's rule exactly — it skips when the compose stack is
not up and ``DATABASE_URL`` was merely *inferred*, and it **fails** when someone stated
the database is there, which CI does.

The stakes are higher than for the tenant store: this table is what Phase 8's H2
reports overhead and error accounting from, and what a Phase 7 dashboard shows a human
as their bill. A sum that is right in memory and wrong in SQL is a wrong invoice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from headroom.core.ledger import LedgerEntry, LedgerQuery, LedgerStore
from headroom.db.ledger import PostgresLedgerStore
from headroom.db.memory import InMemoryLedgerStore
from headroom.db.migrate import run_migrations
from headroom.db.tenants import PostgresTenantStore
from headroom.policy.keys import display_prefix, hash_key, mint_key

from .support.services import COMPOSE_DATABASE_URL, resolve_endpoint

DATABASE = resolve_endpoint("DATABASE_URL", COMPOSE_DATABASE_URL)

#: Fixed rather than "now", so an ordering assertion cannot depend on how fast the
#: test ran and a date-window assertion has an actual window to be inside.
T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

#: Ids the in-memory store does not care about and Postgres validates as UUIDs, so the
#: contract suite has to use real ones or it would only be testing one of the two.
TENANT_A = "aaaaaaaa-0000-4000-8000-000000000001"
TENANT_B = "bbbbbbbb-0000-4000-8000-000000000002"
KEY_A = "aaaaaaaa-0000-4000-8000-00000000000a"
KEY_B = "bbbbbbbb-0000-4000-8000-00000000000b"


def entry(
    request_id: str,
    *,
    tenant_id: str = TENANT_A,
    key_id: str = KEY_A,
    model: str = "mock-model-1",
    provider: str | None = "mock",
    outcome: str = "ok",
    minutes: int = 0,
    input_tokens: int | None = 11,
    output_tokens: int | None = 7,
    reasoning_tokens: int | None = None,
    usd_cost: Decimal | None = Decimal("0.0000115"),
    cost_status: str = "priced",
    seconds: int = 0,
    hours: int = 0,
    days: int = 0,
    cache_disposition: str | None = None,
    cache_avoided_usd: Decimal | None = None,
    failover_hops: int = 0,
) -> LedgerEntry:
    return LedgerEntry(
        request_id=request_id,
        tenant_id=tenant_id,
        key_id=key_id,
        route="/v1/messages",
        dialect="anthropic",
        model=model,
        provider=provider,
        streamed=False,
        outcome=outcome,
        status_code=200,
        upstream_status=200,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        usd_per_mtok_in=Decimal("0.25"),
        usd_per_mtok_out=Decimal("1.25"),
        usd_cost=usd_cost,
        cost_status=cost_status,
        cache_disposition=cache_disposition,
        cache_avoided_usd=cache_avoided_usd,
        failover_hops=failover_hops,
        failover_from="mock" if failover_hops else None,
        started_at=T0 + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds),
    )


# --- fixtures -------------------------------------------------------------------------


async def _seed_control_plane() -> None:
    """The two tenants and keys the ledger's foreign keys point at.

    Written by hand with fixed ids because the RESTRICT constraints in migration 0002
    are not decoration: a ledger row cannot exist without the identity it attributes
    spend to, and the in-memory store has to be exercised against the same ids so the
    two parameters really are running the same test.
    """
    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        await conn.execute("TRUNCATE usage_ledger, virtual_keys, tenants CASCADE")
        for tenant_id, key_id, name in ((TENANT_A, KEY_A, "acme"), (TENANT_B, KEY_B, "globex")):
            await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, $2)", tenant_id, name)
            plaintext = mint_key()
            await conn.execute(
                "INSERT INTO virtual_keys (id, tenant_id, name, key_hash, key_prefix) "
                "VALUES ($1, $2, $3, $4, $5)",
                key_id,
                tenant_id,
                name,
                hash_key(plaintext),
                display_prefix(plaintext),
            )
    finally:
        await conn.close()


@asynccontextmanager
async def open_postgres_ledger() -> AsyncIterator[LedgerStore]:
    if DATABASE.skip_reason is not None:
        pytest.skip(DATABASE.skip_reason)
    if not DATABASE.reachable:
        pytest.fail(f"DATABASE_URL was set to {DATABASE.url} and nothing is listening there")
    await run_migrations(DATABASE.url)
    await _seed_control_plane()
    store = PostgresLedgerStore(url=DATABASE.url)
    try:
        yield store
    finally:
        await store.aclose()
        conn = await asyncpg.connect(DATABASE.url, timeout=10)
        try:
            # Truncate on the way out too, so `make test` leaves the compose database
            # as it found it and the README's demo still works afterwards.
            await conn.execute("TRUNCATE usage_ledger, virtual_keys, tenants CASCADE")
        finally:
            await conn.close()


@pytest.fixture(params=["memory", "postgres"])
async def ledger(request: pytest.FixtureRequest) -> AsyncIterator[LedgerStore]:
    """The store under test — the same tests, both implementations."""
    if request.param == "memory":
        yield InMemoryLedgerStore()
        return
    async with open_postgres_ledger() as resolved:
        yield resolved


# --- writing ---------------------------------------------------------------------------


async def test_a_recorded_row_comes_back_whole(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1"))

    row = await ledger.get("hr_1")
    assert row is not None
    assert row.tenant_id == TENANT_A
    assert row.model == "mock-model-1"
    assert row.usd_cost == Decimal("0.0000115")
    assert row.cost_status == "priced"
    assert row.started_at == T0


async def test_money_survives_the_round_trip_as_decimal(ledger: LedgerStore) -> None:
    """The column is NUMERIC and the driver hands back Decimal. No float anywhere."""
    await ledger.record(entry("hr_1", usd_cost=Decimal("0.000011500000")))

    row = await ledger.get("hr_1")
    assert row is not None
    assert isinstance(row.usd_cost, Decimal)
    assert row.usd_cost == Decimal("0.0000115")


async def test_recording_the_same_request_twice_writes_one_row(ledger: LedgerStore) -> None:
    """**The idempotence the fire-and-forget writer depends on.**

    A retried write after a crash must not double a tenant's bill. The first row wins;
    the second call is a no-op, not an error and not a second row.
    """
    await ledger.record(entry("hr_1", usd_cost=Decimal("1")))
    await ledger.record(entry("hr_1", usd_cost=Decimal("999")))

    rows = await ledger.list_entries(LedgerQuery())
    assert len(rows) == 1
    assert rows[0].usd_cost == Decimal("1")


async def test_an_unknown_request_id_is_none_not_an_error(ledger: LedgerStore) -> None:
    assert await ledger.get("hr_nope") is None


async def test_a_null_cost_stays_null(ledger: LedgerStore) -> None:
    """Distinct from zero all the way to the column, which is the whole design."""
    await ledger.record(entry("hr_1", usd_cost=None, cost_status="usage_unknown"))

    row = await ledger.get("hr_1")
    assert row is not None and row.usd_cost is None


# --- reading back ------------------------------------------------------------------------


async def test_rows_come_back_newest_first(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_old", minutes=0))
    await ledger.record(entry("hr_new", minutes=10))

    rows = await ledger.list_entries(LedgerQuery())
    assert [row.request_id for row in rows] == ["hr_new", "hr_old"]


async def test_rows_filter_by_tenant(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_a", tenant_id=TENANT_A, key_id=KEY_A))
    await ledger.record(entry("hr_b", tenant_id=TENANT_B, key_id=KEY_B))

    rows = await ledger.list_entries(LedgerQuery(tenant_id=TENANT_B))
    assert [row.request_id for row in rows] == ["hr_b"]


async def test_rows_filter_by_key_model_provider_and_outcome(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", model="mock-model-1"))
    await ledger.record(entry("hr_2", model="mock-reasoner-1", outcome="upstream_error"))

    assert len(await ledger.list_entries(LedgerQuery(model="mock-reasoner-1"))) == 1
    assert len(await ledger.list_entries(LedgerQuery(outcome="upstream_error"))) == 1
    assert len(await ledger.list_entries(LedgerQuery(provider="mock"))) == 2
    assert len(await ledger.list_entries(LedgerQuery(key_id=KEY_A))) == 2


async def test_the_time_window_is_half_open(ledger: LedgerStore) -> None:
    """``since <= started_at < until``, so adjacent windows neither overlap nor skip.

    An inclusive upper bound would double-count every row on a day boundary, which is
    exactly the arithmetic a monthly invoice is made of.
    """
    await ledger.record(entry("hr_at_since", minutes=0))
    await ledger.record(entry("hr_at_until", minutes=10))

    rows = await ledger.list_entries(LedgerQuery(since=T0, until=T0 + timedelta(minutes=10)))
    assert [row.request_id for row in rows] == ["hr_at_since"]


async def test_limit_and_offset_page_without_skipping_a_row(ledger: LedgerStore) -> None:
    for index in range(5):
        await ledger.record(entry(f"hr_{index}", minutes=index))

    first = await ledger.list_entries(LedgerQuery(limit=2))
    second = await ledger.list_entries(LedgerQuery(limit=2, offset=2))
    assert [row.request_id for row in first] == ["hr_4", "hr_3"]
    assert [row.request_id for row in second] == ["hr_2", "hr_1"]


async def test_rows_sharing_a_timestamp_have_a_stable_order(ledger: LedgerStore) -> None:
    """Two requests can land in the same microsecond; a page boundary must not shuffle."""
    for name in ("hr_a", "hr_b", "hr_c"):
        await ledger.record(entry(name, minutes=0))

    once = [row.request_id for row in await ledger.list_entries(LedgerQuery())]
    twice = [row.request_id for row in await ledger.list_entries(LedgerQuery())]
    assert once == twice == ["hr_c", "hr_b", "hr_a"]


async def test_a_malformed_tenant_filter_matches_nothing(ledger: LedgerStore) -> None:
    """``?tenant_id=banana`` is an empty result, not a 500 about a failed cast."""
    await ledger.record(entry("hr_1"))

    assert await ledger.list_entries(LedgerQuery(tenant_id="banana")) == []


# --- totals -------------------------------------------------------------------------------


async def test_totals_sum_spend_per_tenant(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", usd_cost=Decimal("1.5")))
    await ledger.record(entry("hr_2", usd_cost=Decimal("2.5")))
    await ledger.record(entry("hr_3", tenant_id=TENANT_B, key_id=KEY_B, usd_cost=Decimal("0.25")))

    totals = {total.tenant_id: total for total in await ledger.totals(LedgerQuery())}
    assert totals[TENANT_A].usd_cost == Decimal("4.0")
    assert totals[TENANT_A].requests == 2
    assert totals[TENANT_B].usd_cost == Decimal("0.25")


async def test_totals_sum_tokens_including_the_reasoning_breakdown(
    ledger: LedgerStore,
) -> None:
    await ledger.record(entry("hr_1", input_tokens=10, output_tokens=20, reasoning_tokens=15))
    await ledger.record(entry("hr_2", input_tokens=1, output_tokens=2, reasoning_tokens=1))

    (total,) = await ledger.totals(LedgerQuery())
    assert (total.input_tokens, total.output_tokens, total.reasoning_tokens) == (11, 22, 16)


async def test_a_total_says_how_many_rows_it_could_not_price(ledger: LedgerStore) -> None:
    """**The number that stops a total from being a confident understatement.**

    Unpriced rows are excluded from the sum — counting them as zero would be a lie —
    and counted beside it, so a dashboard can show "and N requests we could not price"
    instead of quietly rounding them away.
    """
    await ledger.record(entry("hr_1", usd_cost=Decimal("1")))
    await ledger.record(entry("hr_2", usd_cost=None, cost_status="usage_unknown"))

    (total,) = await ledger.totals(LedgerQuery())
    assert total.usd_cost == Decimal("1")
    assert total.requests == 2
    assert total.unpriced_requests == 1


async def test_a_total_counts_the_errors_phase_8_reports(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1"))
    await ledger.record(entry("hr_2", outcome="upstream_error", usd_cost=Decimal(0)))

    (total,) = await ledger.totals(LedgerQuery())
    assert total.errored_requests == 1


async def test_totals_split_by_model_on_request(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", model="mock-model-1", usd_cost=Decimal("1")))
    await ledger.record(entry("hr_2", model="mock-reasoner-1", usd_cost=Decimal("2")))

    grouped = {total.model: total for total in await ledger.totals(LedgerQuery(), by_model=True)}
    assert grouped["mock-model-1"].usd_cost == Decimal("1")
    assert grouped["mock-reasoner-1"].usd_cost == Decimal("2")

    (combined,) = await ledger.totals(LedgerQuery())
    assert combined.model is None
    assert combined.usd_cost == Decimal("3")


async def test_totals_honour_the_same_filters_as_the_list(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", usd_cost=Decimal("1"), minutes=0))
    await ledger.record(entry("hr_2", usd_cost=Decimal("2"), minutes=30))

    (total,) = await ledger.totals(LedgerQuery(since=T0 + timedelta(minutes=10)))
    assert total.usd_cost == Decimal("2")
    assert total.requests == 1


async def test_totals_of_nothing_is_an_empty_list_not_a_zero_row(
    ledger: LedgerStore,
) -> None:
    assert await ledger.totals(LedgerQuery(tenant_id=TENANT_B)) == []


# --- what the dashboard added (Phase 7) -----------------------------------------------------
#
# Counters over columns Phases 5 and 6 already wrote. Asserted against both stores for
# H-021's reason and one more: a `count(*) FILTER` and a Python `sum(1 for …)` are two
# different sentences about the same rule, and the ways they drift — a NULL treated as a
# zero, a disposition spelled differently — are exactly the ways a savings figure becomes
# quietly wrong rather than obviously broken.


async def test_a_total_counts_each_cache_disposition_separately(ledger: LedgerStore) -> None:
    """Five values, not three. "Off" and "on but never applicable" have different fixes."""
    for index, disposition in enumerate(
        ("cache_hit_exact", "cache_hit_semantic", "cache_miss", "cache_bypass", "cache_disabled")
    ):
        await ledger.record(entry(f"hr_{index}", minutes=index, cache_disposition=disposition))

    (total,) = await ledger.totals(LedgerQuery())
    assert total.cache_hits_exact == 1
    assert total.cache_hits_semantic == 1
    assert total.cache_misses == 1
    assert total.cache_bypasses == 1
    assert total.cache_disabled == 1


async def test_a_total_sums_what_the_hits_avoided(ledger: LedgerStore) -> None:
    await ledger.record(
        entry(
            "hr_1",
            cache_disposition="cache_hit_exact",
            usd_cost=Decimal(0),
            cost_status="not_billable",
            cache_avoided_usd=Decimal("0.0000115"),
        )
    )
    await ledger.record(
        entry(
            "hr_2",
            minutes=1,
            cache_disposition="cache_hit_semantic",
            usd_cost=Decimal(0),
            cost_status="not_billable",
            cache_avoided_usd=Decimal("0.0000230"),
        )
    )
    await ledger.record(entry("hr_3", minutes=2, cache_disposition="cache_miss"))

    (total,) = await ledger.totals(LedgerQuery())
    assert total.cache_avoided_usd == Decimal("0.0000345")
    assert isinstance(total.cache_avoided_usd, Decimal)


async def test_a_total_counts_the_savings_it_could_not_add_up(ledger: LedgerStore) -> None:
    """H-025's rule, on the savings column: **a total says how much it is missing.**

    A hit on an entry whose own cost was never known — an unpriced model, a stream nobody
    could meter — carries NULL here. Note what the sum *cannot* tell anybody: skipping a
    NULL and adding it as zero produce the identical figure, always, because zero is the
    additive identity. So the honest sum is not enough on its own, and the counter beside
    it is the whole mechanism: without it the console's "avoided" tile is a confident
    understatement the moment one unpriced model enters a tenant's traffic.
    """
    await ledger.record(
        entry("hr_1", cache_disposition="cache_hit_exact", cache_avoided_usd=Decimal("0.5"))
    )
    await ledger.record(
        entry("hr_2", minutes=1, cache_disposition="cache_hit_exact", cache_avoided_usd=None)
    )
    # A *miss* also has a NULL avoided cost, and must not be counted here — otherwise the
    # figure would mean "requests that were not hits", which nobody asked for.
    await ledger.record(entry("hr_3", minutes=2, cache_disposition="cache_miss"))

    (total,) = await ledger.totals(LedgerQuery())
    assert total.cache_hits_exact == 2
    assert total.cache_avoided_usd == Decimal("0.5")
    assert total.cache_avoided_unknown == 1


async def test_a_total_counts_the_requests_that_failed_over(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1"))
    await ledger.record(entry("hr_2", minutes=1, failover_hops=1))
    await ledger.record(entry("hr_3", minutes=2, failover_hops=2))

    (total,) = await ledger.totals(LedgerQuery())
    assert total.requests == 3
    assert total.failover_requests == 2


# --- the series --------------------------------------------------------------------------


async def test_a_series_groups_rows_into_buckets_oldest_first(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", seconds=10))
    await ledger.record(entry("hr_2", seconds=50))
    await ledger.record(entry("hr_3", minutes=1))

    points = await ledger.series(LedgerQuery(), bucket="minute")

    assert [point.requests for point in points] == [2, 1]
    assert points[0].bucket_start == T0
    assert points[1].bucket_start == T0 + timedelta(minutes=1)
    assert points[0].usd_cost == Decimal("0.0000230")


async def test_every_grain_truncates_the_way_postgres_does(ledger: LedgerStore) -> None:
    """One row per hour for three hours: 3 minute-buckets, 3 hour-buckets, 1 day-bucket."""
    for index in range(3):
        await ledger.record(entry(f"hr_{index}", hours=index))

    assert len(await ledger.series(LedgerQuery(), bucket="minute")) == 3
    assert len(await ledger.series(LedgerQuery(), bucket="hour")) == 3
    day = await ledger.series(LedgerQuery(), bucket="day")
    assert len(day) == 1
    assert day[0].bucket_start == datetime(2026, 6, 1, tzinfo=UTC)
    assert day[0].requests == 3


async def test_a_series_keeps_the_newest_buckets_when_it_has_to_choose(
    ledger: LedgerStore,
) -> None:
    """A chart that dropped its newest point would be worse than one missing its oldest."""
    for index in range(5):
        await ledger.record(entry(f"hr_{index}", minutes=index))

    points = await ledger.series(LedgerQuery(limit=2), bucket="minute")

    assert len(points) == 2
    assert points[0].bucket_start == T0 + timedelta(minutes=3)
    assert points[1].bucket_start == T0 + timedelta(minutes=4)


async def test_a_series_carries_the_counters_the_live_view_reads(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", cache_disposition="cache_miss"))
    await ledger.record(
        entry(
            "hr_2",
            seconds=1,
            cache_disposition="cache_hit_semantic",
            usd_cost=Decimal(0),
            cache_avoided_usd=Decimal("0.0000115"),
        )
    )
    await ledger.record(entry("hr_3", seconds=2, failover_hops=1))
    await ledger.record(
        entry("hr_4", seconds=3, outcome="upstream_error", usd_cost=None, cost_status="unpriced")
    )

    (point,) = await ledger.series(LedgerQuery(), bucket="minute")
    assert point.requests == 4
    assert point.cache_hits == 1
    assert point.cache_avoided_usd == Decimal("0.0000115")
    assert point.failover_requests == 1
    assert point.errored_requests == 1
    assert point.unpriced_requests == 1


async def test_a_series_honours_the_same_filters_as_the_list(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_1", tenant_id=TENANT_A, key_id=KEY_A))
    await ledger.record(entry("hr_2", tenant_id=TENANT_B, key_id=KEY_B, seconds=1))

    points = await ledger.series(LedgerQuery(tenant_id=TENANT_B), bucket="minute")

    assert len(points) == 1 and points[0].requests == 1


async def test_a_bucket_with_no_requests_is_absent_not_zero(ledger: LedgerStore) -> None:
    """Gap-filling belongs to whoever knows the x-domain, not to the store."""
    await ledger.record(entry("hr_1"))
    await ledger.record(entry("hr_2", minutes=5))

    points = await ledger.series(LedgerQuery(), bucket="minute")

    assert [point.bucket_start for point in points] == [T0, T0 + timedelta(minutes=5)]


async def test_a_series_of_nothing_is_an_empty_list(ledger: LedgerStore) -> None:
    assert await ledger.series(LedgerQuery(), bucket="hour") == []


async def test_an_unknown_grain_is_refused_by_both_stores(ledger: LedgerStore) -> None:
    """Refused at the store, not only at the route: `date_trunc` would take 'week'."""
    with pytest.raises(ValueError, match="bucket must be one of"):
        await ledger.series(LedgerQuery(), bucket="week")


# --- the daily rollup (Phase 9) --------------------------------------------------------
#
# The rollup is what a scheduled Lambda writes and what the console's history view reads,
# and it is the only *derived* table in this schema. Two properties carry the weight, and
# both are asserted against Postgres and the dict in the same run:
#
#   * **A rollup equals what `totals` says about the same day.** They are the same
#     question over different windows, so a disagreement means one of them is wrong about
#     a NULL, a filter, or a disposition — silently, in a number a human reads as a bill.
#   * **A day is replaced, never accumulated into.** That is what makes re-running safe,
#     which is what makes it safe to fire by hand at a gate and to retry after a failed
#     schedule.

DAY = T0.date()


async def test_a_rollup_says_what_totals_say_about_the_same_day(ledger: LedgerStore) -> None:
    """The load-bearing one. Two code paths, one answer, or the table is a second truth."""
    await ledger.record(entry("hr_1"))
    await ledger.record(entry("hr_2", minutes=5, cache_disposition="cache_hit_exact"))
    await ledger.record(entry("hr_3", minutes=9, outcome="upstream_error", failover_hops=1))

    written = await ledger.write_daily_rollup(DAY)
    [live] = await ledger.totals(LedgerQuery(tenant_id=TENANT_A))

    assert len(written) == 1
    rollup = written[0]
    assert rollup.day == DAY
    assert rollup.tenant_id == TENANT_A
    assert (rollup.requests, rollup.usd_cost) == (live.requests, live.usd_cost)
    assert (rollup.input_tokens, rollup.output_tokens) == (live.input_tokens, live.output_tokens)
    assert rollup.errored_requests == live.errored_requests
    assert rollup.failover_requests == live.failover_requests
    assert rollup.cache_hits_exact == live.cache_hits_exact
    assert rollup.unpriced_requests == live.unpriced_requests
    assert rollup.computed_at is not None


async def test_a_rollup_sums_only_the_rows_that_had_a_cost(ledger: LedgerStore) -> None:
    """H-025's rule survives the aggregation: NULL is skipped *and* counted."""
    await ledger.record(entry("hr_priced"))
    await ledger.record(entry("hr_unknown", minutes=1, usd_cost=None, cost_status="usage_unknown"))

    [rollup] = await ledger.write_daily_rollup(DAY)

    assert rollup.requests == 2
    assert rollup.usd_cost == Decimal("0.0000115")
    assert rollup.unpriced_requests == 1


async def test_a_rollup_counts_the_savings_it_could_not_add_up(ledger: LedgerStore) -> None:
    """`cache_avoided_unknown`, one table over. A count, because a zero sums invisibly."""
    await ledger.record(
        entry(
            "hr_known",
            cache_disposition="cache_hit_semantic",
            cache_avoided_usd=Decimal("0.0000115"),
        )
    )
    await ledger.record(
        entry("hr_unknown", minutes=1, cache_disposition="cache_hit_exact", cache_avoided_usd=None)
    )

    [rollup] = await ledger.write_daily_rollup(DAY)

    assert rollup.cache_avoided_usd == Decimal("0.0000115")
    assert rollup.cache_avoided_unknown == 1


async def test_the_day_is_utc_and_it_is_the_requests_own_arrival(ledger: LedgerStore) -> None:
    """A row at 23:30Z and one at 00:30Z are different days, whatever the server's zone.

    `started_at`, never `created_at`: the writer is fire-and-forget with a drain queue, so
    a request that arrived before midnight can land its row after it — and it belongs to
    the day it happened.
    """
    late = datetime(2026, 6, 1, 23, 30, tzinfo=UTC)
    early = datetime(2026, 6, 2, 0, 30, tzinfo=UTC)
    await ledger.record(replace(entry("hr_late"), started_at=late))
    await ledger.record(replace(entry("hr_early"), started_at=early))

    [first] = await ledger.write_daily_rollup(late.date())
    [second] = await ledger.write_daily_rollup(early.date())

    assert (first.day, first.requests) == (late.date(), 1)
    assert (second.day, second.requests) == (early.date(), 1)


async def test_a_day_with_no_requests_gets_no_row(ledger: LedgerStore) -> None:
    """Absent, not a row of zeros — the `UsageBucket` rule, one table over."""
    assert await ledger.write_daily_rollup(DAY) == []
    assert await ledger.list_rollups() == []


async def test_running_the_rollup_twice_replaces_the_day_rather_than_doubling_it(
    ledger: LedgerStore,
) -> None:
    """The property the whole schedule rests on: firing it again is safe, always."""
    await ledger.record(entry("hr_1"))
    await ledger.write_daily_rollup(DAY)

    # A row that arrived after the first run — the late-drain case the handler's
    # two-day window exists for.
    await ledger.record(entry("hr_2", minutes=1))
    await ledger.write_daily_rollup(DAY)

    rollups = await ledger.list_rollups()
    assert len(rollups) == 1
    assert rollups[0].requests == 2
    assert rollups[0].usd_cost == Decimal("0.000023")


async def test_each_tenant_gets_its_own_row_for_a_day(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_a"))
    await ledger.record(entry("hr_b", tenant_id=TENANT_B, key_id=KEY_B, minutes=1))

    written = await ledger.write_daily_rollup(DAY)

    assert [row.tenant_id for row in written] == sorted([TENANT_A, TENANT_B])
    assert all(row.requests == 1 for row in written)


async def test_rollups_come_back_oldest_day_first(ledger: LedgerStore) -> None:
    for offset in (2, 0, 1):
        await ledger.record(entry(f"hr_{offset}", days=offset))
        await ledger.write_daily_rollup(DAY + timedelta(days=offset))

    days = [row.day for row in await ledger.list_rollups()]
    assert days == [DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)]


async def test_the_rollup_window_is_half_open_on_the_day(ledger: LedgerStore) -> None:
    """`since <= day < until`, matching `LedgerQuery` so adjacent ranges cannot overlap."""
    for offset in range(3):
        await ledger.record(entry(f"hr_{offset}", days=offset))
        await ledger.write_daily_rollup(DAY + timedelta(days=offset))

    window = await ledger.list_rollups(since=DAY, until=DAY + timedelta(days=2))
    assert [row.day for row in window] == [DAY, DAY + timedelta(days=1)]


async def test_a_limit_keeps_the_most_recent_days_not_the_most_recent_rows(
    ledger: LedgerStore,
) -> None:
    """The `dense_rank` property: "the last two days" is two days, not two rows.

    Three days, two tenants busy on each. A row limit would return two rows and call it
    two days of history, which is the shape of a chart that quietly loses a tenant.
    """
    for offset in range(3):
        await ledger.record(entry(f"hr_a{offset}", days=offset))
        await ledger.record(entry(f"hr_b{offset}", tenant_id=TENANT_B, key_id=KEY_B, days=offset))
        await ledger.write_daily_rollup(DAY + timedelta(days=offset))

    recent = await ledger.list_rollups(limit=2)

    assert len(recent) == 4
    assert {row.day for row in recent} == {DAY + timedelta(days=1), DAY + timedelta(days=2)}


async def test_rollups_filter_by_tenant(ledger: LedgerStore) -> None:
    await ledger.record(entry("hr_a"))
    await ledger.record(entry("hr_b", tenant_id=TENANT_B, key_id=KEY_B, minutes=1))
    await ledger.write_daily_rollup(DAY)

    assert [row.tenant_id for row in await ledger.list_rollups(tenant_id=TENANT_B)] == [TENANT_B]


# --- Postgres-only proofs ------------------------------------------------------------------


@pytest.fixture
async def postgres_ledger() -> AsyncIterator[LedgerStore]:
    async with open_postgres_ledger() as store:
        yield store


async def test_the_ledger_refuses_a_row_for_a_tenant_that_does_not_exist(
    postgres_ledger: LedgerStore,
) -> None:
    """The RESTRICT foreign keys are real, and this is what makes them worth having.

    A ledger row is an invoice line; one pointing at nothing is an orphan the moment
    it is written. The database says no rather than the application remembering to.
    """
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await postgres_ledger.record(
            entry("hr_1", tenant_id="cccccccc-0000-4000-8000-00000000000c")
        )


async def test_a_tenant_with_ledger_rows_cannot_be_deleted(
    postgres_ledger: LedgerStore,
) -> None:
    """H-022's design, now load-bearing: deleting a tenant would erase its history."""
    await postgres_ledger.record(entry("hr_1"))

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute("DELETE FROM virtual_keys WHERE id = $1", KEY_A)
    finally:
        await conn.close()


async def test_the_stored_cost_keeps_twelve_decimal_places(
    postgres_ledger: LedgerStore,
) -> None:
    """NUMERIC(24, 12): a millionth of a cent survives the column, not just the code."""
    await postgres_ledger.record(entry("hr_1", usd_cost=Decimal("0.000000000001")))

    row = await postgres_ledger.get("hr_1")
    assert row is not None and row.usd_cost == Decimal("0.000000000001")


async def test_a_rollup_shrinks_when_the_day_it_summarises_does(
    postgres_ledger: LedgerStore,
) -> None:
    """The half of "replace, never accumulate" that only raw SQL can set up.

    `LedgerStore` has no delete — a ledger row is an invoice line and nothing removes one
    — so the growth case is all the contract suite above can reach. The shrink case is
    not hypothetical here: this repo's own Postgres fixture runs `TRUNCATE usage_ledger,
    virtual_keys, tenants CASCADE` on every `make test`, and a rollup that accumulated
    would go on reporting a day the ledger no longer has anything to say about.
    """
    await postgres_ledger.record(entry("hr_1"))
    await postgres_ledger.record(entry("hr_2", minutes=1))
    [before] = await postgres_ledger.write_daily_rollup(DAY)
    assert before.requests == 2

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        await conn.execute("DELETE FROM usage_ledger WHERE request_id = $1", "hr_2")
    finally:
        await conn.close()

    [after] = await postgres_ledger.write_daily_rollup(DAY)
    assert after.requests == 1
    assert len(await postgres_ledger.list_rollups()) == 1


async def test_a_rollup_cannot_outlive_the_tenant_it_attributes_spend_to(
    postgres_ledger: LedgerStore,
) -> None:
    """`ON DELETE RESTRICT` on `daily_rollups` too — H-022's rule, seventh migration."""
    await postgres_ledger.record(entry("hr_1"))
    await postgres_ledger.write_daily_rollup(DAY)

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute("DELETE FROM tenants WHERE id = $1", TENANT_A)
    finally:
        await conn.close()


async def test_the_control_plane_store_and_the_ledger_share_a_database(
    postgres_ledger: LedgerStore,
) -> None:
    """A sanity check that the seeded identities are the ones the API would see."""
    tenants = PostgresTenantStore(url=DATABASE.url)
    try:
        tenant = await tenants.get_tenant(TENANT_A)
        assert tenant is not None and tenant.name == "acme"
    finally:
        await tenants.aclose()
