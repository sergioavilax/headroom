"""``RateLimitStore`` on DynamoDB — refill and consume as one conditional write.

The sentence this file exists to make true is the budget store's, one noun over:

> **Every consumption from a token bucket is a single conditional write to a single
> item.**

There is no read to be stale, no refill computed in application code, and no window in
which two racers can both decide they fit. ``headroom/core/limits.py`` explains *why* the
bucket is stored as a time rather than as a count of tokens — the short version is that a
``ConditionExpression`` compares an attribute to a value and does no arithmetic, so a
stored count can only be checked by reading it first, and reading it first is D-019.

**The item.** One per ``(scope, dimension)``::

    bucket_id     "tenant#<uuid>#requests"   partition key
    tat           theoretical arrival time, epoch NANOSECONDS  <- the whole bucket
    scope_kind    "tenant" | "key"           descriptive; never read by the gate
    scope_ref     the tenant or key id       descriptive
    dimension     "requests" | "tokens"      descriptive
    expires_at    epoch seconds, for DynamoDB TTL (H-035)
    updated_at    ISO stamp

``tat`` is the bucket. Everything else on the item is for a human or for the garbage
collector.

**Two branches, and neither of them is a read.** The one term GCRA needs that DynamoDB
cannot express is ``max(tat, now)``, so admission is attempted as two mutually exclusive
conditional writes:

.. code-block:: text

    hot   cond: tat > :now AND tat <= :ceiling     upd: SET tat = tat + :charge
    cold  cond: attribute_not_exists(tat) OR tat <= :now
                                                  upd: SET tat = :now_plus_charge

*Hot* is the bucket in use — ``tat`` already leads the clock, so the charge is added to
it. *Cold* is the bucket at rest or absent, where the charge starts from ``now``; that
branch is what stops an idle bucket accumulating unbounded credit, and removing it is
sabotage C in ``tests/test_rate_limit_hammer.py``. First use needs no separate create:
the cold branch's ``UpdateItem`` makes the item.

**Hot is attempted first, and the failure pays for the diagnosis.** Under load — the only
time a rate limiter's cost matters — the bucket is hot and one write is all that happens.
When hot fails, ``ReturnValuesOnConditionCheckFailure=ALL_OLD`` hands back the very item
the condition was evaluated against, so "cold" and "genuinely refused" are told apart with
**no second read**, and a refusal's ``retry-after`` is computed from the ``tat`` the
condition actually saw. That is the same trick the budget gate's refusal path uses
(H-032), applied to a different question.

**Nothing is cached, and nothing is refunded.** Not the bucket, not its config. The
non-refund rule and its consequences live one layer up, in ``headroom/policy/limits.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from botocore.exceptions import ClientError

from headroom.core.limits import (
    NANOS_PER_S,
    REFUSED_EXCEEDS_CAPACITY,
    REFUSED_RATE_LIMITED,
    WINDOW_S,
    BucketKey,
    BucketState,
    Consumption,
    RateLimitStore,
    available_units,
    burst_ns,
    emission_interval_ns,
    from_ns,
    reset_after_s,
    to_ns,
)
from headroom.db.dynamo import DynamoClient, buckets_table_name, translate_dynamo_error

__all__ = ["DynamoRateLimitStore"]

#: A conditional write that loses a race is retried, because losing means the item moved
#: under us and the answer may now be different. Bounded, for the reason the budget store
#: bounds its own: "keep trying until it works" on a contended item is how an admission
#: check becomes a latency incident. Exhausting the attempts refuses, never admits.
_MAX_ATTEMPTS: Final = 4

_CONDITION_FAILED: Final = "ConditionalCheckFailedException"

#: How long after its last use a bucket item may be reaped. Two windows: long enough that
#: a bucket in periodic use is never resurrected mid-flight, short enough that the table
#: holds roughly the scopes that are actually sending traffic. **Deleting an idle bucket
#: is exactly equivalent to leaving it**, because ``tat <= now`` and "no item" are the
#: same state to the cold branch — which is why a TTL is right here and was wrong for
#: budget reservations (H-032 rejects it there: deleting a reservation record without
#: decrementing its counter would strand the budget *and* destroy the evidence).
#:
#: The attribute is written on every consumption; **enabling** TTL on the table is
#: Terraform's job in Phase 9. Nothing here depends on it running, which is the only
#: honest way to depend on a background reaper with a 48-hour service target.
_TTL_S: Final = 2 * WINDOW_S


def _n(value: int) -> dict[str, str]:
    return {"N": str(value)}


def _s(value: str) -> dict[str, str]:
    return {"S": value}


def _tat_of(item: Mapping[str, Any] | None) -> int | None:
    """The stored ``tat``, or ``None`` when the bucket does not exist yet."""
    if not item:
        return None
    raw = item.get("tat")
    return int(raw["N"]) if isinstance(raw, Mapping) and "N" in raw else None


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


# --- expressions ----------------------------------------------------------------------
#
# Written out here, once, so the argument this file makes is readable in one place rather
# than assembled from f-strings at three call sites. Both conditions are bare
# attribute-to-value comparisons, which is the entire reason the bucket is a time.

#: The bucket is in use and has room: ``tat`` leads the clock but not by more than the
#: capacity this request would need to leave behind it.
_HOT_CONDITION: Final = "tat > :now AND tat <= :ceiling"
_HOT_UPDATE: Final = "SET tat = tat + :charge, updated_at = :updated, expires_at = :ttl"

#: The bucket is at rest, or has never existed: the charge starts from ``now``. This is
#: ``max(tat, now)`` — the clamp DynamoDB cannot compute — expressed as a condition.
_COLD_CONDITION: Final = "attribute_not_exists(tat) OR tat <= :now"
_COLD_UPDATE: Final = (
    "SET tat = :start, scope_kind = :scope_kind, scope_ref = :scope_ref, "
    "dimension = :dimension, updated_at = :updated, expires_at = :ttl"
)


class DynamoRateLimitStore(RateLimitStore):
    """Token buckets on DynamoDB conditional writes (BUILD_PLAN L2, assumption A1)."""

    __slots__ = ("_client", "_owns_client", "_table")

    def __init__(self, client: DynamoClient | None = None, *, table: str | None = None) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else DynamoClient()
        self._table = table if table is not None else buckets_table_name()

    @property
    def table(self) -> str:
        return self._table

    @property
    def client(self) -> DynamoClient:
        return self._client

    async def _ready(self) -> None:
        await self._client.ensure_table(self._table, partition_key="bucket_id")

    async def _update(self, **kwargs: Any) -> dict[str, Any]:
        """One ``UpdateItem``, always asking for the item a failed condition saw."""
        return await self._client.call(
            "update_item",
            TableName=self._table,
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
            **kwargs,
        )

    # --- admission --------------------------------------------------------------------

    async def consume(
        self, key: BucketKey, *, limit_per_min: int, cost: int, when: datetime
    ) -> Consumption:
        await self._ready()
        interval = emission_interval_ns(limit_per_min)
        capacity_ns = burst_ns(limit_per_min)
        charge = cost * interval
        now = to_ns(when)

        if charge > capacity_ns:
            # Larger than the bucket has ever held. Waiting cannot help, so nothing is
            # written and no `retry-after` is invented — see H-038.
            return Consumption(
                key=key,
                admitted=False,
                limit_per_min=limit_per_min,
                available=await self._available(key, limit_per_min, now),
                refusal=REFUSED_EXCEEDS_CAPACITY,
                cost=cost,
            )

        ceiling = now + capacity_ns - charge
        values = {
            ":now": _n(now),
            ":ceiling": _n(ceiling),
            ":charge": _n(charge),
            ":start": _n(now + charge),
            ":updated": _s(_iso(when)),
            ":ttl": _n(now // NANOS_PER_S + _TTL_S),
        }

        for _ in range(_MAX_ATTEMPTS):
            try:
                item = (
                    await self._update(
                        Key={"bucket_id": _s(key.id)},
                        ConditionExpression=_HOT_CONDITION,
                        UpdateExpression=_HOT_UPDATE,
                        ExpressionAttributeValues={
                            name: values[name]
                            for name in (":now", ":ceiling", ":charge", ":updated", ":ttl")
                        },
                        ReturnValues="ALL_NEW",
                    )
                )["Attributes"]
            except ClientError as exc:
                if _code(exc) != _CONDITION_FAILED:
                    raise translate_dynamo_error(exc) from exc
                outcome = await self._after_hot_failed(
                    key, exc.response.get("Item"), limit_per_min, cost, now, values
                )
                if outcome is not None:
                    return outcome
                continue
            return self._granted(key, limit_per_min, cost, item, now)

        # Every attempt lost a race on a contended bucket. Refusing is the safe answer:
        # admitting because the store was busy is the failure this file exists to prevent.
        return Consumption(
            key=key,
            admitted=False,
            limit_per_min=limit_per_min,
            available=0,
            retry_after_s=1,
            refusal=REFUSED_RATE_LIMITED,
            cost=cost,
        )

    async def _after_hot_failed(
        self,
        key: BucketKey,
        old: Mapping[str, Any] | None,
        limit_per_min: int,
        cost: int,
        now: int,
        values: Mapping[str, dict[str, str]],
    ) -> Consumption | None:
        """Diagnose a failed hot condition from the item it failed against.

        Three outcomes, told apart **without a second read** because
        ``ReturnValuesOnConditionCheckFailure`` returned the item the condition saw:

        * the bucket is absent or at rest → take the cold branch;
        * ``tat`` is beyond the ceiling → a real refusal, and the ``retry-after`` comes
          from the same ``tat``;
        * neither → the item moved between the condition and this decision. Retry.

        Returns the final answer, or ``None`` meaning "go round again".
        """
        tat = _tat_of(old)
        if tat is None or tat <= now:
            return await self._cold(key, limit_per_min, cost, now, values)

        ceiling = int(values[":ceiling"]["N"])
        if tat > ceiling:
            return Consumption(
                key=key,
                admitted=False,
                limit_per_min=limit_per_min,
                available=available_units(tat, now, limit_per_min),
                # Exactly how long until this request fits: the excess of `tat` over the
                # ceiling it had to be under. Floored at one second, because a
                # `retry-after: 0` is an invitation to the retry storm a 429 is supposed
                # to damp.
                retry_after_s=max(1, -(-(tat - ceiling) // NANOS_PER_S)),
                refusal=REFUSED_RATE_LIMITED,
                cost=cost,
            )
        return None

    async def _cold(
        self,
        key: BucketKey,
        limit_per_min: int,
        cost: int,
        now: int,
        values: Mapping[str, dict[str, str]],
    ) -> Consumption | None:
        """Consume from a bucket at rest, or create it. ``None`` means "somebody else
        moved it first" — go round and try the hot branch against what they left."""
        try:
            item = (
                await self._update(
                    Key={"bucket_id": _s(key.id)},
                    ConditionExpression=_COLD_CONDITION,
                    UpdateExpression=_COLD_UPDATE,
                    ExpressionAttributeValues={
                        ":now": values[":now"],
                        ":start": values[":start"],
                        ":updated": values[":updated"],
                        ":ttl": values[":ttl"],
                        ":scope_kind": _s(key.scope_kind),
                        ":scope_ref": _s(key.scope_id),
                        ":dimension": _s(key.dimension),
                    },
                    ReturnValues="ALL_NEW",
                )
            )["Attributes"]
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            return None
        return self._granted(key, limit_per_min, cost, item, now)

    @staticmethod
    def _granted(
        key: BucketKey, limit_per_min: int, cost: int, item: Mapping[str, Any], now: int
    ) -> Consumption:
        tat = _tat_of(item)
        assert tat is not None  # the write that just succeeded set it
        return Consumption(
            key=key,
            admitted=True,
            limit_per_min=limit_per_min,
            available=available_units(tat, now, limit_per_min),
            cost=cost,
        )

    # --- reading ------------------------------------------------------------------------

    async def state(self, key: BucketKey, *, limit_per_min: int, when: datetime) -> BucketState:
        await self._ready()
        now = to_ns(when)
        tat = await self._get_tat(key)
        # An absent bucket and a bucket at rest are the same state, so a read never has to
        # write one — which is also what makes the TTL on this table safe.
        effective = now if tat is None or tat < now else tat
        return BucketState(
            key=key,
            limit_per_min=limit_per_min,
            available=available_units(effective, now, limit_per_min),
            reset_after_s=reset_after_s(effective, now),
            reset_at=from_ns(effective),
        )

    async def _available(self, key: BucketKey, limit_per_min: int, now: int) -> int:
        tat = await self._get_tat(key)
        return limit_per_min if tat is None else available_units(tat, now, limit_per_min)

    async def _get_tat(self, key: BucketKey) -> int | None:
        try:
            result = await self._client.call(
                "get_item",
                TableName=self._table,
                Key={"bucket_id": _s(key.id)},
                # Strongly consistent: a limit an operator reads during an incident must
                # not be a second behind the traffic they are watching.
                ConsistentRead=True,
            )
        except ClientError as exc:
            raise translate_dynamo_error(exc) from exc
        return _tat_of(result.get("Item"))

    async def clear(self, key: BucketKey) -> bool:
        await self._ready()
        try:
            await self._client.call(
                "delete_item",
                TableName=self._table,
                Key={"bucket_id": _s(key.id)},
                ConditionExpression="attribute_exists(bucket_id)",
            )
        except ClientError as exc:
            if _code(exc) != _CONDITION_FAILED:
                raise translate_dynamo_error(exc) from exc
            return False
        return True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
