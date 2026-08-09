"""Token buckets: the records that cross the store boundary, and the arithmetic itself.

BUILD_PLAN Phase 4, first sentence: *"Token-bucket rate limits (requests/min and
tokens/min per key and per tenant) ... enforced on DynamoDB conditional writes (A1)."*
This is the half PR-4 deferred. The budget gate's file of the same shape is
``core/budgets.py``, and the two are deliberately parallel — one interface, two
implementations, one contract suite — but the *primitive* underneath is different
enough to be worth reading carefully.

**A token bucket cannot be stored as a count of tokens.** The obvious item — ``tokens``
plus ``refilled_at`` — needs ``min(capacity, tokens + elapsed * rate) - cost >= 0``
evaluated at write time, and a DynamoDB ``ConditionExpression`` compares an attribute to
a value and does no arithmetic. Every implementation that stores a count therefore reads
the item, refills it in application code, decides, and writes the result back. That is
Backline's **D-019** with a different noun, and ``tests/test_rate_limit_hammer.py``
sabotage A shows it failing exactly the way the budget's did.

**So the bucket is stored as a time.** One number per bucket: ``tat``, the *theoretical
arrival time* of the next unit — the moment at which the bucket will have refilled to
full. This is the GCRA formulation (the leaky-bucket dual used by ATM traffic shapers and
by every rate limiter that has to be atomic), and its whole value here is that admission
becomes a **bare comparison of an attribute to a value**::

    ConditionExpression:  tat <= :now + burst_ns - :charge_ns
    UpdateExpression:     SET tat = tat + :charge_ns

which is a single conditional write, with no read to be stale and no window to lose an
update in. The available tokens are not stored; they are *implied* by how far ``tat``
leads the clock:

.. code-block:: text

    T          emission interval  = ceil(60s / limit_per_min)   nanoseconds per unit
    burst      capacity           = limit_per_min units
    D          delay tolerance    = burst * T                   nanoseconds
    available  at time `now`      = clamp((now + D - tat) / T, 0, burst)
    admit cost c                  iff  tat <= now + D - c*T
    on admit                      tat := max(tat, now) + c*T

The one term DynamoDB cannot express is ``max(tat, now)`` — and dropping it is not
cosmetic: without the clamp an idle bucket accumulates unbounded credit, so a bucket
untouched for an hour would admit an hour's worth of traffic in one burst. It is
recovered with a **second conditional write** rather than with a read: one expression for
the bucket in use (``tat > now``, add to it) and one for the bucket at rest
(``tat <= now``, set it to ``now + charge``). Each is atomic on its own, only one can
apply, and which one to attempt is learned from the item a failed condition hands back —
never from a separate read. See ``headroom/db/buckets.py``; sabotage C in the hammer is
the version with the clamp removed.

**Nanoseconds, and integers everywhere.** ``T`` is rounded **up**, which makes the
limiter fractionally stricter than its nominal rate — the same direction the budget
estimate errs in, and for the same reason: a limit that leaks is not a limit. At
`limit=7` the rounding costs 4 parts in 10^11. Epoch nanoseconds is a 19-digit integer,
comfortably inside DynamoDB's 38 significant digits even after ``+ D``.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

__all__ = [
    "DIMENSIONS",
    "DIM_REQUESTS",
    "DIM_TOKENS",
    "NANOS_PER_S",
    "REFUSED_EXCEEDS_CAPACITY",
    "REFUSED_RATE_LIMITED",
    "SCOPES",
    "SCOPE_KEY",
    "SCOPE_TENANT",
    "WINDOW_NS",
    "WINDOW_S",
    "BucketKey",
    "BucketState",
    "Consumption",
    "RateLimit",
    "RateLimitStore",
    "available_units",
    "burst_ns",
    "emission_interval_ns",
    "from_ns",
    "reset_after_s",
    "to_ns",
]

# --- units ---------------------------------------------------------------------------

NANOS_PER_S: Final = 1_000_000_000

#: The window every limit is quoted in. BUILD_PLAN says *requests/min and tokens/min*,
#: so the unit is a minute and there is no second one to configure. A token bucket has
#: no window in the fixed-window sense — this is only the period the rate is *quoted*
#: in, which is why a limit of 60/min does not permit 60 requests at 59.9 seconds and
#: 60 more at 60.1 (``tests/test_rate_limit_hammer.py`` sabotage B is that bug).
WINDOW_S: Final = 60
WINDOW_NS: Final = WINDOW_S * NANOS_PER_S


def emission_interval_ns(limit_per_min: int) -> int:
    """Nanoseconds of credit one unit costs, at ``limit_per_min`` units a minute.

    Rounded **up**. The remainder is at most one nanosecond per unit, and it is spent in
    the direction that refuses rather than the direction that leaks — the same asymmetry
    the budget estimate is built on (H-034): an over-strict limiter produces a visible
    429 the tenant reports; an under-strict one produces load nobody notices until the
    provider does.
    """
    if limit_per_min <= 0:
        raise ValueError(f"a rate limit must be at least 1 per minute, got {limit_per_min}")
    return math.ceil(WINDOW_NS / limit_per_min)


def burst_ns(limit_per_min: int) -> int:
    """How far ahead of the clock ``tat`` may run: the bucket's capacity, in time.

    Capacity equals one minute's worth of units, so a bucket at rest admits a burst of
    exactly ``limit_per_min`` and then meters the rest out at the emission interval.
    There is deliberately **no separate burst knob**: it would be a second number to
    explain, a second column to migrate, and a second thing to get wrong, for a case
    nobody has asked for. A tenant who wants a smaller burst wants a smaller limit.
    """
    return emission_interval_ns(limit_per_min) * limit_per_min


def available_units(tat_ns: int, now_ns: int, limit_per_min: int) -> int:
    """Whole units the bucket holds at ``now_ns``. Never negative, never over capacity.

    Derived from ``tat`` rather than stored, which is the property that makes the whole
    design a single conditional write. Floored, so "available" is a number of requests
    that will actually be admitted rather than a fraction that rounds up into a lie.
    """
    interval = emission_interval_ns(limit_per_min)
    slack = now_ns + burst_ns(limit_per_min) - tat_ns
    return max(0, min(limit_per_min, slack // interval))


def reset_after_s(tat_ns: int, now_ns: int) -> int:
    """Seconds until the bucket is full again — ``tat`` catching up with the clock.

    Rounded up and floored at zero. A bucket at rest answers ``0``.
    """
    return max(0, math.ceil((tat_ns - now_ns) / NANOS_PER_S))


def to_ns(when: datetime) -> int:
    """A request's wall-clock stamp as epoch nanoseconds.

    ``datetime.timestamp()`` is a float and loses nanoseconds above microsecond scale;
    a microsecond of drift is six orders of magnitude below the smallest emission
    interval this can produce, so the conversion goes through microseconds deliberately
    rather than pretending to a precision the source does not have.
    """
    return int(when.astimezone(UTC).timestamp() * 1_000_000) * 1000


def from_ns(ns: int) -> datetime:
    """Epoch nanoseconds back to a UTC datetime, for the admin view."""
    return datetime.fromtimestamp(ns / NANOS_PER_S, tz=UTC)


# --- what a limit is attached to -------------------------------------------------------

#: The two scopes BUILD_PLAN names: *"per key and per tenant"*. Unlike budgets — where
#: enforcing two caps would mean two *reservations* and a compensating release when the
#: second fails (H-033) — a bucket consumption has nothing to settle, so both scopes are
#: enforced from the first line. See docs/DECISIONS.md H-036.
SCOPE_TENANT: Final = "tenant"
SCOPE_KEY: Final = "key"
SCOPES: Final = (SCOPE_TENANT, SCOPE_KEY)

#: The two dimensions, also BUILD_PLAN's own words: *"requests/min and tokens/min"*.
DIM_REQUESTS: Final = "requests"
DIM_TOKENS: Final = "tokens"
DIMENSIONS: Final = (DIM_REQUESTS, DIM_TOKENS)

#: Why a bucket said no. ``rate_limited`` heals by waiting; ``exceeds_capacity`` does
#: not, and the difference is the difference between a `retry-after` and a lie.
REFUSED_RATE_LIMITED: Final = "rate_limited"
REFUSED_EXCEEDS_CAPACITY: Final = "rate_limit_exceeds_capacity"


@dataclass(frozen=True, slots=True)
class RateLimit:
    """One scope's configured limits. ``None`` in a field means *unlimited*.

    Stored as two nullable columns on ``tenants`` and ``virtual_keys`` rather than in
    DynamoDB beside the bucket, because BUILD_PLAN L2 is explicit about the split —
    Postgres holds *config*, DynamoDB holds *token buckets and budget reservations only*
    — and because it makes the configuration free to read on the request path: the
    authenticator already loads both rows, so the limits arrive on the ``Principal`` with
    no extra round trip and no new cache (docs/DECISIONS.md H-037).
    """

    requests_per_min: int | None = None
    tokens_per_min: int | None = None

    @property
    def configured(self) -> bool:
        """Whether any limit applies at all. An unconfigured scope is never consumed."""
        return self.requests_per_min is not None or self.tokens_per_min is not None

    def per_min(self, dimension: str) -> int | None:
        """The limit for one dimension, or ``None`` if that dimension is unlimited."""
        if dimension == DIM_REQUESTS:
            return self.requests_per_min
        if dimension == DIM_TOKENS:
            return self.tokens_per_min
        raise ValueError(f"unknown rate-limit dimension {dimension!r}")


@dataclass(frozen=True, slots=True)
class BucketKey:
    """One bucket: a scope and a dimension. The DynamoDB partition key.

    One item per ``(scope, dimension)`` rather than one item per scope holding both.
    The alternative would make a scope's two dimensions consume atomically together, and
    it would not remove the property that has to be documented anyway — a request
    consumes from *two scopes*, which are necessarily two items — so it buys elegance in
    one place at the cost of a four-branch update expression in another. See H-036.
    """

    scope_kind: str
    scope_id: str
    dimension: str

    @property
    def id(self) -> str:
        """``tenant#<uuid>#requests`` — the stored key, and a greppable log value."""
        return f"{self.scope_kind}#{self.scope_id}#{self.dimension}"

    @property
    def label(self) -> str:
        """``tenant:requests`` — how a refusal names itself in a header and a log line."""
        return f"{self.scope_kind}:{self.dimension}"


@dataclass(frozen=True, slots=True)
class BucketState:
    """A bucket as an operator sees it: what it holds now and when it is full again.

    Read-only. ``/admin/limits`` reports it; nothing on the request path calls for it,
    because a read before a write is the bug this module exists to avoid.
    """

    key: BucketKey
    limit_per_min: int
    available: int
    reset_after_s: int
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class Consumption:
    """What one bucket said when a request asked it for ``cost`` units."""

    key: BucketKey
    admitted: bool
    limit_per_min: int
    #: Units left in this bucket *after* the decision. Zero on a refusal is normal; a
    #: refusal with units left means the request asked for more than remained.
    available: int
    #: Seconds until this request would fit. ``None`` when waiting cannot help — the
    #: request is larger than the bucket's whole capacity, and the honest header is no
    #: header rather than a number that will be wrong again on arrival.
    retry_after_s: int | None = None
    #: ``rate_limited`` | ``rate_limit_exceeds_capacity`` on a refusal, else ``None``.
    refusal: str | None = None
    #: What the request asked for, echoed back so the refusal message can quote it.
    cost: int = 0


class RateLimitStore(ABC):
    """Where token buckets live, and the three operations the gate needs.

    Two implementations and one contract suite over both, the H-021 shape — with the same
    caveat ``BudgetStore`` carries and for the same reason: the in-memory store reproduces
    the *semantics*, not the *concurrency*. Its operations do not suspend, so they cannot
    interleave, so they cannot demonstrate that a design is race-free. That proof belongs
    to DynamoDB Local, and ``tests/test_rate_limit_hammer.py`` is written against it alone.
    """

    @abstractmethod
    async def consume(
        self, key: BucketKey, *, limit_per_min: int, cost: int, when: datetime
    ) -> Consumption:
        """Take ``cost`` units out of one bucket, atomically, or refuse.

        The contract is the phase: **refill and consume are one operation.** An
        implementation that reads the bucket, refills it in Python, decides, and writes
        the result back has reconstructed D-019 in a new noun and will be caught by the
        hammer.

        ``when`` is the request's own arrival time — never a wall clock read inside the
        store — so a test can drive a refill without sleeping and a replayed fixture
        cannot land in the wrong instant. Consumption is **not refunded** when a later
        bucket refuses: see ``headroom/policy/limits.py``.
        """

    @abstractmethod
    async def state(self, key: BucketKey, *, limit_per_min: int, when: datetime) -> BucketState:
        """What the bucket holds at ``when``, without touching it. The admin read.

        A bucket nobody has consumed from is full, which is why this never has to create
        an item: *absent* and *at rest* are the same state, and that is also what makes
        the DynamoDB TTL on this table safe (H-035).
        """

    @abstractmethod
    async def clear(self, key: BucketKey) -> bool:
        """Forget a bucket, restoring it to full. ``False`` if there was nothing there.

        The operator's escape hatch — a limit lowered by mistake leaves ``tat`` far in
        the future, and waiting it out is not an incident response.
        """

    async def aclose(self) -> None:
        """Release resources. A no-op for stores that hold none."""
        return None
