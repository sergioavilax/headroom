"""Budgets: the records that cross the store boundary, and the unit money travels in.

This is Phase 4's half of the ``core/`` storage-interface split (BUILD_PLAN §0.5), the
same shape ``core/storage.py`` and ``core/ledger.py`` already have. What makes it worth
reading is not the dataclasses; it is the two decisions encoded in them.

**A budget is not a number, it is a bookkeeping identity.** For one scope and one
window, at all times::

    remaining = budget - spent - reserved

``remaining`` is stored rather than derived, because it is the attribute the DynamoDB
``ConditionExpression`` compares against, and a condition that had to *compute*
``budget - spent - reserved`` could not exist: DynamoDB conditions compare an attribute
to a value and do no arithmetic. Storing the difference is what turns "may this request
proceed?" into a single atomic conditional write instead of a read followed by a write —
which is Backline's **D-019** scar exactly (check, then add, and every concurrent
request passes the check before any of them records anything). Here the check *is* the
subtraction: one operation, one item, or nothing happens at all.

The identity is not an invariant maintained by discipline. Every mutation moves
``remaining`` and its components in the *same* single-item update, so an implementation
can only break it by writing a wrong expression — which
``tests/test_budget_store.py::test_the_arithmetic_identity_holds_after_every_operation``
checks after every operation, on both implementations.

**Money crosses this boundary as an integer count of picodollars** (1e-12 USD), never a
``Decimal`` and never — obviously — a float. The reasons are in docs/DECISIONS.md H-030;
the short version is that DynamoDB's arithmetic and comparisons are the primitives the
whole atomicity argument rests on, and integers are the only numeric domain where "add
this, and only if the result still fits" is beyond argument. 1e-12 is not an arbitrary
scale: it is exactly ``headroom.metering.cost.USD_QUANTUM``, the ledger's own stored
precision, so the round trip ``Decimal -> int -> Decimal`` is lossless for every value
the meter can produce. ``tests/test_budget_store.py`` pins that equality.
"""

from __future__ import annotations

import calendar
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Final

__all__ = [
    "ADMIT_EXCEEDED",
    "ADMIT_NO_BUDGET",
    "ADMIT_RESERVED",
    "PICOS_PER_USD",
    "RESERVATION_TTL_S",
    "WINDOWS",
    "WINDOW_MONTHLY",
    "WINDOW_TOTAL",
    "Budget",
    "BudgetScope",
    "BudgetStore",
    "Reservation",
    "ReserveResult",
    "SweepResult",
    "from_picos",
    "to_picos",
    "window_for",
]

# --- the unit ----------------------------------------------------------------------

#: Picodollars per dollar. ``10**-12`` USD is exactly ``metering.cost.USD_QUANTUM``,
#: the ledger's ``NUMERIC(24, 12)`` precision, so a settled cost converts to an integer
#: and back without rounding. Comfortably inside DynamoDB's 38-significant-digit
#: number limit: a $1,000,000 budget is 19 digits.
PICOS_PER_USD: Final = 10**12
_PICO_EXPONENT: Final = 12


def to_picos(usd: Decimal, *, conservative: bool = False) -> int:
    """A dollar amount as whole picodollars.

    ``conservative=True`` rounds **up**, and is used for a reservation estimate: an
    estimate that rounded down would reserve fractionally less than it eventually
    settles, which is the wrong direction for a gate. Everything else — a settled cost
    from the meter, a budget an operator typed — is already exact at this scale and
    rounds half-up, which is a no-op for it.
    """
    scaled = usd.scaleb(_PICO_EXPONENT)
    rounding = ROUND_CEILING if conservative else ROUND_HALF_UP
    return int(scaled.to_integral_value(rounding=rounding))


def from_picos(picos: int) -> Decimal:
    """Picodollars back to dollars. Exact, and negative values are meaningful.

    ``remaining`` goes negative when a request settles for more than it reserved (a
    caller whose model overran the estimated ceiling). That overshoot is recorded
    rather than clamped: clamping would quietly forgive spend the tenant really
    incurred, and the next request is refused precisely because the number is honest.
    """
    return Decimal(picos).scaleb(-_PICO_EXPONENT)


#: How long a hold survives without a settlement before a sweep may release it.
#: Fifteen minutes: comfortably longer than any single model call, including a long
#: stream on a slow provider, and short enough that a ``SIGKILL`` under load does not
#: leave a tenant's cap encumbered for the rest of the month. It is an *upper bound on
#: a leak*, not a request timeout — a live request that outlives it still settles
#: normally, because a settlement and a sweep are conditioned on the same hold and only
#: one of them can win.
RESERVATION_TTL_S: Final = 900


# --- windows -----------------------------------------------------------------------

#: Every request the budget has ever seen. No reset, ever.
WINDOW_TOTAL: Final = "total"
#: The calendar month, in UTC. See :func:`window_for` for why calendar and why UTC.
WINDOW_MONTHLY: Final = "monthly"

WINDOWS: Final = (WINDOW_MONTHLY, WINDOW_TOTAL)

#: The far-future stamp a lifetime budget's window "expires" at, so the reservation
#: condition can be one uniform expression (``window_expires_at > :now``) rather than
#: an ``OR`` over an optional attribute. 9999-12-31T23:59:59Z as an epoch second.
_NEVER: Final = 253402300799


def window_for(window: str, when: datetime) -> tuple[str, int]:
    """The window id a request dated ``when`` falls in, and when that window ends.

    Returned as ``(window_id, expires_at_epoch_seconds)``. The id is what the ledger
    row and the admin view name; the expiry is what the DynamoDB condition compares
    ``now`` against, which is what lets one expression serve both window types.

    A **calendar** month rather than a rolling 30 days, deliberately: a rolling window
    cannot be a counter. It would require summing the last 30 days of history on every
    admission, which is a query, not a conditional write — and this whole phase exists
    because the answer has to be one atomic operation. A calendar month is also how
    every provider invoices and how an operator thinks about "the monthly cap".

    In **UTC**, and resolved from the request's own ``started_at`` — the same rule the
    dated price schedule follows (H-023). Wall-clock "now" would let a queued
    settlement or a replayed fixture land in the wrong month.

    A new month needs no reset job: the id changes, and the first request of the month
    rolls the counters. That is the entire mechanism.
    """
    if window == WINDOW_TOTAL:
        return WINDOW_TOTAL, _NEVER
    if window != WINDOW_MONTHLY:
        raise ValueError(f"unknown budget window {window!r} (known: {', '.join(WINDOWS)})")
    moment = when.astimezone(UTC)
    last_day = calendar.monthrange(moment.year, moment.month)[1]
    end = datetime(moment.year, moment.month, last_day, 23, 59, 59, tzinfo=UTC) + timedelta(
        seconds=1
    )
    return f"{moment.year:04d}-{moment.month:02d}", int(end.timestamp())


# --- records -----------------------------------------------------------------------

#: The only scope kind Phase 4 enforces. Per-key budgets would need a *second*
#: reservation on the same request, and two conditional writes are not one atomic
#: operation — the compensating release when the second fails is a new place for
#: D-019 to grow back. Named here so a later phase extends rather than rewrites
#: (docs/DECISIONS.md H-033).
SCOPE_TENANT: Final = "tenant"


@dataclass(frozen=True, slots=True)
class BudgetScope:
    """What a budget is attached to. One partition key in DynamoDB."""

    kind: str
    id: str

    @classmethod
    def tenant(cls, tenant_id: str) -> BudgetScope:
        return cls(kind=SCOPE_TENANT, id=tenant_id)

    @property
    def key(self) -> str:
        """``tenant#<uuid>`` — the stored partition key, and a greppable log value."""
        return f"{self.kind}#{self.id}"


@dataclass(frozen=True, slots=True)
class Budget:
    """One scope's cap and its live counters for one window.

    ``remaining`` is stored, not computed on read: it is the attribute the conditional
    write tests, and a view that recomputed it would be describing a different number
    from the one the gate actually used.
    """

    scope: BudgetScope
    usd: Decimal
    window: str
    window_id: str
    spent: Decimal
    reserved: Decimal
    remaining: Decimal
    #: Live reservations — requests admitted and not yet settled.
    reservations: int = 0
    #: Reservations the sweeper handed back because the process that took them never
    #: came back. Non-zero means requests are dying between admission and settlement,
    #: which is worth an alert in Phase 9.
    expired_releases: int = 0
    expired_released: Decimal = Decimal(0)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def committed(self) -> Decimal:
        """Spent **plus** reserved — the figure BUILD_PLAN §0.2 rule 5 says the gate
        must read. Landed spend alone is D-019's scar."""
        return self.spent + self.reserved

    @property
    def identity_holds(self) -> bool:
        """``remaining == budget - spent - reserved``. Asserted after every operation."""
        return self.remaining == self.usd - self.spent - self.reserved


@dataclass(frozen=True, slots=True)
class Reservation:
    """Budget held for one in-flight request. The handle a settlement needs.

    Carries its own ``window_id`` so a request that crosses a month boundary settles
    against the window it was admitted to, never the one that happens to be current
    when it finishes.
    """

    scope: BudgetScope
    request_id: str
    usd: Decimal
    window_id: str
    expires_at: datetime


# Outcomes of an admission attempt. Three, because "this tenant has no budget" and
# "this tenant is over budget" are opposite answers and a boolean would merge them.
ADMIT_NO_BUDGET: Final = "no_budget"
ADMIT_RESERVED: Final = "reserved"
ADMIT_EXCEEDED: Final = "exceeded"


@dataclass(frozen=True, slots=True)
class ReserveResult:
    """What happened when a request asked for budget."""

    status: str
    reservation: Reservation | None = None
    #: The budget as it stood when the answer was decided. Present on a refusal so the
    #: 402 can quote real figures, and on a grant so a caller can log the headroom left.
    budget: Budget | None = None

    @property
    def admitted(self) -> bool:
        """Whether the request may proceed. **No budget means yes**, deliberately: a
        gateway does not refuse tenants nobody has configured a cap for."""
        return self.status in (ADMIT_RESERVED, ADMIT_NO_BUDGET)


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What a sweep of expired reservations handed back."""

    released: int = 0
    usd: Decimal = Decimal(0)


class BudgetStore(ABC):
    """Where budgets live, and the four operations the gate needs.

    Two implementations and one contract suite over both, the H-021 shape — with one
    honest caveat stated here rather than discovered later: the in-memory store
    reproduces the *semantics*, not the *concurrency*. Its operations do not suspend,
    so it cannot interleave and therefore cannot demonstrate that the design is
    race-free. That proof belongs to DynamoDB Local, and
    ``tests/test_budget_stampede.py`` is written against it alone for exactly that
    reason.
    """

    @abstractmethod
    async def reserve(
        self, scope: BudgetScope, *, request_id: str, usd: Decimal, when: datetime
    ) -> ReserveResult:
        """Hold ``usd`` against ``scope``'s budget, atomically, or refuse.

        The contract is the phase: **the check and the deduction are one operation.**
        An implementation that reads the balance and then writes it back has
        reconstructed D-019 and will be caught by the stampede.

        ``when`` is the request's own arrival time; the store resolves the window from
        it and rolls a stale one. ``request_id`` makes the hold idempotent — reserving
        twice for one request is a bug, not a second hold.
        """

    @abstractmethod
    async def settle(self, reservation: Reservation, *, usd: Decimal, when: datetime) -> bool:
        """Replace a hold with what the request actually cost, atomically.

        ``reserved -= held``, ``spent += usd``, ``remaining += held - usd``, in one
        operation. Returns ``False`` when the hold was already gone — swept, or lost to
        a window roll — which is a no-op rather than an error: settling twice must not
        move the counters twice.
        """

    @abstractmethod
    async def sweep_expired(self, scope: BudgetScope, *, when: datetime) -> SweepResult:
        """Release holds whose owner never came back. Idempotent, exactly-once per hold.

        This is the answer to "a crashed process must not strand reserved budget
        forever". Expired holds are **released, not charged**: the gateway has no
        evidence such a request cost anything, the ledger will not claim it did either,
        and charging on suspicion would let a restart quietly eat a tenant's month.
        """

    @abstractmethod
    async def get(self, scope: BudgetScope, *, when: datetime) -> Budget | None:
        """The budget as it applies to a request dated ``when``, or ``None`` if unset.

        Reports the *effective* window: when the stored window has already ended, the
        counters read as the fresh window's — which is what the next request will see,
        and therefore the honest answer to "how much can this tenant spend right now".
        """

    @abstractmethod
    async def set_budget(
        self, scope: BudgetScope, *, usd: Decimal, window: str, when: datetime
    ) -> Budget:
        """Create or change a cap. Changing the amount adjusts ``remaining`` by the
        delta; changing the *window type* starts a fresh window with fresh counters."""

    @abstractmethod
    async def clear_budget(self, scope: BudgetScope) -> bool:
        """Remove a cap entirely. ``False`` if there was none. The scope becomes
        uncapped, and any live hold against it stops existing."""

    @abstractmethod
    async def list_budgets(self, *, when: datetime) -> list[Budget]:
        """Every configured budget. The admin overview; not on any request path."""

    async def aclose(self) -> None:
        """Release resources. A no-op for stores that hold none."""
        return None
