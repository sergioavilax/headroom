"""``BudgetStore`` on DynamoDB — the phase, in one file.

Everything here exists to make one sentence true:

> **Every mutation of a budget is a single conditional write to a single item.**

DynamoDB guarantees that an ``UpdateItem`` is atomic on one item and that its
``ConditionExpression`` is evaluated against the item's committed state at the moment
the write applies. Nothing else in this file matters if that sentence stops being true,
because the sentence *is* the fix for Backline's **D-019**: a gate that read the balance,
decided, and then wrote it back let a hundred concurrent requests each pass the check
before any of them had recorded a thing. There is no read-then-write here to race. The
admission check and the deduction are the same operation::

    ConditionExpression: remaining_picos >= :estimate
    UpdateExpression:    SET remaining_picos = remaining_picos - :estimate, ...

The condition is a bare attribute-to-value comparison because that is the only kind
DynamoDB has — conditions do no arithmetic. That is why ``remaining`` is a stored
attribute rather than something computed from ``budget - spent - reserved``: the design
of the item shape is downstream of what a condition can express.

**The item.** One per scope, holding config, window stamp, counters, and the live
reservations::

    scope_id                "tenant#<uuid>"   partition key
    budget_picos            the cap for one window
    budget_window           "monthly" | "total"
    window_id               "2026-08" | "total"
    window_expires_at       epoch seconds; a far-future sentinel for "total"
    spent_picos             settled spend in this window
    reserved_picos          sum of the live reservations below
    remaining_picos         budget - spent - reserved   <- what the condition tests
    reservations            { "<request_id>": { "p": picos, "x": expires_at } }
    expired_releases        holds the sweeper handed back (observability)
    expired_released_picos

Keeping the reservations *inside* the counter item is what avoids a transaction: a
reservation recorded in a second item would be a dual write, and a crash between the two
either strands budget or invents it. Here the hold and the deduction are the same
mutation, so they cannot disagree. The cost is DynamoDB's 400 KB item limit — a few
thousand simultaneously in-flight requests for one tenant — which is documented,
bounded by the sweeper, and far above anything this gateway will see.

**The window needs no reset job.** ``window_expires_at`` is part of the admission
condition, so a request arriving after the month ends fails the condition, and the
failure path rolls the window with a compare-and-set on ``window_id``. Exactly one
concurrent request wins the roll; the rest retry and take the normal path.

**Nothing is cached.** Not the balance, not the cap, not whether a scope has one. The
auth cache exists because a stale *authorization* is a bounded risk (H-018); a stale
*balance* is the scar this phase is named after, and H-018 says so explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from botocore.exceptions import ClientError

from headroom.core.budgets import (
    ADMIT_EXCEEDED,
    ADMIT_NO_BUDGET,
    ADMIT_RESERVED,
    RESERVATION_TTL_S,
    Budget,
    BudgetScope,
    BudgetStore,
    Reservation,
    ReserveResult,
    SweepResult,
    from_picos,
    to_picos,
    window_for,
)
from headroom.core.errors import ControlPlaneUnavailable
from headroom.db.dynamo import DynamoClient, budgets_table_name, translate_dynamo_error

__all__ = ["DynamoBudgetStore"]

#: A conditional write that loses a race is retried, because losing means the item moved
#: under us and the answer may now be different. Bounded, because "keep trying until it
#: works" on a contended item is how an admission check becomes a latency incident.
_MAX_ATTEMPTS: Final = 4

_CONDITION_FAILED: Final = "ConditionalCheckFailedException"

# Reservation sub-attributes, kept to one character each: they are repeated once per
# in-flight request inside a 400 KB item, and the names are never read by a human
# outside this file.
_HELD: Final = "p"
_EXPIRES: Final = "x"


def _n(value: int) -> dict[str, str]:
    return {"N": str(value)}


def _s(value: str) -> dict[str, str]:
    return {"S": value}


def _int_of(item: Mapping[str, Any], name: str, default: int = 0) -> int:
    raw = item.get(name)
    return int(raw["N"]) if isinstance(raw, Mapping) and "N" in raw else default


def _str_of(item: Mapping[str, Any], name: str, default: str = "") -> str:
    raw = item.get(name)
    return str(raw["S"]) if isinstance(raw, Mapping) and "S" in raw else default


def _reservations_of(item: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = item.get("reservations")
    if not isinstance(raw, Mapping) or "M" not in raw:
        return {}
    return dict(raw["M"])


def _hold_of(entry: Mapping[str, Any]) -> tuple[int, int]:
    """One reservation's ``(picos_held, expires_at)``."""
    inner = entry.get("M", {})
    return _int_of(inner, _HELD), _int_of(inner, _EXPIRES)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _budget_of(item: Mapping[str, Any]) -> Budget:
    """Decode a stored item into the record the rest of the gateway sees."""
    return Budget(
        scope=BudgetScope(kind=_str_of(item, "scope_kind"), id=_str_of(item, "scope_ref")),
        usd=from_picos(_int_of(item, "budget_picos")),
        window=_str_of(item, "budget_window"),
        window_id=_str_of(item, "window_id"),
        spent=from_picos(_int_of(item, "spent_picos")),
        reserved=from_picos(_int_of(item, "reserved_picos")),
        remaining=from_picos(_int_of(item, "remaining_picos")),
        reservations=len(_reservations_of(item)),
        expired_releases=_int_of(item, "expired_releases"),
        expired_released=from_picos(_int_of(item, "expired_released_picos")),
        created_at=_parse_iso(_str_of(item, "created_at")),
        updated_at=_parse_iso(_str_of(item, "updated_at")),
    )


def _rolled_view(item: Mapping[str, Any], window_id: str) -> Budget:
    """How a budget reads once its stored window has ended.

    No write happens: this is what the *next* request will see after it rolls the
    counters, and therefore the honest answer to "how much can this tenant spend now".
    Reporting last month's spend as current would be a dashboard that lies on the first
    of the month.
    """
    budget = _budget_of(item)
    return Budget(
        scope=budget.scope,
        usd=budget.usd,
        window=budget.window,
        window_id=window_id,
        spent=Decimal(0),
        reserved=Decimal(0),
        remaining=budget.usd,
        reservations=0,
        expired_releases=budget.expired_releases,
        expired_released=budget.expired_released,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
    )


# --- expressions --------------------------------------------------------------------
#
# Written out here, once, so the argument this phase makes is readable in one place
# rather than assembled from f-strings at four call sites.

#: The gate. Four clauses, all evaluated atomically against the committed item:
#: the scope has a budget; this window is still open; there is room; and this request
#: has not already reserved. The same statement then performs the deduction.
_RESERVE_CONDITION: Final = (
    "attribute_exists(scope_id) "
    "AND window_expires_at > :now "
    "AND remaining_picos >= :estimate "
    "AND attribute_not_exists(reservations.#rid)"
)

_RESERVE_UPDATE: Final = (
    "SET remaining_picos = remaining_picos - :estimate, "
    "reserved_picos = reserved_picos + :estimate, "
    "reservations.#rid = :hold, "
    "updated_at = :updated"
)

#: The first request of a new window rolls it. Compare-and-set on the *old* window id,
#: so exactly one of a burst wins and the losers retry the ordinary path.
_ROLL_CONDITION: Final = (
    "window_id = :old_window AND window_expires_at <= :now AND budget_picos >= :estimate"
)

_ROLL_UPDATE: Final = (
    "SET window_id = :window, window_expires_at = :expires, "
    "spent_picos = :zero, reserved_picos = :estimate, "
    "remaining_picos = budget_picos - :estimate, "
    "reservations = :holds, updated_at = :updated"
)

#: Settlement, and release (which is settlement at zero). Conditioned on the hold's own
#: amount, so a settlement can neither run twice nor move the counters by the wrong
#: number: if the hold is gone, or is not the size we think, nothing happens.
_SETTLE_CONDITION: Final = "reservations.#rid.#held = :held"

_SETTLE_UPDATE: Final = (
    "REMOVE reservations.#rid "
    "SET reserved_picos = reserved_picos - :held, "
    "spent_picos = spent_picos + :actual, "
    "remaining_picos = remaining_picos + :returned, "
    "updated_at = :updated"
)

_EXPIRE_UPDATE: Final = (
    "REMOVE reservations.#rid "
    "SET reserved_picos = reserved_picos - :held, "
    "remaining_picos = remaining_picos + :held, "
    "expired_releases = if_not_exists(expired_releases, :zero) + :one, "
    "expired_released_picos = if_not_exists(expired_released_picos, :zero) + :held, "
    "updated_at = :updated"
)


class DynamoBudgetStore(BudgetStore):
    """Budgets on DynamoDB conditional writes (BUILD_PLAN L2, assumption A1)."""

    __slots__ = ("_client", "_owns_client", "_table")

    def __init__(self, client: DynamoClient | None = None, *, table: str | None = None) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else DynamoClient()
        self._table = table if table is not None else budgets_table_name()

    @property
    def table(self) -> str:
        return self._table

    @property
    def client(self) -> DynamoClient:
        return self._client

    async def _ready(self) -> None:
        await self._client.ensure_table(self._table, partition_key="scope_id")

    async def _update(self, **kwargs: Any) -> dict[str, Any]:
        """One ``UpdateItem``, always asking for the item a failed condition saw.

        ``ReturnValuesOnConditionCheckFailure`` is what makes a refusal cost one call
        rather than two: the diagnosis in :meth:`_after_refusal` reads the very item the
        condition was evaluated against, with no second round trip and no window in
        which it could have changed underneath.
        """
        return await self._client.call(
            "update_item",
            TableName=self._table,
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
            **kwargs,
        )

    # --- admission ------------------------------------------------------------------

    async def reserve(
        self, scope: BudgetScope, *, request_id: str, usd: Decimal, when: datetime
    ) -> ReserveResult:
        await self._ready()
        # Rounded **up**: an estimate that reserved fractionally less than it settles
        # would be a gate that admits one request too many at the boundary.
        estimate = to_picos(usd, conservative=True)
        now = int(when.timestamp())
        expires = now + RESERVATION_TTL_S

        for _ in range(_MAX_ATTEMPTS):
            try:
                item = (
                    await self._update(
                        Key={"scope_id": _s(scope.key)},
                        ConditionExpression=_RESERVE_CONDITION,
                        UpdateExpression=_RESERVE_UPDATE,
                        ExpressionAttributeNames={"#rid": request_id},
                        ExpressionAttributeValues={
                            ":now": _n(now),
                            ":estimate": _n(estimate),
                            ":hold": {"M": {_HELD: _n(estimate), _EXPIRES: _n(expires)}},
                            ":updated": _s(_iso(when)),
                        },
                        ReturnValues="ALL_NEW",
                    )
                )["Attributes"]
            except ClientError as exc:
                if _code(exc) != _CONDITION_FAILED:
                    raise translate_dynamo_error(exc) from exc
                outcome = await self._after_refusal(
                    scope, exc.response.get("Item"), request_id, estimate, when
                )
                if outcome is not None:
                    return outcome
                continue
            return ReserveResult(
                status=ADMIT_RESERVED,
                reservation=Reservation(
                    scope=scope,
                    request_id=request_id,
                    usd=from_picos(estimate),
                    window_id=_str_of(item, "window_id"),
                    expires_at=datetime.fromtimestamp(expires, tz=UTC),
                ),
                budget=_budget_of(item),
            )

        # Every attempt lost a race. Refusing is the safe answer: admitting on a
        # contended item is exactly the mistake this file exists to prevent.
        return ReserveResult(status=ADMIT_EXCEEDED, budget=await self.get(scope, when=when))

    async def _after_refusal(
        self,
        scope: BudgetScope,
        old: Mapping[str, Any] | None,
        request_id: str,
        estimate: int,
        when: datetime,
    ) -> ReserveResult | None:
        """Diagnose a failed condition from the item it failed against.

        ``ReturnValuesOnConditionCheckFailure`` hands back the item the condition was
        evaluated against, so the four ways the condition can fail are told apart
        **without a second read** — which matters, because this is the path a refused
        request takes and a refusal should not cost more than a grant.

        Returns the final answer, or ``None`` meaning "state has been repaired, try the
        write again".
        """
        if not old:
            # No item at all: nobody configured a cap for this scope. Not a refusal.
            return ReserveResult(status=ADMIT_NO_BUDGET)

        reservations = _reservations_of(old)
        held = reservations.get(request_id)
        if held is not None:
            # This request already holds budget. Idempotent rather than an error: the
            # only way here is a retry of a request that succeeded.
            amount, expires_at = _hold_of(held)
            return ReserveResult(
                status=ADMIT_RESERVED,
                reservation=Reservation(
                    scope=scope,
                    request_id=request_id,
                    usd=from_picos(amount),
                    window_id=_str_of(old, "window_id"),
                    expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
                ),
                budget=_budget_of(old),
            )

        now = int(when.timestamp())
        if _int_of(old, "window_expires_at") <= now:
            return await self._roll(scope, old, request_id, estimate, when)

        # A crashed process's hold must never be the reason a live request is refused,
        # so the sweep happens *before* the refusal rather than on a timer. If it frees
        # anything, the write is retried; if it frees nothing, the refusal is real.
        swept = await self._release_expired(scope, reservations, when)
        if swept.released:
            return None

        if _int_of(old, "remaining_picos") < estimate:
            return ReserveResult(status=ADMIT_EXCEEDED, budget=_budget_of(old))
        # The condition failed for none of the reasons above, which means the item moved
        # between the read and this decision. Retry.
        return None

    async def _roll(
        self,
        scope: BudgetScope,
        old: Mapping[str, Any],
        request_id: str,
        estimate: int,
        when: datetime,
    ) -> ReserveResult | None:
        """Start the next window and take this request's hold in the same write."""
        window = _str_of(old, "budget_window")
        window_id, expires_at = window_for(window, when)
        if _int_of(old, "budget_picos") < estimate:
            # The request cannot fit even in a fresh window; rolling would not help.
            return ReserveResult(status=ADMIT_EXCEEDED, budget=_rolled_view(old, window_id))

        now = int(when.timestamp())
        hold_expires = now + RESERVATION_TTL_S
        try:
            item = (
                await self._update(
                    Key={"scope_id": _s(scope.key)},
                    ConditionExpression=_ROLL_CONDITION,
                    UpdateExpression=_ROLL_UPDATE,
                    ExpressionAttributeValues={
                        ":old_window": _s(_str_of(old, "window_id")),
                        ":now": _n(now),
                        ":estimate": _n(estimate),
                        ":window": _s(window_id),
                        ":expires": _n(expires_at),
                        ":zero": _n(0),
                        ":holds": {
                            "M": {
                                request_id: {"M": {_HELD: _n(estimate), _EXPIRES: _n(hold_expires)}}
                            }
                        },
                        ":updated": _s(_iso(when)),
                    },
                    ReturnValues="ALL_NEW",
                )
            )["Attributes"]
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            # Another request rolled it first. Retry the ordinary path against the
            # window they created.
            return None
        return ReserveResult(
            status=ADMIT_RESERVED,
            reservation=Reservation(
                scope=scope,
                request_id=request_id,
                usd=from_picos(estimate),
                window_id=window_id,
                expires_at=datetime.fromtimestamp(hold_expires, tz=UTC),
            ),
            budget=_budget_of(item),
        )

    # --- settlement -----------------------------------------------------------------

    async def settle(self, reservation: Reservation, *, usd: Decimal, when: datetime) -> bool:
        await self._ready()
        held = to_picos(reservation.usd)
        actual = to_picos(usd)
        try:
            await self._update(
                Key={"scope_id": _s(reservation.scope.key)},
                ConditionExpression=_SETTLE_CONDITION,
                UpdateExpression=_SETTLE_UPDATE,
                ExpressionAttributeNames={"#rid": reservation.request_id, "#held": _HELD},
                ExpressionAttributeValues={
                    ":held": _n(held),
                    ":actual": _n(actual),
                    # May be negative: a request that overran its estimate eats into
                    # the headroom of the next one, which is the honest consequence.
                    ":returned": _n(held - actual),
                    ":updated": _s(_iso(when)),
                },
            )
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            # The hold is gone — already settled, swept, or lost to a window roll. A
            # no-op, deliberately: settling twice would move the counters twice.
            return False
        return True

    async def sweep_expired(self, scope: BudgetScope, *, when: datetime) -> SweepResult:
        await self._ready()
        item = await self._get_item(scope)
        if item is None:
            return SweepResult()
        return await self._release_expired(scope, _reservations_of(item), when)

    async def _release_expired(
        self, scope: BudgetScope, reservations: Mapping[str, Any], when: datetime
    ) -> SweepResult:
        now = int(when.timestamp())
        released = 0
        total = 0
        for request_id, entry in reservations.items():
            amount, expires_at = _hold_of(entry)
            if expires_at > now:
                continue
            try:
                await self._update(
                    Key={"scope_id": _s(scope.key)},
                    ConditionExpression=_SETTLE_CONDITION,
                    UpdateExpression=_EXPIRE_UPDATE,
                    ExpressionAttributeNames={"#rid": request_id, "#held": _HELD},
                    ExpressionAttributeValues={
                        ":held": _n(amount),
                        ":zero": _n(0),
                        ":one": _n(1),
                        ":updated": _s(_iso(when)),
                    },
                )
            except ClientError as exc:
                if _code(exc) != _CONDITION_FAILED:
                    raise translate_dynamo_error(exc) from exc
                # Someone settled or swept it first. Exactly-once, by condition.
                continue
            released += 1
            total += amount
        return SweepResult(released=released, usd=from_picos(total))

    # --- reading and administration --------------------------------------------------

    async def _get_item(self, scope: BudgetScope) -> dict[str, Any] | None:
        try:
            result = await self._client.call(
                "get_item",
                TableName=self._table,
                Key={"scope_id": _s(scope.key)},
                # Strongly consistent: a budget read that could be a second behind is a
                # budget read nobody can act on during an incident.
                ConsistentRead=True,
            )
        except ClientError as exc:
            raise translate_dynamo_error(exc) from exc
        item = result.get("Item")
        return dict(item) if item else None

    async def get(self, scope: BudgetScope, *, when: datetime) -> Budget | None:
        """The budget as it applies at ``when``.

        **This read sweeps.** Expired holds are released before the numbers are
        reported, so an operator asking "how much is reserved" during an incident gets
        the true figure rather than one inflated by processes that died. Releasing an
        already-expired hold changes nothing that is still live, and it is idempotent —
        the trade is a GET with a side effect, taken deliberately and only on the admin
        path (the request path never calls this).
        """
        await self._ready()
        item = await self._get_item(scope)
        if item is None:
            return None
        if await self._sweep_item(scope, item, when):
            refreshed = await self._get_item(scope)
            item = refreshed if refreshed is not None else item
        now = int(when.timestamp())
        if _int_of(item, "window_expires_at") <= now:
            return _rolled_view(item, window_for(_str_of(item, "budget_window"), when)[0])
        return _budget_of(item)

    async def _sweep_item(
        self, scope: BudgetScope, item: Mapping[str, Any], when: datetime
    ) -> bool:
        reservations = _reservations_of(item)
        if not reservations:
            return False
        return bool((await self._release_expired(scope, reservations, when)).released)

    async def set_budget(
        self, scope: BudgetScope, *, usd: Decimal, window: str, when: datetime
    ) -> Budget:
        await self._ready()
        budget = to_picos(usd)
        window_id, expires_at = window_for(window, when)
        stamp = _iso(when)

        for _ in range(_MAX_ATTEMPTS):
            item = await self._get_item(scope)
            if item is None:
                created = await self._create(scope, budget, window, window_id, expires_at, stamp)
                if created is not None:
                    return created
                continue
            fresh_window = _str_of(item, "budget_window") != window or _int_of(
                item, "window_expires_at"
            ) <= int(when.timestamp())
            updated = (
                await self._reset(scope, item, budget, window, window_id, expires_at, stamp)
                if fresh_window
                else await self._reprice(scope, item, budget, stamp)
            )
            if updated is not None:
                return updated
        raise ControlPlaneUnavailable(
            f"budget for {scope.key} is being changed concurrently; try again"
        )

    async def _create(
        self,
        scope: BudgetScope,
        budget: int,
        window: str,
        window_id: str,
        expires_at: int,
        stamp: str,
    ) -> Budget | None:
        item = {
            "scope_id": _s(scope.key),
            "scope_kind": _s(scope.kind),
            "scope_ref": _s(scope.id),
            "budget_picos": _n(budget),
            "budget_window": _s(window),
            "window_id": _s(window_id),
            "window_expires_at": _n(expires_at),
            "spent_picos": _n(0),
            "reserved_picos": _n(0),
            "remaining_picos": _n(budget),
            "reservations": {"M": {}},
            "expired_releases": _n(0),
            "expired_released_picos": _n(0),
            "created_at": _s(stamp),
            "updated_at": _s(stamp),
        }
        try:
            await self._client.call(
                "put_item",
                TableName=self._table,
                Item=item,
                ConditionExpression="attribute_not_exists(scope_id)",
            )
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            return None  # somebody created it first; go round again and patch it
        return _budget_of(item)

    async def _reset(
        self,
        scope: BudgetScope,
        old: Mapping[str, Any],
        budget: int,
        window: str,
        window_id: str,
        expires_at: int,
        stamp: str,
    ) -> Budget | None:
        """Switch window type, or adopt a window that has already ended: fresh counters.

        A cap "per month" and a cap "for all time" count different things, so carrying
        a total across the change would answer neither question.
        """
        try:
            item = (
                await self._update(
                    Key={"scope_id": _s(scope.key)},
                    ConditionExpression="window_id = :seen_window AND budget_picos = :seen_budget",
                    UpdateExpression=(
                        "SET budget_picos = :budget, budget_window = :window, "
                        "window_id = :window_id, window_expires_at = :expires, "
                        "spent_picos = :zero, reserved_picos = :zero, "
                        "remaining_picos = :budget, reservations = :empty, "
                        "created_at = if_not_exists(created_at, :updated), updated_at = :updated"
                    ),
                    ExpressionAttributeValues={
                        ":seen_window": _s(_str_of(old, "window_id")),
                        ":seen_budget": _n(_int_of(old, "budget_picos")),
                        ":budget": _n(budget),
                        ":window": _s(window),
                        ":window_id": _s(window_id),
                        ":expires": _n(expires_at),
                        ":zero": _n(0),
                        ":empty": {"M": {}},
                        ":updated": _s(stamp),
                    },
                    ReturnValues="ALL_NEW",
                )
            )["Attributes"]
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            return None
        return _budget_of(item)

    async def _reprice(
        self, scope: BudgetScope, old: Mapping[str, Any], budget: int, stamp: str
    ) -> Budget | None:
        """Change the cap within the current window: ``remaining`` moves by the delta.

        Conditioned on the cap we read, so a concurrent change loses rather than being
        overwritten — and ``remaining`` is adjusted rather than recomputed, so spend and
        live holds are preserved exactly.
        """
        seen = _int_of(old, "budget_picos")
        if seen == budget:
            return _budget_of(old)
        try:
            item = (
                await self._update(
                    Key={"scope_id": _s(scope.key)},
                    ConditionExpression="budget_picos = :seen AND window_id = :seen_window",
                    UpdateExpression=(
                        "SET budget_picos = :budget, "
                        "remaining_picos = remaining_picos + :delta, updated_at = :updated"
                    ),
                    ExpressionAttributeValues={
                        ":seen": _n(seen),
                        ":seen_window": _s(_str_of(old, "window_id")),
                        ":budget": _n(budget),
                        ":delta": _n(budget - seen),
                        ":updated": _s(stamp),
                    },
                    ReturnValues="ALL_NEW",
                )
            )["Attributes"]
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            return None
        return _budget_of(item)

    async def clear_budget(self, scope: BudgetScope) -> bool:
        await self._ready()
        try:
            await self._client.call(
                "delete_item",
                TableName=self._table,
                Key={"scope_id": _s(scope.key)},
                ConditionExpression="attribute_exists(scope_id)",
            )
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            return False
        return True

    async def list_budgets(self, *, when: datetime) -> list[Budget]:
        """Every configured budget. A ``Scan``, and that is fine: this table holds one
        item per budgeted scope and nothing on a request path reads it."""
        await self._ready()
        budgets: list[Budget] = []
        start: dict[str, Any] | None = None
        now = int(when.timestamp())
        while True:
            kwargs: dict[str, Any] = {"TableName": self._table, "Limit": 100}
            if start is not None:
                kwargs["ExclusiveStartKey"] = start
            try:
                page = await self._client.call("scan", **kwargs)
            except ClientError as exc:
                raise translate_dynamo_error(exc) from exc
            for item in page.get("Items", []):
                budgets.append(
                    _rolled_view(item, window_for(_str_of(item, "budget_window"), when)[0])
                    if _int_of(item, "window_expires_at") <= now
                    else _budget_of(item)
                )
            start = page.get("LastEvaluatedKey")
            if not start:
                break
        return sorted(budgets, key=lambda budget: budget.scope.key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
