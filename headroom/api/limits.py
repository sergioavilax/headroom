"""``/admin/limits`` — set a token-bucket limit, and see what is left of the bucket.

Four routes behind the same root admin token as the rest of ``/admin`` (H-019), sitting
beside ``/admin/budgets`` and shaped like it on purpose: one place to read *the
configuration and the live state together*, because "why did that request get a 429" is
answered by neither on its own.

**The config and the bucket come from two different datastores, and the view joins them.**
The limits are Postgres columns on the tenant or the key (H-037, BUILD_PLAN L2); the
bucket is a DynamoDB item holding one number. So a GET here reads the control plane for
what the limit *is* and the bucket store for what is *left of it right now*, and reports
both. Nothing on the request path does this: reading a bucket before consuming from it is
precisely the bug the whole design avoids, and this route is not on that path.

**PUT replaces, it does not patch.** A dimension absent from the body is *unlimited*, not
*unchanged* — because that is the only reading under which the API can express "remove
this limit" at all, and a limits API that can only ever tighten is a trap during an
incident. ``DELETE`` is the same operation with both dimensions cleared, plus a reset of
the buckets themselves: a limit lowered by mistake leaves ``tat`` minutes into the future,
and waiting it out is not an incident response.

**A change bites immediately here, and within the auth cache's TTL everywhere else.**
The limits ride the ``Principal`` (H-037), so these routes invalidate their own process's
cache entry exactly as a scope change does — the same guarantee, the same five seconds,
and the same sentence as H-018.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from headroom.api.admin import AdminAuth, AdminError
from headroom.api.deps import GatewayDep
from headroom.api.gateway import Gateway
from headroom.core.limits import (
    DIMENSIONS,
    SCOPE_KEY,
    SCOPE_TENANT,
    SCOPES,
    BucketKey,
    RateLimit,
)

__all__ = ["router"]

router = APIRouter(prefix="/admin/limits", tags=["admin", "limits"])


def _now() -> datetime:
    return datetime.now(UTC)


class LimitSet(BaseModel):
    """``PUT /admin/limits/{scope_kind}/{scope_id}``.

    Both fields are plain JSON integers, unlike ``/admin/budgets``'s quoted money: a
    request count is a count, it is exact in a double up to 2^53, and there is nothing
    here for floating point to lose. ``null`` — or an omitted field — means *unlimited*.
    """

    model_config = ConfigDict(extra="forbid")

    #: At least 1. Zero would mean "admit nothing", which already has two better
    #: spellings (deactivate the tenant, revoke the key) and one bad property: the
    #: emission interval is 60s/limit, and zero has none. ``migrations/0004`` carries
    #: the same constraint, so the rule holds against a hand-written UPDATE too.
    requests_per_min: int | None = Field(default=None, ge=1)
    tokens_per_min: int | None = Field(default=None, ge=1)

    def as_limits(self) -> RateLimit:
        return RateLimit(requests_per_min=self.requests_per_min, tokens_per_min=self.tokens_per_min)


class BucketView(BaseModel):
    """One dimension's live bucket. Only present for a dimension that has a limit."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    limit_per_min: int
    #: Units the bucket holds right now — what the next request will be measured against.
    available: int
    #: Seconds until the bucket is full again. ``0`` for a bucket at rest.
    reset_after_s: int
    reset_at: datetime


class LimitView(BaseModel):
    """One scope's configured limits and the state of each bucket behind them."""

    model_config = ConfigDict(extra="forbid")

    scope: str
    scope_kind: str
    scope_id: str
    #: The tenant or key's own name, so a listing is readable without a second lookup.
    name: str
    requests_per_min: int | None
    tokens_per_min: int | None
    buckets: list[BucketView]


async def _resolve(gateway: Gateway, scope_kind: str, scope_id: str) -> tuple[str, RateLimit]:
    """The scope's display name and its configured limits, or a 404.

    Checked against the control plane rather than assumed, for ``/admin/budgets``'s
    reason: a typo'd id must not quietly configure a limit for something nobody will
    ever authenticate as.
    """
    if scope_kind not in SCOPES:
        raise AdminError(
            status.HTTP_404_NOT_FOUND,
            "unknown_scope",
            f"scope kind must be one of {', '.join(SCOPES)}, got {scope_kind!r}",
        )
    if scope_kind == SCOPE_TENANT:
        tenant = await gateway.store.get_tenant(scope_id)
        if tenant is None:
            raise AdminError(
                status.HTTP_404_NOT_FOUND, "tenant_not_found", f"no tenant {scope_id!r}"
            )
        return tenant.name, tenant.limits
    key = await gateway.store.get_key(scope_id)
    if key is None:
        raise AdminError(status.HTTP_404_NOT_FOUND, "key_not_found", f"no key {scope_id!r}")
    return key.name, key.limits


async def _view(
    gateway: Gateway, scope_kind: str, scope_id: str, name: str, limits: RateLimit
) -> LimitView:
    """Join the configuration with the live buckets behind it."""
    buckets: list[BucketView] = []
    when = _now()
    for dimension in DIMENSIONS:
        limit = limits.per_min(dimension)
        if limit is None:
            continue
        key = BucketKey(scope_kind=scope_kind, scope_id=scope_id, dimension=dimension)
        state = await gateway.limits.store.state(key, limit_per_min=limit, when=when)
        buckets.append(
            BucketView(
                dimension=dimension,
                limit_per_min=limit,
                available=state.available,
                reset_after_s=state.reset_after_s,
                reset_at=state.reset_at,
            )
        )
    return LimitView(
        scope=f"{scope_kind}#{scope_id}",
        scope_kind=scope_kind,
        scope_id=scope_id,
        name=name,
        requests_per_min=limits.requests_per_min,
        tokens_per_min=limits.tokens_per_min,
        buckets=buckets,
    )


@router.get("", response_model=list[LimitView], dependencies=[AdminAuth])
async def list_limits(gateway: GatewayDep) -> list[LimitView]:
    """Every scope that has a limit. Scopes with none do not appear — they are unlimited.

    Tenants first, then keys, each in creation order: the same shape ``/admin/budgets``
    lists in, and the order an operator built them in.
    """
    views: list[LimitView] = []
    for tenant in await gateway.store.list_tenants():
        if tenant.limits.configured:
            views.append(await _view(gateway, SCOPE_TENANT, tenant.id, tenant.name, tenant.limits))
    for key in await gateway.store.list_keys():
        if key.limits.configured:
            views.append(await _view(gateway, SCOPE_KEY, key.id, key.name, key.limits))
    return views


@router.get("/{scope_kind}/{scope_id}", response_model=LimitView, dependencies=[AdminAuth])
async def get_limits(scope_kind: str, scope_id: str, gateway: GatewayDep) -> LimitView:
    """One scope's limits and the live state of its buckets.

    An unlimited scope is a 200 with nulls and an empty ``buckets`` list, not a 404: the
    scope exists, and "this tenant has no limit" is an answer rather than an absence.
    (``/admin/budgets`` 404s in the same situation, and the difference is deliberate —
    a budget is a *record* that exists or does not; a limit is a *property* of a tenant
    that is always defined, and sometimes defined as unlimited.)
    """
    name, limits = await _resolve(gateway, scope_kind, scope_id)
    return await _view(gateway, scope_kind, scope_id, name, limits)


@router.put("/{scope_kind}/{scope_id}", response_model=LimitView, dependencies=[AdminAuth])
async def set_limits(
    scope_kind: str, scope_id: str, body: Annotated[LimitSet, ...], gateway: GatewayDep
) -> LimitView:
    """Replace a scope's limits. An absent dimension is unlimited, not unchanged.

    Buckets are **not** reset by a change. Raising a limit gives the existing bucket more
    room immediately (its ``tat`` is unchanged and the ceiling it is compared against
    moves out); lowering one leaves whatever the scope has already consumed consumed,
    which is the honest behaviour — a limit lowered mid-minute should not hand back the
    traffic that was already admitted under the old one. ``DELETE`` is how an operator
    asks for a reset.
    """
    name, _ = await _resolve(gateway, scope_kind, scope_id)
    limits = body.as_limits()
    await _write(gateway, scope_kind, scope_id, limits)
    return await _view(gateway, scope_kind, scope_id, name, limits)


@router.delete("/{scope_kind}/{scope_id}", response_model=LimitView, dependencies=[AdminAuth])
async def clear_limits(scope_kind: str, scope_id: str, gateway: GatewayDep) -> LimitView:
    """Remove a scope's limits **and** empty its buckets. The incident-response route.

    Both halves matter. Clearing the configuration alone would leave a bucket whose
    ``tat`` sits minutes in the future, and nothing would consume from it again — which is
    harmless but confusing on the next GET. Emptying the buckets alone would leave the
    limit in force. Doing both is what an operator means by "turn this off".
    """
    name, _ = await _resolve(gateway, scope_kind, scope_id)
    await _write(gateway, scope_kind, scope_id, RateLimit())
    for dimension in DIMENSIONS:
        await gateway.limits.store.clear(
            BucketKey(scope_kind=scope_kind, scope_id=scope_id, dimension=dimension)
        )
    return await _view(gateway, scope_kind, scope_id, name, RateLimit())


async def _write(gateway: Gateway, scope_kind: str, scope_id: str, limits: RateLimit) -> None:
    """Persist the limits and make them bite in this process on the very next request."""
    if scope_kind == SCOPE_TENANT:
        await gateway.store.set_tenant_limits(scope_id, limits)
        # A narrowed limit has to bite now, not in five seconds — the same invalidation,
        # and the same argument, as a narrowed scope (`headroom/api/admin.py`).
        gateway.authenticator.cache.invalidate_tenant(scope_id)
        return
    await gateway.store.set_key_limits(scope_id, limits)
    gateway.authenticator.cache.invalidate_key(scope_id)
