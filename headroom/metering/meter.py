"""The meter: one request's context and usage in, one priced ledger row out.

Everything upstream of this file answers a narrow question — the dialects say what the
provider reported, ``prices.py`` says what the rates were, ``cost.py`` does the
arithmetic — and this is where those answers become a row somebody can be billed for.
It holds two decisions of its own.

**The price is resolved against the request's own date, never "now".** ``when`` is
``RequestContext.started_at``, the wall-clock stamp taken when the request arrived.
Metering happens after the response, the write is queued and may drain seconds later,
and a fixture can be replayed months afterwards; pricing at wall-clock time would let
any of those move a cost across a schedule boundary. That is D-017 wearing a different
hat, and it is the difference the dated-price test asserts.

**Whether a request is billable is decided from the upstream status, not the outcome.**
The rule and its one deliberate exception (H-025):

* the provider answered **< 400** — a model ran, so price the usage;
* the provider answered **>= 400** — a rejected request is not billed by any provider,
  so the cost is ``0`` and that zero is a measurement;
* **no answer at all** — the request never reached a model (unroutable, refused by
  scope, connection failed, body unparseable), so again ``0``;
* **except a timeout**, where the request *was* sent and no answer came back. The
  provider may well have generated and billed it, and Headroom cannot know. That one
  case stays "billable" and falls through to ``usage_unknown`` with a NULL cost —
  because "we do not know" is a thing this schema can say, and guessing is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from headroom.core.context import RequestContext
from headroom.core.errors import ProviderTimeout
from headroom.core.ledger import LedgerEntry
from headroom.metering.cost import CostBreakdown, price_usage
from headroom.metering.prices import PriceBook
from headroom.metering.usage import Usage
from headroom.metering.writer import LedgerWriter

__all__ = ["Meter"]


@dataclass(slots=True)
class Meter:
    """Prices a finished request and hands the row to the writer.

    Constructed once per gateway. ``writer`` is optional so a deployment — or a test —
    can meter without persisting: the context is still stamped and the log line still
    carries the cost, which is exactly the fallback H-027 relies on.
    """

    prices: PriceBook
    writer: LedgerWriter | None = None

    def price(self, ctx: RequestContext, usage: Usage) -> CostBreakdown:
        """Cost this request, without writing anything."""
        return price_usage(
            usage,
            model=ctx.model,
            when=_request_date(ctx),
            prices=self.prices,
            billable=_is_billable(ctx),
        )

    def measure(self, ctx: RequestContext, usage: Usage) -> CostBreakdown:
        """Price the request and stamp the context. **Writes nothing.**

        Split out from :meth:`record` in Phase 4 because the budget's settlement sits
        between the two halves: what a hold settles at is decided from ``cost_status``,
        which this sets, and the settled figure is a column on the row, which
        :meth:`commit` writes. Measuring first, settling, then committing is the only
        order in which the row can carry both.
        """
        breakdown = self.price(ctx, usage)
        ctx.apply_metering(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            stop_reason=usage.stop_reason,
            usd_cost=breakdown.usd_cost,
            cost_status=breakdown.status,
        )
        return breakdown

    def commit(self, ctx: RequestContext, usage: Usage, breakdown: CostBreakdown) -> None:
        """Queue the ledger row for an already-measured request.

        Synchronous and non-blocking by construction: the only I/O it can reach is a
        queue ``put`` (H-027).
        """
        entry = build_entry(ctx, usage, breakdown)
        if entry is not None and self.writer is not None:
            self.writer.submit(entry)

    def record(self, ctx: RequestContext, usage: Usage) -> CostBreakdown:
        """Measure and commit in one call — everything with no budget to settle.

        Returns the breakdown so a caller can assert on it.
        """
        breakdown = self.measure(ctx, usage)
        self.commit(ctx, usage, breakdown)
        return breakdown


def build_entry(ctx: RequestContext, usage: Usage, breakdown: CostBreakdown) -> LedgerEntry | None:
    """The ledger row for a finished request, or ``None`` if it has no owner.

    A request that never authenticated has no tenant and no key. Inventing either
    would put unattributable rows in a table whose entire job is attribution, and the
    structured log line already records those refusals — so the ledger declines them
    (H-025). ``model`` is likewise required: a body that named none never had a price.
    """
    if ctx.tenant_id is None or ctx.key_id is None or ctx.model is None:
        return None
    price = breakdown.price
    return LedgerEntry(
        request_id=ctx.request_id,
        tenant_id=ctx.tenant_id,
        key_id=ctx.key_id,
        route=ctx.route,
        dialect=ctx.dialect or "",
        model=ctx.model,
        provider=ctx.provider,
        streamed=ctx.stream,
        outcome=ctx.outcome,
        status_code=ctx.status_code,
        upstream_status=ctx.upstream_status,
        error_source=ctx.error_source,
        error_reason=ctx.error_reason,
        stop_reason=usage.stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        # The rates, copied in rather than referenced: this is the line that makes a
        # landed cost immune to a later edit of config/models.yaml.
        price_effective_from=None if price is None else price.effective_from,
        usd_per_mtok_in=None if price is None else price.usd_per_mtok_in,
        usd_per_mtok_out=None if price is None else price.usd_per_mtok_out,
        usd_cost=breakdown.usd_cost,
        cost_status=breakdown.status,
        # Phase 4's account of the same request. Copied off the context rather than
        # recomputed: the gate has already decided them, and a second opinion here
        # would be a second source of truth for a number the tenant was charged.
        budget_status=ctx.budget_status,
        budget_reserved_usd=ctx.budget_reserved_usd,
        budget_settled_usd=ctx.budget_settled_usd,
        upstream_latency_ms=ctx.upstream_latency_ms,
        ttft_ms=ctx.time_to_first_token_ms,
        passthrough_overhead_ms=ctx.passthrough_overhead_ms,
        total_ms=ctx.total_ms,
        started_at=ctx.started_at,
    )


def _request_date(ctx: RequestContext) -> date:
    """The day the request was received, in UTC. The only date a price is resolved on."""
    return ctx.started_at.date()


def _is_billable(ctx: RequestContext) -> bool:
    """Whether a model ran and may therefore have charged for it. See the module docstring."""
    if ctx.upstream_status is not None:
        return ctx.upstream_status < 400
    return ctx.error_reason == ProviderTimeout.reason
