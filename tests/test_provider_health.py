"""The rolling window, the breaker built on it, and the probe that lets a provider back.

BUILD_PLAN §P6 asks for *"provider health tracking (rolling error/latency windows)"* and
*"a circuit breaker [that] trips a provider out of rotation after a threshold and probes
it back in"*. They are one mechanism seen twice — the window is the evidence, the breaker
is the verdict — so they are tested together, on a clock the test advances by hand.

Four properties carry the weight, and each is a way the design could be wrong:

* **it trips on a ratio over a window, not on a streak**, so a provider failing half its
  requests is caught as well as one failing all of them;
* **it never skips the last candidate**, because a breaker that could refuse the only
  remaining upstream converts a provider's outage into the gateway's — strictly worse
  than trying and failing;
* **one probe at a time**, so a recovered provider is not stampeded by everything that
  queued behind it while it was out;
* **a probe that succeeds clears the window**, without which the failures that tripped
  the breaker are still sitting in it and the next single blip re-trips immediately —
  the bug that turns a ten-second outage into a ten-minute one.
"""

from __future__ import annotations

import asyncio

import pytest

from headroom.policy.health import (
    BREAKER_CLOSED,
    BREAKER_HALF_OPEN,
    BREAKER_OPEN,
    HealthPolicy,
    HealthTracker,
)
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import FakeClock, GatewayHarness, gateway_harness


def _tracker(clock: FakeClock, **overrides: float | int) -> HealthTracker:
    policy = HealthPolicy(**overrides)  # type: ignore[arg-type]
    tracker = HealthTracker(policy, clock=clock)
    tracker.track("vllm_a", "openai_compat")
    return tracker


# --------------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------------


def test_the_published_thresholds_are_pinned() -> None:
    """Twenty samples, a floor of five, half of them failing, ten seconds out.

    Published numbers, so a change is deliberate. The cooldown in particular is chosen to
    be watchable: long enough that a tripped provider is not hammered, short enough that
    recovery is visible in a demo somebody is filming.
    """
    policy = HealthPolicy()
    assert (policy.window, policy.min_samples) == (20, 5)
    assert (policy.failure_ratio, policy.cooldown_s) == (0.5, 10.0)


def test_a_single_failure_does_not_trip_a_cold_provider() -> None:
    """The floor exists so the first request of a process's life cannot trip a breaker."""
    tracker = _tracker(FakeClock())

    tracker.record("vllm_a", ok=False, reason="upstream_timeout")

    assert tracker.state_of("vllm_a") == BREAKER_CLOSED
    assert tracker.snapshot("vllm_a").failure_ratio == 1.0


def test_the_breaker_trips_on_a_ratio_over_the_window_not_on_a_streak() -> None:
    """Alternating success and failure is a sick provider, and the ratio catches it.

    A consecutive-failures rule would never fire here, and this is the more dangerous
    provider: every failed request costs a timeout, and half of them succeed just often
    enough to look alive.
    """
    tracker = _tracker(FakeClock())

    for index in range(6):
        tracker.record("vllm_a", ok=index % 2 == 0, reason=None if index % 2 == 0 else "boom")

    snapshot = tracker.snapshot("vllm_a")
    assert snapshot.failure_ratio == 0.5
    assert tracker.state_of("vllm_a") == BREAKER_OPEN


def test_a_healthy_provider_stays_closed_however_long_it_runs() -> None:
    tracker = _tracker(FakeClock())

    for _ in range(50):
        tracker.record("vllm_a", ok=True, latency_ms=12.0)

    assert tracker.state_of("vllm_a") == BREAKER_CLOSED
    snapshot = tracker.snapshot("vllm_a")
    assert snapshot.total_successes == 50
    # The window is rolling, so it holds the last twenty and not all fifty.
    assert snapshot.samples == 20
    assert snapshot.p50_latency_ms == 12.0


def test_latency_is_recorded_only_for_completed_responses() -> None:
    """A provider that has only ever failed has no latency, and says so.

    Reporting ``0`` there would be H-025's mistake in a new column: a number that looks
    like a measurement and is really an absence.
    """
    tracker = _tracker(FakeClock())

    tracker.record("vllm_a", ok=False, reason="upstream_timeout")
    assert tracker.snapshot("vllm_a").p50_latency_ms is None

    for value in (10.0, 20.0, 30.0, 40.0):
        tracker.record("vllm_a", ok=True, latency_ms=value)
    snapshot = tracker.snapshot("vllm_a")
    assert snapshot.p50_latency_ms == 20.0
    assert snapshot.p95_latency_ms == 40.0


def test_the_last_error_is_kept_for_the_operator() -> None:
    tracker = _tracker(FakeClock())

    tracker.record("vllm_a", ok=False, reason="upstream_timeout")
    tracker.record("vllm_a", ok=False, reason="upstream_status_529")

    snapshot = tracker.snapshot("vllm_a")
    assert snapshot.last_error == "upstream_status_529"
    assert snapshot.consecutive_failures == 2


# --------------------------------------------------------------------------------
# The breaker: trip, cool down, probe, recover
# --------------------------------------------------------------------------------


def _trip(tracker: HealthTracker, name: str = "vllm_a", times: int = 5) -> None:
    for _ in range(times):
        tracker.record(name, ok=False, reason="upstream_timeout")


def test_an_open_breaker_refuses_until_the_cooldown_elapses() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    _trip(tracker)

    assert tracker.admit("vllm_a") is False
    clock.advance(9.9)
    assert tracker.admit("vllm_a") is False
    assert tracker.snapshot("vllm_a").reopen_in_s == pytest.approx(0.1)


def test_the_cooldown_admits_exactly_one_probe() -> None:
    """Half-open means *one* request finds out, not everything that queued up.

    Without the single-probe rule a recovered provider is stampeded the instant it
    reopens, which is how a breaker turns recovery into a second outage.
    """
    clock = FakeClock()
    tracker = _tracker(clock)
    _trip(tracker)
    clock.advance(10.0)

    assert tracker.admit("vllm_a") is True
    assert tracker.state_of("vllm_a") == BREAKER_HALF_OPEN
    assert tracker.admit("vllm_a") is False


def test_a_successful_probe_closes_the_breaker_and_clears_the_window() -> None:
    """Recovery has to be complete, or the next blip re-trips on the old evidence.

    The lifetime counters survive, because they are the record of what happened; the
    window does not, because it is the basis of a decision about *now*.
    """
    clock = FakeClock()
    tracker = _tracker(clock)
    _trip(tracker)
    clock.advance(10.0)
    tracker.admit("vllm_a")

    tracker.record("vllm_a", ok=True, latency_ms=5.0)

    snapshot = tracker.snapshot("vllm_a")
    assert snapshot.state == BREAKER_CLOSED
    assert snapshot.samples == 0
    assert snapshot.total_failures == 5
    # And one failure right afterwards does not put it straight back.
    tracker.record("vllm_a", ok=False, reason="boom")
    assert tracker.state_of("vllm_a") == BREAKER_CLOSED


def test_a_failed_probe_re_opens_for_another_full_cooldown() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    _trip(tracker)
    clock.advance(10.0)
    tracker.admit("vllm_a")

    tracker.record("vllm_a", ok=False, reason="still down")

    assert tracker.state_of("vllm_a") == BREAKER_OPEN
    assert tracker.admit("vllm_a") is False
    assert tracker.snapshot("vllm_a").reopen_in_s == pytest.approx(10.0)


def test_clearing_a_provider_closes_it_immediately() -> None:
    """The incident-response path: an operator who fixed it should not have to wait."""
    clock = FakeClock()
    tracker = _tracker(clock)
    _trip(tracker)

    tracker.clear("vllm_a")

    assert tracker.admit("vllm_a") is True
    assert tracker.state_of("vllm_a") == BREAKER_CLOSED
    assert tracker.snapshot("vllm_a").total_failures == 5


# --------------------------------------------------------------------------------
# The breaker in the chain
# --------------------------------------------------------------------------------


async def test_a_tripped_primary_is_skipped_and_the_hop_is_still_counted() -> None:
    """The steady state after an outage: no attempt wasted, and the row still says so.

    ``failover_hops`` counts *slots passed over*, not failures — so once the breaker has
    tripped and ``mock_a`` is no longer being tried at all, the ledger keeps reporting
    that the primary did not serve. The alternative (counting only real attempts) would
    make the hop count drop back to zero the moment the incident became persistent, which
    is exactly when somebody is reading it.
    """
    clock = FakeClock()
    async with gateway_harness(chain=("mock_a", "mock_b"), clock=clock) as harness:
        harness.book.set("fault@mock_a", MockScript.timeout())
        harness.book.set("fault@mock_b", MockScript.anthropic_message("served by mock_b"))

        for _ in range(5):
            await harness.post("/v1/messages", anthropic_request(), script="fault")
        assert harness.health.state_of("mock_a") == BREAKER_OPEN

        attempts_before = len(harness.providers["mock_a"].received)
        response = await harness.post("/v1/messages", anthropic_request(), script="fault")

        assert response.status_code == 200
        assert len(harness.providers["mock_a"].received) == attempts_before
        ctx = harness.last_context()
        assert ctx.failover_attempts == ("mock_a:breaker_open", "mock_b:ok")
        assert (ctx.failover_hops, ctx.failover_from) == (1, "mock_a")
        assert ctx.failover_error == "breaker_open"


async def test_the_breaker_never_skips_the_last_candidate() -> None:
    """A single-provider route stays served-or-honest, never refused by our own breaker.

    Tripping is only useful when there is somewhere else to go. With one provider a
    breaker that skipped would replace an upstream's 529 — which the caller can read and
    act on — with a gateway error about a decision they cannot see.
    """
    async with gateway_harness(chain=("mock_a",)) as harness:
        harness.book.set("down", MockScript.error(529, dialect="anthropic"))

        for _ in range(8):
            response = await harness.post("/v1/messages", anthropic_request(), script="down")

        assert harness.health.state_of("mock_a") == BREAKER_OPEN
        assert response.status_code == 529
        assert len(harness.providers["mock_a"].received) == 8


async def test_a_probe_re_admits_the_primary_after_the_cooldown() -> None:
    """The other half of the demo: bring the GPU back, and traffic returns to it."""
    clock = FakeClock()
    async with gateway_harness(chain=("mock_a", "mock_b"), clock=clock) as harness:
        harness.book.set("fault@mock_a", MockScript.timeout())
        harness.book.set("fault@mock_b", MockScript.anthropic_message("served by mock_b"))
        for _ in range(5):
            await harness.post("/v1/messages", anthropic_request(), script="fault")
        assert harness.health.state_of("mock_a") == BREAKER_OPEN

        # The operator restarts the instance, and the cooldown elapses.
        harness.book.set("fault@mock_a", MockScript.anthropic_message("served by mock_a"))
        clock.advance(10.0)
        response = await harness.post("/v1/messages", anthropic_request(), script="fault")

        assert "served by mock_a" in response.text
        assert harness.health.state_of("mock_a") == BREAKER_CLOSED
        assert harness.last_context().failover_hops == 0


async def test_a_mid_stream_cut_counts_against_the_provider_that_did_it(
    chain: GatewayHarness,
) -> None:
    """The delivery-side score, and the reason it is taken at the *end* of a stream.

    A `docker kill` on a live vLLM produces exactly this: headers arrived, bytes flowed,
    the connection died. Scoring the provider when its headers arrived would call that a
    success and the breaker would never trip during the demo it was built for.
    """
    chain.book.set("cut", MockScript.anthropic_stream("hello there", cut_after_chunks=4))

    for _ in range(5):
        await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")

    snapshot = chain.health.snapshot("mock_a")
    assert snapshot.total_failures == 5
    assert snapshot.last_error == "upstream_stream_cut"
    assert snapshot.state == BREAKER_OPEN


async def test_a_client_disconnect_is_not_the_providers_fault(chain: GatewayHarness) -> None:
    """The caller hung up. That is not evidence about an upstream, and it is not scored.

    Counting it would let one client with an aggressive timeout trip a breaker for every
    other tenant on the gateway.
    """
    gate = asyncio.Event()  # never set: the upstream stays open until the client quits
    chunks = list(MockScript.anthropic_stream("hello there").chunks)
    chain.book.set("slow", MockScript(chunks=chunks, gate=gate, gate_before_chunk=2))

    run = chain.start("/v1/messages", anthropic_request(stream=True), script="slow")
    await run.next_message()
    await run.next_body()
    run.disconnect()
    await run.finish()

    assert run.scope["state"]["ctx"].outcome == "client_disconnect"
    snapshot = chain.health.snapshot("mock_a")
    assert (snapshot.total_failures, snapshot.total_successes) == (0, 0)


async def test_an_upstream_client_error_does_not_count_as_ill_health(
    chain: GatewayHarness,
) -> None:
    """A 400 is a healthy provider correctly refusing a bad request.

    Counting it would let one tenant's malformed payloads trip a breaker for everybody
    else — a denial of service delivered through the health system.
    """
    chain.book.set("bad", MockScript.error(400, dialect="anthropic"))

    for _ in range(8):
        await chain.post("/v1/messages", anthropic_request(), script="bad")

    snapshot = chain.health.snapshot("mock_a")
    assert snapshot.state == BREAKER_CLOSED
    assert snapshot.total_failures == 0
    assert snapshot.total_successes == 8
