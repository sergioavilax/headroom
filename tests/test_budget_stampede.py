"""THE D-019 TEST. Sixty-four requests, a budget that fits five, and no oversubscription.

Backline's **D-019**: a budget gate checked spend, then added spend, in two separate
operations. Under concurrency every request passed the check before any of them had
recorded anything, and the budget was blown. The fix there was a reservation-based
redesign. Here reservations are not a fix, they are the design — and this file is where
that claim is either true or it is not.

**It runs against DynamoDB Local, and only against DynamoDB Local.** The in-memory store
implements the same semantics and would pass every assertion below, and passing would
mean nothing: its operations never suspend, so they cannot interleave, so there is no
race for a correct design to survive. The proof requires a real datastore, real network
round trips, and real threads — which is also why every DynamoDB call in
``headroom/db/dynamo.py`` is dispatched to a thread pool rather than blocking the loop.
A gate that blocked the event loop would *serialise* this test and pass it while being
completely wrong.

**The numbers are arranged to be checkable by hand.** The mock is scripted to report
exactly the usage the estimate assumed, so actual cost == estimate == one unit, and the
cap is exactly ``AFFORDABLE`` units. Five requests fit, fifty-nine do not, the budget
lands on precisely zero, and nothing rounds.

The second half of the file is the sabotage: the same stampede against a gate that
checks and then writes, which is D-019 reconstructed. It is expected to blow the budget,
and it does. Both runs' numbers go in docs/PHASE_LOG.md verbatim.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError

from headroom.core.budgets import (
    ADMIT_EXCEEDED,
    ADMIT_NO_BUDGET,
    ADMIT_RESERVED,
    RESERVATION_TTL_S,
    BudgetScope,
    Reservation,
    ReserveResult,
    from_picos,
    to_picos,
)
from headroom.core.ledger import LedgerQuery
from headroom.db.budgets import DynamoBudgetStore, _budget_of
from headroom.dialects.anthropic import ANTHROPIC
from headroom.metering.prices import load_price_book
from headroom.policy.budgets import Estimate, estimate_usd
from headroom.providers.mock import MockScript

from .support.budgets import dynamo_budget_store
from .support.harness import GatewayHarness, gateway_harness

#: Requests fired at once. Well above what the budget can afford, which is the point.
STAMPEDE = 64
#: How many of them the cap is sized to admit.
AFFORDABLE = 5

MODEL = "mock-model-1"
MAX_TOKENS = 32

#: The exact bytes every racer sends. Fixed so the estimate is one deterministic number
#: rather than something that drifts with a fixture's default text.
BODY: dict[str, Any] = {
    "model": MODEL,
    "max_tokens": MAX_TOKENS,
    "messages": [{"role": "user", "content": "stampede"}],
}
RAW_BODY = json.dumps(BODY, separators=(",", ":")).encode()

WHEN = datetime.now(UTC)


def projection() -> Estimate:
    """One request's estimate — and, by construction below, its actual cost too."""
    return estimate_usd(ANTHROPIC, BODY, RAW_BODY, model=MODEL, when=WHEN, prices=load_price_book())


def unit_cost() -> Decimal:
    return projection().usd


async def arm(harness: GatewayHarness) -> None:
    """Script the mock to report exactly the usage the estimate assumed, and set the cap.

    ``actual == estimate`` is the worst case a budget gate has to survive — every
    admitted request consuming its entire reserved ceiling — and it makes the closing
    arithmetic exact: five grants spend the cap to the picodollar, with nothing to
    round and no tolerance anywhere in the assertions.
    """
    projected = projection()
    harness.book.set(
        "stampede",
        MockScript.anthropic_message(
            "hello",
            input_tokens=projected.input_tokens,
            output_tokens=projected.output_tokens,
        ),
    )
    await harness.set_budget(projected.usd * AFFORDABLE)


@pytest.fixture
async def racing_gateway() -> AsyncIterator[GatewayHarness]:
    """A gateway whose budget store is DynamoDB Local, with a cap sized to the unit."""
    async with dynamo_budget_store() as store, gateway_harness(budgets=store) as harness:
        await arm(harness)
        yield harness


async def fire(harness: GatewayHarness) -> list[httpx.Response]:
    """All of them at once, and nothing is allowed to raise."""
    responses = await asyncio.gather(
        *(harness.post("/v1/messages", RAW_BODY, script="stampede") for _ in range(STAMPEDE))
    )
    return list(responses)


def report(label: str, responses: list[httpx.Response], budget: Any, cap: Decimal) -> str:
    served = sum(1 for response in responses if response.status_code == 200)
    refused = sum(1 for response in responses if response.status_code == 402)
    return (
        f"\n{label}\n"
        f"  requests fired         {len(responses)}\n"
        f"  served (200)           {served}\n"
        f"  refused (402)          {refused}\n"
        f"  budget                 ${cap:f}\n"
        f"  settled spend          ${budget.spent:f}\n"
        f"  still reserved         ${budget.reserved:f}\n"
        f"  remaining              ${budget.remaining:f}\n"
        f"  overspend              ${budget.spent - cap:f}\n"
        f"  spend / budget         {(budget.spent / cap):.2f}x\n"
    )


# --- the gate holds --------------------------------------------------------------------


async def test_the_stampede(racing_gateway: GatewayHarness) -> None:
    """Sixty-four concurrent requests against a budget that affords five.

    Four assertions, and the first is the one the phase exists for:

    1. **Total settled spend never exceeds the budget.**
    2. Exactly the affordable number succeed; the rest are refused with 402.
    3. The arithmetic is exact afterwards — ``remaining == budget - spent - reserved``,
       with no hold left behind by a request that was refused.
    4. Nothing crashed: every one of the sixty-four got a real HTTP answer.
    """
    cap = unit_cost() * AFFORDABLE

    responses = await fire(racing_gateway)

    budget = await racing_gateway.budget()
    print(report("ATOMIC GATE (shipped)", responses, budget, cap))

    served = [response for response in responses if response.status_code == 200]
    refused = [response for response in responses if response.status_code == 402]

    assert len(served) + len(refused) == STAMPEDE, "every request got a real answer"
    assert budget.spent <= cap, f"BUDGET BLOWN: settled ${budget.spent} against a ${cap} cap"
    assert len(served) == AFFORDABLE
    assert len(refused) == STAMPEDE - AFFORDABLE
    assert budget.spent == cap, "the cap is consumed exactly, to the picodollar"
    assert budget.reserved == 0, "no refused request left a hold behind"
    assert budget.remaining == 0
    assert budget.reservations == 0
    assert budget.identity_holds


async def test_every_refusal_is_a_ledger_row_and_no_provider_call(
    racing_gateway: GatewayHarness,
) -> None:
    """The fifty-nine refusals cost nothing upstream, and are still accounted for."""
    responses = await fire(racing_gateway)

    served = sum(1 for response in responses if response.status_code == 200)
    assert len(racing_gateway.provider.received) == served, (
        "a refused request must never reach the provider"
    )

    await racing_gateway.writer.drain()
    rows = await racing_gateway.ledger.list_entries(LedgerQuery(limit=1000))
    statuses = [row.budget_status for row in rows]
    assert len(rows) == STAMPEDE, "every request, served or refused, has a row"
    assert statuses.count("exceeded") == STAMPEDE - served
    assert statuses.count("reserved") == served


# --- the sabotage: D-019, reconstructed -------------------------------------------------


class NaiveBudgetStore(DynamoBudgetStore):
    """The D-019 bug, rebuilt on purpose.

    Identical to the shipped store in every respect except one: admission **reads the
    balance, decides, and then writes**, as two separate operations with an await
    between them. That is precisely what Backline's gate did, and it is precisely what
    the conditional write in ``headroom/db/budgets.py`` exists to make impossible.

    Nothing here is exotic. It is the obvious implementation — the one anybody writes
    first — and it passes every single-threaded test in this repo.
    """

    async def reserve(
        self, scope: BudgetScope, *, request_id: str, usd: Decimal, when: datetime
    ) -> ReserveResult:
        await self._ready()
        estimate = to_picos(usd, conservative=True)

        # 1. READ the balance.
        item = await self._get_item(scope)
        if item is None:
            return ReserveResult(status=ADMIT_NO_BUDGET)
        remaining = int(item["remaining_picos"]["N"])
        reserved = int(item["reserved_picos"]["N"])

        # 2. DECIDE. Correct in isolation, and a lie by the time it is acted on.
        if remaining < estimate:
            return ReserveResult(status=ADMIT_EXCEEDED, budget=_budget_of(item))

        # 3. WRITE. Unconditional, because the check "already passed".
        expires = int(when.timestamp()) + RESERVATION_TTL_S
        await self._client.call(
            "update_item",
            TableName=self.table,
            Key={"scope_id": {"S": scope.key}},
            UpdateExpression=(
                "SET remaining_picos = :remaining, reserved_picos = :reserved, "
                "reservations.#rid = :hold"
            ),
            ExpressionAttributeNames={"#rid": request_id},
            ExpressionAttributeValues={
                ":remaining": {"N": str(remaining - estimate)},
                ":reserved": {"N": str(reserved + estimate)},
                ":hold": {"M": {"p": {"N": str(estimate)}, "x": {"N": str(expires)}}},
            },
        )
        return ReserveResult(
            status=ADMIT_RESERVED,
            reservation=Reservation(
                scope=scope,
                request_id=request_id,
                usd=from_picos(estimate),
                window_id=str(item["window_id"]["S"]),
                expires_at=datetime.fromtimestamp(expires, tz=UTC),
            ),
        )


@pytest.fixture
async def sabotaged_gateway() -> AsyncIterator[GatewayHarness]:
    """The same gateway, the same budget, the same stampede — the D-019 gate."""
    async with dynamo_budget_store() as shipped:
        naive = NaiveBudgetStore(shipped.client, table=shipped.table)
        async with gateway_harness(budgets=naive) as harness:
            await arm(harness)
            yield harness


async def test_the_sabotage_blows_the_budget(
    sabotaged_gateway: GatewayHarness,
) -> None:
    """**This test asserts the bug.**

    It exists to prove the stampede above can actually catch what it claims to catch.
    A concurrency test that passes against both a correct and a broken implementation
    is not a test, it is a decoration — the Backline discipline of proving a test fails
    against the old code, applied to the one property this whole phase is about.

    Under the naive gate the budget is not merely exceeded, it is exceeded by an
    order of magnitude: every racer reads the same untouched balance, every racer
    decides it fits, and every racer is right about a world that no longer exists by
    the time it writes.
    """
    cap = unit_cost() * AFFORDABLE

    responses = await fire(sabotaged_gateway)

    budget = await sabotaged_gateway.budget()
    print(report("SABOTAGED GATE (D-019: check, then write)", responses, budget, cap))

    served = sum(1 for response in responses if response.status_code == 200)
    assert served > AFFORDABLE, (
        "the sabotage did not oversubscribe — the stampede is not exercising a race, "
        "and the atomic result above therefore proves nothing"
    )
    assert budget.spent > cap, (
        f"the naive gate settled ${budget.spent} against a ${cap} cap — this assertion "
        f"is the bug, and it firing is the point"
    )
    # And it does not merely overspend: the books stop balancing. Unconditional writes
    # of a stale `reserved` clobber each other, so the item ends up claiming a
    # *negative* amount is held and a positive amount remains, while twelve times the
    # cap has already been settled. That is the shape of a lost update, and it is why
    # "check, then write" cannot be repaired by checking harder.
    assert not budget.identity_holds, (
        "the naive gate produced consistent arithmetic, which means this run did not "
        "actually race and the sabotage is not reproducing D-019"
    )
    assert budget.reserved < 0


# --- the second sabotage: atomic, and still wrong ------------------------------------------


class LandedOnlyBudgetStore(DynamoBudgetStore):
    """BUILD_PLAN's own named sabotage: *"a deliberately naive landed-only gate"*.

    This one is the more instructive of the two, because it fixes the obvious bug and
    keeps the real one. The check and the deduction are a **single atomic conditional
    write** — everything the previous sabotage got wrong is right here — and the
    condition asks the wrong question:

        spent_picos <= :budget_minus_estimate

    Landed spend. Not committed spend. Between admission and settlement a request has
    cost nothing *yet*, so sixty-four concurrent requests all see ``spent == 0``, all
    pass a condition that is genuinely evaluated atomically, and all proceed.

    That is BUILD_PLAN §0.2 rule 5 in its exact words — *"the budget gate reads
    committed spend, reserved + landed, never landed alone"* — and it is why the shipped
    condition tests ``remaining``, a number that moves the instant a hold is taken.
    Atomicity is necessary and it is not sufficient.
    """

    async def reserve(
        self, scope: BudgetScope, *, request_id: str, usd: Decimal, when: datetime
    ) -> ReserveResult:
        await self._ready()
        estimate = to_picos(usd, conservative=True)
        item = await self._get_item(scope)
        if item is None:
            return ReserveResult(status=ADMIT_NO_BUDGET)
        ceiling = int(item["budget_picos"]["N"]) - estimate
        expires = int(when.timestamp()) + RESERVATION_TTL_S

        try:
            await self._client.call(
                "update_item",
                TableName=self.table,
                Key={"scope_id": {"S": scope.key}},
                # One atomic operation. The condition is simply the wrong one.
                ConditionExpression="spent_picos <= :ceiling",
                UpdateExpression=(
                    "SET remaining_picos = remaining_picos - :estimate, "
                    "reserved_picos = reserved_picos + :estimate, "
                    "reservations.#rid = :hold"
                ),
                ExpressionAttributeNames={"#rid": request_id},
                ExpressionAttributeValues={
                    ":ceiling": {"N": str(ceiling)},
                    ":estimate": {"N": str(estimate)},
                    ":hold": {"M": {"p": {"N": str(estimate)}, "x": {"N": str(expires)}}},
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            return ReserveResult(status=ADMIT_EXCEEDED, budget=_budget_of(item))

        return ReserveResult(
            status=ADMIT_RESERVED,
            reservation=Reservation(
                scope=scope,
                request_id=request_id,
                usd=from_picos(estimate),
                window_id=str(item["window_id"]["S"]),
                expires_at=datetime.fromtimestamp(expires, tz=UTC),
            ),
        )


@pytest.fixture
async def landed_only_gateway() -> AsyncIterator[GatewayHarness]:
    async with dynamo_budget_store() as shipped:
        landed = LandedOnlyBudgetStore(shipped.client, table=shipped.table)
        async with gateway_harness(budgets=landed) as harness:
            await arm(harness)
            yield harness


async def test_a_landed_only_gate_blows_the_budget_even_though_it_is_atomic(
    landed_only_gateway: GatewayHarness,
) -> None:
    """**This test also asserts a bug**, and a subtler one.

    Its arithmetic stays perfectly consistent throughout — ``remaining`` never
    disagrees with ``budget - spent - reserved`` — which is exactly why a landed-only
    gate survives code review. Everything about it looks right except the number it
    compares, and the money is gone all the same.

    It is also *partly effective*, which is the most dangerous property a broken guard
    can have. Settlements land while the stampede is still running, so ``spent`` does
    eventually climb past the ceiling and the tail of the burst really is refused. An
    operator watching this in production sees 402s in the logs and concludes the budget
    is working. It is working at roughly a seventh of its stated cap.
    """
    cap = unit_cost() * AFFORDABLE

    responses = await fire(landed_only_gateway)

    budget = await landed_only_gateway.budget()
    print(report("SABOTAGED GATE (atomic, but reads LANDED spend)", responses, budget, cap))

    served = sum(1 for response in responses if response.status_code == 200)
    assert served > AFFORDABLE
    assert budget.spent > cap, (
        f"a landed-only gate settled ${budget.spent} against a ${cap} cap — atomicity "
        f"alone does not make a budget gate correct"
    )
    # The books balance perfectly. That is the whole warning.
    assert budget.identity_holds
    assert budget.remaining < 0
