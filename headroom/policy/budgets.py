"""The budget gate: what a request might cost, held before it runs, settled after.

BUILD_PLAN §0.2 rule 5, verbatim: *"The budget gate reads **committed** spend — reserved
+ landed — never landed alone (D-019's scar; in Headroom this isn't just a harness rule,
it's the product)."* This module is the product half. ``headroom/db/budgets.py`` makes
the arithmetic atomic; this file decides what number goes into it and what number comes
back out.

Three decisions, and each is the answer to a question the plan asks by name.

**What does an unfinished request cost?** Nobody knows, so the gate reserves an upper
bound and corrects it afterwards. :func:`estimate_usd` builds that bound from the two
things the gateway can see before the provider does anything — the caller's own
``max_tokens`` ceiling and the size of the body they sent — priced at the rate in effect
on the request's own date (H-023). Deliberately conservative, because the failure modes
are asymmetric: an over-estimate refuses a request that would have fit and the tenant
notices immediately, while an under-estimate lets spend past the gate and nobody notices
until the invoice.

**What does a finished request cost?** Whatever the meter says — with one deliberate
exception. When the cost is *unknown* (a timeout, a cut stream, a streamed OpenAI
request that never asked for usage), the request still reached a model, and the honest
budget treatment of "a model ran and we cannot ask what it charged" is to keep the bound
we already reserved rather than release it to zero. The ledger still records ``NULL``,
because the ledger is an invoice and states facts; the budget is a guard rail and states
bounds. See :func:`settlement_for` and docs/DECISIONS.md H-031.

**When does the hold go away?** In a ``finally``, synchronously, on every path — and if
the process dies before that, the hold expires and the sweeper hands it back. Phase 3's
ledger writer is explicitly *not* reused for this (H-027 says so in as many words): a
lost ledger row is a reporting gap, a lost reservation is D-019 growing back.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from headroom.core.budgets import (
    ADMIT_EXCEEDED,
    ADMIT_NO_BUDGET,
    BudgetScope,
    BudgetStore,
    Reservation,
)
from headroom.core.context import RequestContext
from headroom.core.errors import BudgetExceeded
from headroom.dialects.base import Dialect
from headroom.metering.cost import (
    COST_NOT_BILLABLE,
    COST_PARTIAL,
    COST_PRICED,
    COST_UNPRICED_MODEL,
    quantize_usd,
    usd_for_tokens,
)
from headroom.metering.prices import PriceBook

__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "EST_BYTES_PER_TOKEN",
    "BudgetGate",
    "Estimate",
    "estimate_usd",
    "settlement_for",
]

#: Bytes of request body assumed to be worth one input token.
#:
#: Three, not four. English prose tokenizes at roughly four bytes per token, so three
#: over-counts by about a third — which is the direction a *bound* has to err. The body
#: is also JSON, and its braces, keys, and escapes are bytes the model never sees, which
#: pushes the same way.
#:
#: The honest limitation, stated rather than discovered: this is a heuristic over bytes,
#: and it is worst for a request carrying a base64 image, where megabytes of body
#: correspond to a few thousand image tokens. Such a request is over-reserved, and can
#: be refused against a budget it would in fact have fitted. Capping the estimate at the
#: model's context window (below) bounds the damage; removing it entirely would mean
#: tokenizing the request in the gateway, which is a per-request cost on the first-token
#: path for a number that is corrected milliseconds later anyway.
EST_BYTES_PER_TOKEN: Final = 3

#: Assumed generated-token ceiling when the caller states none.
#:
#: Only reachable on the OpenAI dialect (the Messages API requires ``max_tokens``). 4096
#: is the common default cap across OpenAI-compatible servers and is a number an
#: operator can reason about; it is not a guarantee, and a model that generates more
#: settles for more — see :func:`settlement_for` for what that costs.
DEFAULT_MAX_OUTPUT_TOKENS: Final = 4096


@dataclass(frozen=True, slots=True)
class Estimate:
    """The upper bound a request is admitted against, and how it was arrived at.

    Kept as a record rather than a bare ``Decimal`` because the 402 quotes it back to
    the caller, the ledger stores it, and ``tests/test_budget_estimate.py`` checks the
    arithmetic term by term. A formula nobody can see the parts of is a formula nobody
    can audit.
    """

    usd: Decimal
    input_tokens: int
    output_tokens: int
    #: ``False`` when no price row covers this model on this date — the estimate is
    #: then ``$0`` and the request is effectively invisible to the budget, exactly as
    #: it is invisible to the ledger's totals (H-023: unpriced is never "free", it is
    #: *unknown*, and a gate cannot bound an unknown).
    priced: bool = True


def estimate_usd(
    dialect: Dialect,
    body: dict[str, Any],
    raw_body: bytes,
    *,
    model: str,
    when: datetime,
    prices: PriceBook,
) -> Estimate:
    """The worst case this request can cost, at the rates in effect on ``when``.

    ::

        input_tokens  = min(ceil(len(body) / 3), context_window)
        output_tokens = max_tokens from the body, else 4096
        usd           = input_tokens * rate_in / 1e6 + output_tokens * rate_out / 1e6

    Both halves are included on purpose. Generated tokens dominate the *rate* (output is
    typically 4-5x input) but prompt tokens dominate the *count* — a long-context
    request with ``max_tokens: 64`` can cost far more on its prompt than on its answer,
    and an output-only estimate would wave it straight through a nearly-exhausted
    budget. The prompt half is capped at the model's context window when
    ``config/models.yaml`` states one, because that is a real ceiling on how many input
    tokens can exist rather than another heuristic.
    """
    entry = prices.resolve(model)
    price = entry.price_at(when.date()) if entry is not None else None
    output_tokens = dialect.max_output_tokens(body) or DEFAULT_MAX_OUTPUT_TOKENS
    input_tokens = math.ceil(len(raw_body) / EST_BYTES_PER_TOKEN)
    if entry is not None and entry.context_window:
        input_tokens = min(input_tokens, entry.context_window)

    if price is None:
        return Estimate(
            usd=quantize_usd(Decimal(0)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            priced=False,
        )
    total = usd_for_tokens(input_tokens, price.usd_per_mtok_in) + usd_for_tokens(
        output_tokens, price.usd_per_mtok_out
    )
    return Estimate(usd=quantize_usd(total), input_tokens=input_tokens, output_tokens=output_tokens)


def settlement_for(ctx: RequestContext, reservation: Reservation) -> Decimal:
    """What a finished request actually takes out of the budget.

    Decided from the meter's ``cost_status`` (``headroom/metering/cost.py``), because
    that field already encodes exactly the distinction this needs — *do we know what
    this cost, and if not, why not*:

    ==================  ====================  ===========================================
    ``cost_status``     Settles at            Why
    ==================  ====================  ===========================================
    ``priced``          the actual cost       Known exactly.
    ``partial``         the actual cost       A lower bound, and labelled as one on the
                                              row (H-026). The budget inherits the same
                                              caveat rather than inventing a number for
                                              the cache tiers nobody can rate.
    ``not_billable``    **$0** — released     No model ran. An upstream 4xx/5xx, an
                                              unroutable model, a refusal. Providers do
                                              not bill a rejected request and neither
                                              does this.
    ``unpriced_model``  **$0** — released     There was no price to reserve against
                                              either; the estimate was $0.
    ``usage_unknown``   **the estimate**      A model ran and we cannot ask what it
                                              charged: a timeout, a cut stream, a
                                              client that hung up. Releasing would be a
                                              cheerful guess that it cost nothing.
    ==================  ====================  ===========================================

    The last row is the one that earns the table, and it is deliberately *not* what the
    ledger does. H-025 writes NULL there, because a ledger row is an invoice line and
    "we do not know" is a thing it can say. A budget cannot say that: it has to hold a
    number, and the only defensible number is the bound already reserved.
    """
    status = ctx.cost_status
    if status in (COST_PRICED, COST_PARTIAL) and ctx.usd_cost is not None:
        return ctx.usd_cost
    if status in (COST_NOT_BILLABLE, COST_UNPRICED_MODEL):
        return Decimal(0)
    # `usage_unknown`, or a request that was never metered at all.
    return reservation.usd


@dataclass(slots=True)
class BudgetGate:
    """Admission and settlement, wired to one :class:`BudgetStore`.

    Held on the ``Gateway`` beside the meter, and used at two points in
    ``headroom/api/proxy.py``: :meth:`admit` before the upstream is opened, and
    :meth:`settle` wherever a request ends.
    """

    store: BudgetStore
    prices: PriceBook
    #: Settlements started on a cancelled task, kept referenced so they can finish and
    #: so a test can wait for them. See :meth:`settle`.
    _pending: set[asyncio.Task[None]] = field(default_factory=set, init=False)

    # --- admission ------------------------------------------------------------------

    async def admit(
        self,
        ctx: RequestContext,
        dialect: Dialect,
        body: dict[str, Any],
        raw_body: bytes,
        *,
        estimate: Estimate | None = None,
    ) -> None:
        """Hold this request's worst case against its tenant's budget, or refuse it.

        Raises :class:`~headroom.core.errors.BudgetExceeded` (402) when the hold cannot
        be taken. Because it raises *before* the caller returns, and because the proxy
        calls it before ``provider.open``, a refused request never reaches an upstream —
        which is asserted directly rather than reasoned about
        (``tests/test_budget_gate.py`` checks the MockProvider was never called).

        ``estimate`` is Phase 4b's one seam: the rate limiter runs first and needs the
        same bound in tokens, so the proxy computes it once and hands it to both gates.
        Passing ``None`` recomputes it here, which keeps every existing caller — and the
        gate's own tests — working unchanged. It is the *same* function either way; the
        parameter exists to stop one request being measured twice, not to let two callers
        measure it differently.
        """
        if ctx.tenant_id is None or ctx.model is None:  # pragma: no cover - proxy order
            return
        if estimate is None:
            estimate = estimate_usd(
                dialect, body, raw_body, model=ctx.model, when=ctx.started_at, prices=self.prices
            )
        scope = BudgetScope.tenant(ctx.tenant_id)
        result = await self.store.reserve(
            scope, request_id=ctx.request_id, usd=estimate.usd, when=ctx.started_at
        )
        ctx.budget_status = result.status

        if result.status == ADMIT_NO_BUDGET:
            # No cap configured for this tenant. Nothing held, nothing to record: an
            # estimate on a row no budget ever looked at would be noise.
            return
        if result.status == ADMIT_EXCEEDED:
            # Recorded even though nothing was held, so the 402's row says how much the
            # request wanted — otherwise the only surviving evidence of a refused
            # request is that it was refused.
            ctx.budget_reserved_usd = estimate.usd
            raise BudgetExceeded(_refusal_message(result.budget, estimate))

        assert result.reservation is not None  # ADMIT_RESERVED carries one
        ctx.budget_reservation = result.reservation
        ctx.budget_reserved_usd = result.reservation.usd

    # --- settlement -----------------------------------------------------------------

    async def settle(self, ctx: RequestContext, *, shielded: bool = False) -> None:
        """Replace this request's hold with what it actually cost. First call wins.

        ``shielded`` is for the one path that cannot simply await: a client disconnect
        arrives as :class:`asyncio.CancelledError`, and an ``await`` inside that handler
        can be cancelled again before it completes. There the settlement runs as its own
        task, shielded, and this coroutine gives up waiting rather than delaying the
        cancellation. Nothing is lost if it never finishes — the hold expires and the
        sweeper releases it, which is precisely the failure the expiry exists for.
        """
        reservation = ctx.budget_reservation
        if reservation is None:
            return
        # Cleared before the await, so a second caller — the middleware backstop, a
        # retry — cannot settle the same hold twice even if this one is still in flight.
        ctx.budget_reservation = None
        actual = settlement_for(ctx, reservation)
        ctx.budget_settled_usd = actual

        if not shielded:
            await self.store.settle(reservation, usd=actual, when=ctx.started_at)
            return

        task = asyncio.ensure_future(self._settle_quietly(reservation, actual, ctx.started_at))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)

    async def _settle_quietly(
        self, reservation: Reservation, actual: Decimal, when: datetime
    ) -> None:
        """A settlement that must not raise: it runs detached on the disconnect path,
        where there is no caller left to tell and the sweeper is the safety net."""
        with contextlib.suppress(Exception):
            await self.store.settle(reservation, usd=actual, when=when)

    async def drain(self) -> None:
        """Wait for detached settlements. Called at shutdown and by the test harness.

        The same shape as ``LedgerWriter.drain`` (H-027) and for the same reason: a
        test that asserted immediately after a disconnect would be racing a background
        task, and a suite that sleeps and hopes is a suite that flakes in CI.
        """
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    async def aclose(self) -> None:
        await self.drain()
        await self.store.aclose()


def _refusal_message(budget: Any, estimate: Estimate) -> str:
    """The 402's human half: the numbers a developer needs to unblock themselves.

    Every figure here is the tenant's own — their cap, their spend, their request's
    estimate — so quoting them discloses nothing the caller does not already own, and
    withholding them would make the most common support question ("why 402?")
    unanswerable without an operator.
    """
    if budget is None:  # pragma: no cover - a refusal always carries the budget
        return "this request would exceed the tenant's budget"
    window = "lifetime" if budget.window == "total" else f"{budget.window} ({budget.window_id})"
    return (
        f"this request would exceed the tenant's {window} budget of "
        f"${budget.usd:f}: ${budget.spent:f} settled plus ${budget.reserved:f} reserved "
        f"leaves ${budget.remaining:f}, and this request reserves ${estimate.usd:f} "
        f"({estimate.input_tokens} prompt + {estimate.output_tokens} generated tokens "
        f"at the current rate)"
    )
