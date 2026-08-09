"""The price registry: dated schedules, and the file the gateway actually ships with.

This file has two halves and they are testing different things.

The first half tests the **mechanism** — that a price history resolves by date, that
appending a future row cannot reach backwards, that a float never becomes money — using
books built in code, so the assertions are about the code rather than about today's
rates.

The second half tests the **committed** ``config/models.yaml``, because a price file
that loads is not the same as a price file that is right. It pins the properties the
rest of the suite depends on: that every ``mock-`` entry is flat (so no test cost moves
on a calendar day), and that the one real dated boundary in the file — Anthropic's
published Sonnet 5 introductory price, which ends 2026-08-31 — resolves on both sides.
"""

from __future__ import annotations

import tempfile
import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from headroom.core.errors import ConfigurationError
from headroom.metering.prices import (
    DEFAULT_MODELS_PATH,
    MATCH_PREFIX,
    ModelPrices,
    PriceBook,
    PriceRow,
    load_price_book,
)

# --- the mechanism ------------------------------------------------------------------


def row(when: str, price_in: str, price_out: str) -> PriceRow:
    return PriceRow(
        effective_from=date.fromisoformat(when),
        usd_per_mtok_in=Decimal(price_in),
        usd_per_mtok_out=Decimal(price_out),
    )


def book(*rows: PriceRow, model: str = "m", match: str = "exact") -> PriceBook:
    return PriceBook(
        [
            ModelPrices(
                model=model, dialects=("anthropic",), context_window=None, rows=rows, match=match
            )
        ]
    )


def test_a_price_resolves_to_the_row_in_effect_on_that_day() -> None:
    prices = book(row("2026-01-01", "1", "2"), row("2026-06-01", "3", "4"))

    assert prices.price_for("m", date(2026, 5, 31)) == row("2026-01-01", "1", "2")
    assert prices.price_for("m", date(2026, 6, 1)) == row("2026-06-01", "3", "4")


def test_the_effective_date_is_inclusive_on_its_own_day() -> None:
    """A rate published "from the 1st" applies on the 1st, not from the 2nd."""
    prices = book(row("2026-06-01", "3", "4"))

    assert prices.price_for("m", date(2026, 5, 31)) is None
    assert prices.price_for("m", date(2026, 6, 1)) == row("2026-06-01", "3", "4")


def test_a_date_before_the_history_starts_is_unpriced_not_free() -> None:
    """The honest answer to "what did this cost before we wrote any rate down"."""
    prices = book(row("2026-06-01", "3", "4"))

    assert prices.price_for("m", date(2020, 1, 1)) is None


def test_a_model_nobody_entered_is_unpriced() -> None:
    """Never a default, never a nearest neighbour, never zero — D-017's whole shape."""
    assert book(row("2026-01-01", "1", "2")).price_for("gpt-5", date(2026, 6, 1)) is None


def test_adding_a_future_price_cannot_reach_backwards() -> None:
    """**The D-017 property, at the registry.**

    A price change is an append. The same request, resolved before and after the
    append, must resolve to the same row — because the new row's date is not on or
    before the request's, so it was never a candidate. (The ledger's half of this
    guarantee — that a row already written keeps its rates — is asserted in
    ``tests/test_metering.py``.)
    """
    asked_on = date(2026, 5, 1)
    before = book(row("2026-01-01", "1", "2"))
    after = book(row("2026-01-01", "1", "2"), row("2026-06-01", "999", "999"))

    assert before.price_for("m", asked_on) == after.price_for("m", asked_on)


def test_file_order_is_not_price_order() -> None:
    """Rows sort by date at load, so a diff that moves a line cannot move a price."""
    prices = load_price_book(
        _write_prices(
            """
        models:
          m:
            prices:
              - {effective_from: "2026-06-01", usd_per_mtok_in: "3", usd_per_mtok_out: "4"}
              - {effective_from: "2026-01-01", usd_per_mtok_in: "1", usd_per_mtok_out: "2"}
        """
        )
    )

    assert prices.price_for("m", date(2026, 3, 1)) == row("2026-01-01", "1", "2")
    assert prices.price_for("m", date(2026, 7, 1)) == row("2026-06-01", "3", "4")


# --- matching -----------------------------------------------------------------------


def test_a_prefix_entry_prices_a_whole_family() -> None:
    prices = book(row("1970-01-01", "1", "2"), model="mock-", match=MATCH_PREFIX)

    assert prices.price_for("mock-model-1", date(2026, 6, 1)) is not None
    assert prices.price_for("mock-reasoner-1", date(2026, 6, 1)) is not None
    assert prices.price_for("claude-haiku-4-5", date(2026, 6, 1)) is None


def test_an_exact_entry_beats_a_prefix_that_also_matches() -> None:
    prices = PriceBook(
        [
            ModelPrices("mock-", (), None, (row("1970-01-01", "1", "1"),), match=MATCH_PREFIX),
            ModelPrices("mock-special", (), None, (row("1970-01-01", "9", "9"),)),
        ]
    )

    resolved = prices.price_for("mock-special", date(2026, 6, 1))
    assert resolved is not None and resolved.usd_per_mtok_in == Decimal("9")


def test_the_longest_matching_prefix_wins() -> None:
    """The routing table's rule (H-013), so one family can carve out a sub-family."""
    prices = PriceBook(
        [
            ModelPrices("claude-", (), None, (row("1970-01-01", "1", "1"),), match=MATCH_PREFIX),
            ModelPrices(
                "claude-opus", (), None, (row("1970-01-01", "5", "5"),), match=MATCH_PREFIX
            ),
        ]
    )

    opus = prices.price_for("claude-opus-5", date(2026, 6, 1))
    haiku = prices.price_for("claude-haiku-4-5", date(2026, 6, 1))
    assert opus is not None and opus.usd_per_mtok_in == Decimal("5")
    assert haiku is not None and haiku.usd_per_mtok_in == Decimal("1")


# --- money is never a float ---------------------------------------------------------


def _write_prices(body: str) -> Path:
    """A price file on disk, so the *loader* is what these tests exercise."""
    target = Path(tempfile.mkdtemp()) / "models.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    return target


def test_an_unquoted_rate_is_a_load_error_not_a_float() -> None:
    """The trap this rule exists for: YAML reads a bare 3.00 as a float.

    Refused at load, with a message that says what to write instead — because a rate
    that has already been through a binary float is wrong before anything multiplies it.
    """
    path = _write_prices(
        """
        models:
          m:
            prices:
              - {effective_from: "2026-01-01", usd_per_mtok_in: 3.00, usd_per_mtok_out: "4"}
        """
    )

    with pytest.raises(ConfigurationError) as caught:
        load_price_book(path)
    assert "float" in str(caught.value)


def test_rates_load_as_decimal_and_stay_exact() -> None:
    """0.1 + 0.2 is the whole argument. In Decimal it is 0.3; in float it is not."""
    prices = load_price_book(
        _write_prices(
            """
        models:
          m:
            prices:
              - {effective_from: "2026-01-01", usd_per_mtok_in: "0.1", usd_per_mtok_out: "0.2"}
        """
        )
    )

    resolved = prices.price_for("m", date(2026, 6, 1))
    assert resolved is not None
    assert isinstance(resolved.usd_per_mtok_in, Decimal)
    assert resolved.usd_per_mtok_in + resolved.usd_per_mtok_out == Decimal("0.3")


def test_two_rows_on_the_same_day_are_refused() -> None:
    """Which one applied would depend on file order — a price decided by a diff."""
    path = _write_prices(
        """
        models:
          m:
            prices:
              - {effective_from: "2026-01-01", usd_per_mtok_in: "1", usd_per_mtok_out: "2"}
              - {effective_from: "2026-01-01", usd_per_mtok_in: "9", usd_per_mtok_out: "9"}
        """
    )

    with pytest.raises(ConfigurationError):
        load_price_book(path)


def test_a_negative_rate_is_refused() -> None:
    path = _write_prices(
        """
        models:
          m:
            prices:
              - {effective_from: "2026-01-01", usd_per_mtok_in: "-1", usd_per_mtok_out: "2"}
        """
    )

    with pytest.raises(ConfigurationError):
        load_price_book(path)


def test_an_unknown_field_is_refused() -> None:
    """``extra="forbid"``, so a typo fails at startup instead of silently doing nothing."""
    path = _write_prices(
        """
        models:
          m:
            usd_per_mtok: "1"
            prices: []
        """
    )

    with pytest.raises(ConfigurationError):
        load_price_book(path)


def test_a_missing_file_is_a_configuration_error() -> None:
    """Never an empty book: a gateway that priced nothing would look like it worked."""
    with pytest.raises(ConfigurationError):
        load_price_book(Path("/nonexistent/models.yaml"))


# --- the committed config/models.yaml -----------------------------------------------


@pytest.fixture(scope="module")
def shipped() -> PriceBook:
    """The price book the gateway is built with. Not a fixture — the real file."""
    return load_price_book(DEFAULT_MODELS_PATH)


def test_the_shipped_price_file_loads(shipped: PriceBook) -> None:
    assert len(shipped) > 0
    assert DEFAULT_MODELS_PATH.exists()


def test_every_mock_model_is_flat_priced(shipped: PriceBook) -> None:
    """**The reason test costs never move on a calendar day.**

    Real models get a price *history*; mock models deliberately get exactly one row,
    effective from the epoch. A second row with a later date would make every
    exact-cost assertion in this suite start failing on the day it took effect —
    and a suite that fails on a Tuesday for reasons nobody changed is a suite people
    learn to ignore (docs/DECISIONS.md H-023).
    """
    mock_entries = [entry for entry in shipped.models() if entry.model.startswith("mock")]

    assert mock_entries, "config/models.yaml must price the mock models"
    for entry in mock_entries:
        assert len(entry.rows) == 1, f"{entry.model} has a dated price history; it must be flat"
        assert entry.rows[0].effective_from == date(1970, 1, 1)


def test_the_mock_rate_is_the_one_every_cost_assertion_expects(shipped: PriceBook) -> None:
    """Pinned here, once, so a changed rate fails with a message about the rate."""
    price = shipped.price_for("mock-model-1", date(2026, 6, 1))

    assert price is not None
    assert price.usd_per_mtok_in == Decimal("0.25")
    assert price.usd_per_mtok_out == Decimal("1.25")


def test_both_mock_models_the_suite_uses_are_priced(shipped: PriceBook) -> None:
    for model in ("mock-model-1", "mock-reasoner-1", "mock-echo"):
        assert shipped.price_for(model, date(2026, 6, 1)) is not None, model


def test_the_local_vllm_model_is_honest_zero_not_unpriced(shipped: PriceBook) -> None:
    """A GPU on the operator's desk costs $0 in API terms, and says so.

    Stated rather than left to the unpriced fallback, so a $0.00 row on that model
    means "free" and not "we have no idea".
    """
    price = shipped.price_for("cyankiwi/Qwen3.6-27B-AWQ-INT4", date(2026, 6, 1))

    assert price is not None and price.is_free


def test_the_real_dated_boundary_in_the_shipped_file(shipped: PriceBook) -> None:
    """**D-017 against a real vendor-published date, not a contrivance.**

    Anthropic published Claude Sonnet 5 at an introductory $2/$10 per MTok *through
    2026-08-31*, reverting to $3/$15. So the identical request costs different money
    on either side of that midnight, and the shipped price file knows it.
    """
    last_intro_day = shipped.price_for("claude-sonnet-5", date(2026, 8, 31))
    first_standard_day = shipped.price_for("claude-sonnet-5", date(2026, 9, 1))

    assert last_intro_day is not None and first_standard_day is not None
    assert last_intro_day.usd_per_mtok_in == Decimal("2.00")
    assert last_intro_day.usd_per_mtok_out == Decimal("10.00")
    assert first_standard_day.usd_per_mtok_in == Decimal("3.00")
    assert first_standard_day.usd_per_mtok_out == Decimal("15.00")


def test_a_real_model_before_its_first_row_is_unpriced(shipped: PriceBook) -> None:
    """Earlier history is deliberately not modelled — guessing a start date is D-017.

    Headroom has never billed a request before those rows begin, so "unknown" is the
    true answer and the ledger has a way to say it.
    """
    assert shipped.price_for("claude-haiku-4-5", date(2026, 8, 7)) is None
    assert shipped.price_for("claude-haiku-4-5", date(2026, 8, 8)) is not None


def test_every_shipped_rate_is_a_decimal(shipped: PriceBook) -> None:
    """No float reaches the arithmetic, asserted over the whole committed file."""
    for entry in shipped.models():
        for price in entry.rows:
            assert isinstance(price.usd_per_mtok_in, Decimal)
            assert isinstance(price.usd_per_mtok_out, Decimal)
