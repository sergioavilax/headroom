"""H2's analysis, against fixture rows — the run itself is the operator's, the arithmetic is not.

The $8 run happens once, on a machine with a key. What must not happen once is the code
that decides what it *meant*: every verdict `analyze.py` can reach is exercised here on
synthetic rows, including the ones that would invalidate the run, so the adjudication is
known to be capable of saying no before the money is spent.
"""

from __future__ import annotations

from typing import Any

import pytest

from experiments.h2.analyze import (
    NOISE_BOUND,
    OVERHEAD_TARGET_P50_MS,
    PRIMARY_REFERENCE,
    REFERENCE_OVERALL,
    analyse,
    percentile,
)


def row(**overrides: Any) -> dict[str, Any]:
    """A healthy H2 ledger row: passthrough, cache off, no hop, priced."""
    base: dict[str, Any] = {
        "outcome": "ok",
        "status_code": 200,
        "error_reason": None,
        "cost_status": "priced",
        "budget_status": "reserved",
        "cache_disposition": "cache_disabled",
        "failover_hops": 0,
        "passthrough_overhead_ms": 0.02,
        "input_tokens": 1000,
        "output_tokens": 200,
        "usd_cost": "0.004000000000",
    }
    return {**base, **overrides}


# --- percentiles ---------------------------------------------------------------------------


def test_the_percentile_is_nearest_rank_and_never_invents_a_value() -> None:
    values = [float(n) for n in range(1, 101)]
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.95) == 95.0
    assert percentile(values, 0.99) == 99.0
    assert percentile([], 0.5) is None
    assert percentile([2.0], 0.99) == 2.0


# --- H-047's checkable-after-the-fact clause ------------------------------------------------


def test_a_run_with_every_row_cache_disabled_holds() -> None:
    result = analyse([row() for _ in range(10)])
    proof = result["cache_disabled_proof"]
    assert proof["rows_not_cache_disabled"] == 0
    assert proof["verdict"] == "HOLDS"


def test_one_cache_hit_invalidates_the_whole_overhead_figure() -> None:
    """H-047: a hit answers in microseconds without a provider, so an overhead figure
    measured over one is a hit-rate figure wearing its name."""
    rows = [row() for _ in range(9)] + [row(cache_disposition="cache_hit_exact")]
    proof = analyse(rows)["cache_disabled_proof"]
    assert proof["rows_not_cache_disabled"] == 1
    assert proof["verdict"] == "INVALIDATES THE RUN"


# --- overhead ------------------------------------------------------------------------------


def test_the_pre_registered_overhead_target_is_the_one_h_051_named() -> None:
    assert OVERHEAD_TARGET_P50_MS == 50.0


def test_overhead_holds_below_the_target_and_is_falsified_above_it() -> None:
    assert (
        analyse([row(passthrough_overhead_ms=0.02) for _ in range(20)])["overhead"]["primary"][
            "verdict"
        ]
        == "HOLDS"
    )
    assert (
        analyse([row(passthrough_overhead_ms=80.0) for _ in range(20)])["overhead"]["primary"][
            "verdict"
        ]
        == "FALSIFIED"
    )


def test_the_weakness_of_the_pre_registered_metric_travels_with_it() -> None:
    """H-065: reporting a sub-millisecond figure against a 50 ms target without saying so
    would be flattering by construction."""
    caveat = analyse([row()])["overhead"]["primary"]["caveat"]
    assert "weak test" in caveat


# --- error accounting ----------------------------------------------------------------------


def test_failover_hops_and_error_reasons_are_reported_whatever_they_say() -> None:
    rows = [
        row(),
        row(outcome="upstream_timeout", error_reason="upstream_timeout", status_code=504),
    ]
    errors = analyse(rows)["errors"]
    assert errors["outcomes"] == {"ok": 1, "upstream_timeout": 1}
    assert errors["error_reasons"] == {"upstream_timeout": 1}
    assert errors["failover_hops"] == {0: 2}


# --- parity (H-064) ------------------------------------------------------------------------


def test_the_primary_comparator_is_the_direct_local_run() -> None:
    assert PRIMARY_REFERENCE == "direct_local"
    assert REFERENCE_OVERALL["direct_local"] == 93.3
    assert REFERENCE_OVERALL["aws"] == 92.5
    assert REFERENCE_OVERALL["sweep"] == 91.6
    assert NOISE_BOUND == 3.0


@pytest.mark.parametrize(
    ("overall", "verdict"),
    [
        (93.3, "WITHIN NOISE"),
        (90.3, "WITHIN NOISE"),
        (96.3, "WITHIN NOISE"),
        (90.2, "OUTSIDE NOISE"),
    ],
)
def test_parity_is_judged_against_the_pre_registered_bound(overall: float, verdict: str) -> None:
    result = analyse([row()], summary={"overall": overall, "total_cost_usd": "0.004"})
    assert result["parity"]["verdict"] == verdict
    assert result["parity"]["delta"] == pytest.approx(round(overall - 93.3, 2))


def test_the_missing_paired_control_is_stated_before_any_result_is_read() -> None:
    parity = analyse([row()], summary={"overall": 93.0, "total_cost_usd": "0.004"})["parity"]
    assert "no same-day paired control" in parity["limitation"]
    assert "not an effect size" in parity["limitation"]


# --- the two-meter cross-check (§H2.4) ------------------------------------------------------


def test_two_meters_that_agree_to_a_cent_agree() -> None:
    rows = [row(usd_cost="4.000000000000")]
    cross = analyse(rows, summary={"overall": 93.0, "total_cost_usd": "4.005"})["cross_check"]
    assert cross["verdict"] == "AGREE"


def test_a_disagreement_between_the_meters_is_a_finding_not_a_rounding() -> None:
    rows = [row(usd_cost="4.000000000000")]
    cross = analyse(rows, summary={"overall": 93.0, "total_cost_usd": "4.500"})["cross_check"]
    assert cross["verdict"] == "DISAGREE"
    assert cross["delta_usd"].startswith("0.5")
