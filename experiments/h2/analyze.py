"""The H2 run, adjudicated against the pre-registration and nothing else.

Reads Headroom's ledger rows for the H2 tenant and, when it is handed one, Backline's own
`summary.json`, then reports every pre-registered quantity — including the ones that come
out badly. No metric is invented here; each maps to a clause of
`experiments/PRE_REGISTRATION.md` §2 and the code names which::

    uv run python -m experiments.h2.analyze --rows h2-rows.json \\
        --summary ~/code/backline/data/evals/<run-id>/summary.json

**Overhead is three numbers and the pre-registered one is the weakest** (H-065). This
module prints them in that order, with the weakness attached rather than footnoted:
`passthrough_overhead_ms` is the promised metric and measures the *forwarding* cost, which
lives in microseconds; the gateway's real cost is admission work that the ledger cannot
separate from provider time, so it is measured on the MockProvider by `bench.py`; and the
caller-visible answer is Backline's own end-to-end latency against its three references.

**The cache-disabled proof is a count, and it must be zero** (H-047). Any row whose
`cache_disposition` is not `cache_disabled` invalidates the overhead figure outright, so it
is reported first and the verdict says so.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from experiments.provenance import RESULTS_DIR, provenance, read_json, write_json

__all__ = ["REFERENCE_OVERALL", "analyse", "main", "percentile"]

#: PRE_REGISTRATION §H2.2 / H-064. The *primary* comparator is the direct-local run: it
#: differs from this treatment by exactly one thing, the gateway hop.
REFERENCE_OVERALL: Final[dict[str, float]] = {
    "direct_local": 93.3,
    "aws": 92.5,
    "sweep": 91.6,
}
#: Backline's own per-question latency percentiles for the same three runs — H-065's third
#: number, the caller-visible one, and the noisiest.
REFERENCE_LATENCY_MS: Final[dict[str, dict[str, int]]] = {
    "direct_local": {"p50": 12678, "p95": 65855},
    "aws": {"p50": 12508, "p95": 71122},
    "sweep": {"p50": 13033, "p95": 75870},
}
#: What each reference run cost, for the two-meter cross-check to sit beside.
REFERENCE_SPEND_USD: Final[dict[str, str]] = {
    "direct_local": "7.880494",
    "aws": "8.007034",
    "sweep": "8.094698",
}
PRIMARY_REFERENCE: Final = "direct_local"
NOISE_BOUND: Final = 3.0
#: H-051 named this column, in Phase 6, before this data existed.
OVERHEAD_TARGET_P50_MS: Final = 50.0

RESULT_PATH: Final = RESULTS_DIR / "h2_analysis.json"


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile: the ``ceil(p * N)``-th smallest observation.

    No interpolation, deliberately. An interpolated p95 invents a value that no request
    ever took, and every figure in this report is meant to be a measurement somebody could
    find in the ledger.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return round(ordered[rank - 1], 4)


def _spread(values: list[float]) -> dict[str, float | None]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 4) if values else None,
    }


def analyse(rows: list[dict[str, Any]], *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    ok_rows = [row for row in rows if row["outcome"] == "ok"]
    overhead = [
        float(row["passthrough_overhead_ms"])
        for row in ok_rows
        if row.get("passthrough_overhead_ms") is not None
    ]
    dispositions = Counter(str(row.get("cache_disposition")) for row in rows)
    not_disabled = sum(count for value, count in dispositions.items() if value != "cache_disabled")

    tokens_in = sum(int(row["input_tokens"] or 0) for row in rows)
    tokens_out = sum(int(row["output_tokens"] or 0) for row in rows)
    metered = sum(
        (Decimal(str(row["usd_cost"])) for row in rows if row.get("usd_cost") is not None),
        Decimal("0"),
    )

    result: dict[str, Any] = {
        "schema": "h2_analysis/1",
        "provenance": provenance(produced_by="experiments/h2/analyze.py"),
        "rows": len(rows),
        # H-047's checkable-after-the-fact clause. First, because a non-zero count here
        # means the overhead figure below is a hit-rate figure and nothing else matters.
        "cache_disabled_proof": {
            "statement": "every row carries cache_disposition = cache_disabled",
            "dispositions": dict(dispositions),
            "rows_not_cache_disabled": not_disabled,
            "verdict": "HOLDS" if not_disabled == 0 else "INVALIDATES THE RUN",
        },
        "overhead": {
            "primary": {
                "metric": "passthrough_overhead_ms (first upstream byte -> first byte out)",
                "source": "H-051, named in Phase 6 before this data existed",
                "target_p50_ms": OVERHEAD_TARGET_P50_MS,
                **_spread(overhead),
                "caveat": (
                    "expected to be sub-millisecond (P1 measured 0.006 ms, P6 0.019 ms), so "
                    "meeting a 50 ms target by four orders of magnitude is a weak test. It is "
                    "reported first because it is the metric that was promised."
                ),
            },
            "secondary_admission_cost": (
                "see experiments/results/h2_bench.json — upstream_latency_ms over MockProvider "
                "requests, where the provider costs ~0 and what remains is the gateway's own "
                "admission work (H-065)"
            ),
        },
        "errors": {
            "outcomes": dict(Counter(str(row["outcome"]) for row in rows)),
            "error_reasons": dict(
                Counter(str(row["error_reason"]) for row in rows if row.get("error_reason"))
            ),
            "cost_status": dict(Counter(str(row["cost_status"]) for row in rows)),
            "budget_status": dict(
                Counter(str(row.get("budget_status")) for row in rows if row.get("budget_status"))
            ),
            "failover_hops": dict(Counter(int(row.get("failover_hops") or 0) for row in rows)),
        },
        "headroom_meter": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usd_cost": str(metered),
            "unpriced_rows": sum(1 for row in rows if row["cost_status"] != "priced"),
        },
    }

    if overhead:
        p50 = result["overhead"]["primary"]["p50"]
        result["overhead"]["primary"]["verdict"] = (
            "HOLDS" if p50 is not None and p50 < OVERHEAD_TARGET_P50_MS else "FALSIFIED"
        )

    if summary is not None:
        result["parity"] = _parity(summary)
        result["cross_check"] = _cross_check(summary, tokens_in, tokens_out, metered)
    else:
        result["parity"] = {"note": "no Backline summary supplied — pass --summary"}
    return result


def _parity(summary: dict[str, Any]) -> dict[str, Any]:
    overall = float(summary["overall"]) if "overall" in summary else None
    reference = REFERENCE_OVERALL[PRIMARY_REFERENCE]
    delta = round(overall - reference, 2) if overall is not None else None
    return {
        "statement": f"|overall - {reference}| <= {NOISE_BOUND} (H-064: the direct-local run)",
        "overall": overall,
        "primary_reference": PRIMARY_REFERENCE,
        "reference_overall": reference,
        "delta": delta,
        "bound": NOISE_BOUND,
        "context": {
            name: {
                "overall": score,
                "delta": round(overall - score, 2),
                "latency_p50_ms": REFERENCE_LATENCY_MS[name]["p50"],
                "spend_usd": REFERENCE_SPEND_USD[name],
            }
            for name, score in REFERENCE_OVERALL.items()
            if overall is not None
        },
        "limitation": (
            "no same-day paired control (H-064): between-day drift sits inside this residual "
            "and cannot be separated from the gateway's effect. The claim is about a bound, "
            "not an effect size."
        ),
        "verdict": (
            "WITHIN NOISE"
            if delta is not None and abs(delta) <= NOISE_BOUND
            else ("OUTSIDE NOISE" if delta is not None else "NO DATA")
        ),
    }


def _cross_check(
    summary: dict[str, Any], tokens_in: int, tokens_out: int, metered: Decimal
) -> dict[str, Any]:
    """Two independent meters over one stream of traffic. Pre-registered as falsifiable."""
    backline_cost = Decimal(str(summary.get("total_cost_usd", "0")))
    delta = (metered - backline_cost).copy_abs()
    return {
        "statement": "token totals agree exactly; cost agrees to within $0.01 (§H2.4)",
        "headroom_usd": str(metered),
        "backline_usd": str(backline_cost),
        "delta_usd": str(delta),
        "headroom_tokens": {"input": tokens_in, "output": tokens_out},
        "note": (
            "Backline meters from the SDK's usage block; Headroom meters from the same block "
            "observed in the stream. Backline uses no prompt caching, so H-026's `partial` "
            "caveat cannot arise."
        ),
        "verdict": "AGREE" if delta <= Decimal("0.01") else "DISAGREE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h2.analyze",
        description="Adjudicate the H2 run against the pre-registration. Free.",
    )
    parser.add_argument("--rows", required=True, help="JSON array of the H2 tenant's ledger rows")
    parser.add_argument("--summary", default=None, help="Backline's summary.json for the run")
    parser.add_argument("--out", default=str(RESULT_PATH))
    args = parser.parse_args(argv)

    summary = read_json(Path(args.summary)) if args.summary else None
    result = analyse(read_json(Path(args.rows)), summary=summary)
    write_json(Path(args.out), result)

    proof = result["cache_disabled_proof"]
    primary = result["overhead"]["primary"]
    print(
        f"{result['rows']} rows\n"
        f"  cache disabled (H-047): {proof['verdict']} "
        f"({proof['rows_not_cache_disabled']} rows not cache_disabled)\n"
        f"  passthrough overhead: p50 {primary['p50']} ms, p95 {primary['p95']} ms, "
        f"p99 {primary['p99']} ms -> {primary.get('verdict', 'NO DATA')}\n"
        f"  outcomes: {result['errors']['outcomes']}\n"
        f"  failover hops: {result['errors']['failover_hops']} (must be {{0: n}})\n"
        f"  parity: {result['parity'].get('verdict', result['parity'].get('note'))}"
    )
    if "cross_check" in result:
        cross = result["cross_check"]
        print(
            f"  two-meter cross-check: {cross['verdict']} "
            f"(Headroom ${cross['headroom_usd']} vs Backline ${cross['backline_usd']})"
        )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
