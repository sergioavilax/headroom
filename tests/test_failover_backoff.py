"""Jittered exponential backoff, verified without waiting for any of it.

BUILD_PLAN §P6 asks for *"retry with jittered exponential backoff"* and names no
parameters, so ``BackoffPolicy`` chooses them and this file is where they stop being
adjustable in silence. Everything here runs in microseconds: the executor takes its
``sleep`` as a parameter, and CI passes a recorder that appends the requested duration
and returns immediately (``tests/support/harness.py``). A backoff verified by actually
sleeping is a test somebody deletes the week the suite gets slow — and a jittered one
verified against a real RNG is a test that flakes.

Two properties are asserted separately because they are separately wrong-able:

* **the shape** — doubling, capped, uniform over ``[0, ceiling]`` — against the policy
  object, with the jitter supplied as a number;
* **the schedule** — *when* the executor sleeps at all — end to end, because the
  interesting decision is not the curve but the rule that a **fresh** provider is worth
  nothing to wait for and an already-failed one is (docs/DECISIONS.md H-050).
"""

from __future__ import annotations

import pytest

from headroom.policy.failover import BackoffPolicy
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness, gateway_harness

POLICY = BackoffPolicy()


# --------------------------------------------------------------------------------
# The curve
# --------------------------------------------------------------------------------


def test_the_published_parameters_are_the_ones_the_readme_can_quote() -> None:
    """50 ms, doubling, capped at 2 s. Pinned so a change is a diff and not a drift."""
    assert (POLICY.base_s, POLICY.multiplier, POLICY.cap_s) == (0.05, 2.0, 2.0)


@pytest.mark.parametrize(
    ("retry_index", "ceiling"),
    [(0, 0.05), (1, 0.1), (2, 0.2), (3, 0.4), (4, 0.8), (5, 1.6), (6, 2.0), (20, 2.0)],
)
def test_the_ceiling_doubles_and_then_stops(retry_index: int, ceiling: float) -> None:
    """Exponential to the cap, flat after it. The cap is what makes the bound arithmetic."""
    assert POLICY.ceiling_s(retry_index) == pytest.approx(ceiling)


@pytest.mark.parametrize("jitter", [0.0, 0.25, 0.5, 0.999])
def test_full_jitter_samples_the_whole_interval(jitter: float) -> None:
    """``uniform(0, ceiling)``, not ``ceiling`` — because the failure being prevented is
    a *synchronised* retry.

    A burst of requests that all failed at the same instant would all come back at the
    same instant under a fixed delay; the delay would merely move the stampede. Sampling
    the interval spreads it, which is the whole reason "jittered" is in the plan's
    sentence rather than just "exponential".
    """
    assert POLICY.delay_s(0, jitter) == pytest.approx(0.05 * jitter)
    assert 0.0 <= POLICY.delay_s(3, jitter) <= POLICY.ceiling_s(3)


def test_the_worst_case_is_a_number_not_a_feeling() -> None:
    """The bound an operator needs: how long can one request lie asleep?

    With ``MAX_ATTEMPT_LIMIT`` at 5, the most a single-provider route can spend waiting
    is four retries' worth of ceiling — 750 ms — and that is the ceiling, not the mean,
    since full jitter halves it in expectation.
    """
    assert POLICY.worst_case_s(0) == 0.0
    assert POLICY.worst_case_s(1) == pytest.approx(0.05)
    assert POLICY.worst_case_s(2) == pytest.approx(0.15)
    assert POLICY.worst_case_s(4) == pytest.approx(0.75)
    assert POLICY.worst_case_s(4) < 1.0


def test_a_custom_policy_is_honoured_term_by_term() -> None:
    """The parameters are data, so a deployment can change them without a fork."""
    slow = BackoffPolicy(base_s=1.0, multiplier=3.0, cap_s=5.0)

    assert [slow.ceiling_s(index) for index in range(4)] == [1.0, 3.0, 5.0, 5.0]


# --------------------------------------------------------------------------------
# The schedule — when the executor sleeps at all
# --------------------------------------------------------------------------------


async def test_moving_to_a_fresh_provider_costs_no_delay(chain: GatewayHarness) -> None:
    """The decision worth arguing with, asserted: **no** sleep on the way to a fallback.

    Nothing about ``mock_a`` being down suggests ``mock_b`` needs a moment to collect
    itself, and on a gateway whose product is first-token latency an unnecessary 50 ms is
    50 ms of the thing being sold. A fixed pre-fallback delay is what most retry
    libraries do, because they were written for one endpoint.
    """
    chain.book.set("fault@mock_a", MockScript.error(529, dialect="anthropic"))
    chain.book.set("fault@mock_b", MockScript.anthropic_message("served"))

    response = await chain.post("/v1/messages", anthropic_request(), script="fault")

    assert response.status_code == 200
    assert chain.sleeper.delays == []


async def test_coming_back_to_a_failed_provider_costs_the_backoff() -> None:
    """A single-provider route with ``max_attempts: 3`` — the retry half of the plan.

    The attempt sequence is ``a, a, a``, so both retries are against a provider that has
    already failed *this request*, and both pay. The recorded delays are the ceilings
    because the harness pins the jitter draw at 1.0 — the randomness is tested against
    the policy above, and the schedule is tested here.
    """
    async with gateway_harness(chain=("mock_a",), max_attempts=3) as harness:
        harness.book.set("down", MockScript.error(529, dialect="anthropic"))

        response = await harness.post("/v1/messages", anthropic_request(), script="down")

        assert response.status_code == 529
        assert harness.sleeper.delays == [pytest.approx(0.05), pytest.approx(0.1)]
        assert harness.sleeper.total_s <= POLICY.worst_case_s(2)
        assert len(harness.providers["mock_a"].received) == 3


async def test_a_wrapped_chain_pays_only_on_the_repeat() -> None:
    """``max_attempts: 3`` over two providers is ``a, b, a`` — one sleep, not two.

    Every fresh candidate before any repeat, and the delay charged exactly once, at the
    moment the executor comes back to somebody it has already heard from.
    """
    async with gateway_harness(chain=("mock_a", "mock_b"), max_attempts=3) as harness:
        harness.book.set("down@mock_a", MockScript.error(529, dialect="anthropic"))
        harness.book.set("down@mock_b", MockScript.error(503, dialect="anthropic"))

        response = await harness.post("/v1/messages", anthropic_request(), script="down")

        assert harness.sleeper.delays == [pytest.approx(0.05)]
        assert len(harness.providers["mock_a"].received) == 2
        assert len(harness.providers["mock_b"].received) == 1
        # Fail closed on the LAST failure, which after the wrap is mock_a's 529 again.
        assert response.status_code == 529
        assert harness.last_context().failover_attempts == (
            "mock_a:upstream_status_529",
            "mock_b:upstream_status_503",
            "mock_a:upstream_status_529",
        )
        assert harness.last_context().failover_hops == 2


async def test_a_route_with_no_chain_never_sleeps(gateway: GatewayHarness) -> None:
    """One attempt means no retry means no backoff — Phase 5's path, bit for bit.

    This is the assertion that keeps the phase additive: a deployment that has configured
    no failover has gained no latency, no timer, and no code on its hot path.
    """
    gateway.book.set("down", MockScript.error(529, dialect="anthropic"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="down")

    assert response.status_code == 529
    assert gateway.sleeper.delays == []
    assert len(gateway.provider.received) == 1


async def test_a_successful_request_never_sleeps(chain: GatewayHarness) -> None:
    """The happy path is untouched, which is the only path most requests take."""
    chain.book.set("ok", MockScript.anthropic_message("served"))

    await chain.post("/v1/messages", anthropic_request(), script="ok")

    assert chain.sleeper.delays == []
