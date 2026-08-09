"""Turning tokens and a price row into dollars — exactly, and with a stated honesty.

**Decimal end to end.** Rates parse from strings into ``Decimal`` (``prices.py``),
arithmetic happens in ``Decimal``, storage is ``NUMERIC``, and serialization is a
string. No value on the path from ``config/models.yaml`` to an API response is ever a
``float``. The reason is the oldest one in the book — ``0.1 + 0.2 != 0.3`` in binary
floating point — and the consequence here is an invoice, so the type discipline is
worth more than the convenience.

**Every cost carries a status.** The interesting part of a cost meter is not the
arithmetic, it is the set of requests the arithmetic does not cover, and a meter that
writes ``0.00`` for those is indistinguishable from one that works. So a computed cost
always arrives labelled:

===================  ===========  =======================================================
``cost_status``      ``usd_cost``  Meaning
===================  ===========  =======================================================
``priced``           exact         Usage known, price known, nothing else was billed.
``partial``          lower bound   The provider billed prompt-cache tiers this price file
                                   cannot rate. The tokens are recorded; the figure is a
                                   bound, not a total (H-026).
``unpriced_model``   ``NULL``      No price row covers this model on this date. The
                                   tokens are still recorded. **Not** zero — unknown.
``usage_unknown``    ``NULL``      No usage block reached the gateway, or only part of
                                   one did (a stream cut before the totals). Whatever
                                   was reported is recorded; the cost is not guessed.
``not_billable``     ``0``         The request never reached a model: an upstream error,
                                   an unroutable model, a scope refusal. Providers do
                                   not bill a rejected request, and this is the one
                                   status where ``0`` is a measurement.
===================  ===========  =======================================================

``NULL`` and ``0`` mean different things and the distinction is load-bearing: Phase 8's
H2 reports error accounting off this table, and a dashboard that sums a column of
guessed zeros produces a number that is precisely wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from headroom.metering.prices import PriceBook, PriceRow
from headroom.metering.usage import Usage

__all__ = [
    "COST_NOT_BILLABLE",
    "COST_PARTIAL",
    "COST_PRICED",
    "COST_UNPRICED_MODEL",
    "COST_USAGE_UNKNOWN",
    "CostBreakdown",
    "price_usage",
    "quantize_usd",
    "usd_for_tokens",
]

COST_PRICED: Final = "priced"
COST_PARTIAL: Final = "partial"
COST_UNPRICED_MODEL: Final = "unpriced_model"
COST_USAGE_UNKNOWN: Final = "usage_unknown"
COST_NOT_BILLABLE: Final = "not_billable"

#: Rates are quoted per million tokens, the unit every provider publishes.
TOKENS_PER_MTOK: Final = Decimal(1_000_000)

#: Twelve decimal places — a millionth of a cent, stored as ``NUMERIC(24, 12)``.
#: Sized so the smallest realistic charge is exact rather than rounded: a single
#: output token at $1/Mtok is ``0.000001``, six places, with six to spare. Rates with
#: up to six decimals therefore quantize losslessly, and the mock fixtures land on
#: their expected values to the last digit rather than to within an epsilon.
USD_QUANTUM: Final = Decimal("0.000000000001")


def quantize_usd(value: Decimal) -> Decimal:
    """Round a computed cost to the ledger's stored precision.

    Explicit rather than left to Postgres: the value asserted by a test, the value
    returned by the API, and the value in the column must be the same object, and
    ``NUMERIC`` would otherwise do this rounding invisibly on the way in.
    """
    return value.quantize(USD_QUANTUM, rounding=ROUND_HALF_UP)


def usd_for_tokens(tokens: int, usd_per_mtok: Decimal) -> Decimal:
    """Cost of ``tokens`` at a per-million-token rate. Exact in decimal arithmetic."""
    return Decimal(tokens) * usd_per_mtok / TOKENS_PER_MTOK


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """What one request cost, the rates used, and how much to trust the figure."""

    status: str
    usd_cost: Decimal | None = None
    #: The row that was in effect. Copied into the ledger so a later edit to
    #: ``config/models.yaml`` cannot move a landed cost — the D-017 guarantee.
    price: PriceRow | None = None
    #: The two halves, kept apart because the dashboard charts them separately and
    #: because a request that is all prompt looks very different from one that is all
    #: generation at the same total.
    usd_input: Decimal | None = None
    usd_output: Decimal | None = None

    @property
    def is_exact(self) -> bool:
        """The figure is a total, not a bound and not a placeholder."""
        return self.status in (COST_PRICED, COST_NOT_BILLABLE)


def price_usage(
    usage: Usage,
    *,
    model: str | None,
    when: date,
    prices: PriceBook,
    billable: bool = True,
) -> CostBreakdown:
    """Price one request's usage at the rates in effect on ``when``.

    ``when`` is the request's own date — ``RequestContext.started_at`` — never "now".
    Metering runs after the response, a ledger write can be retried, and a run can be
    replayed from a fixture; pricing at wall-clock time would let any of those move a
    cost across a schedule boundary.

    ``billable=False`` is the caller stating that no model ran: an upstream error, an
    unroutable model, a key denied by scope. It short-circuits to ``$0`` with the
    ``not_billable`` status, and the rate in effect is still resolved and recorded so
    the row can say what the request *would* have cost.
    """
    price = prices.price_for(model, when) if model else None

    if not billable:
        return CostBreakdown(
            status=COST_NOT_BILLABLE,
            usd_cost=quantize_usd(Decimal(0)),
            price=price,
            usd_input=quantize_usd(Decimal(0)),
            usd_output=quantize_usd(Decimal(0)),
        )

    if not usage.is_complete:
        # Either nothing was reported, or a stream died before the totals arrived.
        # Whatever counts we do have are still written; the cost is not invented.
        return CostBreakdown(status=COST_USAGE_UNKNOWN, price=price)

    if price is None:
        return CostBreakdown(status=COST_UNPRICED_MODEL)

    # mypy: `is_complete` is exactly the guarantee that these two are not None.
    assert usage.input_tokens is not None
    assert usage.output_tokens is not None

    usd_input = usd_for_tokens(usage.input_tokens, price.usd_per_mtok_in)
    usd_output = usd_for_tokens(usage.output_tokens, price.usd_per_mtok_out)

    # A free model cannot be under-billed, so cache tiers it does not charge for are
    # not a caveat — only a non-zero rate turns unpriceable tokens into a bound.
    incomplete = usage.reports_cache_activity and not price.is_free

    return CostBreakdown(
        status=COST_PARTIAL if incomplete else COST_PRICED,
        usd_cost=quantize_usd(usd_input + usd_output),
        price=price,
        usd_input=quantize_usd(usd_input),
        usd_output=quantize_usd(usd_output),
    )
