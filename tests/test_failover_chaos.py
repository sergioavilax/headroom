"""The chaos suite: scripted fault schedules through the whole stack, at three intensities.

BUILD_PLAN §P6's first proof — *"MockProvider fault schedules (bursts of 529s, timeouts,
mid-stream cuts) driven through the full stack, asserting zero caller-visible 5xx for
pre-first-token faults, correct backoff timing bounds, breaker trip/recovery, and honest
terminal events for mid-stream faults"* — and the seed of §P8.H3, which promotes exactly
this to a reported experiment with the same three intensities.

**The schedules are deterministic, not random.** Each intensity is a fixed list of fault
kinds indexed by request number, so a failure here is reproducible from the test name
alone and a number in the P8 report is a number somebody else can regenerate. Randomised
chaos finds more bugs and reports none of them twice.

**The claim being measured is precise, and its exclusion is the interesting half.** Zero
caller-visible 5xx for **pre-first-token** faults. A mid-stream cut is deliberately *not*
in that promise — it cannot be, because the status line is spent by the time it happens —
and what it gets instead is a terminal error event, 100% of the time, which is H3's
falsification condition stated from the other side: any silent truncation reaching a
caller is a real bug and would be a real (unflattering, still published) finding.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from headroom.core.errors import UpstreamStreamCut
from headroom.core.ledger import LedgerQuery
from headroom.policy.failover import BackoffPolicy
from headroom.policy.health import BREAKER_CLOSED, BREAKER_OPEN, HealthPolicy
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import FakeClock, GatewayHarness, gateway_harness
from .support.streams import anthropic_text, event_pairs

ANSWER = "the fallback answered this one"

#: Every pre-first-token fault the MockProvider can inject, by name. Each is scripted
#: onto the primary; the fallback always answers normally.
PRE_TOKEN_FAULTS = {
    "overloaded": lambda: MockScript.error(529, dialect="anthropic"),
    "rate_limited": lambda: MockScript.error(429, dialect="anthropic", retry_after="3"),
    "server_error": lambda: MockScript.error(500, dialect="anthropic"),
    "timeout": MockScript.timeout,
    "connect_error": MockScript.connect_error,
}


@dataclass(frozen=True)
class Intensity:
    """One scripted run: how many requests, and which of them the primary ruins."""

    name: str
    requests: int
    #: Fault name per request index; ``None`` means the primary answers normally.
    schedule: tuple[str | None, ...]

    @property
    def faults(self) -> int:
        return sum(1 for entry in self.schedule if entry is not None)


def _schedule(name: str, requests: int, every: int) -> Intensity:
    """Fault every ``every``-th request, cycling through the fault kinds in order.

    Cycling rather than repeating one kind, so an intensity exercises the whole taxonomy
    — a 529, a 429, a 500, a timeout, and a connect error all appear in every run long
    enough to reach them.
    """
    kinds = list(PRE_TOKEN_FAULTS)
    schedule: list[str | None] = []
    faulted = 0
    for index in range(requests):
        if index % every == 0:
            schedule.append(kinds[faulted % len(kinds)])
            faulted += 1
        else:
            schedule.append(None)
    return Intensity(name=name, requests=requests, schedule=tuple(schedule))


INTENSITIES = [
    _schedule("light", requests=20, every=4),  # 25% of requests hit a broken primary
    _schedule("heavy", requests=20, every=2),  # 50%
    _schedule("brutal", requests=20, every=1),  # 100% — the primary is simply gone
]


@pytest.mark.parametrize("intensity", INTENSITIES, ids=lambda i: i.name)
async def test_no_caller_sees_a_5xx_at_any_intensity(intensity: Intensity) -> None:
    """The headline: every request gets a real answer, whatever the primary is doing.

    The breaker is deliberately given a clock that never moves, so once it trips it stays
    tripped for the whole run — which is the steady state of a real outage and the harder
    case, since the fallback then carries everything.
    """
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

        assert statuses == [200] * intensity.requests
        assert max(statuses) < 500
        assert intensity.faults >= 5  # every fault kind was reached at least once


@pytest.mark.parametrize("intensity", INTENSITIES, ids=lambda i: i.name)
async def test_every_request_is_metered_exactly_once_under_chaos(intensity: Intensity) -> None:
    """One row per request, and the hop count on each row tells the truth about it.

    The ledger is what §P8.H3 reports from, so "the chaos ran and the numbers are
    reconstructable afterwards" is part of the claim rather than a nicety.
    """
    clock = FakeClock()
    async with gateway_harness(chain=("mock_a", "mock_b"), clock=clock) as harness:
        harness.book.set("chaos@mock_b", MockScript.anthropic_message(ANSWER))
        expected_hops: list[int] = []

        for index, fault in enumerate(intensity.schedule):
            script = (
                PRE_TOKEN_FAULTS[fault]()
                if fault is not None
                else MockScript.anthropic_message("the primary answered this one")
            )
            harness.book.set("chaos@mock_a", script)
            await harness.post(
                "/v1/messages", anthropic_request(text=f"question {index}"), script="chaos"
            )
            expected_hops.append(harness.last_context().failover_hops)

        await harness.writer.drain()
        rows = await harness.ledger.list_entries(LedgerQuery(limit=intensity.requests))
        assert len(rows) == intensity.requests
        assert sum(row.failover_hops for row in rows) == sum(expected_hops)
        assert all(row.outcome == "ok" for row in rows)
        # Once the breaker trips, the primary is skipped rather than tried — but the row
        # keeps reporting that the primary did not serve. Hops never go backwards.
        assert sum(1 for row in rows if row.failover_hops) >= intensity.faults


@pytest.mark.parametrize("intensity", INTENSITIES, ids=lambda i: i.name)
async def test_the_backoff_stays_inside_its_published_bound(intensity: Intensity) -> None:
    """Timing bounds, measured rather than trusted — and on this chain they are zero.

    With one fallback and one attempt each, the executor never comes back to a provider
    that already failed *this request*, so it never sleeps at all. That is H-050's
    decision showing up as a number: failover to a fresh candidate is free, and the
    backoff budget is spent only where it buys something.
    """
    clock = FakeClock()
    async with gateway_harness(chain=("mock_a", "mock_b"), clock=clock) as harness:
        harness.book.set("chaos@mock_b", MockScript.anthropic_message(ANSWER))
        for index, fault in enumerate(intensity.schedule):
            harness.book.set(
                "chaos@mock_a",
                PRE_TOKEN_FAULTS[fault]()
                if fault is not None
                else MockScript.anthropic_message("ok"),
            )
            await harness.post(
                "/v1/messages", anthropic_request(text=f"question {index}"), script="chaos"
            )

        assert harness.sleeper.delays == []


async def test_a_retrying_chain_stays_inside_its_worst_case_under_chaos() -> None:
    """The same bound where sleeping *does* happen: one provider, three attempts.

    Every request fails all three times, so every request pays the whole backoff budget —
    150 ms, which is the number ``BackoffPolicy.worst_case_s`` publishes and the number an
    operator sizing a client timeout needs. The breaker is held off with a high sample
    floor so that this test measures the backoff and nothing else; what the breaker does
    to a retry budget is the *next* test, because it is a different claim.
    """
    async with gateway_harness(
        chain=("mock_a",), max_attempts=3, health=HealthPolicy(min_samples=1000)
    ) as harness:
        harness.book.set("down", MockScript.error(529, dialect="anthropic"))

        for _ in range(5):
            response = await harness.post("/v1/messages", anthropic_request(), script="down")
            assert response.status_code == 529
            assert len(harness.providers["mock_a"].received) % 3 == 0

        assert harness.sleeper.delays == [0.05, 0.1] * 5
        assert harness.sleeper.total_s == pytest.approx(5 * BackoffPolicy().worst_case_s(2))
        assert harness.sleeper.total_s == pytest.approx(0.75)


async def test_a_tripped_breaker_stops_a_retry_budget_being_spent_on_a_dead_provider() -> None:
    """Emergent, worth pinning, and the right behaviour: retries stop, the last try stays.

    A single-provider route with ``max_attempts: 3`` is asking to be retried. Once the
    breaker has decided the provider is down, retrying it twice more per request buys
    nothing but latency — so the first two slots are skipped and only the final one, the
    one the breaker may never skip, is actually attempted. The caller still gets the
    upstream's own 529 rather than a gateway error, and the backoff budget stops being
    spent on a provider we already know the answer about.

    Recorded here rather than left to be rediscovered: it falls out of two rules that were
    each decided for their own reasons (H-050's "backoff only on a repeat", H-052's "never
    skip the last candidate"), and it is exactly what an operator would want if asked.
    """
    async with gateway_harness(chain=("mock_a",), max_attempts=3) as harness:
        harness.book.set("down", MockScript.error(529, dialect="anthropic"))

        for _ in range(2):
            await harness.post("/v1/messages", anthropic_request(), script="down")
        assert harness.health.state_of("mock_a") == BREAKER_OPEN
        tried_while_closed = len(harness.providers["mock_a"].received)
        slept_while_closed = list(harness.sleeper.delays)

        for _ in range(5):
            response = await harness.post("/v1/messages", anthropic_request(), script="down")
            assert response.status_code == 529

        assert tried_while_closed == 6  # two requests, three attempts each
        assert slept_while_closed == [0.05, 0.1, 0.05, 0.1]
        # Five more requests, one upstream call each, and not a millisecond of backoff.
        assert len(harness.providers["mock_a"].received) == tried_while_closed + 5
        assert harness.sleeper.delays == slept_while_closed
        assert harness.last_context().failover_attempts == (
            "mock_a:breaker_open",
            "mock_a:breaker_open",
            "mock_a:upstream_status_529",
        )


# --------------------------------------------------------------------------------
# Mid-stream faults: the exclusion, stated as its own promise
# --------------------------------------------------------------------------------


#: Eight words, so the fixture is 3 opening frames + 8 deltas + 3 closing frames = 14
#: chunks and every cut point below lands somewhere different in the stream.
CUTTABLE = "an answer long enough to be cut anywhere"


@pytest.mark.parametrize("cut_at", [1, 4, 8, 12])
async def test_a_mid_stream_cut_always_surfaces_as_a_terminal_event(
    chain: GatewayHarness, cut_at: int
) -> None:
    """100% of the time, at every point in the stream. Never a silent truncation.

    This is §P8.H3's falsification condition run forwards: the experiment is falsified by
    *any* silent truncation reaching a caller, so the test asserts the positive at four
    different cut points — including ``12``, which is *after* the last text delta and
    after ``content_block_stop``, where the fragment reads as a finished answer and only
    the missing ``message_stop`` gives it away. That is the quiet failure H-008 named,
    and the one a gateway is most likely to let through.
    """
    chain.book.set("cut", MockScript.anthropic_stream(CUTTABLE, cut_after_chunks=cut_at))

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")

    events = [name for name, _ in event_pairs(response.content)]
    assert events[-1] == "error"
    assert "message_stop" not in events
    assert chain.last_context().outcome == UpstreamStreamCut.reason
    assert chain.last_context().failover_hops == 0
    assert chain.providers["mock_b"].received == []


async def test_a_burst_of_mid_stream_cuts_trips_the_breaker_and_recovers() -> None:
    """Trip and recovery, end to end, on a clock the test advances.

    The shape of the two-GPU demo without a GPU: the primary starts cutting streams, the
    breaker takes it out of rotation, the fallback carries the traffic, the primary comes
    back, and a single probe re-admits it.
    """
    clock = FakeClock()
    async with gateway_harness(chain=("mock_a", "mock_b"), clock=clock) as harness:
        harness.book.set(
            "cut@mock_a", MockScript.anthropic_stream("half an answer", cut_after_chunks=5)
        )
        harness.book.set("cut@mock_b", MockScript.anthropic_stream(ANSWER))

        for _ in range(5):
            await harness.post("/v1/messages", anthropic_request(stream=True), script="cut")
        assert harness.health.state_of("mock_a") != BREAKER_CLOSED

        # Tripped: the fallback now carries whole, clean answers.
        response = await harness.post("/v1/messages", anthropic_request(stream=True), script="cut")
        assert anthropic_text(response.content) == ANSWER
        assert b"event: error" not in response.content

        # The operator restarts the instance; one probe brings it back into rotation.
        harness.book.set("cut@mock_a", MockScript.anthropic_stream("the primary is back"))
        clock.advance(10.0)
        recovered = await harness.post("/v1/messages", anthropic_request(stream=True), script="cut")

        assert anthropic_text(recovered.content) == "the primary is back"
        assert harness.health.state_of("mock_a") == BREAKER_CLOSED
        assert harness.last_context().failover_hops == 0
