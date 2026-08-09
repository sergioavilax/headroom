"""One contract suite over both rate-limit stores — the H-021 shape, applied to time.

Every assertion below runs against ``InMemoryRateLimitStore`` **and** against
``DynamoRateLimitStore`` on DynamoDB Local, in the same run. A behaviour asserted here is
asserted of the implementation that ships, or it is not asserted at all — which matters
more than usual here, because the two implementations express the *same* arithmetic in
very different ways: one is a ``max()`` and a comparison in Python, the other is two
mutually exclusive conditional writes.

**Every clock in this file is an argument.** ``consume`` and ``state`` take the moment
they are being asked about, so refill is tested by advancing a variable rather than by
sleeping — and the tests that pin the exact refill rate would otherwise be the flakiest
in the repo. There is no ``time.sleep`` anywhere in this suite, and
``tests/test_pytest_policy.py`` is the reason there never will be.

What this file cannot do is prove the design is race-free — a suite that awaits one call
at a time cannot interleave anything. That is ``test_rate_limit_hammer.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from headroom.core.limits import (
    DIM_REQUESTS,
    DIM_TOKENS,
    REFUSED_EXCEEDS_CAPACITY,
    REFUSED_RATE_LIMITED,
    SCOPE_KEY,
    SCOPE_TENANT,
    WINDOW_NS,
    WINDOW_S,
    BucketKey,
    RateLimit,
    RateLimitStore,
    available_units,
    burst_ns,
    emission_interval_ns,
)
from headroom.db.memory import InMemoryRateLimitStore

from .support.limits import dynamo_bucket_store

T0 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

BUCKET = BucketKey(
    scope_kind=SCOPE_TENANT, scope_id="11111111-2222-3333-4444-555555555555", dimension=DIM_REQUESTS
)


@pytest.fixture(params=["memory", "dynamodb"])
async def buckets(request: pytest.FixtureRequest) -> AsyncIterator[RateLimitStore]:
    """The same contract, twice."""
    if request.param == "memory":
        in_memory: RateLimitStore = InMemoryRateLimitStore()
        yield in_memory
        await in_memory.aclose()
        return
    async with dynamo_bucket_store() as store:
        yield store


def at(seconds: float) -> datetime:
    """``T0`` plus an offset. The only clock in this file."""
    return T0 + timedelta(seconds=seconds)


async def take(
    buckets: RateLimitStore, *, limit: int, cost: int = 1, when: datetime, key: BucketKey = BUCKET
) -> bool:
    outcome = await buckets.consume(key, limit_per_min=limit, cost=cost, when=when)
    return outcome.admitted


async def drain(buckets: RateLimitStore, *, limit: int, when: datetime) -> int:
    """Consume until the bucket refuses, at one instant. Returns how many fitted."""
    admitted = 0
    while await take(buckets, limit=limit, when=when):
        admitted += 1
        assert admitted <= limit * 4, "a bucket that never refuses is not a bucket"
    return admitted


# --- the arithmetic, before any store ---------------------------------------------------


def test_the_emission_interval_rounds_up_so_the_limiter_never_leaks() -> None:
    """A limit that divides the minute exactly is exact; one that does not errs strict.

    The direction is the point. Rounding *down* would emit slightly faster than the
    configured rate — a limiter that leaks — and the leak would compound over a long
    burst. Rounding up over-charges by at most one nanosecond per unit.
    """
    assert emission_interval_ns(60) == WINDOW_NS // 60 == 1_000_000_000
    assert emission_interval_ns(1) == WINDOW_NS
    # 60e9 / 7 is 8571428571.43…, and the stored interval is the ceiling of it.
    assert emission_interval_ns(7) == 8_571_428_572
    assert emission_interval_ns(7) * 7 > WINDOW_NS


def test_a_limit_of_zero_or_less_is_refused_by_the_arithmetic_itself() -> None:
    """Belt and braces with ``migrations/0004``'s CHECK and the admin API's ``ge=1``.

    A zero limit has no emission interval, so there is no sensible bucket to build; the
    failure is loud here rather than a division by zero three frames deeper.
    """
    for bad in (0, -1):
        with pytest.raises(ValueError, match="at least 1 per minute"):
            emission_interval_ns(bad)


def test_capacity_is_one_windows_worth_and_available_is_clamped_at_both_ends() -> None:
    now = 1_000 * WINDOW_NS
    assert burst_ns(60) == 60 * emission_interval_ns(60)
    # A bucket whose `tat` is far in the past is full, not overflowing.
    assert available_units(0, now, 60) == 60
    # One whose `tat` is far in the future is empty, not negative.
    assert available_units(now + 10 * WINDOW_NS, now, 60) == 0
    # And exactly at rest, it is full.
    assert available_units(now, now, 60) == 60


# --- a bucket, end to end -----------------------------------------------------------------


async def test_a_fresh_bucket_is_full_and_writes_nothing_to_be_full(
    buckets: RateLimitStore,
) -> None:
    """*Absent* and *at rest* are the same state — which is what makes the TTL safe."""
    state = await buckets.state(BUCKET, limit_per_min=5, when=T0)
    assert state.available == 5
    assert state.reset_after_s == 0
    assert state.reset_at == T0
    # Reading it did not bring it into being.
    assert await buckets.clear(BUCKET) is False


async def test_a_burst_takes_exactly_the_capacity_and_then_refuses(
    buckets: RateLimitStore,
) -> None:
    """Five, then no more — at one instant, so no refill can blur the count."""
    assert await drain(buckets, limit=5, when=T0) == 5

    refused = await buckets.consume(BUCKET, limit_per_min=5, cost=1, when=T0)
    assert refused.admitted is False
    assert refused.refusal == REFUSED_RATE_LIMITED
    assert refused.available == 0
    assert refused.limit_per_min == 5
    # A full bucket drained at one instant needs a full emission interval to yield one
    # more unit: 60s / 5 = 12s.
    assert refused.retry_after_s == WINDOW_S // 5


async def test_refill_is_one_unit_per_emission_interval(buckets: RateLimitStore) -> None:
    """The rate itself, on a controlled clock. No sleeps, and no tolerance."""
    await drain(buckets, limit=5, when=T0)
    interval_s = WINDOW_S / 5

    # A hair before the interval elapses: still nothing.
    assert await take(buckets, limit=5, when=at(interval_s - 0.001)) is False
    # Exactly one interval later: exactly one unit, and then empty again.
    assert await take(buckets, limit=5, when=at(interval_s)) is True
    assert await take(buckets, limit=5, when=at(interval_s)) is False
    # Three more intervals: exactly three units.
    assert await drain(buckets, limit=5, when=at(interval_s * 4)) == 3


async def test_a_full_window_of_idleness_refills_to_capacity_and_no_further(
    buckets: RateLimitStore,
) -> None:
    """**The property the cold branch exists for.**

    A bucket idle for an hour must admit a burst of *capacity*, not an hour's worth of
    traffic. Without the ``max(tat, now)`` clamp — which DynamoDB cannot express in a
    condition, and which the shipped store recovers with its second conditional write —
    ``tat`` would sit an hour in the past and this test would admit 300 requests instead
    of 5. That is sabotage C in ``test_rate_limit_hammer.py``.
    """
    await drain(buckets, limit=5, when=T0)

    assert await drain(buckets, limit=5, when=at(WINDOW_S)) == 5
    assert await drain(buckets, limit=5, when=at(3600)) == 5
    assert (await buckets.state(BUCKET, limit_per_min=5, when=at(3600))).available == 0


async def test_the_bucket_meters_a_sustained_stream_at_the_configured_rate(
    buckets: RateLimitStore,
) -> None:
    """Sixty a minute means one a second, forever — not sixty and then a wall.

    A burst of the full capacity, then one request per second for a minute: every one of
    them fits, because each second has emitted exactly one unit's worth of credit.
    """
    assert await drain(buckets, limit=60, when=T0) == 60
    for second in range(1, WINDOW_S + 1):
        assert await take(buckets, limit=60, when=at(second)) is True, f"refused at {second}s"
        assert await take(buckets, limit=60, when=at(second)) is False, (
            f"over-admitted at {second}s"
        )


async def test_retry_after_is_the_true_wait_and_never_zero(buckets: RateLimitStore) -> None:
    """A `retry-after` a caller can act on, and never an invitation to retry at once."""
    await drain(buckets, limit=6, when=T0)  # interval = 10s

    assert (await buckets.consume(BUCKET, limit_per_min=6, cost=1, when=T0)).retry_after_s == 10
    assert (await buckets.consume(BUCKET, limit_per_min=6, cost=1, when=at(5))).retry_after_s == 5
    # 0.4s of wait rounds up to 1, not down to 0: a `retry-after: 0` is the retry storm a
    # 429 exists to damp.
    assert (await buckets.consume(BUCKET, limit_per_min=6, cost=1, when=at(9.6))).retry_after_s == 1


async def test_a_costly_request_takes_proportionally_more(buckets: RateLimitStore) -> None:
    """The tokens dimension: one request can be worth thousands of units."""
    tokens = BucketKey(scope_kind=SCOPE_TENANT, scope_id="acme", dimension=DIM_TOKENS)

    first = await buckets.consume(tokens, limit_per_min=10_000, cost=4_000, when=T0)
    assert first.admitted is True
    assert first.available == 6_000

    second = await buckets.consume(tokens, limit_per_min=10_000, cost=4_000, when=T0)
    assert second.admitted is True
    assert second.available == 2_000

    third = await buckets.consume(tokens, limit_per_min=10_000, cost=4_000, when=T0)
    assert third.admitted is False
    assert third.available == 2_000, "a refusal consumes nothing"
    assert third.cost == 4_000


async def test_a_request_larger_than_the_whole_bucket_is_refused_with_no_retry_after(
    buckets: RateLimitStore,
) -> None:
    """Waiting cannot help, so no ``retry-after`` is invented (H-038).

    Reachable only on the tokens dimension: a request costs one unit of the requests
    bucket, and every limit is at least 1.
    """
    tokens = BucketKey(scope_kind=SCOPE_TENANT, scope_id="acme", dimension=DIM_TOKENS)

    outcome = await buckets.consume(tokens, limit_per_min=1_000, cost=4_096, when=T0)
    assert outcome.admitted is False
    assert outcome.refusal == REFUSED_EXCEEDS_CAPACITY
    assert outcome.retry_after_s is None
    # And it did not consume anything on its way to being impossible.
    assert (await buckets.state(tokens, limit_per_min=1_000, when=T0)).available == 1_000


async def test_buckets_are_independent_across_scopes_and_dimensions(
    buckets: RateLimitStore,
) -> None:
    """Four buckets, one request: draining any one leaves the other three untouched.

    This is the property that makes "per key and per tenant" cost four *independent*
    facts rather than one shared counter with four names.
    """
    others = [
        BucketKey(scope_kind=SCOPE_TENANT, scope_id=BUCKET.scope_id, dimension=DIM_TOKENS),
        BucketKey(scope_kind=SCOPE_KEY, scope_id="a-key", dimension=DIM_REQUESTS),
        BucketKey(scope_kind=SCOPE_KEY, scope_id="a-key", dimension=DIM_TOKENS),
    ]
    assert len({bucket.id for bucket in [BUCKET, *others]}) == 4

    await drain(buckets, limit=5, when=T0)
    assert (await buckets.state(BUCKET, limit_per_min=5, when=T0)).available == 0
    for other in others:
        assert (await buckets.state(other, limit_per_min=5, when=T0)).available == 5


async def test_a_limit_change_reprices_the_future_without_resetting_the_bucket(
    buckets: RateLimitStore,
) -> None:
    """``tat`` is an absolute time, so a changed limit is a changed *price per unit*.

    Worth stating precisely, because it is the property ``/admin/limits`` relies on when
    it changes a limit without touching the bucket, and it is not the obvious one: a
    bucket's capacity **in time** is always one window, whatever the limit. So a drained
    bucket stays drained across a limit change — its ``tat`` is a minute ahead either way
    — but every unit now costs a different amount of that minute, so the *wait* collapses
    in proportion.

    A drained 5-per-minute bucket needs 12 seconds for its next request. Re-read as a
    600-per-minute bucket, the same stored number needs 0.1 seconds.
    """
    await drain(buckets, limit=5, when=T0)
    assert await take(buckets, limit=5, when=T0) is False
    assert await take(buckets, limit=600, when=T0) is False

    slow = await buckets.consume(BUCKET, limit_per_min=5, cost=1, when=T0)
    fast = await buckets.consume(BUCKET, limit_per_min=600, cost=1, when=T0)
    assert slow.retry_after_s == 12
    assert fast.retry_after_s == 1  # 0.1s, floored at the smallest honest header value

    # And a tenth of a second later the raised limit really has emitted a unit, where the
    # old one would still have eleven and a half seconds to go.
    assert await take(buckets, limit=600, when=at(0.1)) is True
    assert await take(buckets, limit=5, when=at(0.1)) is False


async def test_clearing_a_bucket_makes_it_full_again(buckets: RateLimitStore) -> None:
    """The operator's escape hatch: a limit lowered by mistake is not an hour's wait."""
    await drain(buckets, limit=5, when=T0)
    assert await take(buckets, limit=5, when=T0) is False

    assert await buckets.clear(BUCKET) is True
    assert (await buckets.state(BUCKET, limit_per_min=5, when=T0)).available == 5
    assert await take(buckets, limit=5, when=T0) is True

    assert await buckets.clear(BUCKET) is True
    assert await buckets.clear(BUCKET) is False, "clearing twice is not an error"


async def test_state_reports_when_the_bucket_will_be_full(buckets: RateLimitStore) -> None:
    """What ``/admin/limits`` shows an operator, and it is a real prediction."""
    await drain(buckets, limit=5, when=T0)

    state = await buckets.state(BUCKET, limit_per_min=5, when=T0)
    assert state.available == 0
    assert state.reset_after_s == WINDOW_S
    assert state.reset_at == T0 + timedelta(seconds=WINDOW_S)
    # Half a window later, half the capacity is back and the wait has halved.
    halfway = await buckets.state(BUCKET, limit_per_min=5, when=at(WINDOW_S / 2))
    assert halfway.available == 2  # floored: 2.5 units of credit is two whole requests
    assert halfway.reset_after_s == WINDOW_S // 2


# --- the configuration record ---------------------------------------------------------


def test_an_unconfigured_scope_is_never_consumed_from() -> None:
    """``configured`` is what keeps this phase free for every tenant nobody capped."""
    assert RateLimit().configured is False
    assert RateLimit(requests_per_min=10).configured is True
    assert RateLimit(tokens_per_min=10).configured is True


def test_a_limit_record_answers_per_dimension() -> None:
    limits = RateLimit(requests_per_min=10, tokens_per_min=None)
    assert limits.per_min(DIM_REQUESTS) == 10
    assert limits.per_min(DIM_TOKENS) is None
    with pytest.raises(ValueError, match="unknown rate-limit dimension"):
        limits.per_min("bytes")


def test_a_bucket_key_is_greppable_and_unambiguous() -> None:
    """The id ends up in DynamoDB; the label ends up in a header and a log line."""
    key = BucketKey(scope_kind=SCOPE_TENANT, scope_id="abc", dimension=DIM_TOKENS)
    assert key.id == "tenant#abc#tokens"
    assert key.label == "tenant:tokens"
