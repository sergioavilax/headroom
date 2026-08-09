"""``LedgerStore`` on Postgres — where the invoice actually lives.

Same habits as ``headroom/db/tenants.py``: hand-written SQL against
``migrations/0002_usage_ledger.sql``, one prepared statement per operation, no query
built by string concatenation. Two things are specific to this file.

**The insert is ``ON CONFLICT (request_id) DO NOTHING``.** The writer is fire-and-
forget (``headroom/metering/writer.py``) and therefore retryable; a retry that landed a
second row would double a tenant's bill, which is worse than the crash it was
recovering from. The UNIQUE index migration 0002 puts on ``request_id`` is what makes
"write it again" safe.

**Money crosses the boundary as ``Decimal``.** asyncpg maps ``NUMERIC`` to
``decimal.Decimal`` in both directions, so no value on this path is ever a ``float``.
The filters in ``list_entries`` and ``totals`` are the one place a query varies by
input, and they vary by *binding NULL* rather than by assembling SQL — every predicate
is present in the statement and disabled by a null parameter, so there is exactly one
plan and nothing to escape.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from headroom.core.ledger import LedgerEntry, LedgerQuery, LedgerStore, UsageTotals
from headroom.db.pool import DatabasePool

__all__ = ["PostgresLedgerStore"]

_COLUMNS = """
    id, request_id, tenant_id, key_id, route, dialect, model, provider, streamed,
    outcome, status_code, upstream_status, error_source, error_reason, stop_reason,
    input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens,
    price_effective_from, usd_per_mtok_in, usd_per_mtok_out, usd_cost, cost_status,
    upstream_latency_ms, ttft_ms, passthrough_overhead_ms, total_ms,
    cache_disposition, failover_hops, started_at, created_at
"""

_INSERT = f"""
INSERT INTO usage_ledger (
    request_id, tenant_id, key_id, route, dialect, model, provider, streamed,
    outcome, status_code, upstream_status, error_source, error_reason, stop_reason,
    input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens,
    price_effective_from, usd_per_mtok_in, usd_per_mtok_out, usd_cost, cost_status,
    upstream_latency_ms, ttft_ms, passthrough_overhead_ms, total_ms,
    cache_disposition, failover_hops, started_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8,
    $9, $10, $11, $12, $13, $14,
    $15, $16, $17, $18, $19,
    $20, $21, $22, $23, $24,
    $25, $26, $27, $28,
    $29, $30, $31
)
-- Idempotent by request id: the writer may retry, and a second row would double-bill.
ON CONFLICT (request_id) DO NOTHING
RETURNING {_COLUMNS}
"""

#: Every filter is in the statement and switched off by a NULL parameter. One plan,
#: no concatenation, and adding a filter means adding a parameter rather than a branch.
_WHERE = """
     WHERE ($1::uuid      IS NULL OR tenant_id  = $1::uuid)
       AND ($2::uuid      IS NULL OR key_id     = $2::uuid)
       AND ($3::text      IS NULL OR model      = $3::text)
       AND ($4::text      IS NULL OR provider   = $4::text)
       AND ($5::text      IS NULL OR outcome    = $5::text)
       AND ($6::timestamptz IS NULL OR started_at >= $6::timestamptz)
       AND ($7::timestamptz IS NULL OR started_at <  $7::timestamptz)
"""


def _entry(row: asyncpg.Record) -> LedgerEntry:
    return LedgerEntry(
        id=str(row["id"]),
        request_id=row["request_id"],
        tenant_id=str(row["tenant_id"]),
        key_id=str(row["key_id"]),
        route=row["route"],
        dialect=row["dialect"],
        model=row["model"],
        provider=row["provider"],
        streamed=row["streamed"],
        outcome=row["outcome"],
        status_code=row["status_code"],
        upstream_status=row["upstream_status"],
        error_source=row["error_source"],
        error_reason=row["error_reason"],
        stop_reason=row["stop_reason"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        reasoning_tokens=row["reasoning_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_write_tokens=row["cache_write_tokens"],
        price_effective_from=row["price_effective_from"],
        usd_per_mtok_in=row["usd_per_mtok_in"],
        usd_per_mtok_out=row["usd_per_mtok_out"],
        usd_cost=row["usd_cost"],
        cost_status=row["cost_status"],
        upstream_latency_ms=row["upstream_latency_ms"],
        ttft_ms=row["ttft_ms"],
        passthrough_overhead_ms=row["passthrough_overhead_ms"],
        total_ms=row["total_ms"],
        cache_disposition=row["cache_disposition"],
        failover_hops=row["failover_hops"],
        started_at=row["started_at"],
        created_at=row["created_at"],
    )


def _filters(query: LedgerQuery) -> list[Any]:
    """The seven ``_WHERE`` parameters, in order. A non-UUID id matches nothing."""
    return [
        _uuid_or_none(query.tenant_id),
        _uuid_or_none(query.key_id),
        query.model,
        query.provider,
        query.outcome,
        query.since,
        query.until,
    ]


def _uuid_or_none(value: str | None) -> str | None:
    """Keep a malformed id out of the cast: ``?tenant_id=banana`` is empty, not a 500."""
    if value is None:
        return None
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return _IMPOSSIBLE_UUID
    return value


#: A syntactically valid UUID that `gen_random_uuid()` will not produce in this
#: universe. Used so a malformed filter matches zero rows rather than erroring.
_IMPOSSIBLE_UUID = "00000000-0000-0000-0000-000000000000"


class PostgresLedgerStore(LedgerStore):
    """The ledger table, behind the interface the meter and the admin API use."""

    __slots__ = ("_owns_pool", "pool")

    def __init__(self, pool: DatabasePool | None = None, *, url: str | None = None) -> None:
        self._owns_pool = pool is None
        self.pool = pool if pool is not None else DatabasePool(url)

    async def record(self, entry: LedgerEntry) -> None:
        async with self.pool.connection() as conn:
            await conn.fetchrow(
                _INSERT,
                entry.request_id,
                entry.tenant_id,
                entry.key_id,
                entry.route,
                entry.dialect,
                entry.model,
                entry.provider,
                entry.streamed,
                entry.outcome,
                entry.status_code,
                entry.upstream_status,
                entry.error_source,
                entry.error_reason,
                entry.stop_reason,
                entry.input_tokens,
                entry.output_tokens,
                entry.reasoning_tokens,
                entry.cache_read_tokens,
                entry.cache_write_tokens,
                entry.price_effective_from,
                entry.usd_per_mtok_in,
                entry.usd_per_mtok_out,
                entry.usd_cost,
                entry.cost_status,
                entry.upstream_latency_ms,
                entry.ttft_ms,
                entry.passthrough_overhead_ms,
                entry.total_ms,
                entry.cache_disposition,
                entry.failover_hops,
                entry.started_at,
            )

    async def get(self, request_id: str) -> LedgerEntry | None:
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM usage_ledger WHERE request_id = $1", request_id
            )
        return None if row is None else _entry(row)

    async def list_entries(self, query: LedgerQuery) -> list[LedgerEntry]:
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM usage_ledger {_WHERE} "
                # started_at, then request_id: two requests can share a microsecond and
                # a page boundary that reshuffles under pagination is a silent skip.
                "ORDER BY started_at DESC, request_id DESC LIMIT $8 OFFSET $9",
                *_filters(query),
                query.limit,
                query.offset,
            )
        return [_entry(row) for row in rows]

    async def totals(self, query: LedgerQuery, *, by_model: bool = False) -> list[UsageTotals]:
        grouping = "tenant_id, model" if by_model else "tenant_id"
        selected = grouping if by_model else "tenant_id, NULL::text AS model"
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {selected},
                       count(*)                                      AS requests,
                       coalesce(sum(input_tokens), 0)                AS input_tokens,
                       coalesce(sum(output_tokens), 0)               AS output_tokens,
                       coalesce(sum(reasoning_tokens), 0)            AS reasoning_tokens,
                       -- Sums only the rows that have a cost; the ones it skipped are
                       -- counted beside it so the total is never quietly understated.
                       coalesce(sum(usd_cost), 0)                    AS usd_cost,
                       count(*) FILTER (WHERE usd_cost IS NULL)      AS unpriced_requests,
                       count(*) FILTER (WHERE outcome <> 'ok')       AS errored_requests
                  FROM usage_ledger
                {_WHERE}
              GROUP BY {grouping}
              ORDER BY usd_cost DESC, {grouping}
                """,
                *_filters(query),
            )
        return [
            UsageTotals(
                tenant_id=str(row["tenant_id"]),
                model=row["model"],
                requests=row["requests"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                reasoning_tokens=row["reasoning_tokens"],
                usd_cost=row["usd_cost"],
                unpriced_requests=row["unpriced_requests"],
                errored_requests=row["errored_requests"],
            )
            for row in rows
        ]

    async def aclose(self) -> None:
        if self._owns_pool:
            await self.pool.aclose()
