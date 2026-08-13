"""The README's claims, recomputed from the artifacts they came from.

BUILD_PLAN §P11: *"a README whose claims are **pinned by tests** recomputing every number
from committed artifacts"*. This is that file, and the failure it exists to catch is the
one every portfolio repo has: **a front door that was true once.** A number typed into a
README is a copy, and a copy drifts silently — the experiment is re-run, the curve moves,
the sweep is re-swept, and the headline keeps quoting a figure nothing in the tree
produces any more. Nothing goes red, because nothing was ever checked.

So every figure in `README.md` is either

* **recomputed** here from the JSON that produced it — the H1 curve out of
  `h1_curve.json`, the parity verdict and the two-meter residual out of
  `h2_analysis.json`, each overhead figure out of the one ledger row it was measured on,
  the zero-drop arc out of the three load-loop captures, the mock unit cost out of the
  shipped `config/models.yaml` through the gateway's own pricing code; or
* **held to a constant in the code** it describes (the cache's default threshold, the auth
  cache's TTL, the backoff's published worst case); or
* **held to another committed document** where the primary source is a screenshot or a
  terminal that no longer exists — `docs/PHASE_LOG.md` and the evidence READMEs. That is a
  weaker pin and it is used only where nothing stronger exists, which is stated at each
  site rather than hidden.

Two structural checks sit beside the numbers, and they are the ones most likely to fire
in ordinary work: **every relative path the README links to exists**, and **every `H-NNN`
it cites is a real heading in `docs/DECISIONS.md`**. A dead link to an artifact and a
missing artifact are the same failure — a claim with nothing behind it.

**The one number that cannot come from a file is the test count**, so it comes from the
session's own collection. That makes the check honest and makes it skip loudly (H-012's
rule) when the suite is run in part, because `pytest tests/test_docs.py` legitimately
collects one file and the README is not about one file.

**Parsing is crude on purpose**, in `tests/test_deploy_aws.py`'s house style: substring
assertions over the rendered markdown, because the thing being asserted is that two
strings are the same string. A pretty parser would let a claim survive being re-worded
into something the parser no longer recognises, which is the failure mode, not a nicety.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from headroom.core.cache import DEFAULT_SIMILARITY_THRESHOLD
from headroom.metering.cost import usd_for_tokens
from headroom.metering.prices import load_price_book
from headroom.policy.auth import AUTH_CACHE_TTL_S
from headroom.policy.failover import BackoffPolicy

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text(encoding="utf-8")
PHASE_LOG = (REPO / "docs" / "PHASE_LOG.md").read_text(encoding="utf-8")
DECISIONS = (REPO / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
REPORT = (REPO / "experiments" / "results" / "REPORT.md").read_text(encoding="utf-8")
P10_EVIDENCE = (REPO / "docs" / "evidence" / "p10-eks" / "README.md").read_text(encoding="utf-8")


def artifact(relative: str) -> Any:
    """One committed JSON artifact, by its path from the repo root."""
    return json.loads((REPO / relative).read_text(encoding="utf-8"))


H1 = artifact("experiments/results/h1_curve.json")
H2 = artifact("experiments/results/h2_analysis.json")
H2_BENCH = artifact("experiments/results/h2_bench.json")
H3_CHAOS = artifact("experiments/results/h3_chaos.json")
H3_KILL = artifact("experiments/results/h3_livekill.json")
BACKLINE_GATEWAY = artifact("docs/evidence/p8-experiments/h2-backline-summary-gateway.json")
AWS_ROW = artifact("docs/evidence/p9-aws/06-live-ledger-row.json")
EKS_ROW = artifact("docs/evidence/p10-eks/day1-live-ledger-row.json")
ROLLOUT_1 = artifact("docs/evidence/p10-eks/09-load-loop-run1-1drop.json")
ROLLOUT_2 = artifact("docs/evidence/p10-eks/09b-load-loop-run2-sleep15-2drops.json")
ROLLOUT_3 = artifact("docs/evidence/p10-eks/09c-load-loop-run3-drain.json")
KILL_LOOP = artifact("docs/evidence/p10-eks/15-failover-loop.json")
KILL_LEDGER = artifact("docs/evidence/p10-eks/16-failover-ledger.json")
CORPUS = artifact("tests/fixtures/semantic_corpus.json")

#: The capture that closes A7. While it is absent the README's cloud-cost table is
#: required to say `pending`; the day it lands, this test tightens (see the test below).
BILLING_CAPTURE = REPO / "docs" / "evidence" / "p10-eks" / "23-billing.png"


# --- helpers -----------------------------------------------------------------------------


def says(*fragments: str) -> None:
    """Every fragment appears in the README, verbatim."""
    missing = [fragment for fragment in fragments if fragment not in README]
    assert not missing, (
        "the README no longer carries these, and the artifact says they are the numbers:\n  "
        + "\n  ".join(repr(fragment) for fragment in missing)
    )


def grid_point(curve: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    """The sweep's row at one threshold. The grid is 0.700 → 0.990 in steps of 0.005."""
    for point in curve:
        if abs(float(point["threshold"]) - threshold) < 1e-9:
            return point
    raise AssertionError(f"no grid point at {threshold} — the sweep's grid has changed")


def prompt_space() -> dict[str, Any]:
    """The primary embedding space: the prompt as `Dialect.cache_probe` sends it (H-060)."""
    space: dict[str, Any] = H1["spaces"]["prompt"]
    return space


def money(value: str | Decimal) -> Decimal:
    return Decimal(str(value))


# --- H1: the headline ---------------------------------------------------------------------


def test_the_two_numbers_the_whole_h1_finding_rests_on() -> None:
    """The overlap. Everything else about H1 is downstream of these two figures."""
    combined = prompt_space()["combined"]["summary"]
    worst_wrong = combined["max_swa_similarity"]
    best_correct = combined["min_correct_similarity"]

    assert worst_wrong > best_correct, (
        "the bands no longer overlap, which would mean a threshold exists after all — "
        "the README's central claim needs rewriting, not this test"
    )
    says(f"{worst_wrong:.6f}", f"{best_correct:.6f}")
    says("0.999539", "0.889850")


def test_tau_zero_does_not_exist_across_the_pre_registered_grid() -> None:
    """τ₀ is a rule fixed before the curve (H-063), and it selects nothing."""
    assert prompt_space()["combined"]["tau_zero"] is None
    assert H1["spaces"]["body"]["combined"]["tau_zero"] is None
    grid = H1["pre_registration"]["grid"]
    assert (grid["start"], grid["stop"]) == (0.70, 0.99)
    assert all(point["silent_wrong_answer"] > 0 for point in prompt_space()["combined"]["curve"])
    says("**τ₀ — the recommended safe", "does not exist**", "0.70 → 0.99", "`τ₀ = None`")


def test_the_headline_counts_are_the_two_families_at_the_shipped_default() -> None:
    """98 of 130 novel questions answered, 92 wrong; 389 of 390 paraphrases, 382 right."""
    novel = grid_point(prompt_space()["families"]["novel_question"]["curve"], 0.90)
    paraphrase = grid_point(prompt_space()["families"]["paraphrase"]["curve"], 0.90)

    assert (novel["probes"], novel["hits"], novel["silent_wrong_answer"]) == (130, 98, 92)
    assert (paraphrase["probes"], paraphrase["hits"], paraphrase["correct"]) == (390, 389, 382)
    says(
        "**98 of 130 never-before-seen questions",
        "92 of those answers are provably wrong",
        "**389 of 390 genuine paraphrases**",
        "**382 of them correctly**",
    )


def test_the_threshold_table_is_the_committed_curve_row_for_row() -> None:
    """Every cell of the README's both-families table, recomputed from `h1_curve.json`."""
    curve = prompt_space()["combined"]["curve"]
    for threshold in (0.70, 0.85, 0.90, 0.95, 0.99):
        point = grid_point(curve, threshold)
        row = (
            f"| {point['probes']} | {point['hits']} | {point['correct']} | "
            f"**{point['silent_wrong_answer']}** | "
            f"${money(point['usd_saved']).quantize(Decimal('0.01'))} |"
        )
        assert row in README, (
            f"the README's row for threshold {threshold} is not the one the curve produces.\n"
            f"expected to find: {row}"
        )
    # "one hit in five", at the shipped default. Rounded down, deliberately: the figure is
    # 20.3% of hits and the sentence must not overstate it.
    shipped = grid_point(curve, 0.90)
    assert shipped["swa_rate_of_hits"] >= 0.20
    says("**one hit in five is a wrong answer served with confidence**")


def test_the_corpus_the_curve_was_swept_over() -> None:
    corpus = H1["corpus"]
    assert (corpus["questions"], corpus["probes"]) == (130, 390)
    assert len(corpus["excluded"]) == 3
    assert prompt_space()["combined"]["summary"]["probes"] == 520
    says("130 questions carrying an exact answer key", "520 probes", "390", "130")
    says("Three of Backline's 133 questions are excluded")


def test_the_mechanism_pair_is_the_one_the_report_names() -> None:
    """The 0.999539 pair, whose text lives in REPORT.md rather than in a JSON field."""
    for fragment in ("2026-02", "2026-04", "5 findings", "2 findings"):
        assert fragment in README
        assert fragment in REPORT, (
            f"{fragment!r} is in the README but no longer in REPORT.md, which is where the "
            "worked pair is adjudicated"
        )


# --- H2: parity, overhead, and the two meters ---------------------------------------------


def test_the_parity_verdict_is_the_committed_adjudication() -> None:
    parity = H2["parity"]
    assert parity["verdict"] == "WITHIN NOISE"
    assert (parity["overall"], parity["reference_overall"]) == (93.7, 93.3)
    assert (parity["delta"], parity["bound"]) == (0.4, 3.0)
    assert parity["context"]["aws"]["overall"] == 92.5
    assert parity["context"]["sweep"]["overall"] == 91.6
    says(
        "**93.7 vs 93.3 direct**",
        "Δ **+0.4** against a pre-registered bound of 3.0 — **WITHIN NOISE**",
        "| **93.7** | — |",
        "| 93.3 | **+0.4** |",
        "| 92.5 | +1.2 |",
        "| 91.6 | +2.1 |",
        "no same-day paired control",
    )


def test_the_passthrough_overhead_is_the_suites_own_column() -> None:
    overhead = H2["overhead"]["primary"]
    assert overhead["n"] == H2["rows"] == 462
    assert overhead["verdict"] == "HOLDS"
    says(
        f"p50 {overhead['p50']:.4f} ms",
        f"p95 {overhead['p95']:.4f}",
        f"p99 {overhead['p99']:.4f}",
        "462 live suite requests",
        f"`< {overhead['target_p50_ms']:.0f} ms`",
    )


def test_each_live_overhead_number_cites_the_row_it_was_measured_on() -> None:
    """One request on ECS, one on EKS. The figure and the file it came from, together."""
    aws = AWS_ROW["passthrough_overhead_ms"]
    eks = EKS_ROW["passthrough_overhead_ms"]
    says(
        f"**{aws:.4f} ms**",
        "docs/evidence/p9-aws/06-live-ledger-row.json",
        f"**{eks:.4f} ms**",
        "docs/evidence/p10-eks/day1-live-ledger-row.json",
    )
    # The two rows are the same request shape on two runtimes — which is the claim the
    # README makes about them, so it is asserted rather than assumed.
    for row in (AWS_ROW, EKS_ROW):
        assert row["model"] == "claude-haiku-4-5"
        assert row["failover_hops"] == 0
        assert row["cache_disposition"] == "cache_disabled"
        assert row["outcome"] == "ok"


def test_the_live_rows_price_their_own_arithmetic() -> None:
    """15 in at $1.00/MTok + 7 out at $5.00/MTok = $0.000050, on both runtimes."""
    for row in (AWS_ROW, EKS_ROW):
        expected = usd_for_tokens(
            row["input_tokens"], money(row["usd_per_mtok_in"])
        ) + usd_for_tokens(row["output_tokens"], money(row["usd_per_mtok_out"]))
        assert money(row["usd_cost"]) == expected
        assert (row["input_tokens"], row["output_tokens"]) == (15, 7)
    says("15 in / 7 out at $1.00/$5.00 per MTok = $0.000050")


def test_the_admission_cost_and_its_share_of_a_request() -> None:
    in_memory, dynamo = H2_BENCH["configurations"]
    writes = H2_BENCH["conditional_write_cost_ms"]
    latency_p50 = BACKLINE_GATEWAY["latency_ms_p50"]
    share = 100.0 * dynamo["admission_ms"]["p50"] / latency_p50

    assert in_memory["requests"] == dynamo["requests"] == 2000
    says(
        f"**p50 {in_memory['admission_ms']['p50']:.3f} ms**",
        f"**{dynamo['admission_ms']['p50']:.3f} ms**",
        f"**{writes['p50']:.3f} ms**",
        "2,000 keyless requests",
        f"**{latency_p50:,} ms** p50 per question",
        f"**{share:.3f}% of a request**",
    )


def test_the_two_meters_agree_and_the_residual_is_one_named_request() -> None:
    cross = H2["cross_check"]
    assert cross["verdict"] == "AGREE"
    assert money(cross["delta_usd"]) == money(cross["headroom_usd"]) - money(cross["backline_usd"])
    assert money(cross["delta_usd"]) < money(cross["bound_usd"])
    assert money(cross["residual_attribution"]["delta_excluding_other_models_usd"]) == 0
    assert cross["residual_attribution"]["rows_on_other_models"] == 1
    says(
        "$7.541253",
        "$7.540398",
        "$0.000855",
        "hr_e171f6024fc64772a66840fda6aab05a",
        "twelve decimal places on 461 requests",
    )


def test_the_cache_was_provably_off_for_the_whole_run() -> None:
    """H-047's clause: one hit would have made the overhead figure a hit-rate figure."""
    proof = H2["cache_disabled_proof"]
    assert proof["verdict"] == "HOLDS"
    assert proof["rows_not_cache_disabled"] == 0
    assert proof["dispositions"] == {"cache_disabled": 462}


# --- the cluster: dropped requests, and the kill demos -------------------------------------


def test_the_zero_drop_arc_is_three_committed_runs_in_that_order() -> None:
    """A set containing only the third would be a set nobody could check."""
    rows = [
        (ROLLOUT_1, 8331, 1),
        (ROLLOUT_2, 8326, 2),
        (ROLLOUT_3, 8342, 0),
    ]
    for run, requests, dropped in rows:
        assert (run["requests"], run["dropped"]) == (requests, dropped)
        says(f"| {requests} | **{dropped}** | {round(run['max_gap_ms']):.0f} |")
    assert ROLLOUT_3["ok"] == ROLLOUT_3["requests"] and ROLLOUT_3["incidents"] == []
    # Run 2 is the diagnosis only because the sleep really was longer and the number
    # really did not improve. Both halves are load-bearing, so both are asserted.
    assert "sleep15" in ROLLOUT_2["label"]
    assert ROLLOUT_2["dropped"] >= ROLLOUT_1["dropped"]


def test_the_gpu_kill_from_us_east_1_is_the_run_it_cites() -> None:
    assert (KILL_LOOP["requests"], KILL_LOOP["ok"], KILL_LOOP["dropped"]) == (92, 92, 0)
    says("**92 requests, 92 ok, 0 dropped**", "**92 requests, 92 ok, 0 dropped**")

    rows = KILL_LEDGER if isinstance(KILL_LEDGER, list) else KILL_LEDGER["rows"]
    assert len(rows) == 40
    assert all(row["status_code"] == 200 for row in rows)
    direct = [row for row in rows if row["failover_hops"] == 0]
    hopped = [row for row in rows if row["failover_hops"] == 1]
    reasons = {reason: 0 for reason in {row["failover_error"] for row in hopped}}
    for row in hopped:
        reasons[row["failover_error"]] += 1
    assert (len(direct), len(hopped)) == (22, 18)
    assert reasons == {"breaker_open": 10, "upstream_unavailable": 8}
    says(
        "40 rows, all\n200, 22 on `vllm_a` at `failover_hops: 0` and 18 flipped to `vllm_b`, "
        "of which 10\n`breaker_open` and 8 `upstream_unavailable`",
    )


def test_the_two_gpu_kill_on_the_desk_is_the_h3_recording() -> None:
    window = H3_KILL["window"]
    assert window["requests"] == 270
    assert H3_KILL["status_codes"] == {"200": 270}
    assert H3_KILL["clause_1_no_caller_visible_5xx"]["caller_visible_5xx"] == 0
    assert H3_KILL["failover"]["served_by"] == {"vllm_a": 129, "vllm_b": 141}
    assert H3_KILL["failover"]["reasons"] == {
        "upstream_unavailable": 58,
        "breaker_open": 82,
        "upstream_timeout": 1,
    }
    assert len(H3_KILL["outages"]) == 2
    says(
        f"**{window['requests']} requests over {round(window['span_s'] / 60)} minutes",
        "129 served by the primary, 141 by the fallback (58 after a real connection\nrefusal, "
        "82 skipped by an open breaker, one after a timeout)",
        "two kill-and-restore cycles",
    )


def test_the_keyless_chaos_numbers_are_the_ones_ci_runs() -> None:
    clause_1 = H3_CHAOS["clause_1_no_caller_visible_5xx"]
    clause_3 = H3_CHAOS["clause_3_mid_stream_faults_are_terminal_events"]
    total = sum(intensity["requests"] for intensity in clause_1["intensities"])
    assert total == 60
    assert all(item["caller_visible_5xx"] == 0 for item in clause_1["intensities"])
    assert [item["fault_rate"] for item in clause_1["intensities"]] == [0.25, 0.5, 1.0]
    assert (clause_3["cut_points"], clause_3["terminal_error_events"]) == (4, 4)
    assert (clause_3["silent_truncations"], clause_3["splices"]) == (0, 0)
    says(
        f"{total} requests",
        "**0 caller-visible 5xx at 25%, 50% and 100% fault\nrates**",
        "**4/4 terminal error events, 0 silent truncations, 0 splices.**",
    )


def test_the_breaker_and_backoff_constants_are_the_shipped_ones() -> None:
    """Quoted in the README, produced by the code, recorded in the chaos artifact."""
    policy = H3_CHAOS["policy"]
    assert (policy["breaker_window"], policy["breaker_min_samples"]) == (20, 5)
    assert (policy["breaker_failure_ratio"], policy["breaker_cooldown_s"]) == (0.5, 10.0)
    backoff = BackoffPolicy()
    assert (backoff.base_s, backoff.cap_s) == (policy["backoff_base_s"], policy["backoff_cap_s"])
    # "worst case 150 ms across three attempts" — three attempts is two retries.
    assert round(backoff.worst_case_s(2) * 1000) == 150
    says(
        "Rolling\nwindow of 20, trips at a 0.5 failure ratio once there are 5 samples, "
        "10-second cooldown",
        "50 ms, doubling, capped at 2 s — worst case 150 ms across three attempts",
        "The 10-second cooldown is legible in the raw attempt\nspacing",
    )


def test_the_console_capture_numbers_come_from_the_evidence_readme() -> None:
    """A screenshot cannot be recomputed, so it is pinned to the list that describes it."""
    for fragment in ("104 requests", "66 served by a fallback", "CALLER-VISIBLE 5XX: 0"):
        assert fragment in README
        assert fragment in P10_EVIDENCE, (
            f"{fragment!r} is in the README but not in docs/evidence/p10-eks/README.md, "
            "which is the only place the screenshot's contents are written down"
        )


def test_the_drain_repro_and_the_instrument_failures_are_in_the_phase_log() -> None:
    """Terminal output from a laptop rig and a lost run: pinned to where they were recorded."""
    for fragment in (
        "12150 requests   2 dropped",
        "12142 requests   1 dropped",
        "12372 requests   1 dropped",
        "12087 requests   0 dropped",
        "12108 requests   0 dropped",
        "12359 requests   0 dropped",
    ):
        assert fragment in README
        assert fragment in PHASE_LOG, f"{fragment!r} is not in the phase log that recorded it"
    for fragment in ("8122 requests, 8122 dropped", "fourteen legitimate"):
        assert fragment in README
    assert "8122 requests, 8122 dropped" in P10_EVIDENCE


# --- the local stack ------------------------------------------------------------------------


def test_the_mock_unit_cost_is_the_shipped_price_book() -> None:
    """$0.0000115 is arithmetic over `config/models.yaml`, not a number in a docstring."""
    price = load_price_book().price_for("mock-model-1", date.today())
    assert price is not None, "the committed price book no longer prices the mock models"
    cost = usd_for_tokens(11, price.usd_per_mtok_in) + usd_for_tokens(7, price.usd_per_mtok_out)
    assert cost == Decimal("0.0000115")
    says(f"= ${cost.quantize(Decimal('1.000000000000'))} (priced)")

    # The README quotes the demo's own line, which carries the rates as the admin API
    # serialises them. Parsed and compared as money rather than as text, so a change to
    # either the price book or that serialisation fails here instead of drifting.
    quoted = re.search(r"(\d+) x \$([\d.]+)/MTok \+ (\d+) x \$([\d.]+)/MTok = \$([\d.]+)", README)
    assert quoted is not None, "the README no longer shows the demo pricing a request"
    tokens_in, rate_in, tokens_out, rate_out, total = quoted.groups()
    assert (int(tokens_in), int(tokens_out)) == (11, 7)
    assert (money(rate_in), money(rate_out)) == (price.usd_per_mtok_in, price.usd_per_mtok_out)
    assert money(total) == cost


def test_the_caches_default_threshold_is_the_constant_the_gateway_uses() -> None:
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.90
    assert H1["pre_registration"]["shipped_default_threshold"] == DEFAULT_SIMILARITY_THRESHOLD
    says(f"The shipped default is **{DEFAULT_SIMILARITY_THRESHOLD:.2f}**")
    # And the README says the finding did not move it, which is a claim about this repo's
    # own conduct: raising it is exactly the move H1 says does not work.
    says(
        "The shipped default is left where it is and documented as unsafe rather than "
        "quietly\nraised"
    )


def test_the_twelve_question_corpus_bands_are_its_committed_vectors() -> None:
    """0.9237 and 0.8511 — the gap 0.90 was chosen to sit in, recomputed by dot product."""
    assert CORPUS["normalized"] is True, "cosine is a dot product only for unit vectors"
    questions = {question["id"]: question["vector"] for question in CORPUS["questions"]}
    own: list[float] = []
    other: list[float] = []
    for probe in CORPUS["probes"]:
        for question_id, vector in questions.items():
            similarity = sum(a * b for a, b in zip(probe["vector"], vector, strict=True))
            (own if question_id == probe["source"] else other).append(similarity)
    says(f"at worst **{min(own):.4f}**", f"at\nbest **{max(other):.4f}**")
    assert max(other) < DEFAULT_SIMILARITY_THRESHOLD < min(own)


def test_the_auth_cache_ttl_is_the_documented_number() -> None:
    says(f"**{AUTH_CACHE_TTL_S:.0f} seconds**\n(`AUTH_CACHE_TTL_S`)")


# --- money -----------------------------------------------------------------------------------


def test_the_spend_table_is_the_reports_own_arithmetic() -> None:
    h2_total = money(H2["headroom_meter"]["usd_cost"])
    assert h2_total == money("7.541253")
    says("| H2 — the 133-question suite through the gateway | $10.00 | **$7.541253** |")
    # The H1 figure is a range because two redraw rounds were overwritten before commit.
    # It is REPORT.md's number and the README must not quietly narrow it.
    # The en dashes below are the README's own typography, so ruff's confusable-character
    # rule is silenced rather than the text being changed to satisfy it.
    for fragment in ("$0.443670", "≈ $0.53–0.57", "$20.00"):  # noqa: RUF001
        assert fragment in README
    assert "$0.443670" in REPORT and "$0.53–0.57" in REPORT  # noqa: RUF001
    says("**≈ $8.08–8.12**", "**≈ $8.10**")  # noqa: RUF001


def test_the_cloud_cost_table_says_pending_until_the_billing_capture_lands() -> None:
    """The one claim in this README that was deliberately incomplete — and no longer is.

    Written in Phase 11 to tighten by itself: while `23-billing.png` was absent the table
    had to say `pending`, and the day the capture arrived this test failed until a number
    replaced the word. It fired on 2026-08-12, which is why the branch below is now the
    live one. It stays two-branched: delete the evidence and the README must go back to
    admitting it has none.
    """
    if not BILLING_CAPTURE.exists():
        says("| **pending** |", "**The actuals column is pending and says so.**")
        assert "**pending**" in P10_EVIDENCE, (
            "the evidence list must agree with the README about what has not arrived"
        )
        return

    assert "**pending**" not in README, (
        f"{BILLING_CAPTURE.name} has landed, so the cloud-cost table's actual column "
        "must carry the number rather than `pending` — and this test is why you are "
        "reading about it now rather than finding it in six months"
    )
    for fragment in ("23-billing.png", "23-billing.txt"):
        assert fragment in README, f"the README stopped citing {fragment}"

    # Both totals, because the gap between them is the finding (H-102). A README that
    # quotes only the tag-attributable $3.07 under-reports what the project actually cost.
    for figure, why in (
        ("$3.5556", "the Headroom-attributable total"),
        ("$3.0706", "what the Project tag can see"),
        ("$3.07", "what the console capture reads"),
        ("$0.4850", "the gap between them"),
        ("$2.2228", "the empty-Layer bucket"),
        ("72.4%", "the empty-Layer share"),
        ("$6.10/day", "the rate, which is what answers A7"),
    ):
        assert figure in README, f"the cost table no longer carries {figure} — {why}"


def test_the_undershoot_is_attributed_to_the_short_window_and_not_to_efficiency() -> None:
    """$3.56 against a pre-registered $20-25 is the single most quotable number in this
    repo and the single easiest one to quote dishonestly. The window was fourteen hours,
    not three days (H-096), and the rate came in *over* the estimate — so the copy has to
    say compression, in those words, everywhere the total appears."""
    for doc, name in ((README, "README.md"), (PHASE_LOG, "docs/PHASE_LOG.md")):
        assert "fourteen hours, not three days" in doc, (
            f"{name} quotes the undershoot without saying the window was compressed"
        )
        assert "not** efficiency" in doc or "not efficiency" in doc, (
            f"{name} does not rule out the efficiency reading of the undershoot"
        )
    # And the rate, which is the number that actually answers A7, came in above estimate.
    assert "$6.10/day" in README and "$5.58/day" in README


def test_the_phase_9_billing_capture_is_absent_and_every_list_says_so() -> None:
    """H-102: `18-billing.png` was not captured, because by the time `Layer` was active
    there was no Phase 9 split left for it to show. The evidence README's own rule is that
    an absent capture whose absence *is* the finding gets said in those words — so no list
    may still be waiting for the file, and the file may not quietly reappear without the
    prose changing with it."""
    p9_capture = REPO / "docs" / "evidence" / "p9-aws" / "18-billing.png"
    p9_evidence = (REPO / "docs" / "evidence" / "p9-aws" / "README.md").read_text(encoding="utf-8")
    if p9_capture.exists():
        pytest.fail(
            "18-billing.png is in the repo, but every list in these docs records it as "
            "not captured with a reason — update the prose in the same commit as the file"
        )
    for doc, name in (
        (p9_evidence, "docs/evidence/p9-aws/README.md"),
        (P10_EVIDENCE, "docs/evidence/p10-eks/README.md"),
    ):
        assert "18-billing" in doc, f"{name} stopped mentioning the capture entirely"
        assert "not captured" in doc, (
            f"{name} still lists 18-billing.png as expected rather than as not captured"
        )


def test_the_phase_9_cost_read_and_the_phase_10_rate_are_the_logged_ones() -> None:
    for fragment in ("$0.04", "$5.58/day", "$3.25", "$20–25"):  # noqa: RUF001
        assert fragment in README
        assert fragment in PHASE_LOG, (
            f"{fragment!r} is in the README but not in the phase log that recorded it"
        )


# --- structure: no claim without an artifact --------------------------------------------------


def test_every_path_the_readme_links_to_exists() -> None:
    """A dead link to an artifact and a missing artifact are the same failure."""
    links = re.findall(r"\]\((?!https?://|#)([^)#\s]+)", README)
    assert links, "the README has stopped linking to anything, which cannot be right"
    missing = sorted({link for link in links if not (REPO / link).exists()})
    assert not missing, "the README links to paths that are not in the repo:\n  " + "\n  ".join(
        missing
    )


def test_every_decision_the_readme_cites_is_a_real_entry() -> None:
    cited = sorted(set(re.findall(r"\bH-(\d{3})\b", README)))
    assert cited, "the README no longer cross-links the decision log"
    headings = set(re.findall(r"^## H-(\d{3}) — ", DECISIONS, flags=re.MULTILINE))
    missing = sorted(number for number in cited if number not in headings)
    assert not missing, f"the README cites H-{', H-'.join(missing)}, which docs/DECISIONS.md "
    # And the ranges it quotes are real: the log runs H-000 to its highest heading.
    assert f"H-000 … H-{max(headings)}" in README


def test_the_readme_is_not_still_announcing_that_it_is_a_stub() -> None:
    """The Phase 0 placeholder, and the phase-by-phase progress line that outlived it.

    This is not decoration: that block sat at the top of the file saying *"Phase 4 ← here"*
    for six phases, which is exactly the drift the rest of this file exists to catch, in
    the one place a stranger reads first.
    """
    for stale in ("Under construction", "This README is a stub", "← here", "🚧"):
        assert stale not in README, f"the README still carries {stale!r}"


def test_the_quickstart_really_is_one_command() -> None:
    """`make demo` exists, is one target, and runs the script the README describes."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^demo:.*##", makefile, flags=re.MULTILINE), "no `make demo` target"
    assert "scripts/demo.py" in makefile
    assert (REPO / "scripts" / "demo.py").exists()
    says(
        "```bash\ngit clone https://github.com/sergioavilax/headroom && cd headroom\nmake demo\n```"
    )
    # The demo's own headline — the count it prints when everything holds — is quoted in
    # the README, so the two cannot drift apart without one of them being edited.
    checks = len(re.findall(r"^  ok    ", README, flags=re.MULTILINE))
    assert f"{checks}" in README


def test_the_claimed_test_count_is_the_number_this_session_collected(
    request: pytest.FixtureRequest,
) -> None:
    """The one figure with no artifact behind it, so it comes from the run itself.

    Skips loudly rather than lying when the suite is run in part — `pytest
    tests/test_docs.py` legitimately collects one file, and the README is not about one
    file. Same rule as H-012: a check that cannot be made is announced, never assumed.
    """
    # Comparing collected *files* would be wrong — `tests/test_live_smoke.py` contributes
    # nothing to a keyless collection and that is invariant 4 working, not a partial run.
    # So the question asked is about the invocation instead: were the committed testpaths
    # collected, with no `-k` and with the committed marker expression untouched?
    tests_dir = (REPO / "tests").resolve()
    targets = [Path(arg.split("::")[0]).resolve() for arg in request.config.args]
    whole_suite = any(target in (tests_dir, REPO) for target in targets)
    unfiltered = not request.config.option.keyword and (
        request.config.option.markexpr == "not live"
    )
    if not (whole_suite and unfiltered):
        pytest.skip(
            "this run is not the whole keyless suite (a path, a -k, or a -m was given) and "
            "the README's count is about the whole keyless suite. Run `make test`."
        )
    claimed = re.search(r"# ([\d,]+) keyless tests", README)
    assert claimed is not None, "the README no longer states a test count"
    assert int(claimed.group(1).replace(",", "")) == len(request.session.items), (
        f"the README says {claimed.group(1)} keyless tests and this session collected "
        f"{len(request.session.items)}"
    )
