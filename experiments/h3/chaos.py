"""The mock chain at three fault intensities, promoted from a green test to a figure.

`tests/test_failover_chaos.py` has asserted §P8.H3's clauses on every pull request since
Phase 6. What it does not do is *report*: a passing test is a tick, and REPORT.md
adjudicates numbers. This module drives the same scenarios and writes them out.

**It imports the schedules from the test rather than restating them.** `INTENSITIES` and
`PRE_TOKEN_FAULTS` come straight out of `tests/test_failover_chaos.py`, so the artifact
describes the thing CI actually runs; a copy here would be a second definition free to
drift from the one under test, and the number in the report would slowly stop meaning what
the tick means.

That makes this the one module in `experiments/` that imports from `tests/`, which is worth
naming. `tests/support/` has been an explicit harness layer since Phase 1, the MockProvider
it drives is production code (`headroom/providers/mock.py`), and the alternative is
duplicating a fault-injection rig. The dependency points at the harness, never the reverse.

Keyless, deterministic, $0.00: no GPU, no provider, no network.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, Final

from experiments.artifacts import RESULTS_DIR, provenance, write_json
from headroom.core.ledger import LedgerQuery
from headroom.policy.failover import BackoffPolicy
from headroom.policy.health import BREAKER_OPEN, HealthPolicy
from headroom.providers.mock import MockScript

__all__ = ["RESULT_PATH", "main", "run_all"]

RESULT_PATH: Final = RESULTS_DIR / "h3_chaos.json"

ANSWER: Final = "the fallback answered this one"
#: The chaos suite's own cut points and its own cuttable answer, so the artifact and the
#: test describe the same experiment. 12 lands *after* the last text delta, where the
#: fragment reads as a finished answer and only the missing terminal marker gives it away —
#: the hardest case for clause 3.
CUT_POINTS: Final = (1, 4, 8, 12)
CUTTABLE: Final = "an answer long enough to be cut anywhere"


async def _run_intensity(intensity: Any) -> dict[str, Any]:
    from tests.support.fixtures import anthropic_request
    from tests.support.harness import FakeClock, gateway_harness
    from tests.test_failover_chaos import PRE_TOKEN_FAULTS

    clock = FakeClock()
    async with gateway_harness(chain=("mock_a", "mock_b"), clock=clock) as harness:
        harness.book.set("chaos@mock_b", MockScript.anthropic_message(ANSWER))
        statuses: list[int] = []
        for index, fault in enumerate(intensity.schedule):
            script = (
                PRE_TOKEN_FAULTS[fault]()
                if fault is not None
                else MockScript.anthropic_message("the primary answered this one")
            )
            harness.book.set("chaos@mock_a", script)
            response = await harness.post(
                "/v1/messages", anthropic_request(text=f"question {index}"), script="chaos"
            )
            statuses.append(response.status_code)

        await harness.writer.drain()
        rows = await harness.ledger.list_entries(LedgerQuery(limit=intensity.requests))
        breaker = harness.health.state_of("mock_a")

    return {
        "intensity": intensity.name,
        "requests": intensity.requests,
        "faults_injected": intensity.faults,
        "fault_rate": round(intensity.faults / intensity.requests, 3),
        "statuses": dict(sorted({code: statuses.count(code) for code in set(statuses)}.items())),
        "caller_visible_5xx": sum(1 for code in statuses if code >= 500),
        "ledger_rows": len(rows),
        "rows_ok": sum(1 for row in rows if row.outcome == "ok"),
        "hops": sum(row.failover_hops for row in rows),
        "primary_breaker": breaker,
    }


async def _run_cuts() -> dict[str, Any]:
    """Clause 3 at the frame level — the half a ledger cannot see."""
    from tests.support.fixtures import anthropic_request
    from tests.support.harness import gateway_harness
    from tests.support.streams import event_pairs

    results: list[dict[str, Any]] = []
    for after in CUT_POINTS:
        async with gateway_harness(chain=("mock_a", "mock_b")) as harness:
            harness.book.set(
                "cut@mock_a", MockScript.anthropic_stream(CUTTABLE, cut_after_chunks=after)
            )
            harness.book.set("cut@mock_b", MockScript.anthropic_stream(ANSWER))
            response = await harness.post(
                "/v1/messages", anthropic_request(stream=True), script="cut"
            )
            events = [name for name, _ in event_pairs(response.content)]
            served_by_fallback = ANSWER.encode() in response.content
            results.append(
                {
                    "cut_after_chunks": after,
                    "terminal_error_event": events[-1] == "error",
                    "message_stop_present": "message_stop" in events,
                    "fallback_spliced_in": served_by_fallback,
                }
            )
    return {
        "cut_points": len(results),
        "terminal_error_events": sum(1 for row in results if row["terminal_error_event"]),
        "silent_truncations": sum(
            1
            for row in results
            if not row["terminal_error_event"] and not row["message_stop_present"]
        ),
        "splices": sum(1 for row in results if row["fallback_spliced_in"]),
        "detail": results,
    }


async def run_all() -> dict[str, Any]:
    from tests.test_failover_chaos import INTENSITIES

    intensities = [await _run_intensity(intensity) for intensity in INTENSITIES]
    cuts = await _run_cuts()
    backoff = BackoffPolicy()
    policy = HealthPolicy()
    return {
        "schema": "h3_chaos/1",
        "provenance": provenance(
            produced_by="experiments/h3/chaos.py",
            notes="Deterministic, keyless, $0.00. Same schedules CI asserts on every PR.",
        ),
        "policy": {
            "breaker_window": policy.window,
            "breaker_min_samples": policy.min_samples,
            "breaker_failure_ratio": policy.failure_ratio,
            "breaker_cooldown_s": policy.cooldown_s,
            "backoff_base_s": backoff.base_s,
            "backoff_cap_s": backoff.cap_s,
            "backoff_worst_case_s": backoff.worst_case_s(3),
            "breaker_open_state": BREAKER_OPEN,
        },
        "clause_1_no_caller_visible_5xx": {
            "statement": "zero caller-visible 5xx for pre-first-token faults, at every intensity",
            "intensities": intensities,
            "verdict": (
                "HOLDS"
                if all(row["caller_visible_5xx"] == 0 for row in intensities)
                else "FALSIFIED"
            ),
        },
        "clause_3_mid_stream_faults_are_terminal_events": {
            "statement": "mid-stream faults surface as terminal error events, 100%, never silently",
            **cuts,
            "verdict": (
                "HOLDS"
                if cuts["terminal_error_events"] == cuts["cut_points"]
                and cuts["silent_truncations"] == 0
                and cuts["splices"] == 0
                else "FALSIFIED"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h3.chaos",
        description="Run the mock chain at three fault intensities and report. Keyless, free.",
    )
    parser.add_argument("--out", default=str(RESULT_PATH))
    args = parser.parse_args(argv)

    result = asyncio.run(run_all())
    write_json(Path(args.out), result)
    for row in result["clause_1_no_caller_visible_5xx"]["intensities"]:
        print(
            f"  {row['intensity']:<7} {row['requests']} requests, "
            f"{row['faults_injected']} faults ({row['fault_rate']:.0%}) -> "
            f"{row['statuses']}, caller-visible 5xx: {row['caller_visible_5xx']}, "
            f"hops {row['hops']}, breaker {row['primary_breaker']}"
        )
    cuts = result["clause_3_mid_stream_faults_are_terminal_events"]
    print(
        f"  mid-stream cuts: {cuts['terminal_error_events']}/{cuts['cut_points']} terminal "
        f"error events, {cuts['silent_truncations']} silent truncations, {cuts['splices']} splices"
    )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
