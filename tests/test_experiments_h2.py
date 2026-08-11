"""H2's analysis, against fixture rows — the run itself is the operator's, the arithmetic is not.

The $8 run happens once, on a machine with a key. What must not happen once is the code
that decides what it *meant*: every verdict `analyze.py` can reach is exercised here on
synthetic rows, including the ones that would invalidate the run, so the adjudication is
known to be capable of saying no before the money is spent.

Two of those verdicts were wrong on the real data anyway, and the reason is in this file's
history: the summary fixtures were **invented** (`{"overall": 93.3}`) rather than shaped like
Backline's, so a reader that looked for a key Backline never writes passed every test and
printed `parity: NO DATA` against the real thing (H-072). The fixtures below are now the
real shape, and two pins hold the committed artifacts to the committed inputs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from experiments.h2.adjudicate import adjudicate, gate_reasons
from experiments.h2.analyze import (
    NOISE_BOUND,
    OVERHEAD_TARGET_P50_MS,
    PRIMARY_REFERENCE,
    REFERENCE_OVERALL,
    REQUIRED_COLUMNS,
    analyse,
    overall_score,
    percentile,
    require_columns,
)
from experiments.provenance import REPO_ROOT, RESULTS_DIR, read_json

EVIDENCE = REPO_ROOT / "docs" / "evidence" / "p8-experiments"
LEDGER_ROWS = EVIDENCE / "h2-ledger-rows.json"
GATEWAY_SUMMARY = EVIDENCE / "h2-backline-summary-gateway.json"
REFERENCE_SUMMARY = EVIDENCE / "h2-backline-summary-direct-local.json"
GATE_BASELINE = EVIDENCE / "h2-backline-gate-baseline.json"
RUNBOOK = REPO_ROOT / "experiments" / "RUNBOOK.md"


def row(**overrides: Any) -> dict[str, Any]:
    """A healthy H2 ledger row: passthrough, cache off, no hop, priced."""
    base: dict[str, Any] = {
        "model": "claude-sonnet-5",
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
        "reasoning_tokens": None,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "usd_cost": "0.004000000000",
    }
    return {**base, **overrides}


def summary(**overrides: Any) -> dict[str, Any]:
    """Backline's real `summary.json` shape — categories, and **no** `overall` key."""
    base: dict[str, Any] = {
        "model": "claude-sonnet-5",
        "track": "platform",
        "subset": None,
        "suite_hash": "6eef41c6706f309a",
        "eval_run_id": "00000000-0000-0000-0000-000000000000",
        "n_questions": 100,
        "n_scored": 100,
        "t2_violations": 0,
        "errors": {"n": 0, "by_category": {}, "question_ids": []},
        "total_cost_usd": "0.004",
        "latency_ms_p50": 12000,
        "latency_ms_p95": 60000,
        "judge": {"model": "claude-sonnet-5", "rubric_sha256": "ffe8c9753172"},
        "categories": {"royalty_math": {"n": 100, "score": 93.3, "tiers": {"t1": 93.3}}},
    }
    return {**base, **overrides}


def with_overall(value: float, **overrides: Any) -> dict[str, Any]:
    """A summary whose single category makes the n-weighted overall exactly ``value``."""
    return summary(
        categories={"royalty_math": {"n": 100, "score": value, "tiers": {}}}, **overrides
    )


# --- percentiles ---------------------------------------------------------------------------


def test_the_percentile_is_nearest_rank_and_never_invents_a_value() -> None:
    values = [float(n) for n in range(1, 101)]
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.95) == 95.0
    assert percentile(values, 0.99) == 99.0
    assert percentile([], 0.5) is None
    assert percentile([2.0], 0.99) == 2.0


# --- the export contract (H-072) ------------------------------------------------------------


def test_a_missing_column_is_named_rather_than_raised_on_the_first_row() -> None:
    """The operator's first export omitted the token columns and died on `KeyError:
    input_tokens` — one column at a time, with no clue where the columns come from."""
    rows = [{key: value for key, value in row().items() if not key.endswith("_tokens")}]
    with pytest.raises(ValueError) as caught:
        analyse(rows)
    message = str(caught.value)
    for column in ("cache_read_tokens", "cache_write_tokens", "input_tokens", "output_tokens"):
        assert column in message
    assert "RUNBOOK.md §2f" in message


def test_a_missing_column_read_through_get_would_have_lied_silently() -> None:
    """`cache_disposition` was read with `.get`, so an export without it reported every row
    as *not* cache-disabled and would have invalidated a perfectly good run."""
    rows = [{key: value for key, value in row().items() if key != "cache_disposition"}]
    with pytest.raises(ValueError, match="cache_disposition"):
        analyse(rows)


def test_an_empty_export_is_a_refusal_not_a_run_of_zero_rows() -> None:
    with pytest.raises(ValueError, match="empty"):
        require_columns([])


def test_the_runbook_export_selects_every_column_the_analyzer_reads() -> None:
    """§2f's SQL and :data:`REQUIRED_COLUMNS` are one contract in two files (H-072)."""
    text = RUNBOOK.read_text(encoding="utf-8")
    match = re.search(r"SELECT\s+(request_id.*?)\s+FROM usage_ledger", text, re.DOTALL)
    assert match is not None, "RUNBOOK §2f no longer carries a SELECT ... FROM usage_ledger"
    selected = {
        column.strip().split("::")[0].strip()
        for column in match.group(1).replace("\n", " ").split(",")
    }
    assert selected >= REQUIRED_COLUMNS, (
        "RUNBOOK §2f exports fewer columns than the analyzer reads: "
        f"{sorted(REQUIRED_COLUMNS - selected)}"
    )


def test_the_committed_export_satisfies_the_contract() -> None:
    require_columns(read_json(LEDGER_ROWS))


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


# --- parity (H-064), against the shape Backline actually writes (H-072) ----------------------


def test_the_primary_comparator_is_the_direct_local_run() -> None:
    assert PRIMARY_REFERENCE == "direct_local"
    assert REFERENCE_OVERALL["direct_local"] == 93.3
    assert REFERENCE_OVERALL["aws"] == 92.5
    assert REFERENCE_OVERALL["sweep"] == 91.6
    assert NOISE_BOUND == 3.0


def test_backlines_summary_carries_no_overall_key_and_never_did() -> None:
    """The bug this file's fixtures used to hide. If Backline ever starts writing one, this
    test says so rather than letting two definitions of `overall` drift apart."""
    for path in (GATEWAY_SUMMARY, REFERENCE_SUMMARY):
        assert "overall" not in read_json(path)


def test_overall_is_the_n_weighted_mean_of_the_categories() -> None:
    """`evals/report.py`: ``sum(score * n) / sum(n)``."""
    doc = summary(
        categories={
            "a": {"n": 10, "score": 100.0, "tiers": {}},
            "b": {"n": 30, "score": 60.0, "tiers": {}},
        }
    )
    assert overall_score(doc) == pytest.approx(70.0)


def test_the_committed_summaries_reproduce_their_published_overalls() -> None:
    assert round(overall_score(read_json(GATEWAY_SUMMARY)), 1) == 93.7
    assert round(overall_score(read_json(REFERENCE_SUMMARY)), 1) == 93.3


def test_a_document_with_no_categories_is_named_not_shrugged_at() -> None:
    with pytest.raises(ValueError, match="categories"):
        overall_score({"model": "claude-sonnet-5"})


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
    result = analyse([row()], summary=with_overall(overall))
    assert result["parity"]["verdict"] == verdict
    assert result["parity"]["delta"] == pytest.approx(round(overall - 93.3, 2))


def test_the_missing_paired_control_is_stated_before_any_result_is_read() -> None:
    parity = analyse([row()], summary=with_overall(93.0))["parity"]
    assert "no same-day paired control" in parity["limitation"]
    assert "not an effect size" in parity["limitation"]


def test_the_per_category_table_travels_with_the_verdict() -> None:
    """§H2.2 pre-registers the full per-category table beside the three references."""
    parity = analyse([row()], summary=read_json(GATEWAY_SUMMARY))["parity"]
    assert parity["categories"]["contract_terms"]["score"] == 76.67
    assert set(parity["context"]) == {"direct_local", "aws", "sweep"}


# --- the two-meter cross-check (§H2.4) ------------------------------------------------------


def test_two_meters_that_agree_to_a_cent_agree() -> None:
    rows = [row(usd_cost="4.000000000000")]
    cross = analyse(rows, summary=with_overall(93.0, total_cost_usd="4.005"))["cross_check"]
    assert cross["verdict"] == "AGREE"


def test_a_disagreement_between_the_meters_is_a_finding_not_a_rounding() -> None:
    rows = [row(usd_cost="4.000000000000")]
    cross = analyse(rows, summary=with_overall(93.0, total_cost_usd="4.500"))["cross_check"]
    assert cross["verdict"] == "DISAGREE"
    assert cross["delta_usd"].startswith("0.5")


def test_the_token_half_of_the_cross_check_is_reported_as_not_evaluable() -> None:
    """Backline publishes cost and no token totals, so §H2.4's token clause has no
    counterpart. A pre-registered check with nothing to check against is a defect in the
    pre-registration, and it says so rather than disappearing."""
    cross = analyse([row()], summary=with_overall(93.0))["cross_check"]
    assert cross["backline_tokens"] is None
    assert cross["token_clause"].startswith("NOT EVALUABLE")


def test_rows_outside_the_suites_own_model_are_attributed_not_folded_in() -> None:
    """The §2c pre-flight smoke runs on the same tenant and Backline never metered it."""
    rows = [row(usd_cost="4.000000000000"), row(model="claude-haiku-4-5", usd_cost="0.000855")]
    cross = analyse(rows, summary=with_overall(93.0, total_cost_usd="4.000"))["cross_check"]
    attribution = cross["residual_attribution"]
    assert attribution["rows_on_other_models"] == 1
    assert attribution["usd_on_other_models"] == "0.000855"
    assert float(attribution["delta_excluding_other_models_usd"]) == 0.0


def test_the_no_prompt_caching_premise_is_checked_against_the_rows() -> None:
    assert analyse([row()])["headroom_meter"]["prompt_caching_observed"] is False
    assert analyse([row(cache_read_tokens=64)])["headroom_meter"]["prompt_caching_observed"] is True


# --- the committed run, pinned to its committed inputs --------------------------------------


def _as_written(doc: dict[str, Any]) -> dict[str, Any]:
    """What the file would hold: JSON round-tripped (int keys become strings), no provenance."""
    round_tripped: dict[str, Any] = json.loads(json.dumps(doc))
    return {key: value for key, value in round_tripped.items() if key != "provenance"}


def test_the_committed_analysis_is_the_one_the_committed_inputs_produce() -> None:
    """The H1 curve pin's sibling: a stale result file fails rather than being published."""
    recomputed = analyse(read_json(LEDGER_ROWS), summary=read_json(GATEWAY_SUMMARY))
    committed = read_json(RESULTS_DIR / "h2_analysis.json")
    assert _as_written(recomputed) == _as_written(committed)


def test_the_run_meets_every_pre_registered_h2_clause() -> None:
    """§H2.5's falsification list, read off the committed analysis in one place."""
    result = read_json(RESULTS_DIR / "h2_analysis.json")
    assert result["cache_disabled_proof"]["verdict"] == "HOLDS"
    assert result["overhead"]["primary"]["verdict"] == "HOLDS"
    assert result["errors"]["outcomes"] == {"ok": 462}
    assert result["errors"]["failover_hops"] == {"0": 462}
    assert result["headroom_meter"]["unpriced_rows"] == 0
    assert result["parity"]["verdict"] == "WITHIN NOISE"
    assert result["cross_check"]["verdict"] == "AGREE"


def test_the_two_meters_agree_exactly_once_the_non_suite_smoke_is_set_aside() -> None:
    """Stronger than the pre-registered $0.01, and the residual is one identified request."""
    cross = read_json(RESULTS_DIR / "h2_analysis.json")["cross_check"]
    assert cross["residual_attribution"]["rows_on_other_models"] == 1
    assert float(cross["residual_attribution"]["delta_excluding_other_models_usd"]) == 0.0


def test_the_residual_row_is_the_pre_flight_smoke_by_request_id() -> None:
    """REPORT.md names the $0.000855 residual rather than describing it, so the naming is
    checked: the §2c smoke's own `request_id` is the one row that is not the suite's."""
    smoke = next(
        check
        for check in read_json(RESULTS_DIR / "h2_preflight.json")["checks"]
        if check["check"].startswith("tool round-trip")
    )
    request_id = smoke["detail"].split("request_id=")[1].split(",")[0]

    rows = read_json(LEDGER_ROWS)
    off_suite = [row for row in rows if row["model"] != "claude-sonnet-5"]
    assert [row["request_id"] for row in off_suite] == [request_id]
    assert off_suite[0]["usd_cost"] == "0.000855000000"
    assert off_suite[0]["started_at"] < min(
        row["started_at"] for row in rows if row["model"] == "claude-sonnet-5"
    )


# --- the gate adjudication ------------------------------------------------------------------


def test_the_gate_rules_reproduce_the_operators_verbatim_output() -> None:
    """What the operator pasted back, recomputed from committed evidence."""
    baseline = next(
        entry["categories"]
        for entry in read_json(GATE_BASELINE)["baselines"]
        if (entry["model"], entry["track"], entry.get("subset"))
        == ("claude-sonnet-5", "platform", "full")
    )
    assert gate_reasons(read_json(GATEWAY_SUMMARY), baseline) == [
        "contract_terms: 76.7 vs baseline 85.0 (-8.3 pts > 3)",
        "3 T2 violation(s) — process assertions failed",
    ]


def test_the_reference_run_fails_the_same_gate_on_disjoint_categories() -> None:
    """The whole adjudication turns on this: a transport defect degrades systematically,
    and what the data shows is two runs failing in different places (Backline §A5.5)."""
    result = adjudicate(
        treatment=read_json(GATEWAY_SUMMARY),
        reference=read_json(REFERENCE_SUMMARY),
        baseline_doc=read_json(GATE_BASELINE),
    )
    assert result["gate"]["treatment_fails"] and result["gate"]["reference_fails"]
    assert result["gate"]["reasons_disjoint"]
    assert result["verdict"]["reading"].startswith("variance")


def test_the_gateway_scored_higher_on_two_categories_than_the_reference() -> None:
    """Scatter in both directions, stated as a fact rather than as a defence."""
    result = adjudicate(
        treatment=read_json(GATEWAY_SUMMARY),
        reference=read_json(REFERENCE_SUMMARY),
        baseline_doc=read_json(GATE_BASELINE),
    )
    improved = {
        name: bucket["treatment_minus_reference"]
        for name, bucket in result["categories"].items()
        if bucket["treatment_minus_reference"] > 0
    }
    assert improved == {"abstention": 10.0, "multi_step": 6.11}
    assert result["categories"]["contract_terms"]["treatment_minus_reference"] == -6.0


def test_the_committed_adjudication_is_the_one_the_committed_summaries_produce() -> None:
    """The per-question half needs Backline's `results.jsonl`, which lives in Backline; the
    category and gate halves replay from this repo alone, and they are what the verdict
    rests on."""
    recomputed = adjudicate(
        treatment=read_json(GATEWAY_SUMMARY),
        reference=read_json(REFERENCE_SUMMARY),
        baseline_doc=read_json(GATE_BASELINE),
    )
    committed = read_json(RESULTS_DIR / "h2_gate_adjudication.json")
    for section in ("runs", "comparability", "gate", "categories"):
        assert recomputed[section] == committed[section], section


def test_the_gateway_only_violation_is_named_and_carries_its_own_evidence() -> None:
    """§A5.5 allows a checker false positive to be adjudicated as one; it does not allow it
    to be adjudicated silently."""
    committed = read_json(RESULTS_DIR / "h2_gate_adjudication.json")
    only = committed["per_question"]["t2_violations"]["treatment_only"]
    assert [item["question_id"] for item in only] == ["contract_terms-016"]
    evidence = committed["per_question"]["t2_violations"]["treatment_only_evidence"][0]
    assert evidence["t1"] == {"reference": 1.0, "treatment": 1.0}
    detail = evidence["recorded_check_detail"]["cites_clause"]
    assert detail["reference"]["citations"] == ["FBR-C-00777 §3"]
    assert detail["treatment"]["citations"] == []


def test_the_deterministic_tier_agrees_on_every_question_but_one() -> None:
    """T1 has no judge and no agent trace in it: if the transport altered what the model
    saw or said, this is where it would show."""
    per_question = read_json(RESULTS_DIR / "h2_gate_adjudication.json")["per_question"]
    assert per_question["questions_compared"] == 133
    assert per_question["t1_agreement"]["questions_with_identical_t1"] == 132
    moved = per_question["t1_agreement"]["questions_moved"]
    assert moved == [{"question_id": "hand-abstention-02", "reference": 0.0, "treatment": 1.0}]


def test_the_backline_evidence_committed_here_is_the_run_h2_reports() -> None:
    """Invariant 9: the parity claim must not depend on a directory in another repo that a
    `make test` there can truncate."""
    gateway = read_json(GATEWAY_SUMMARY)
    assert gateway["eval_run_id"] == "21369386-a040-4589-90a1-0e75409711ec"
    assert gateway["total_cost_usd"] == "7.540398"
    assert json.loads(LEDGER_ROWS.read_text(encoding="utf-8")) is not None


def test_the_two_runs_are_comparable_on_everything_but_the_hop() -> None:
    committed = read_json(RESULTS_DIR / "h2_gate_adjudication.json")["comparability"]
    assert committed["suite_hash_matches"]
    assert committed["judge_matches"]
    assert committed["model_matches"]


def test_the_committed_baseline_is_a_composite_and_says_so() -> None:
    """It is a best-of envelope assembled from three runs — which is why every fresh full
    run in this repo's history has failed the gate against it."""
    entry = next(
        item
        for item in read_json(GATE_BASELINE)["baselines"]
        if (item["model"], item["track"]) == ("claude-sonnet-5", "platform")
    )
    assert "composite" in entry["note"]
    assert entry["suite_hash"] == "6eef41c6706f309a"


def test_the_runbook_no_longer_ships_the_export_that_broke() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "input_tokens, output_tokens, reasoning_tokens" in text
    assert "cache_read_tokens, cache_write_tokens" in text


def test_every_results_artifact_this_module_writes_is_committed() -> None:
    for name in (
        "h2_analysis.json",
        "h2_bench.json",
        "h2_preflight.json",
        "h2_gate_adjudication.json",
    ):
        assert (RESULTS_DIR / name).is_file(), name


def test_the_evidence_directory_carries_both_summaries_and_the_baseline() -> None:
    for path in (LEDGER_ROWS, GATEWAY_SUMMARY, REFERENCE_SUMMARY, GATE_BASELINE):
        assert Path(path).is_file(), path
