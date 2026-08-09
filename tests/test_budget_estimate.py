"""The estimate: the number the whole gate is only as good as.

A reservation-based gate is exactly as trustworthy as the bound it reserves. If the
estimate can be lower than the eventual cost, then "total settled spend never exceeds
the budget" is not a property of the design, it is a hope — so this file checks the
formula term by term against figures computed by hand, and then checks the property
that matters: for the fixtures the rest of the suite uses, the estimate really is an
upper bound on what the request turns out to cost.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from headroom.core.budgets import to_picos
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.base import Dialect
from headroom.dialects.openai import OPENAI
from headroom.metering.prices import load_price_book
from headroom.policy.budgets import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    EST_BYTES_PER_TOKEN,
    Estimate,
    estimate_usd,
)

WHEN = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
PRICES = load_price_book()

#: The committed rates for `mock-` models (config/models.yaml, H-023): flat, from the
#: epoch, chosen so every figure in the suite is a terminating decimal.
MOCK_IN = Decimal("0.25")
MOCK_OUT = Decimal("1.25")


def estimate_for(dialect: Dialect, body: dict[str, Any]) -> tuple[Estimate, bytes]:
    raw = json.dumps(body, separators=(",", ":")).encode()
    estimate = estimate_usd(dialect, body, raw, model=str(body["model"]), when=WHEN, prices=PRICES)
    return estimate, raw


def by_hand(input_tokens: int, output_tokens: int) -> Decimal:
    return (Decimal(input_tokens) * MOCK_IN + Decimal(output_tokens) * MOCK_OUT) / Decimal(
        1_000_000
    )


# --- the formula ---------------------------------------------------------------------


def test_the_estimate_is_the_hand_computed_figure() -> None:
    """Every term visible, and the total checked against arithmetic done on paper."""
    body = {
        "model": "mock-model-1",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "hello"}],
    }
    estimate, raw = estimate_for(ANTHROPIC, body)

    expected_input = math.ceil(len(raw) / EST_BYTES_PER_TOKEN)
    assert estimate.input_tokens == expected_input
    assert estimate.output_tokens == 1000
    assert estimate.usd == by_hand(expected_input, 1000)
    assert estimate.priced is True

    # And the same figure spelled out, so a change to the constants above has to be
    # deliberate rather than absorbed: 89 body bytes over 3 is 30 prompt tokens.
    assert len(raw) == 89
    assert expected_input == 30
    assert estimate.usd == Decimal("0.0012575")  # 30 * 0.25/1e6 + 1000 * 1.25/1e6


def test_bytes_per_token_over_counts_on_purpose() -> None:
    """Three bytes per token, not four: a bound has to err upward.

    English prose runs about four bytes to the token, and the body carries JSON
    scaffolding the model never sees, so this over-counts roughly by a third — which is
    the direction that refuses a request rather than the direction that lets spend past.
    """
    assert EST_BYTES_PER_TOKEN == 3


def test_a_caller_who_states_no_ceiling_gets_the_documented_default() -> None:
    """Only reachable on the OpenAI dialect: the Messages API requires ``max_tokens``."""
    body = {"model": "mock-model-1", "messages": [{"role": "user", "content": "hi"}]}
    estimate, _ = estimate_for(OPENAI, body)

    assert estimate.output_tokens == DEFAULT_MAX_OUTPUT_TOKENS == 4096


def test_the_openai_dialect_prefers_max_completion_tokens_over_the_legacy_spelling() -> None:
    body = {
        "model": "mock-model-1",
        "max_tokens": 8,
        "max_completion_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
    }
    estimate, _ = estimate_for(OPENAI, body)

    assert estimate.output_tokens == 64


def test_a_nonsense_ceiling_reads_as_no_ceiling() -> None:
    """The gateway does not validate the body (BUILD_PLAN L4) — the provider will. But
    it must not read ``max_tokens: 0`` as "this request is free"."""
    for value in (0, -1, "many", None, True):
        body = {
            "model": "mock-model-1",
            "max_tokens": value,
            "messages": [{"role": "user", "content": "hi"}],
        }
        estimate, _ = estimate_for(ANTHROPIC, body)
        assert estimate.output_tokens == DEFAULT_MAX_OUTPUT_TOKENS, value


def test_the_prompt_estimate_is_capped_at_the_models_context_window() -> None:
    """A real ceiling rather than another heuristic: more input tokens than the window
    cannot exist. It is what bounds the damage on a request carrying a large image,
    where bytes and tokens stop being proportional."""
    huge = "x" * (400_000 * EST_BYTES_PER_TOKEN)
    body = {
        "model": "mock-model-1",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": huge}],
    }
    estimate, _ = estimate_for(ANTHROPIC, body)

    assert estimate.input_tokens == 200_000  # config/models.yaml's `mock-` window


def test_an_unpriced_model_estimates_zero_and_says_so() -> None:
    """H-023's rule, one layer up: a model nobody entered has an *unknown* cost, and a
    gate cannot bound an unknown. It is admitted, its ledger row is ``unpriced_model``
    with a NULL cost, and the README documents the trap — the fix is to price it."""
    body = {
        "model": "not-in-the-price-book",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "hi"}],
    }
    estimate, _ = estimate_for(ANTHROPIC, body)

    assert estimate.priced is False
    assert estimate.usd == 0


def test_a_free_model_estimates_zero_but_is_priced() -> None:
    """The operator's vLLM is priced at an honest $0.00 (H-023), which is a
    *measurement* — unlike the unpriced case above, which is an absence."""
    body = {
        "model": "cyankiwi/Qwen3.6-27B-AWQ-INT4",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "hi"}],
    }
    estimate, _ = estimate_for(OPENAI, body)

    assert estimate.priced is True
    assert estimate.usd == 0


# --- the property that matters ---------------------------------------------------------


@pytest.mark.parametrize("max_tokens", [8, 32, 1024])
def test_the_estimate_bounds_what_the_canonical_fixture_actually_costs(
    max_tokens: int,
) -> None:
    """The suite's canonical mock reply is 11 in / 7 out — $0.0000115.

    Every estimate for a request that could produce it is larger, which is what makes
    "settled spend never exceeds the budget" a consequence of the design rather than a
    coincidence of the fixtures.
    """
    body = {
        "model": "mock-model-1",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "hi"}],
    }
    estimate, _ = estimate_for(ANTHROPIC, body)

    assert estimate.usd > Decimal("0.0000115")


def test_the_reserved_amount_is_rounded_up_to_a_whole_picodollar() -> None:
    """The store rounds an estimate up on its way into integer picodollars, so a
    sub-quantum fraction can never round *down* into a slightly smaller hold."""
    body = {
        "model": "mock-model-1",
        "max_tokens": 3,
        "messages": [{"role": "user", "content": "hi"}],
    }
    estimate, _ = estimate_for(ANTHROPIC, body)

    assert to_picos(estimate.usd, conservative=True) >= to_picos(estimate.usd)
