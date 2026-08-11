"""``/admin/usage`` — the ledger, read-only and deliberately boring.

Three shapes, because the Phase 7 dashboard needs exactly three: **totals** for the
Overview tiles ("what is this tenant spending, on which model"), a **series** for the
charts ("what has it been doing for the last hour"), and a filtered **list** for the
Requests explorer ("show me that request"). All are ``GET``, all sit behind the same
root admin token as the rest of ``/admin`` (H-019), and none can change a byte of the
ledger. A cost ledger with a write endpoint is a cost ledger somebody eventually writes
to.

**Money leaves as a string.** ``usd_cost`` is ``"0.000011500000"``, never
``0.0000115``. JSON has one number type and it is a double, so serializing a
``NUMERIC`` as a JSON number would undo, at the last step, the exactness the price
file, the Decimal arithmetic, and the column type were all arranged to preserve. The
dashboard parses a string; a naive client that does ``parseFloat`` has made that choice
itself and can see the original.

**A total says how much of the picture it is missing.** ``unpriced_requests`` counts
the rows whose cost is unknown — an unpriced model, a stream that died before its
usage block — and they are excluded from the sum rather than counted as zero. A
dashboard that showed only the sum would render "spend so far" as a confident
understatement, which is the same failure as billing a truncated answer as complete,
one layer up.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict

from headroom.api.admin import AdminAuth, AdminError
from headroom.api.deps import GatewayDep
from headroom.core.ledger import (
    MAX_ROLLUP_DAYS,
    SERIES_BUCKETS,
    DailyRollup,
    LedgerEntry,
    LedgerQuery,
    UsageBucket,
    UsageTotals,
    format_usd,
)

__all__ = ["router"]

router = APIRouter(prefix="/admin/usage", tags=["admin", "usage"])

#: Rows one request may return. The explorer pages; nothing here streams a whole
#: ledger into memory because somebody omitted a filter.
MAX_LIMIT = 1000

#: Buckets one series may return. 1440 is a day at minute grain — enough for the widest
#: chart anybody draws here, and a bound so a `?bucket=minute` over a year cannot ask
#: Postgres to aggregate half a million groups into one JSON array.
MAX_BUCKETS = 1440


class LedgerRowView(BaseModel):
    """One ledger row, as the dashboard reads it."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    tenant_id: str
    key_id: str
    route: str
    dialect: str
    model: str
    provider: str | None
    streamed: bool
    outcome: str
    status_code: int | None
    upstream_status: int | None
    error_source: str | None
    error_reason: str | None
    stop_reason: str | None

    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None

    #: The rates this row was billed at, copied in when it was written. Present here
    #: because "why did this cost that" is the question the explorer exists to answer,
    #: and re-deriving it from today's price file would answer a different one.
    price_effective_from: str | None
    usd_per_mtok_in: str | None
    usd_per_mtok_out: str | None
    usd_cost: str | None
    cost_status: str

    #: What the budget gate did (Phase 4): whether a cap applied, what was held before
    #: the provider ran, and what the hold became. On a 402 row this is the only record
    #: that the request happened at all — no provider was called.
    budget_status: str | None
    budget_reserved_usd: str | None
    budget_settled_usd: str | None

    upstream_latency_ms: float | None
    ttft_ms: float | None
    passthrough_overhead_ms: float | None
    total_ms: float | None

    cache_disposition: str | None
    #: What a hit saved, and where it came from (Phase 5). ``cache_avoided_usd`` is the
    #: *entry's own recorded cost* rather than a re-pricing at today's rates, so it is a
    #: fact about an invoice line that really happened; ``cache_source_request_id`` is
    #: the provenance that makes a semantic hit auditable, and the reason the Requests
    #: explorer can offer "show me the request this answer actually came from".
    cache_avoided_usd: str | None
    cache_similarity: str | None
    cache_source_request_id: str | None
    #: What failover did (Phase 6). ``provider`` above says who served; these say what it
    #: took — how many candidates were passed over, which one first, and why. NULL and
    #: zero on the overwhelming majority of rows, which is the point.
    failover_hops: int
    failover_from: str | None
    failover_error: str | None
    started_at: datetime

    @classmethod
    def of(cls, entry: LedgerEntry) -> LedgerRowView:
        return cls(
            request_id=entry.request_id,
            tenant_id=entry.tenant_id,
            key_id=entry.key_id,
            route=entry.route,
            dialect=entry.dialect,
            model=entry.model,
            provider=entry.provider,
            streamed=entry.streamed,
            outcome=entry.outcome,
            status_code=entry.status_code,
            upstream_status=entry.upstream_status,
            error_source=entry.error_source,
            error_reason=entry.error_reason,
            stop_reason=entry.stop_reason,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            reasoning_tokens=entry.reasoning_tokens,
            cache_read_tokens=entry.cache_read_tokens,
            cache_write_tokens=entry.cache_write_tokens,
            price_effective_from=(
                None
                if entry.price_effective_from is None
                else entry.price_effective_from.isoformat()
            ),
            usd_per_mtok_in=format_usd(entry.usd_per_mtok_in),
            usd_per_mtok_out=format_usd(entry.usd_per_mtok_out),
            usd_cost=format_usd(entry.usd_cost),
            cost_status=entry.cost_status,
            budget_status=entry.budget_status,
            budget_reserved_usd=format_usd(entry.budget_reserved_usd),
            budget_settled_usd=format_usd(entry.budget_settled_usd),
            upstream_latency_ms=entry.upstream_latency_ms,
            ttft_ms=entry.ttft_ms,
            passthrough_overhead_ms=entry.passthrough_overhead_ms,
            total_ms=entry.total_ms,
            cache_disposition=entry.cache_disposition,
            cache_avoided_usd=format_usd(entry.cache_avoided_usd),
            # Not money, but the same argument: a cosine that arrived as NUMERIC(5,4)
            # would become a double on the way through JSON, and the threshold sweep
            # §P8.H1 runs reads this column.
            cache_similarity=None
            if entry.cache_similarity is None
            else str(entry.cache_similarity),
            cache_source_request_id=entry.cache_source_request_id,
            failover_hops=entry.failover_hops,
            failover_from=entry.failover_from,
            failover_error=entry.failover_error,
            started_at=entry.started_at,
        )


class TotalsView(BaseModel):
    """Aggregated spend for one tenant, or one tenant-and-model pair."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    model: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    usd_cost: str
    #: Rows the sum above could not include. Never hidden: a total that quietly
    #: excluded them would understate spend and look precise doing it.
    unpriced_requests: int
    errored_requests: int

    #: The cache's five dispositions, counted, and what the hits saved. Five values and
    #: not three: "I switched it off" and "it is on and never applies to my traffic"
    #: have completely different fixes, and a dashboard that collapsed them would hide
    #: the more common one (Phase 5's deviation 1).
    cache_hits_exact: int
    cache_hits_semantic: int
    cache_misses: int
    cache_bypasses: int
    cache_disabled: int
    cache_avoided_usd: str
    #: Hits the sum above could not include, because their entry's own cost was never
    #: known. ``unpriced_requests`` for the savings column: skipping a NULL and adding it
    #: as zero give the same sum, so only a count can say a saving was left out.
    cache_avoided_unknown: int
    #: Requests whose primary provider did not serve them (Phase 6). The number the kill
    #: demo makes go up.
    failover_requests: int

    @classmethod
    def of(cls, totals: UsageTotals) -> TotalsView:
        return cls(
            tenant_id=totals.tenant_id,
            model=totals.model,
            requests=totals.requests,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            reasoning_tokens=totals.reasoning_tokens,
            usd_cost=format_usd(totals.usd_cost) or "0",
            unpriced_requests=totals.unpriced_requests,
            errored_requests=totals.errored_requests,
            cache_hits_exact=totals.cache_hits_exact,
            cache_hits_semantic=totals.cache_hits_semantic,
            cache_misses=totals.cache_misses,
            cache_bypasses=totals.cache_bypasses,
            cache_disabled=totals.cache_disabled,
            cache_avoided_usd=format_usd(totals.cache_avoided_usd) or "0",
            cache_avoided_unknown=totals.cache_avoided_unknown,
            failover_requests=totals.failover_requests,
        )


class SeriesPointView(BaseModel):
    """One time bucket — a point on the dashboard's cost-over-time chart.

    Buckets with no requests are **absent**, not zero: gap-filling belongs to whoever
    knows the x-domain being drawn (:class:`headroom.core.ledger.UsageBucket`).
    """

    model_config = ConfigDict(extra="forbid")

    #: The bucket's own start, in UTC, truncated to the requested grain.
    bucket_start: datetime
    requests: int
    input_tokens: int
    output_tokens: int
    usd_cost: str
    cache_avoided_usd: str
    cache_hits: int
    errored_requests: int
    unpriced_requests: int
    failover_requests: int

    @classmethod
    def of(cls, point: UsageBucket) -> SeriesPointView:
        return cls(
            bucket_start=point.bucket_start,
            requests=point.requests,
            input_tokens=point.input_tokens,
            output_tokens=point.output_tokens,
            usd_cost=format_usd(point.usd_cost) or "0",
            cache_avoided_usd=format_usd(point.cache_avoided_usd) or "0",
            cache_hits=point.cache_hits,
            errored_requests=point.errored_requests,
            unpriced_requests=point.unpriced_requests,
            failover_requests=point.failover_requests,
        )


class RollupView(BaseModel):
    """One day of one tenant's spend, as the nightly Lambda computed it (Phase 9).

    Field-for-field :class:`TotalsView` minus the model split, plus the day and the
    stamp — because it *is* a total, over a fixed window, computed once instead of on
    every poll. Two vocabularies for one aggregate is how a console ends up comparing
    two different things and calling both "spend".
    """

    model_config = ConfigDict(extra="forbid")

    day: date
    tenant_id: str
    requests: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    usd_cost: str
    unpriced_requests: int
    errored_requests: int
    cache_hits_exact: int
    cache_hits_semantic: int
    cache_misses: int
    cache_bypasses: int
    cache_disabled: int
    cache_avoided_usd: str
    cache_avoided_unknown: int
    failover_requests: int
    #: When the rollup ran. The gap between this and ``day`` is how late the schedule
    #: was, and it is the field that says whether the newest row covers a whole day or
    #: the part of one that had happened when the Lambda fired.
    computed_at: datetime | None

    @classmethod
    def of(cls, rollup: DailyRollup) -> RollupView:
        return cls(
            day=rollup.day,
            tenant_id=rollup.tenant_id,
            requests=rollup.requests,
            input_tokens=rollup.input_tokens,
            output_tokens=rollup.output_tokens,
            reasoning_tokens=rollup.reasoning_tokens,
            usd_cost=format_usd(rollup.usd_cost) or "0",
            unpriced_requests=rollup.unpriced_requests,
            errored_requests=rollup.errored_requests,
            cache_hits_exact=rollup.cache_hits_exact,
            cache_hits_semantic=rollup.cache_hits_semantic,
            cache_misses=rollup.cache_misses,
            cache_bypasses=rollup.cache_bypasses,
            cache_disabled=rollup.cache_disabled,
            cache_avoided_usd=format_usd(rollup.cache_avoided_usd) or "0",
            cache_avoided_unknown=rollup.cache_avoided_unknown,
            failover_requests=rollup.failover_requests,
            computed_at=rollup.computed_at,
        )


def _query(
    tenant_id: str | None,
    key_id: str | None,
    model: str | None,
    provider: str | None,
    outcome: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int = 100,
    offset: int = 0,
) -> LedgerQuery:
    return LedgerQuery(
        tenant_id=tenant_id,
        key_id=key_id,
        model=model,
        provider=provider,
        outcome=outcome,
        since=since,
        until=until,
        limit=min(limit, MAX_LIMIT),
        offset=offset,
    )


@router.get("", response_model=list[LedgerRowView], dependencies=[AdminAuth])
async def list_usage(
    gateway: GatewayDep,
    tenant_id: Annotated[str | None, Query(description="narrow to one tenant")] = None,
    key_id: Annotated[str | None, Query(description="narrow to one virtual key")] = None,
    model: Annotated[str | None, Query(description="exact model id")] = None,
    provider: Annotated[str | None, Query(description="exact provider name")] = None,
    outcome: Annotated[str | None, Query(description="e.g. ok, upstream_error")] = None,
    since: Annotated[datetime | None, Query(description="inclusive lower bound")] = None,
    until: Annotated[datetime | None, Query(description="exclusive upper bound")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LedgerRowView]:
    """Ledger rows matching the filters, newest request first."""
    rows = await gateway.ledger.list_entries(
        _query(tenant_id, key_id, model, provider, outcome, since, until, limit, offset)
    )
    return [LedgerRowView.of(row) for row in rows]


@router.get("/totals", response_model=list[TotalsView], dependencies=[AdminAuth])
async def usage_totals(
    gateway: GatewayDep,
    tenant_id: Annotated[str | None, Query(description="narrow to one tenant")] = None,
    key_id: Annotated[str | None, Query(description="narrow to one virtual key")] = None,
    model: Annotated[str | None, Query(description="exact model id")] = None,
    provider: Annotated[str | None, Query(description="exact provider name")] = None,
    outcome: Annotated[str | None, Query(description="e.g. ok, upstream_error")] = None,
    since: Annotated[datetime | None, Query(description="inclusive lower bound")] = None,
    until: Annotated[datetime | None, Query(description="exclusive upper bound")] = None,
    by_model: Annotated[bool, Query(description="split each tenant by model")] = False,
) -> list[TotalsView]:
    """Spend per tenant — or per tenant and model — over the filtered window."""
    totals = await gateway.ledger.totals(
        _query(tenant_id, key_id, model, provider, outcome, since, until),
        by_model=by_model,
    )
    return [TotalsView.of(total) for total in totals]


@router.get("/series", response_model=list[SeriesPointView], dependencies=[AdminAuth])
async def usage_series(
    gateway: GatewayDep,
    tenant_id: Annotated[str | None, Query(description="narrow to one tenant")] = None,
    key_id: Annotated[str | None, Query(description="narrow to one virtual key")] = None,
    model: Annotated[str | None, Query(description="exact model id")] = None,
    provider: Annotated[str | None, Query(description="exact provider name")] = None,
    outcome: Annotated[str | None, Query(description="e.g. ok, upstream_error")] = None,
    since: Annotated[datetime | None, Query(description="inclusive lower bound")] = None,
    until: Annotated[datetime | None, Query(description="exclusive upper bound")] = None,
    bucket: Annotated[str, Query(description="minute | hour | day")] = "hour",
    limit: Annotated[int, Query(ge=1, le=MAX_BUCKETS)] = 120,
) -> list[SeriesPointView]:
    """Spend and traffic over time, oldest bucket first.

    Declared **above** ``/{request_id}``: FastAPI matches in declaration order, and a
    literal path that arrives after a parameterised one is a route that never runs.
    """
    if bucket not in SERIES_BUCKETS:
        # The literal 422, not `status.HTTP_422_*` — the convention `budgets.py` set,
        # for the Starlette-deprecation reason recorded there.
        raise AdminError(
            422,
            "unknown_bucket",
            f"bucket must be one of {', '.join(SERIES_BUCKETS)}, got {bucket!r}",
        )
    points = await gateway.ledger.series(
        _query(tenant_id, key_id, model, provider, outcome, since, until, limit),
        bucket=bucket,
    )
    return [SeriesPointView.of(point) for point in points]


@router.get("/rollups", response_model=list[RollupView], dependencies=[AdminAuth])
async def usage_rollups(
    gateway: GatewayDep,
    tenant_id: Annotated[str | None, Query(description="narrow to one tenant")] = None,
    since: Annotated[date | None, Query(description="inclusive first day (UTC)")] = None,
    until: Annotated[date | None, Query(description="exclusive last day (UTC)")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ROLLUP_DAYS, description="days, not rows")] = 90,
) -> list[RollupView]:
    """Committed daily rollups, oldest day first — the console's history view.

    **Read-only, like everything else under ``/admin/usage``, and there is deliberately
    no route that fires the rollup.** The schedule is EventBridge's and a manual run is
    ``aws lambda invoke``; a POST here would be a way to make the gateway's own database
    do arbitrary aggregate work from the internet side of an ALB, which is not a thing
    an operator console should be able to ask for. (``ui/lib/proxy.ts`` says the same
    from the other direction: the console proxies seven prefixes and a "Phase 9 rollup
    trigger" is the example it gives of what it must not relay.)

    Declared **above** ``/{request_id}`` — FastAPI matches in declaration order, and a
    literal path that arrives after a parameterised one is a route that never runs.
    """
    rollups = await gateway.ledger.list_rollups(
        tenant_id=tenant_id, since=since, until=until, limit=limit
    )
    return [RollupView.of(rollup) for rollup in rollups]


@router.get("/{request_id}", response_model=LedgerRowView, dependencies=[AdminAuth])
async def get_usage_row(request_id: str, gateway: GatewayDep) -> LedgerRowView:
    """One row by request id — the path from a caller's screenshot to its cost."""
    entry = await gateway.ledger.get(request_id)
    if entry is None:
        raise AdminError(
            status.HTTP_404_NOT_FOUND,
            "ledger_row_not_found",
            f"no ledger row for request {request_id!r}",
        )
    return LedgerRowView.of(entry)
