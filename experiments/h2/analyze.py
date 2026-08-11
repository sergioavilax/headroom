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

**The export contract is checked before any arithmetic** (H-072). An export missing a column
this module reads used to surface as a bare ``KeyError`` on the first row — or, worse, not at
all: a `.get` on an absent `cache_disposition` silently counts every row as *not* cache
disabled and invalidates a perfectly good run. :data:`REQUIRED_COLUMNS` is the contract, it
is checked once up front, and the error names every missing column and the runbook step that
produces them.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from experiments.provenance import RESULTS_DIR, provenance, read_json, write_json

__all__ = [
    "REFERENCE_OVERALL",
    "REQUIRED_COLUMNS",
    "analyse",
    "main",
    "overall_score",
    "percentile",
    "require_columns",
]

#: Every ledger column this module reads. RUNBOOK §2f's export SQL must select all of them,
#: and `test_the_runbook_export_selects_every_column_the_analyzer_reads` holds the two
#: together so they cannot drift again (H-072).
REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "cache_disposition",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_status",
        "error_reason",
        "failover_hops",
        "input_tokens",
        "model",
        "outcome",
        "output_tokens",
        "passthrough_overhead_ms",
        "reasoning_tokens",
        "usd_cost",
    }
)

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


def require_columns(rows: list[dict[str, Any]]) -> None:
    """Refuse an export that is missing a column the analysis reads (H-072).

    Checked against the union of the rows' keys rather than the first row's, because a
    ``json_agg`` export is homogeneous and a caller hand-assembling rows should be told
    about every gap at once rather than one per run.
    """
    if not rows:
        raise ValueError("no ledger rows — the export is empty; check the tenant id in §2f")
    present: set[str] = set()
    for row in rows:
        present |= row.keys()
    missing = sorted(REQUIRED_COLUMNS - present)
    if missing:
        raise ValueError(
            "the ledger export is missing "
            + f"{len(missing)} column(s) this analysis reads: {', '.join(missing)}. "
            "RUNBOOK.md §2f's SELECT is the export that satisfies this contract — re-run it "
            "rather than adding the columns by hand, so the two stay in step."
        )


def overall_score(summary: dict[str, Any]) -> float:
    """Backline's own overall: the n-weighted mean of its category scores.

    `summary.json` **does not carry an `overall` key** — `evals/report.py` computes it at
    render time as ``sum(score * n) / sum(n)`` and prints it to one decimal place. Reading
    a key that was never written is how the first pass at this reported ``parity: NO DATA``
    against a perfectly good summary (H-072). This function is that arithmetic, and nothing
    else; the rounding is left to the caller so the exact value stays visible.
    """
    categories = summary.get("categories")
    if not categories:
        raise ValueError(
            "not an eval summary: no `categories` block. Backline's overall is the "
            "n-weighted mean of per-category scores (evals/report.py), so a summary "
            "without categories has no overall to compare against."
        )
    total = sum(int(bucket["n"]) for bucket in categories.values())
    if total == 0:
        raise ValueError("eval summary scored 0 questions — there is no overall to compute")
    weighted = sum(float(bucket["score"]) * int(bucket["n"]) for bucket in categories.values())
    return weighted / total


def _spread(values: list[float]) -> dict[str, float | None]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 4) if values else None,
    }


def analyse(rows: list[dict[str, Any]], *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    require_columns(rows)
    ok_rows = [row for row in rows if row["outcome"] == "ok"]
    overhead = [
        float(row["passthrough_overhead_ms"])
        for row in ok_rows
        if row["passthrough_overhead_ms"] is not None
    ]
    dispositions = Counter(str(row["cache_disposition"]) for row in rows)
    not_disabled = sum(count for value, count in dispositions.items() if value != "cache_disabled")

    tokens_in = sum(int(row["input_tokens"] or 0) for row in rows)
    tokens_out = sum(int(row["output_tokens"] or 0) for row in rows)
    tokens_reasoning = sum(int(row["reasoning_tokens"] or 0) for row in rows)
    cache_read = sum(int(row["cache_read_tokens"] or 0) for row in rows)
    cache_write = sum(int(row["cache_write_tokens"] or 0) for row in rows)
    metered = sum(
        (Decimal(str(row["usd_cost"])) for row in rows if row["usd_cost"] is not None),
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
            "reasoning_tokens": tokens_reasoning,
            # §H2.4 asserts Backline uses no prompt caching, so H-026's `partial` caveat
            # cannot arise. That was prose; these two counts are the check.
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "prompt_caching_observed": bool(cache_read or cache_write),
            "usd_cost": str(metered),
            "unpriced_rows": sum(1 for row in rows if row["cost_status"] != "priced"),
            "by_model": _by_model(rows),
        },
    }

    if overhead:
        p50 = result["overhead"]["primary"]["p50"]
        result["overhead"]["primary"]["verdict"] = (
            "HOLDS" if p50 is not None and p50 < OVERHEAD_TARGET_P50_MS else "FALSIFIED"
        )

    if summary is not None:
        result["parity"] = _parity(summary)
        result["cross_check"] = _cross_check(summary, tokens_in, tokens_out, metered, rows)
    else:
        result["parity"] = {"note": "no Backline summary supplied — pass --summary"}
    return result


def _by_model(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Rows, tokens and spend per model.

    Plain data, and it earns its place: the H2 tenant also carries the §2c pre-flight smoke,
    which Backline never issued and therefore never metered. Splitting by model shows a
    reader where a two-meter residual comes from instead of leaving them to wonder.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = out.setdefault(
            str(row["model"]),
            {"rows": 0, "input_tokens": 0, "output_tokens": 0, "usd_cost": Decimal("0")},
        )
        bucket["rows"] += 1
        bucket["input_tokens"] += int(row["input_tokens"] or 0)
        bucket["output_tokens"] += int(row["output_tokens"] or 0)
        if row["usd_cost"] is not None:
            bucket["usd_cost"] += Decimal(str(row["usd_cost"]))
    return {
        model: {**bucket, "usd_cost": str(bucket["usd_cost"])}
        for model, bucket in sorted(out.items())
    }


def _parity(summary: dict[str, Any]) -> dict[str, Any]:
    """§H2.2's primary instrument: the Δ bound on overall against the direct-local run.

    `overall` is computed with :func:`overall_score`, not read from the summary — Backline
    never writes that key (H-072). Both the exact mean and the one-decimal figure `evals
    report` prints are reported; the pre-registered comparison is against the published
    ``93.3``, which is that same one-decimal figure for the reference run.
    """
    exact = overall_score(summary)
    overall = round(exact, 1)
    reference = REFERENCE_OVERALL[PRIMARY_REFERENCE]
    delta = round(overall - reference, 2)
    categories = summary["categories"]
    return {
        "statement": f"|overall - {reference}| <= {NOISE_BOUND} (H-064: the direct-local run)",
        "overall": overall,
        "overall_exact": round(exact, 4),
        "overall_source": (
            "n-weighted mean of the summary's per-category scores — Backline's own "
            "arithmetic (evals/report.py), which computes it at render time rather than "
            "storing it"
        ),
        "n_scored": summary.get("n_scored"),
        "n_questions": summary.get("n_questions"),
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
        },
        "categories": {
            name: {"n": bucket["n"], "score": bucket["score"], "tiers": bucket.get("tiers", {})}
            for name, bucket in sorted(categories.items())
        },
        "limitation": (
            "no same-day paired control (H-064): between-day drift sits inside this residual "
            "and cannot be separated from the gateway's effect. The claim is about a bound, "
            "not an effect size."
        ),
        "verdict": "WITHIN NOISE" if abs(delta) <= NOISE_BOUND else "OUTSIDE NOISE",
    }


def _cross_check(
    summary: dict[str, Any],
    tokens_in: int,
    tokens_out: int,
    metered: Decimal,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Two independent meters over one stream of traffic. Pre-registered as falsifiable.

    §H2.4 asks two things of this comparison and Backline can only answer one of them: its
    `summary.json` and its per-question records carry **cost, not tokens**. The token clause
    is reported as *not evaluable* rather than quietly dropped — a pre-registered check that
    turns out to have no counterpart is a defect in the pre-registration, and saying so is
    cheaper than discovering it again.
    """
    backline_cost = Decimal(str(summary.get("total_cost_usd", "0")))
    delta = (metered - backline_cost).copy_abs()
    suite_model = str(summary.get("model", ""))
    off_suite = [row for row in rows if str(row["model"]) != suite_model]
    off_suite_cost = sum(
        (Decimal(str(row["usd_cost"])) for row in off_suite if row["usd_cost"] is not None),
        Decimal("0"),
    )
    on_suite_delta = (metered - off_suite_cost - backline_cost).copy_abs()
    return {
        "statement": "token totals agree exactly; cost agrees to within $0.01 (§H2.4)",
        "headroom_usd": str(metered),
        "backline_usd": str(backline_cost),
        "delta_usd": str(delta),
        "bound_usd": "0.01",
        "headroom_tokens": {"input": tokens_in, "output": tokens_out},
        "backline_tokens": None,
        "token_clause": (
            "NOT EVALUABLE — Backline publishes total_cost_usd but no token totals, in "
            "neither summary.json nor results.jsonl, so there is nothing to compare "
            "Headroom's counts against. Reported, not dropped."
        ),
        "residual_attribution": {
            "suite_model": suite_model,
            "rows_on_other_models": len(off_suite),
            # Fixed-point, not `str`: an exactly-zero Decimal stringifies as `0E-12`, which
            # is correct and unreadable, and this field's whole job is to be read.
            "usd_on_other_models": f"{off_suite_cost:f}",
            "delta_excluding_other_models_usd": f"{on_suite_delta:f}",
            "note": (
                "Backline meters only the traffic it issued. Rows on the H2 tenant that are "
                "not on the run's own model were not part of the suite — the §2c pre-flight "
                "smoke is one — so they are shown separately rather than folded into a "
                "meter disagreement."
            ),
        },
        "note": (
            "Backline meters from the SDK's usage block; Headroom meters from the same block "
            "observed in the stream. Backline uses no prompt caching, so H-026's `partial` "
            "caveat cannot arise — see headroom_meter.prompt_caching_observed for the check."
        ),
        "verdict": "AGREE" if delta <= Decimal("0.01") else "DISAGREE",
    }


def _parity_line(parity: dict[str, Any]) -> str:
    """One line, and it never says `NO DATA` while holding a perfectly good summary."""
    if "verdict" not in parity:
        return str(parity.get("note", "no summary supplied"))
    return (
        f"{parity['verdict']} — overall {parity['overall']} vs "
        f"{parity['primary_reference']} {parity['reference_overall']}, "
        f"delta {parity['delta']:+} against a bound of {parity['bound']}"
    )


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
        f"  parity: {_parity_line(result['parity'])}"
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
