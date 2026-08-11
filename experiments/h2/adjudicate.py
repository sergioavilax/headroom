"""The Backline regression gate's FAIL, adjudicated against the direct-local reference.

§H2.2 pre-registers two instruments and they are not the same instrument:

* the **primary** is the Δ bound on the overall score against the direct-local run — that
  is what H2's parity claim is, and `analyze.py` reports it;
* `python -m evals gate` is the **secondary**, run "on the record with its known failure
  modes", including §H2.2's own advance notice that *"a legitimate fresh run can fail it on
  variance alone (T2 flicker, small-n category swing)"*.

The gate failed on the H2 run. This module decides, from committed evidence, whether that
failure is the pre-declared variance or something the gateway did — by asking the only
question that separates them: **does the reference run fail too, and in the same place?**
Backline's own §A5.5 fixed the reading before either run existed:

    A broken environment degrades systematically — same categories, same direction,
    traceable mechanism.

So the comparison is per category and per question, in both directions, and a movement that
favours the gateway counts exactly as much as one that does not::

    uv run python -m experiments.h2.adjudicate \\
        --treatment docs/evidence/p8-experiments/h2-backline-summary-gateway.json \\
        --reference docs/evidence/p8-experiments/h2-backline-summary-direct-local.json \\
        --baseline  docs/evidence/p8-experiments/h2-backline-gate-baseline.json

The two summaries and the baseline are committed here rather than read from Backline's
`data/evals/` because invariant 9 says evidence lives in the repo, outside every blast
radius — and that directory is one `make test` away from being a former blast radius.

**Nothing here re-runs, re-rolls or re-scores anything** (§H2.5). The per-question detail
comes from Backline's own `results.jsonl`, which lives in Backline; when it is supplied the
findings are folded in and the artifact records where they came from.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Final

from experiments.provenance import RESULTS_DIR, provenance, read_json, write_json

__all__ = ["DROP_THRESHOLD", "adjudicate", "gate_reasons", "main"]

#: `evals/gate.py`'s `DROP_THRESHOLD`, quoted rather than imported: Backline is not a
#: dependency of this repo and a keyless replay must not need it on the path.
DROP_THRESHOLD: Final = 3.0

RESULT_PATH: Final = RESULTS_DIR / "h2_gate_adjudication.json"


def _overall(summary: dict[str, Any]) -> float:
    from experiments.h2.analyze import overall_score

    return overall_score(summary)


def gate_reasons(summary: dict[str, Any], baseline: dict[str, float]) -> list[str]:
    """Backline's gate rules 4 and 5, applied to a committed summary.

    Only the two rules that fire on these runs are restated — a category dropping more than
    :data:`DROP_THRESHOLD` below the baseline, and any T2 violation at all. The strings are
    shaped like `evals/gate.py`'s so a reader can line them up against the verbatim output
    the operator captured.
    """
    reasons: list[str] = []
    for category, baseline_score in sorted(baseline.items()):
        bucket = summary["categories"].get(category)
        if bucket is None:
            reasons.append(f"category {category!r} missing from results")
            continue
        drop = baseline_score - bucket["score"]
        if drop > DROP_THRESHOLD:
            reasons.append(
                f"{category}: {bucket['score']:.1f} vs baseline {baseline_score:.1f} "
                f"(-{drop:.1f} pts > {DROP_THRESHOLD:g})"
            )
    violations = int(summary.get("t2_violations", 0))
    if violations > 0:
        reasons.append(f"{violations} T2 violation(s) — process assertions failed")
    return reasons


def _baseline_entry(doc: dict[str, Any], summary: dict[str, Any]) -> dict[str, float]:
    wanted = (summary["model"], summary["track"], summary.get("subset") or "full")
    for entry in doc.get("baselines", []):
        key = (entry["model"], entry["track"], entry.get("subset") or "full")
        if key == wanted:
            categories: dict[str, float] = entry["categories"]
            return categories
    raise ValueError(f"no committed baseline entry for {wanted}")


def _load_results(given: str | None) -> dict[str, dict[str, Any]] | None:
    """`~` is expanded here so provenance can record the path the operator typed."""
    if given is None:
        return None
    text = Path(given).expanduser().read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return {row["question_id"]: row for row in rows}


def _tier_score(row: dict[str, Any], tier: str) -> float | None:
    bucket = row["tiers"].get(tier)
    return None if bucket is None else float(bucket["score"])


def _t2_failures(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for qid, row in sorted(rows.items()):
        t2 = row["tiers"].get("t2")
        if t2 is None or t2.get("passed", True):
            continue
        failed = sorted(
            name
            for name, detail in t2.items()
            if isinstance(detail, dict) and detail.get("passed") is False
        )
        out.append({"question_id": qid, "category": row["category"], "failed_checks": failed})
    return out


def _anomaly_evidence(
    question_id: str,
    checks: list[str],
    treatment: dict[str, dict[str, Any]],
    reference: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Everything a reader needs to disagree with the adjudication of one violation.

    Backline's own recorded check detail for both runs, side by side, plus the deterministic
    tier. Nothing is re-derived here — a claim about *why* a check failed belongs in
    REPORT.md where it can be argued with, not in an artifact where it would look measured.
    """
    treat, ref = treatment[question_id], reference[question_id]
    return {
        "question_id": question_id,
        "category": treat["category"],
        "failed_checks": checks,
        "t1": {"reference": _tier_score(ref, "t1"), "treatment": _tier_score(treat, "t1")},
        "t3": {"reference": _tier_score(ref, "t3"), "treatment": _tier_score(treat, "t3")},
        "question_score": {"reference": ref["score"], "treatment": treat["score"]},
        "score_composition": (
            "a question scores min(tier scores) (evals/runner.py), so a failed T2 zeroes it "
            "whatever T1 and T3 say"
        ),
        "recorded_check_detail": {
            name: {
                "reference": (ref["tiers"].get("t2") or {}).get(name),
                "treatment": (treat["tiers"].get("t2") or {}).get(name),
            }
            for name in checks
        },
        "answer_chars": {
            "reference": len(ref["answer_text"]),
            "treatment": len(treat["answer_text"]),
        },
    }


def _per_question(
    treatment: dict[str, dict[str, Any]], reference: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """The half that decides it: same questions, same scorer, two transports."""
    shared = sorted(set(treatment) & set(reference))
    up = [q for q in shared if treatment[q]["score"] > reference[q]["score"]]
    down = [q for q in shared if treatment[q]["score"] < reference[q]["score"]]
    t1_moved = [
        {
            "question_id": q,
            "reference": _tier_score(reference[q], "t1"),
            "treatment": _tier_score(treatment[q], "t1"),
        }
        for q in shared
        if _tier_score(treatment[q], "t1") != _tier_score(reference[q], "t1")
    ]

    ct = [q for q in shared if treatment[q]["category"] == "contract_terms"]
    ct_deltas = {q: round(treatment[q]["score"] - reference[q]["score"], 4) for q in ct}
    worst = min(ct_deltas, key=lambda q: ct_deltas[q]) if ct_deltas else None
    total = sum(ct_deltas.values())

    ref_fails = _t2_failures(reference)
    treat_fails = _t2_failures(treatment)
    shared_modes = sorted(
        {tuple(f["failed_checks"]) for f in ref_fails}
        & {tuple(f["failed_checks"]) for f in treat_fails}
    )
    treatment_only = [f for f in treat_fails if tuple(f["failed_checks"]) not in set(shared_modes)]
    return {
        "questions_compared": len(shared),
        "movement": {
            "treatment_higher": len(up),
            "treatment_lower": len(down),
            "identical": len(shared) - len(up) - len(down),
            "reading": (
                "scatter in both directions is the signature of variance; a transport defect "
                "degrades one way (Backline §A5.5)"
            ),
        },
        "t1_agreement": {
            "statement": "T1 is the deterministic answer-key tier — no judge, no agent trace",
            "questions_with_identical_t1": len(shared) - len(t1_moved),
            "questions_moved": t1_moved,
        },
        "contract_terms": {
            "n": len(ct),
            "sum_of_per_question_deltas": round(total, 4),
            "category_points": round(100 * total / len(ct), 2) if ct else None,
            "largest_single_drop": (
                {
                    "question_id": worst,
                    "delta": ct_deltas[worst],
                    "category_points": round(100 * ct_deltas[worst] / len(ct), 2),
                    "share_of_drop": (round(100 * ct_deltas[worst] / total, 1) if total else None),
                }
                if worst is not None
                else None
            ),
            "moved_up": sum(1 for value in ct_deltas.values() if value > 0),
            "moved_down": sum(1 for value in ct_deltas.values() if value < 0),
            "unchanged": sum(1 for value in ct_deltas.values() if value == 0),
        },
        "t2_violations": {
            "reference": ref_fails,
            "treatment": treat_fails,
            "failure_modes_present_in_both": [list(mode) for mode in shared_modes],
            "treatment_only": treatment_only,
            "treatment_only_evidence": [
                _anomaly_evidence(f["question_id"], f["failed_checks"], treatment, reference)
                for f in treatment_only
            ],
        },
    }


def adjudicate(
    *,
    treatment: dict[str, Any],
    reference: dict[str, Any],
    baseline_doc: dict[str, Any],
    treatment_results: dict[str, dict[str, Any]] | None = None,
    reference_results: dict[str, dict[str, Any]] | None = None,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    baseline = _baseline_entry(baseline_doc, treatment)
    treat_reasons = gate_reasons(treatment, baseline)
    ref_reasons = gate_reasons(reference, baseline)

    categories = {
        name: {
            "n": treatment["categories"][name]["n"],
            "baseline": baseline.get(name),
            "reference": reference["categories"][name]["score"],
            "treatment": treatment["categories"][name]["score"],
            "treatment_minus_reference": round(
                treatment["categories"][name]["score"] - reference["categories"][name]["score"], 2
            ),
            "reference_minus_baseline": (
                round(reference["categories"][name]["score"] - baseline[name], 2)
                if name in baseline
                else None
            ),
        }
        for name in sorted(treatment["categories"])
    }

    result: dict[str, Any] = {
        "schema": "h2_gate_adjudication/1",
        "provenance": provenance(
            produced_by="experiments/h2/adjudicate.py",
            inputs=dict(sources or {}),
            notes=(
                "Free and deterministic: arithmetic over committed summaries. No run, no "
                "re-score, no heal pass (§H2.5)."
            ),
        ),
        "what_this_adjudicates": (
            "§H2.2's SECONDARY check. The pre-registered parity instrument is the Δ bound on "
            "the overall score against the direct-local run and it is reported by "
            "analyze.py; this file decides only what the gate's FAIL means."
        ),
        "runs": {
            name: {
                "eval_run_id": summary["eval_run_id"],
                "git_sha": summary.get("git_sha"),
                "suite_hash": summary["suite_hash"],
                "judge": summary.get("judge"),
                "overall": round(_overall(summary), 1),
                "overall_exact": round(_overall(summary), 4),
                "n_scored": summary["n_scored"],
                "t2_violations": summary["t2_violations"],
                "infra_errors": int((summary.get("errors") or {}).get("n", 0)),
                "total_cost_usd": summary["total_cost_usd"],
                "latency_ms_p50": summary["latency_ms_p50"],
                "latency_ms_p95": summary["latency_ms_p95"],
            }
            for name, summary in (("treatment", treatment), ("reference", reference))
        },
        "comparability": {
            "suite_hash_matches": treatment["suite_hash"] == reference["suite_hash"],
            "judge_matches": treatment.get("judge") == reference.get("judge"),
            "model_matches": treatment["model"] == reference["model"],
            "note": (
                "same question set, same judge rubric, same model — the two runs differ by "
                "the gateway hop and by the day they were drawn (§H2.2's stated limitation)"
            ),
        },
        "gate": {
            "rules_applied": (
                "evals/gate.py rule 4 (category drop > 3.0 vs the committed baseline) and "
                "rule 5 (any T2 violation). Quoted, not imported: Backline is not a "
                "dependency of this repo."
            ),
            "treatment_fails": bool(treat_reasons),
            "treatment_reasons": treat_reasons,
            "reference_fails": bool(ref_reasons),
            "reference_reasons": ref_reasons,
            "both_fail": bool(treat_reasons) and bool(ref_reasons),
            "reasons_disjoint": not (set(treat_reasons) & set(ref_reasons)),
            "baseline_note": (
                "the committed baseline is a post-diagnosis COMPOSITE — its own `note` says "
                "it is assembled per category from three different runs — so it is a "
                "best-of envelope no single run has reproduced"
            ),
        },
        "categories": categories,
    }

    if treatment_results is not None and reference_results is not None:
        result["per_question"] = _per_question(treatment_results, reference_results)

    result["verdict"] = _verdict(result)
    return result


def _verdict(result: dict[str, Any]) -> dict[str, Any]:
    gate = result["gate"]
    per_question = result.get("per_question")
    both_fail = gate["both_fail"]
    disjoint = gate["reasons_disjoint"]
    moved_up = moved_down = None
    treatment_only: list[dict[str, Any]] = []
    if per_question:
        moved_up = per_question["movement"]["treatment_higher"]
        moved_down = per_question["movement"]["treatment_lower"]
        treatment_only = per_question["t2_violations"]["treatment_only"]
    return {
        "both_runs_fail_the_same_gate": both_fail,
        "failures_are_disjoint": disjoint,
        "movement_in_both_directions": (
            None if moved_up is None else bool(moved_up and moved_down)
        ),
        "gateway_only_t2_violations": treatment_only,
        "reading": (
            "variance per Backline §A5.5's pre-declared failure modes"
            if both_fail and disjoint
            else "NOT the pre-declared pattern — read the reasons and say so"
        ),
        "unexplained_by_variance": (
            "none" if not treatment_only else "see gateway_only_t2_violations; named, not folded in"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h2.adjudicate",
        description="Adjudicate the Backline gate's FAIL against the direct-local reference. Free.",
    )
    parser.add_argument("--treatment", required=True, help="the gateway run's summary.json")
    parser.add_argument("--reference", required=True, help="the direct-local run's summary.json")
    parser.add_argument("--baseline", required=True, help="Backline's committed baseline.json")
    parser.add_argument("--treatment-results", default=None, help="the gateway run's results.jsonl")
    parser.add_argument(
        "--reference-results", default=None, help="the reference run's results.jsonl"
    )
    parser.add_argument("--out", default=str(RESULT_PATH))
    args = parser.parse_args(argv)

    sources = {
        "treatment_summary": args.treatment,
        "reference_summary": args.reference,
        "gate_baseline": args.baseline,
    }
    if args.treatment_results:
        sources["treatment_results"] = args.treatment_results
    if args.reference_results:
        sources["reference_results"] = args.reference_results

    result = adjudicate(
        treatment=read_json(Path(args.treatment).expanduser()),
        reference=read_json(Path(args.reference).expanduser()),
        baseline_doc=read_json(Path(args.baseline).expanduser()),
        treatment_results=_load_results(args.treatment_results),
        reference_results=_load_results(args.reference_results),
        sources=sources,
    )
    write_json(Path(args.out), result)

    gate = result["gate"]
    print(f"treatment gate: {'FAIL' if gate['treatment_fails'] else 'PASS'}")
    for reason in gate["treatment_reasons"]:
        print(f"  ✗ {reason}")
    print(f"reference gate: {'FAIL' if gate['reference_fails'] else 'PASS'}")
    for reason in gate["reference_reasons"]:
        print(f"  ✗ {reason}")
    print(f"\nboth fail: {gate['both_fail']} · disjoint reasons: {gate['reasons_disjoint']}")
    print(f"reading: {result['verdict']['reading']}")
    if result["verdict"]["gateway_only_t2_violations"]:
        print("gateway-only T2 violations (named, not folded in):")
        for item in result["verdict"]["gateway_only_t2_violations"]:
            print(f"  · {item['question_id']} ({item['category']}) {item['failed_checks']}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
