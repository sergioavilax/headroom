"""One contract suite over both budget stores — the H-021 shape, applied to money.

Every assertion below runs against ``InMemoryBudgetStore`` **and** against
``DynamoBudgetStore`` on DynamoDB Local, in the same run. A behaviour asserted here is
asserted of the implementation that ships, or it is not asserted at all.

The identity ``remaining == budget - spent - reserved`` is checked after almost every
operation, and deliberately so: it is not a property of the arithmetic in these tests,
it is a property of the *update expressions*, and the only way to notice a wrong one is
to look immediately after every write.

What this file cannot do is prove the design is race-free — a dict cannot interleave and
neither can a suite that awaits one call at a time. That is ``test_budget_stampede.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from headroom.core.budgets import (
    ADMIT_EXCEEDED,
    ADMIT_NO_BUDGET,
    ADMIT_RESERVED,
    PICOS_PER_USD,
    RESERVATION_TTL_S,
    WINDOW_MONTHLY,
    WINDOW_TOTAL,
    Budget,
    BudgetScope,
    BudgetStore,
    from_picos,
    to_picos,
    window_for,
)
from headroom.db.memory import InMemoryBudgetStore
from headroom.metering.cost import USD_QUANTUM

from .support.budgets import dynamo_budget_store

AUGUST = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
SCOPE = BudgetScope.tenant("11111111-2222-3333-4444-555555555555")


@pytest.fixture(params=["memory", "dynamodb"])
async def budgets(request: pytest.FixtureRequest) -> AsyncIterator[BudgetStore]:
    """The same contract, twice."""
    if request.param == "memory":
        in_memory: BudgetStore = InMemoryBudgetStore()
        yield in_memory
        await in_memory.aclose()
        return
    async with dynamo_budget_store() as store:
        yield store


async def capped(
    budgets: BudgetStore, usd: str = "10.00", *, window: str = WINDOW_MONTHLY
) -> Budget:
    return await budgets.set_budget(SCOPE, usd=Decimal(usd), window=window, when=AUGUST)


def assert_identity(budget: Budget) -> None:
    """``remaining == budget - spent - reserved``, the whole bookkeeping contract."""
    assert budget.identity_holds, (
        f"broken identity: remaining {budget.remaining} != {budget.usd} - "
        f"{budget.spent} - {budget.reserved}"
    )


# --- the unit ------------------------------------------------------------------------


def test_the_picodollar_scale_is_exactly_the_ledgers_own_precision() -> None:
    """The whole "integers, not Decimals" trade rests on this being lossless.

    If the budget's unit were coarser than the ledger's, a settled cost would have to be
    rounded on its way into the gate — and a gate that rounds is a gate whose arithmetic
    does not add up to the invoice.
    """
    assert Decimal(1).scaleb(-12) == USD_QUANTUM
    assert PICOS_PER_USD == 10**12


@pytest.mark.parametrize(
    "usd",
    ["0", "0.000011500000", "25.00", "0.000000000001", "1000000.00"],
)
def test_money_survives_the_round_trip_through_integers(usd: str) -> None:
    assert from_picos(to_picos(Decimal(usd))) == Decimal(usd)


def test_an_estimate_rounds_up_and_a_cost_rounds_to_nearest() -> None:
    """A reservation that rounded down would admit one request too many at the edge."""
    sub_quantum = Decimal("0.0000000000004")  # 0.4 picodollars
    assert to_picos(sub_quantum, conservative=True) == 1
    assert to_picos(sub_quantum) == 0


# --- windows -------------------------------------------------------------------------


def test_a_monthly_window_is_the_calendar_month_in_utc() -> None:
    window_id, expires_at = window_for(WINDOW_MONTHLY, AUGUST)
    assert window_id == "2026-08"
    assert datetime.fromtimestamp(expires_at, tz=UTC) == SEPTEMBER


def test_a_lifetime_window_never_ends() -> None:
    window_id, expires_at = window_for(WINDOW_TOTAL, AUGUST)
    assert window_id == WINDOW_TOTAL
    assert datetime.fromtimestamp(expires_at, tz=UTC).year == 9999


def test_an_unknown_window_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="unknown budget window 'weekly'"):
        window_for("weekly", AUGUST)


# --- no budget -----------------------------------------------------------------------


async def test_a_scope_with_no_budget_is_admitted_and_holds_nothing(
    budgets: BudgetStore,
) -> None:
    """An uncapped tenant is not a refused tenant. The gateway does not invent limits."""
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("1.00"), when=AUGUST)

    assert result.status == ADMIT_NO_BUDGET
    assert result.admitted
    assert result.reservation is None
    assert await budgets.get(SCOPE, when=AUGUST) is None


# --- reserving -----------------------------------------------------------------------


async def test_a_reservation_moves_reserved_and_remaining_together(
    budgets: BudgetStore,
) -> None:
    await capped(budgets, "10.00")

    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.50"), when=AUGUST)

    assert result.status == ADMIT_RESERVED
    assert result.reservation is not None
    assert result.reservation.usd == Decimal("2.50")
    assert result.reservation.window_id == "2026-08"

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert budget is not None
    assert budget.spent == 0
    assert budget.reserved == Decimal("2.50")
    assert budget.remaining == Decimal("7.50")
    assert budget.committed == Decimal("2.50")
    assert budget.reservations == 1
    assert_identity(budget)


async def test_the_gate_reads_committed_spend_not_landed_spend(budgets: BudgetStore) -> None:
    """BUILD_PLAN §0.2 rule 5, as an assertion.

    Nothing has settled, so *landed* spend is zero and a landed-only gate would admit
    this request happily. It is refused because six dollars are already reserved.
    """
    await capped(budgets, "10.00")
    await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("6.00"), when=AUGUST)

    refused = await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal("5.00"), when=AUGUST)

    assert refused.status == ADMIT_EXCEEDED
    assert refused.budget is not None
    assert refused.budget.spent == 0  # landed spend is zero...
    assert refused.budget.committed == Decimal("6.00")  # ...and committed is not
    assert not refused.admitted


async def test_a_refusal_changes_nothing(budgets: BudgetStore) -> None:
    """The failed condition means the update never applied — not that it was undone."""
    await capped(budgets, "1.00")
    before = await budgets.get(SCOPE, when=AUGUST)

    refused = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)

    after = await budgets.get(SCOPE, when=AUGUST)
    assert refused.status == ADMIT_EXCEEDED
    assert before is not None and after is not None
    assert (after.spent, after.reserved, after.remaining) == (
        before.spent,
        before.reserved,
        before.remaining,
    )
    assert after.reservations == 0


async def test_a_reservation_exactly_the_size_of_the_remainder_is_admitted(
    budgets: BudgetStore,
) -> None:
    """``>=``, not ``>``: a budget is a ceiling a request may reach, not one it must
    stay under."""
    await capped(budgets, "1.00")

    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("1.00"), when=AUGUST)

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert result.status == ADMIT_RESERVED
    assert budget is not None
    assert budget.remaining == 0
    assert_identity(budget)


async def test_reserving_twice_for_one_request_is_idempotent(budgets: BudgetStore) -> None:
    """A retry must not double a hold. The request id is the idempotency key."""
    await capped(budgets, "10.00")

    first = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    second = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert first.status == second.status == ADMIT_RESERVED
    assert budget is not None
    assert budget.reserved == Decimal("2.00")
    assert budget.reservations == 1
    assert_identity(budget)


# --- settling ------------------------------------------------------------------------


async def test_settling_for_less_than_the_estimate_gives_the_difference_back(
    budgets: BudgetStore,
) -> None:
    """The ordinary case: a conservative hold, corrected the moment the truth is known."""
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    assert result.reservation is not None

    settled = await budgets.settle(result.reservation, usd=Decimal("0.25"), when=AUGUST)

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert settled is True
    assert budget is not None
    assert budget.spent == Decimal("0.25")
    assert budget.reserved == 0
    assert budget.remaining == Decimal("9.75")
    assert budget.reservations == 0
    assert_identity(budget)


async def test_settling_for_more_than_the_estimate_eats_into_future_headroom(
    budgets: BudgetStore,
) -> None:
    """The documented over-run policy: record the truth, refuse the next request.

    An estimate is a bound and not a promise — a caller who states no ``max_tokens`` can
    be served more than the default assumed. The alternative to letting ``remaining`` go
    negative would be clamping it at zero, which silently forgives real spend and makes
    the ledger and the gate disagree about what a tenant used.
    """
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    assert result.reservation is not None

    await budgets.settle(result.reservation, usd=Decimal("12.00"), when=AUGUST)

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert budget is not None
    assert budget.spent == Decimal("12.00")
    assert budget.reserved == 0
    assert budget.remaining == Decimal("-2.00")
    assert_identity(budget)

    # And the arithmetic is not merely tidy — the next request is actually refused.
    nxt = await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal("0.01"), when=AUGUST)
    assert nxt.status == ADMIT_EXCEEDED


async def test_settling_twice_moves_the_counters_once(budgets: BudgetStore) -> None:
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    assert result.reservation is not None

    first = await budgets.settle(result.reservation, usd=Decimal("1.00"), when=AUGUST)
    second = await budgets.settle(result.reservation, usd=Decimal("1.00"), when=AUGUST)

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert (first, second) == (True, False)
    assert budget is not None
    assert budget.spent == Decimal("1.00")
    assert_identity(budget)


async def test_settling_at_zero_is_a_release(budgets: BudgetStore) -> None:
    """What an upstream error or an unroutable model costs: nothing, given back whole."""
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    assert result.reservation is not None

    await budgets.settle(result.reservation, usd=Decimal(0), when=AUGUST)

    budget = await budgets.get(SCOPE, when=AUGUST)
    assert budget is not None
    assert (budget.spent, budget.reserved, budget.remaining) == (
        Decimal(0),
        Decimal(0),
        Decimal("10.00"),
    )
    assert_identity(budget)


async def test_settling_a_hold_that_was_never_taken_does_nothing(budgets: BudgetStore) -> None:
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    assert result.reservation is not None
    ghost = type(result.reservation)(
        scope=SCOPE,
        request_id="hr_never_existed",
        usd=Decimal("2.00"),
        window_id="2026-08",
        expires_at=AUGUST,
    )

    assert await budgets.settle(ghost, usd=Decimal("1.00"), when=AUGUST) is False
    budget = await budgets.get(SCOPE, when=AUGUST)
    assert budget is not None
    assert budget.spent == 0
    assert_identity(budget)


# --- the leak, and how it closes ------------------------------------------------------


async def test_a_stranded_reservation_expires_and_the_budget_comes_back(
    budgets: BudgetStore,
) -> None:
    """*"A crashed process must not strand reserved budget forever."*

    Nothing sleeps: the reservation's expiry is computed from the ``when`` it was taken
    at, so the test moves time forward by passing a later one.
    """
    await capped(budgets, "10.00")
    await budgets.reserve(SCOPE, request_id="hr_dead", usd=Decimal("10.00"), when=AUGUST)

    still_held = await budgets.get(SCOPE, when=AUGUST)
    assert still_held is not None
    assert still_held.reserved == Decimal("10.00")
    assert still_held.remaining == 0

    later = AUGUST + timedelta(seconds=RESERVATION_TTL_S + 1)
    swept = await budgets.sweep_expired(SCOPE, when=later)

    budget = await budgets.get(SCOPE, when=later)
    assert swept.released == 1
    assert swept.usd == Decimal("10.00")
    assert budget is not None
    assert budget.reserved == 0
    assert budget.remaining == Decimal("10.00")
    assert budget.spent == 0, "an expired hold is released, never charged"
    assert budget.expired_releases == 1
    assert budget.expired_released == Decimal("10.00")
    assert_identity(budget)


async def test_a_live_reservation_is_not_swept(budgets: BudgetStore) -> None:
    await capped(budgets, "10.00")
    await budgets.reserve(SCOPE, request_id="hr_live", usd=Decimal("4.00"), when=AUGUST)

    swept = await budgets.sweep_expired(
        SCOPE, when=AUGUST + timedelta(seconds=RESERVATION_TTL_S - 1)
    )

    assert swept.released == 0
    budget = await budgets.get(SCOPE, when=AUGUST)
    assert budget is not None
    assert budget.reserved == Decimal("4.00")


async def test_a_dead_process_hold_never_refuses_a_live_request(budgets: BudgetStore) -> None:
    """The reason the sweep runs on the refusal path rather than on a timer.

    Without it, one ``SIGKILL`` mid-request would lock a tenant out of its whole cap
    until something else happened to notice — and nothing else would.
    """
    await capped(budgets, "10.00")
    await budgets.reserve(SCOPE, request_id="hr_dead", usd=Decimal("10.00"), when=AUGUST)

    later = AUGUST + timedelta(seconds=RESERVATION_TTL_S + 1)
    result = await budgets.reserve(SCOPE, request_id="hr_live", usd=Decimal("9.00"), when=later)

    assert result.status == ADMIT_RESERVED
    budget = await budgets.get(SCOPE, when=later)
    assert budget is not None
    assert budget.reserved == Decimal("9.00")
    assert budget.expired_releases == 1
    assert_identity(budget)


async def test_settling_a_swept_reservation_is_a_no_op(budgets: BudgetStore) -> None:
    """The process was not dead after all — it just took too long. Exactly one of the
    sweep and the settlement can win, and the sweep already did."""
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_slow", usd=Decimal("3.00"), when=AUGUST)
    assert result.reservation is not None
    later = AUGUST + timedelta(seconds=RESERVATION_TTL_S + 1)
    await budgets.sweep_expired(SCOPE, when=later)

    assert await budgets.settle(result.reservation, usd=Decimal("1.00"), when=later) is False
    budget = await budgets.get(SCOPE, when=later)
    assert budget is not None
    assert budget.spent == 0
    assert_identity(budget)


# --- window rollover ------------------------------------------------------------------


async def test_a_new_month_starts_the_counters_again(budgets: BudgetStore) -> None:
    """No reset job: the window id changes and the first request of the month rolls it."""
    await capped(budgets, "10.00", window=WINDOW_MONTHLY)
    august = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("6.00"), when=AUGUST)
    assert august.reservation is not None
    await budgets.settle(august.reservation, usd=Decimal("6.00"), when=AUGUST)

    september = await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal("9.00"), when=SEPTEMBER)

    assert september.status == ADMIT_RESERVED, "August's spend must not follow the tenant"
    budget = await budgets.get(SCOPE, when=SEPTEMBER)
    assert budget is not None
    assert budget.window_id == "2026-09"
    assert budget.spent == 0
    assert budget.reserved == Decimal("9.00")
    assert_identity(budget)


async def test_a_lifetime_budget_never_rolls(budgets: BudgetStore) -> None:
    await capped(budgets, "10.00", window=WINDOW_TOTAL)
    first = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("6.00"), when=AUGUST)
    assert first.reservation is not None
    await budgets.settle(first.reservation, usd=Decimal("6.00"), when=AUGUST)

    later = await budgets.reserve(
        SCOPE, request_id="hr_b", usd=Decimal("9.00"), when=SEPTEMBER + timedelta(days=400)
    )

    assert later.status == ADMIT_EXCEEDED
    budget = await budgets.get(SCOPE, when=SEPTEMBER)
    assert budget is not None
    assert budget.window_id == WINDOW_TOTAL
    assert budget.spent == Decimal("6.00")


async def test_reading_a_budget_whose_window_has_ended_reports_the_new_one(
    budgets: BudgetStore,
) -> None:
    """The dashboard must not show August's spend on the first of September."""
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("6.00"), when=AUGUST)
    assert result.reservation is not None
    await budgets.settle(result.reservation, usd=Decimal("6.00"), when=AUGUST)

    budget = await budgets.get(SCOPE, when=SEPTEMBER)

    assert budget is not None
    assert budget.window_id == "2026-09"
    assert budget.spent == 0
    assert budget.remaining == Decimal("10.00")
    assert_identity(budget)


async def test_a_settlement_that_crosses_a_month_boundary_does_not_charge_the_new_month(
    budgets: BudgetStore,
) -> None:
    """A request in flight when the month turns has its hold cleared by the roll.

    Its cost is still in the ledger — that is the invoice, and it is complete — but the
    gate's counter for the new month starts at what the new month has spent, which is
    the only number it can honestly hold.
    """
    await capped(budgets, "10.00")
    august = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("2.00"), when=AUGUST)
    assert august.reservation is not None
    await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal("1.00"), when=SEPTEMBER)

    landed = await budgets.settle(august.reservation, usd=Decimal("2.00"), when=SEPTEMBER)

    budget = await budgets.get(SCOPE, when=SEPTEMBER)
    assert landed is False
    assert budget is not None
    assert budget.spent == 0
    assert budget.reserved == Decimal("1.00")
    assert_identity(budget)


# --- administration --------------------------------------------------------------------


async def test_setting_a_budget_twice_is_idempotent(budgets: BudgetStore) -> None:
    first = await capped(budgets, "25.00")
    second = await capped(budgets, "25.00")

    assert first.usd == second.usd == Decimal("25.00")
    assert second.remaining == Decimal("25.00")
    assert_identity(second)


async def test_raising_a_budget_keeps_spend_and_live_holds(budgets: BudgetStore) -> None:
    await capped(budgets, "10.00")
    settled = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("4.00"), when=AUGUST)
    assert settled.reservation is not None
    await budgets.settle(settled.reservation, usd=Decimal("4.00"), when=AUGUST)
    await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal("3.00"), when=AUGUST)

    raised = await budgets.set_budget(
        SCOPE, usd=Decimal("20.00"), window=WINDOW_MONTHLY, when=AUGUST
    )

    assert raised.usd == Decimal("20.00")
    assert raised.spent == Decimal("4.00")
    assert raised.reserved == Decimal("3.00")
    assert raised.remaining == Decimal("13.00")  # 3 + the 10 that was added
    assert_identity(raised)


async def test_lowering_a_budget_below_what_is_spent_leaves_negative_headroom(
    budgets: BudgetStore,
) -> None:
    """And that is correct, not an error: the next request is refused until the window
    turns or somebody raises the cap again."""
    await capped(budgets, "10.00")
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("8.00"), when=AUGUST)
    assert result.reservation is not None
    await budgets.settle(result.reservation, usd=Decimal("8.00"), when=AUGUST)

    lowered = await budgets.set_budget(
        SCOPE, usd=Decimal("5.00"), window=WINDOW_MONTHLY, when=AUGUST
    )

    assert lowered.remaining == Decimal("-3.00")
    assert_identity(lowered)
    refused = await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal("0.01"), when=AUGUST)
    assert refused.status == ADMIT_EXCEEDED


async def test_changing_the_window_type_starts_the_counters_again(
    budgets: BudgetStore,
) -> None:
    """A monthly cap and a lifetime cap count different things; carrying a total across
    the change would answer neither question."""
    await capped(budgets, "10.00", window=WINDOW_MONTHLY)
    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("4.00"), when=AUGUST)
    assert result.reservation is not None
    await budgets.settle(result.reservation, usd=Decimal("4.00"), when=AUGUST)

    switched = await budgets.set_budget(
        SCOPE, usd=Decimal("10.00"), window=WINDOW_TOTAL, when=AUGUST
    )

    assert switched.window == WINDOW_TOTAL
    assert switched.window_id == WINDOW_TOTAL
    assert switched.spent == 0
    assert switched.remaining == Decimal("10.00")
    assert_identity(switched)


async def test_clearing_a_budget_leaves_the_scope_uncapped(budgets: BudgetStore) -> None:
    await capped(budgets, "1.00")

    assert await budgets.clear_budget(SCOPE) is True
    assert await budgets.get(SCOPE, when=AUGUST) is None
    assert await budgets.clear_budget(SCOPE) is False

    result = await budgets.reserve(SCOPE, request_id="hr_a", usd=Decimal("999"), when=AUGUST)
    assert result.status == ADMIT_NO_BUDGET


async def test_listing_returns_every_configured_scope(budgets: BudgetStore) -> None:
    other = BudgetScope.tenant("99999999-8888-7777-6666-555555555555")
    await capped(budgets, "10.00")
    await budgets.set_budget(other, usd=Decimal("5.00"), window=WINDOW_TOTAL, when=AUGUST)

    listed = await budgets.list_budgets(when=AUGUST)

    assert [budget.scope for budget in listed] == sorted(
        [SCOPE, other], key=lambda scope: scope.key
    )
    assert {budget.usd for budget in listed} == {Decimal("10.00"), Decimal("5.00")}


async def test_a_zero_budget_refuses_everything_that_costs_anything(
    budgets: BudgetStore,
) -> None:
    """A cap of $0 is a real configuration — "this tenant may not spend" — and is not
    the same as having no cap at all."""
    await capped(budgets, "0")

    refused = await budgets.reserve(
        SCOPE, request_id="hr_a", usd=Decimal("0.000000000001"), when=AUGUST
    )
    free = await budgets.reserve(SCOPE, request_id="hr_b", usd=Decimal(0), when=AUGUST)

    assert refused.status == ADMIT_EXCEEDED
    assert free.status == ADMIT_RESERVED, "a $0 request still fits under a $0 cap"
