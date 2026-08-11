"""H3's adjudication, against the committed evidence and against rows built to fail it.

The live half of §P8.H3 is one recording on hardware CI cannot reach, so the thing CI *can*
keep honest is the analysis: it runs against the committed rows on every pull request, and
each clause is also driven with data that should falsify it. A verdict that has never been
seen to say "FALSIFIED" is a verdict nobody should believe.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from experiments.h3.livekill import analyse
from experiments.provenance import REPO_ROOT, RESULTS_DIR, read_json
from headroom.policy.health import HealthPolicy

EVIDENCE = REPO_ROOT / "docs" / "evidence" / "p8-experiments" / "h3-livekill-ledger-rows.json"

pytestmark = pytest.mark.skipif(
    not EVIDENCE.exists(), reason="the live-kill evidence export is not committed"
)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, Any]]:
    return list(read_json(EVIDENCE))


@pytest.fixture(scope="module")
def result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return analyse(rows)


# --- the recording is what H-067 says it is ------------------------------------------------


def test_the_export_is_the_two_gpu_kill_and_the_rest_is_counted_not_dropped(
    result: dict[str, Any],
) -> None:
    """`make seed`'s mock traffic shares the ledger. The filter is reported, not silent."""
    assert result["window"]["requests"] == 270
    assert result["window"]["models"] == ["cyankiwi/Qwen3.6-27B-AWQ-INT4"]
    assert result["not_this_experiment"]["rows"] == 222
    assert result["window"]["span_s"] > 60 * 60  # sustained, not a burst


def test_the_load_was_sustained_rather_than_a_burst(result: dict[str, Any]) -> None:
    assert 2.0 < result["window"]["median_inter_request_s"] < 10.0
    assert len(result["outages"]) == 2


# --- clause 1 -------------------------------------------------------------------------------


def test_no_caller_saw_a_5xx_across_the_whole_recording(result: dict[str, Any]) -> None:
    clause = result["clause_1_no_caller_visible_5xx"]
    assert clause["caller_visible_5xx"] == 0
    assert clause["verdict"] == "HOLDS"
    assert result["failover"]["hops"] == {0: 129, 1: 141}


def test_one_5xx_would_falsify_clause_1(rows: list[dict[str, Any]]) -> None:
    """The sabotage shape: the verdict is known to be capable of failing."""
    poisoned = copy.deepcopy(rows)
    victim = next(row for row in poisoned if str(row["provider"] or "").startswith("vllm"))
    victim["status_code"] = 503
    victim["outcome"] = "upstream_error"
    clause = analyse(poisoned)["clause_1_no_caller_visible_5xx"]
    assert clause["caller_visible_5xx"] == 1
    assert clause["verdict"] == "FALSIFIED"


# --- clause 2 -------------------------------------------------------------------------------


def test_the_bound_is_derived_from_h_052s_published_cooldown(result: dict[str, Any]) -> None:
    """PRE_REGISTRATION §H3.5: the bound has no free parameter a reading of the data could
    have set. It is `COOLDOWN_S` plus the load interval, and the constant is read from the
    policy rather than typed into the document."""
    clause = result["clause_2_recovery_within_bound"]
    assert clause["cooldown_s"] == HealthPolicy().cooldown_s
    assert clause["bound_s"] == pytest.approx(
        clause["cooldown_s"] + clause["load_interval_s"], abs=0.01
    )


def test_the_breaker_re_admitted_on_the_first_request_after_every_cooldown(
    result: dict[str, Any],
) -> None:
    """The mechanism the aggregate bound is a proxy for, checked per probe.

    This is the claim that matters, and it holds exactly. The aggregate bound above it is
    reported as pre-registered, including where it is exceeded — see the report; the two are
    published together rather than the flattering one alone.
    """
    mechanism = result["clause_2_recovery_within_bound"]["mechanism"]
    assert mechanism["probes_checked"] == 37
    assert mechanism["probes_correct"] == 37
    assert mechanism["verdict"] == "HOLDS"


def test_the_pre_registered_aggregate_bound_is_reported_as_measured(result: dict[str, Any]) -> None:
    """Not moved to fit: two probe gaps exceed it, by at most 0.09 s, and that is published."""
    clause = result["clause_2_recovery_within_bound"]
    assert clause["verdict"] == "EXCEEDED"
    assert len(clause["over_bound"]) == 2
    assert max(clause["over_bound"]) - clause["bound_s"] <= 0.1


def test_the_load_interval_was_not_the_constant_the_bound_assumed(result: dict[str, Any]) -> None:
    """The diagnosis, in the data: the loop presented 4.0 s to 20.0 s, not one fixed T."""
    low, high = result["clause_2_recovery_within_bound"]["load_interval_range_s"]
    assert low < 5.0 < high


# --- clause 3 -------------------------------------------------------------------------------


def test_the_ledger_reports_what_it_can_see_about_cuts_and_says_what_it_cannot(
    result: dict[str, Any],
) -> None:
    clause = result["clause_3_mid_stream_faults_are_terminal_events"]
    assert clause["mid_stream_cuts"] == 0
    assert "cannot see the frames" in clause["note"]


# --- the mock half, which CI does run -------------------------------------------------------


@pytest.mark.skipif(
    not (RESULTS_DIR / "h3_chaos.json").exists(), reason="chaos artifact not generated"
)
def test_the_committed_chaos_artifact_describes_the_schedules_ci_actually_runs() -> None:
    """The artifact reads its intensities out of `tests/test_failover_chaos.py`, so a number
    in the report cannot drift away from the tick on the pull request."""
    from tests.test_failover_chaos import INTENSITIES

    artifact = read_json(Path(RESULTS_DIR / "h3_chaos.json"))
    reported = artifact["clause_1_no_caller_visible_5xx"]["intensities"]
    assert [row["intensity"] for row in reported] == [row.name for row in INTENSITIES]
    assert [row["faults_injected"] for row in reported] == [row.faults for row in INTENSITIES]
    assert all(row["caller_visible_5xx"] == 0 for row in reported)
    assert artifact["clause_1_no_caller_visible_5xx"]["verdict"] == "HOLDS"

    cuts = artifact["clause_3_mid_stream_faults_are_terminal_events"]
    assert cuts["terminal_error_events"] == cuts["cut_points"]
    assert cuts["silent_truncations"] == 0
    assert cuts["splices"] == 0
    assert cuts["verdict"] == "HOLDS"
