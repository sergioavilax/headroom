"""Turning tokens into dollars: exactly, in Decimal, with the status that goes with it.

The arithmetic here is three lines long. What is worth testing is everything around
it — which requests get a figure, which get a bound, which get a NULL, and which get a
zero that is a measurement rather than a shrug. A meter that writes ``0.00`` for
"we don't know" passes every arithmetic test ever written and is still wrong.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from headroom.metering.cost import (
    COST_NOT_BILLABLE,
    COST_PARTIAL,
    COST_PRICED,
    COST_UNPRICED_MODEL,
    COST_USAGE_UNKNOWN,
    price_usage,
    quantize_usd,
    usd_for_tokens,
)
from headroom.metering.prices import ModelPrices, PriceBook, PriceRow
from headroom.metering.usage import Usage

WHEN = date(2026, 6, 1)


def prices(
    price_in: str = "0.25", price_out: str = "1.25", model: str = "mock-model-1"
) -> PriceBook:
    return PriceBook(
        [
            ModelPrices(
                model=model,
                dialects=("anthropic",),
                context_window=None,
                rows=(
                    PriceRow(
                        effective_from=date(1970, 1, 1),
                        usd_per_mtok_in=Decimal(price_in),
                        usd_per_mtok_out=Decimal(price_out),
                    ),
                ),
            )
        ]
    )


# --- the arithmetic -----------------------------------------------------------------


def test_a_rate_is_per_million_tokens() -> None:
    assert usd_for_tokens(1_000_000, Decimal("3.00")) == Decimal("3.00")
    assert usd_for_tokens(1, Decimal("1.00")) == Decimal("0.000001")


def test_the_canonical_mock_request_prices_to_an_exact_figure() -> None:
    """11 in at $0.25/Mtok + 7 out at $1.25/Mtok = $0.0000115. To the digit.

    The rates are chosen in ``config/models.yaml`` so this lands on a terminating
    decimal — a fixture whose expected cost needs a tolerance is a fixture that has
    already conceded the argument about floats.
    """
    result = price_usage(
        Usage(input_tokens=11, output_tokens=7), model="mock-model-1", when=WHEN, prices=prices()
    )

    assert result.usd_cost == Decimal("0.0000115")
    assert result.usd_input == Decimal("0.00000275")
    assert result.usd_output == Decimal("0.00000875")
    assert result.status == COST_PRICED


def test_the_reasoning_fixtures_cost_is_driven_by_billed_not_visible_tokens() -> None:
    """21 in, 63 out — of which 57 never appeared in the content stream.

    Eleven visible characters. A meter that counted the text would charge for six
    tokens and undercharge by a factor of ten.
    """
    result = price_usage(
        Usage(input_tokens=21, output_tokens=63, reasoning_tokens=57),
        model="mock-model-1",
        when=WHEN,
        prices=prices(),
    )

    assert result.usd_cost == Decimal("0.000084")


def test_no_float_survives_anywhere_in_a_breakdown() -> None:
    result = price_usage(
        Usage(input_tokens=11, output_tokens=7), model="mock-model-1", when=WHEN, prices=prices()
    )

    for value in (result.usd_cost, result.usd_input, result.usd_output):
        assert isinstance(value, Decimal)
    assert not isinstance(result.usd_cost, float)


def test_the_stored_precision_holds_a_millionth_of_a_cent() -> None:
    assert quantize_usd(Decimal("0.0000115")) == Decimal("0.000011500000")
    # A single output token at $1/Mtok — the smallest realistic charge — is exact.
    assert quantize_usd(usd_for_tokens(1, Decimal("1.00"))) == Decimal("0.000001000000")


def test_zero_tokens_at_a_real_rate_is_exactly_zero() -> None:
    result = price_usage(
        Usage(input_tokens=0, output_tokens=0), model="mock-model-1", when=WHEN, prices=prices()
    )

    assert result.usd_cost == Decimal(0)
    assert result.status == COST_PRICED


# --- the statuses, which are the point ----------------------------------------------


def test_a_model_with_no_price_row_is_unpriced_with_a_null_cost() -> None:
    """Not zero. The tokens are known; the money is not, and the row says which."""
    result = price_usage(
        Usage(input_tokens=11, output_tokens=7), model="gpt-5", when=WHEN, prices=prices()
    )

    assert result.status == COST_UNPRICED_MODEL
    assert result.usd_cost is None


def test_a_request_with_no_usage_block_is_unknown_not_free() -> None:
    result = price_usage(Usage(), model="mock-model-1", when=WHEN, prices=prices())

    assert result.status == COST_USAGE_UNKNOWN
    assert result.usd_cost is None


def test_half_a_usage_block_is_still_unknown() -> None:
    """A stream cut after ``message_start`` knows the prompt and not the answer.

    Billing the prompt alone would be a confident undercharge; this is the shape a
    mid-stream cut actually produces, and it must not price.
    """
    result = price_usage(Usage(input_tokens=11), model="mock-model-1", when=WHEN, prices=prices())

    assert result.status == COST_USAGE_UNKNOWN
    assert result.usd_cost is None


def test_a_request_that_never_ran_costs_exactly_zero() -> None:
    """The one status where zero is a measurement: no provider generated anything."""
    result = price_usage(Usage(), model="mock-model-1", when=WHEN, prices=prices(), billable=False)

    assert result.status == COST_NOT_BILLABLE
    assert result.usd_cost == Decimal(0)
    assert result.is_exact


def test_a_non_billable_request_still_records_the_rate_that_was_in_effect() -> None:
    """So the explorer can answer "what would this have cost" without a second lookup."""
    result = price_usage(Usage(), model="mock-model-1", when=WHEN, prices=prices(), billable=False)

    assert result.price is not None
    assert result.price.usd_per_mtok_in == Decimal("0.25")


def test_provider_cache_tokens_make_the_figure_a_bound_not_a_total() -> None:
    """``config/models.yaml`` has no cache-tier rates, so the cost is under-stated.

    Recorded and *labelled* rather than billed as complete — the same instinct as
    invariant 6, applied to money: an incomplete thing must not look finished
    (docs/DECISIONS.md H-026).
    """
    result = price_usage(
        Usage(input_tokens=11, output_tokens=7, cache_read_tokens=4_000),
        model="mock-model-1",
        when=WHEN,
        prices=prices(),
    )

    assert result.status == COST_PARTIAL
    assert result.usd_cost == Decimal("0.0000115")
    assert not result.is_exact


def test_a_free_model_cannot_be_under_billed_by_cache_tokens() -> None:
    """Zero times anything is zero, so a local GPU's cache activity is not a caveat."""
    result = price_usage(
        Usage(input_tokens=11, output_tokens=7, cache_read_tokens=4_000),
        model="mock-model-1",
        when=WHEN,
        prices=prices("0", "0"),
    )

    assert result.status == COST_PRICED
    assert result.usd_cost == Decimal(0)


def test_a_request_with_no_model_is_unpriced() -> None:
    result = price_usage(
        Usage(input_tokens=1, output_tokens=1), model=None, when=WHEN, prices=prices()
    )

    assert result.status == COST_UNPRICED_MODEL


@pytest.mark.parametrize(
    ("status", "expected"),
    [(COST_PRICED, True), (COST_NOT_BILLABLE, True), (COST_PARTIAL, False)],
)
def test_is_exact_separates_totals_from_bounds(status: str, expected: bool) -> None:
    from headroom.metering.cost import CostBreakdown

    assert CostBreakdown(status=status).is_exact is expected
