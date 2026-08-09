"""``/admin/usage`` — the ledger, read-only and deliberately boring.

Two shapes, because the Phase 7 dashboard needs exactly two: **totals** for the
Overview tiles ("what is this tenant spending, on which model") and a filtered
**list** for the Requests explorer ("show me that request"). Both are ``GET``, both sit
behind the same root admin token as the rest of ``/admin`` (H-019), and neither can
change a byte of the ledger. A cost ledger with a write endpoint is a cost ledger
somebody eventually writes to.

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

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict

from headroom.api.admin import AdminAuth, AdminError
from headroom.api.deps import GatewayDep
from headroom.core.ledger import LedgerEntry, LedgerQuery, UsageTotals, format_usd

__all__ = ["router"]

router = APIRouter(prefix="/admin/usage", tags=["admin", "usage"])

#: Rows one request may return. The explorer pages; nothing here streams a whole
#: ledger into memory because somebody omitted a filter.
MAX_LIMIT = 1000


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

    upstream_latency_ms: float | None
    ttft_ms: float | None
    passthrough_overhead_ms: float | None
    total_ms: float | None

    cache_disposition: str | None
    failover_hops: int
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
            upstream_latency_ms=entry.upstream_latency_ms,
            ttft_ms=entry.ttft_ms,
            passthrough_overhead_ms=entry.passthrough_overhead_ms,
            total_ms=entry.total_ms,
            cache_disposition=entry.cache_disposition,
            failover_hops=entry.failover_hops,
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
