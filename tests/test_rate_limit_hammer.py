"""THE HAMMER. Sixty-four requests at once, a bucket that holds five, and no over-admission.

This is the rate-limit analogue of ``test_budget_stampede.py``, and it makes the same
claim about a different primitive: **refill and consume are one operation, so a burst
cannot admit more than the bucket holds.** The scar is the same one — Backline's D-019,
where a gate read a balance, decided, and then wrote — and it reappears here in the noun
every rate limiter in the wild uses. "Read the bucket, add the tokens that have accrued
since ``refilled_at``, check, write it back" is the obvious implementation, it is what
most libraries do, and sabotage A below is it.

**It runs against DynamoDB Local, and only against DynamoDB Local.** The in-memory store
implements the same semantics and would pass every assertion here, and passing would mean
nothing: its operations never suspend, so they cannot interleave, so there is no race for
a correct design to survive.

**The numbers are arranged to be checkable by hand.** A limit of five per minute is an
emission interval of exactly twelve seconds, and the burst completes in milliseconds — so
no unit can refill during it, and the count is exact rather than approximate. Twelve
seconds of margin against a burst measured in hundredths is the whole reason this test
does not flake.

Three sabotages follow, and the last two are the interesting ones. Sabotage A fails the
hammer, which proves the hammer can catch what it claims to catch. Sabotages B and C
**pass** the hammer — they are genuinely atomic — and fail a property the hammer cannot
see, which is the same lesson the budget's landed-only gate taught one file over:
*atomicity is necessary and it is not sufficient.*
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError

from headroom.core.ledger import LedgerQuery
from headroom.core.limits import (
    DIM_REQUESTS,
    REFUSED_RATE_LIMITED,
    SCOPE_TENANT,
    WINDOW_NS,
    WINDOW_S,
    BucketKey,
    Consumption,
    RateLimitStore,
    available_units,
    burst_ns,
    emission_interval_ns,
    to_ns,
)
from headroom.db.buckets import DynamoRateLimitStore
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness, gateway_harness
from .support.limits import dynamo_bucket_store

#: Requests fired at once. Well above what the bucket can hold, which is the point.
HAMMER = 64
#: How many of them the limit is sized to admit. Five per minute is an emission interval
#: of exactly twelve seconds — see the module docstring.
ADMITTED = 5

BODY: dict[str, Any] = anthropic_request()
RAW_BODY = json.dumps(BODY, separators=(",", ":")).encode()

T0 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
BUCKET = BucketKey(scope_kind=SCOPE_TENANT, scope_id="acme", dimension=DIM_REQUESTS)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


async def fire(harness: GatewayHarness) -> list[httpx.Response]:
    """All of them at once, and nothing is allowed to raise."""
    responses = await asyncio.gather(
        *(harness.post("/v1/messages", RAW_BODY, script="hammer") for _ in range(HAMMER))
    )
    return list(responses)


def report(label: str, responses: list[httpx.Response], available: int, reset_after_s: int) -> str:
    served = sum(1 for response in responses if response.status_code == 200)
    refused = sum(1 for response in responses if response.status_code == 429)
    return (
        f"\n{label}\n"
        f"  requests fired         {len(responses)}\n"
        f"  served (200)           {served}\n"
        f"  refused (429)          {refused}\n"
        f"  bucket capacity        {ADMITTED} requests / {WINDOW_S}s\n"
        f"  emission interval      {emission_interval_ns(ADMITTED) // 10**9}s per request\n"
        f"  available after        {available}\n"
        f"  full again in          {reset_after_s}s\n"
        f"  served / capacity      {served / ADMITTED:.2f}x\n"
    )


async def arm(harness: GatewayHarness) -> None:
    harness.book.set("hammer", MockScript.anthropic_message("hello"))
    await harness.set_limits(requests_per_min=ADMITTED)


@pytest.fixture
async def racing_gateway() -> AsyncIterator[GatewayHarness]:
    """A gateway whose bucket store is DynamoDB Local, with a five-per-minute limit."""
    async with dynamo_bucket_store() as store, gateway_harness(limits=store) as harness:
        await arm(harness)
        yield harness


# --- the bucket holds ------------------------------------------------------------------


async def test_the_hammer(racing_gateway: GatewayHarness) -> None:
    """Sixty-four concurrent requests against a bucket that holds five.

    Five assertions, and the first is the one the phase exists for:

    1. **No more than the bucket holds is ever admitted.**
    2. Exactly the capacity succeeds; the rest are refused with 429.
    3. The bucket's arithmetic is exact afterwards: empty, and a full window from being
       full again — because five admissions at one instant is precisely one window of
       credit at twelve seconds each.
    4. Nothing crashed: every one of the sixty-four got a real HTTP answer.
    5. No refused request reached the provider.
    """
    started = datetime.now(UTC)
    responses = await fire(racing_gateway)
    ended = datetime.now(UTC)

    bucket = await racing_gateway.bucket(DIM_REQUESTS, limit_per_min=ADMITTED)
    print(report("ATOMIC BUCKET (shipped)", responses, bucket.available, bucket.reset_after_s))

    served = [response for response in responses if response.status_code == 200]
    refused = [response for response in responses if response.status_code == 429]

    assert len(served) + len(refused) == HAMMER, "every request got a real answer"
    assert len(served) <= ADMITTED, (
        f"BUCKET OVERDRAWN: {len(served)} admitted against a bucket holding {ADMITTED}"
    )
    assert len(served) == ADMITTED
    assert len(refused) == HAMMER - ADMITTED
    assert len(racing_gateway.provider.received) == ADMITTED

    # The arithmetic, to the emission interval. `tat` is exactly five intervals — one
    # whole window — after whichever racer took the cold branch, and that racer ran
    # somewhere inside the measured burst.
    assert bucket.available == 0
    assert started + timedelta(seconds=WINDOW_S) <= bucket.reset_at
    assert bucket.reset_at <= ended + timedelta(seconds=WINDOW_S)


async def test_every_refusal_is_a_ledger_row_and_carries_a_retry_after(
    racing_gateway: GatewayHarness,
) -> None:
    """The fifty-nine refusals cost nothing upstream, and are still accounted for."""
    responses = await fire(racing_gateway)

    refused = [response for response in responses if response.status_code == 429]
    assert len(refused) == HAMMER - ADMITTED
    for response in refused:
        # Every one of them can tell its caller when to come back, and every one of them
        # is provably ours rather than an upstream's.
        assert 1 <= int(response.headers["retry-after"]) <= WINDOW_S
        assert response.headers["x-headroom-error-source"] == "gateway"
        assert response.headers["x-headroom-ratelimit-scope"] == "tenant:requests"

    await racing_gateway.writer.drain()
    rows = await racing_gateway.ledger.list_entries(LedgerQuery(limit=1000))
    outcomes = [row.outcome for row in rows]
    assert len(rows) == HAMMER, "every request, served or refused, has a row"
    assert outcomes.count("rate_limited") == HAMMER - ADMITTED
    assert outcomes.count("ok") == ADMITTED


# --- sabotage A: D-019, in the noun every rate limiter uses ------------------------------


class ReadThenWriteBucketStore(DynamoRateLimitStore):
    """**The obvious token bucket, and the bug.**

    Store a count and a timestamp; on each request read the item, add the tokens that
    have accrued since ``refilled_at``, check whether the cost fits, write the result
    back. It is what a token bucket looks like in every tutorial, it passes every
    single-threaded test in this repo, and under concurrency every racer reads the same
    full bucket and every racer is right about a world that no longer exists by the time
    it writes.

    This is exactly Backline's D-019 — read, decide, write — which is why the shipped
    store holds a *time* instead of a count: a count can only be checked by reading it
    first, and a time can be checked by a ``ConditionExpression``.
    """

    async def consume(
        self, key: BucketKey, *, limit_per_min: int, cost: int, when: datetime
    ) -> Consumption:
        await self._ready()
        now = to_ns(when)
        interval = emission_interval_ns(limit_per_min)

        # 1. READ the bucket.
        item = await self._client.call(
            "get_item", TableName=self.table, Key={"bucket_id": {"S": key.id}}, ConsistentRead=True
        )
        stored = item.get("Item")
        tokens = int(stored["tokens"]["N"]) if stored else limit_per_min
        refilled_at = int(stored["refilled_at"]["N"]) if stored else now

        # 2. REFILL in application code, and DECIDE. Both correct in isolation, and both
        #    a lie by the time they are acted on.
        tokens = min(limit_per_min, tokens + (now - refilled_at) // interval)
        if tokens < cost:
            return Consumption(
                key=key,
                admitted=False,
                limit_per_min=limit_per_min,
                available=tokens,
                retry_after_s=1,
                refusal=REFUSED_RATE_LIMITED,
                cost=cost,
            )

        # 3. WRITE. Unconditional, because the check "already passed".
        await self._client.call(
            "update_item",
            TableName=self.table,
            Key={"bucket_id": {"S": key.id}},
            UpdateExpression="SET tokens = :tokens, refilled_at = :now",
            ExpressionAttributeValues={
                ":tokens": {"N": str(tokens - cost)},
                ":now": {"N": str(now)},
            },
        )
        return Consumption(
            key=key, admitted=True, limit_per_min=limit_per_min, available=tokens - cost, cost=cost
        )


@pytest.fixture
async def sabotaged_gateway() -> AsyncIterator[GatewayHarness]:
    async with dynamo_bucket_store() as shipped:
        naive = ReadThenWriteBucketStore(shipped.client, table=shipped.table)
        async with gateway_harness(limits=naive) as harness:
            await arm(harness)
            yield harness


async def test_the_sabotage_over_admits(sabotaged_gateway: GatewayHarness) -> None:
    """**This test asserts the bug.**

    It exists to prove the hammer above can actually catch what it claims to catch. A
    concurrency test that passes against both a correct and a broken implementation is
    not a test, it is a decoration — the Backline discipline of proving a test fails
    against the old code, applied to the one property this half of the phase is about.
    """
    responses = await fire(sabotaged_gateway)

    served = sum(1 for response in responses if response.status_code == 200)
    print(report("SABOTAGED (read, refill, decide, write)", responses, 0, WINDOW_S))

    assert served > ADMITTED, (
        "the sabotage did not over-admit — the hammer is not exercising a race, and the "
        "atomic result above therefore proves nothing"
    )
    assert len(sabotaged_gateway.provider.received) == served, (
        "and every one of those extra admissions really did reach a provider"
    )


# --- sabotage B: atomic, and still wrong (the fixed window) -------------------------------


class FixedWindowBucketStore(DynamoRateLimitStore):
    """A counter per clock-minute, reset when the minute changes. **Atomic, and wrong.**

    This is the most common "rate limiter" in production anywhere, and everything the
    previous sabotage got wrong is right here: the check and the increment are a single
    conditional write, evaluated against committed state, with no read to be stale. It
    passes the hammer. It is not a token bucket, and the difference has a name.

    A fixed window admits its whole limit at ``t=59s`` and its whole limit again at
    ``t=61s``: **twice the configured rate, in two seconds, forever, at every minute
    boundary.** The limit is nominally per minute and is actually per minute-as-measured-
    from-a-clock-nobody-agreed-on. A token bucket has no boundary to straddle, because it
    has no window — only a rate and a capacity.
    """

    async def consume(
        self, key: BucketKey, *, limit_per_min: int, cost: int, when: datetime
    ) -> Consumption:
        await self._ready()
        window = str(to_ns(when) // WINDOW_NS)

        try:
            item = (
                await self._update(
                    Key={"bucket_id": {"S": key.id}},
                    # One atomic operation. The semantics are simply the wrong ones.
                    ConditionExpression="window_id = :w AND used <= :ceiling",
                    UpdateExpression="SET used = used + :cost",
                    ExpressionAttributeValues={
                        ":w": {"S": window},
                        ":ceiling": {"N": str(limit_per_min - cost)},
                        ":cost": {"N": str(cost)},
                    },
                    ReturnValues="ALL_NEW",
                )
            )["Attributes"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            old = exc.response.get("Item")
            if old and old.get("window_id", {}).get("S") == window:
                return Consumption(
                    key=key,
                    admitted=False,
                    limit_per_min=limit_per_min,
                    available=limit_per_min - int(old["used"]["N"]),
                    retry_after_s=1,
                    refusal=REFUSED_RATE_LIMITED,
                    cost=cost,
                )
            # A new minute: start the counter again. Also atomic, and also the bug.
            try:
                item = (
                    await self._update(
                        Key={"bucket_id": {"S": key.id}},
                        ConditionExpression="attribute_not_exists(bucket_id) OR window_id <> :w",
                        UpdateExpression="SET window_id = :w, used = :cost",
                        ExpressionAttributeValues={":w": {"S": window}, ":cost": {"N": str(cost)}},
                        ReturnValues="ALL_NEW",
                    )
                )["Attributes"]
            except ClientError:
                return await self.consume(key, limit_per_min=limit_per_min, cost=cost, when=when)
        return Consumption(
            key=key,
            admitted=True,
            limit_per_min=limit_per_min,
            available=limit_per_min - int(item["used"]["N"]),
            cost=cost,
        )


@pytest.fixture
async def fixed_window_gateway() -> AsyncIterator[GatewayHarness]:
    async with dynamo_bucket_store() as shipped:
        fixed = FixedWindowBucketStore(shipped.client, table=shipped.table)
        async with gateway_harness(limits=fixed) as harness:
            await arm(harness)
            yield harness


async def test_a_fixed_window_passes_the_hammer(fixed_window_gateway: GatewayHarness) -> None:
    """**This test asserts that a broken limiter looks perfect** under the headline test.

    Which is the point of running it: the hammer proves atomicity, and atomicity is not
    the whole claim. Everything a reviewer would check about this implementation passes.
    """
    responses = await fire(fixed_window_gateway)

    served = sum(1 for response in responses if response.status_code == 200)
    print(report("SABOTAGED (atomic, but a fixed window)", responses, 0, WINDOW_S))
    assert served == ADMITTED, "a fixed window really is atomic — that is the trap"


async def _admitted_across_the_boundary(store: RateLimitStore, *, limit: int) -> int:
    """How many requests fit in the two seconds straddling a clock-minute boundary.

    ``T0`` is exactly on a minute, so ``at(59)`` and ``at(61)`` sit either side of the
    next one. A rate limiter worth the name admits at most ``limit`` across a two-second
    span, whatever the wall clock happens to say.
    """
    admitted = 0
    for when in (at(59), at(61)):
        for _ in range(limit * 2):
            outcome = await store.consume(BUCKET, limit_per_min=limit, cost=1, when=when)
            if not outcome.admitted:
                break
            admitted += 1
    return admitted


async def test_the_shipped_bucket_has_no_boundary_to_straddle() -> None:
    """Five per minute means five in any two seconds, including *those* two seconds."""
    async with dynamo_bucket_store() as shipped:
        assert await _admitted_across_the_boundary(shipped, limit=ADMITTED) == ADMITTED


async def test_the_fixed_window_admits_double_the_limit_across_the_boundary() -> None:
    """**This test asserts the bug the hammer cannot see.**

    Ten requests in two seconds against a five-per-minute limit: twice the configured rate,
    at every minute boundary, forever. Nothing about the implementation is un-atomic —
    the counter is perfect. It is counting the wrong thing.
    """
    async with dynamo_bucket_store() as shipped:
        fixed = FixedWindowBucketStore(shipped.client, table=shipped.table)
        admitted = await _admitted_across_the_boundary(fixed, limit=ADMITTED)

    print(
        f"\nBOUNDARY BURST (limit {ADMITTED}/min, two seconds spanning a minute)\n"
        f"  shipped token bucket   {ADMITTED}\n"
        f"  fixed-window counter   {admitted}\n"
    )
    assert admitted == 2 * ADMITTED


# --- sabotage C: atomic, one write, and still wrong (no clamp) ----------------------------


class UnclampedBucketStore(DynamoRateLimitStore):
    """GCRA without ``max(tat, now)``: **one** conditional write, and unbounded credit.

    This is the tempting simplification of the shipped store, and the reason
    ``headroom/db/buckets.py`` has two branches instead of one. The condition and the
    update are a single atomic operation — strictly simpler than what ships, and one
    fewer round trip on an idle bucket — and it passes the hammer.

    What it loses is the clamp. When a bucket has been idle, ``tat`` falls behind the
    clock, and every admission merely adds to a number that is already in the past. The
    burst it will then admit is proportional to **how long the bucket was idle**, not to
    its capacity. An hour of quiet buys an hour of traffic, instantly.
    """

    async def consume(
        self, key: BucketKey, *, limit_per_min: int, cost: int, when: datetime
    ) -> Consumption:
        await self._ready()
        interval = emission_interval_ns(limit_per_min)
        now = to_ns(when)
        charge = cost * interval
        ceiling = now + burst_ns(limit_per_min) - charge

        try:
            item = (
                await self._update(
                    Key={"bucket_id": {"S": key.id}},
                    ConditionExpression="attribute_not_exists(tat) OR tat <= :ceiling",
                    UpdateExpression="SET tat = if_not_exists(tat, :now) + :charge",
                    ExpressionAttributeValues={
                        ":ceiling": {"N": str(ceiling)},
                        ":now": {"N": str(now)},
                        ":charge": {"N": str(charge)},
                    },
                    ReturnValues="ALL_NEW",
                )
            )["Attributes"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            return Consumption(
                key=key,
                admitted=False,
                limit_per_min=limit_per_min,
                available=0,
                retry_after_s=1,
                refusal=REFUSED_RATE_LIMITED,
                cost=cost,
            )
        return Consumption(
            key=key,
            admitted=True,
            limit_per_min=limit_per_min,
            available=available_units(int(item["tat"]["N"]), now, limit_per_min),
            cost=cost,
        )


@pytest.fixture
async def unclamped_gateway() -> AsyncIterator[GatewayHarness]:
    async with dynamo_bucket_store() as shipped:
        unclamped = UnclampedBucketStore(shipped.client, table=shipped.table)
        async with gateway_harness(limits=unclamped) as harness:
            await arm(harness)
            yield harness


async def test_an_unclamped_bucket_passes_the_hammer(unclamped_gateway: GatewayHarness) -> None:
    """**Also asserts that a broken limiter looks perfect.** A fresh bucket has no idle
    credit to accumulate, so on the hammer's own terms this is indistinguishable."""
    responses = await fire(unclamped_gateway)

    served = sum(1 for response in responses if response.status_code == 200)
    print(report("SABOTAGED (atomic, one write, no clamp)", responses, 0, WINDOW_S))
    assert served == ADMITTED


async def _burst_after_an_hour_of_quiet(store: RateLimitStore, *, limit: int) -> int:
    """Drain the bucket, wait an hour on a controlled clock, then drain it again."""
    while (await store.consume(BUCKET, limit_per_min=limit, cost=1, when=T0)).admitted:
        pass
    admitted = 0
    while (await store.consume(BUCKET, limit_per_min=limit, cost=1, when=at(3600))).admitted:
        admitted += 1
        if admitted > 1000:  # a runaway sabotage must not become a runaway test
            break
    return admitted


async def test_the_shipped_bucket_never_holds_more_than_its_capacity() -> None:
    """An hour of quiet buys a burst of five, which is what "capacity" means."""
    async with dynamo_bucket_store() as shipped:
        assert await _burst_after_an_hour_of_quiet(shipped, limit=ADMITTED) == ADMITTED


async def test_an_unclamped_bucket_accumulates_an_hours_worth_of_credit() -> None:
    """**This test asserts the bug the hammer cannot see**, and it is a big one.

    An hour idle, then 299 requests admitted instantly against a five-per-minute limit —
    sixty times the configured rate, because the bucket's ``tat`` spent that hour falling
    further and further behind the clock with nothing to stop it. That single missing
    ``max()`` is what the shipped store's second conditional write buys.
    """
    async with dynamo_bucket_store() as shipped:
        unclamped = UnclampedBucketStore(shipped.client, table=shipped.table)
        admitted = await _burst_after_an_hour_of_quiet(unclamped, limit=ADMITTED)

    print(
        f"\nBURST AFTER AN HOUR IDLE (limit {ADMITTED}/min)\n"
        f"  shipped token bucket   {ADMITTED}\n"
        f"  unclamped GCRA         {admitted}\n"
    )
    assert admitted > ADMITTED * 10, (
        "the unclamped store did not accumulate credit — the clamp it is missing is not "
        "being exercised, and the shipped store's second branch proves nothing"
    )
