"""``/admin/cache`` — switch a tenant's cache on, tune it, and empty it.

Four routes behind the same root admin token as the rest of ``/admin`` (H-019), shaped
like ``/admin/limits`` on purpose, with three differences that are each a decision.

**PUT replaces, and an absent field means *the documented default*** — not "unchanged",
and not "unlimited" as it does for a rate limit. There is no such thing as an unlimited
TTL or an unlimited similarity threshold; there is only "the number this build ships
with", and storing NULL is how a tenant says *follow the default wherever it goes*
rather than pinning today's value forever.

**Enabling ``semantic`` probes the embedder, in the request that asked for it.** A model
that cannot be loaded — the ``embed`` extra not installed, weights missing from the image
— is an operator's problem and it surfaces here, naming the fix, rather than as a quiet
degradation on some tenant's traffic an hour later. This is the one place the gateway
deliberately pays the cost of loading a model synchronously.

**DELETE disables *and* purges, and the order matters.** Flipping the mode alone would
leave every entry in place while the ``Principal`` carrying the old mode lives out the
auth cache's five seconds (H-018) — a window in which another process could still serve a
hit. Purging alone would leave the tenant caching again on the next miss. Doing both, with
the purge second, closes the window from the data side: after this returns there is
nothing left to serve, whatever any process still believes about the mode.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from headroom.api.admin import AdminAuth, AdminError
from headroom.api.deps import GatewayDep
from headroom.api.gateway import Gateway
from headroom.core.cache import (
    CACHE_DISABLED,
    CACHE_MODES,
    CACHE_SEMANTIC,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TTL_S,
    CacheSettings,
)
from headroom.core.errors import ConfigurationError
from headroom.core.storage import Tenant

__all__ = ["router"]

router = APIRouter(prefix="/admin/cache", tags=["admin", "cache"])


class CacheSet(BaseModel):
    """``PUT /admin/cache/{tenant_id}``."""

    model_config = ConfigDict(extra="forbid")

    #: ``disabled`` | ``exact`` | ``semantic``. Required, and deliberately so: there is
    #: no defaulting one's way into a cache being on.
    mode: str
    ttl_s: int | None = Field(default=None, ge=1)
    #: A plain JSON number, unlike ``/admin/budgets``'s quoted money. A similarity
    #: threshold is a comparison bound against a cosine that was a float before it got
    #: here; nothing about it is exact-decimal arithmetic. The column is
    #: ``NUMERIC(5,4)`` so that what is PUT is what is GET, which is a round-trip
    #: property rather than a monetary one.
    #:
    #: Bounded strictly on both sides for the reason migration 0005 states: 0 admits
    #: anything as "similar", and 1 is the exact layer with extra steps.
    similarity_threshold: float | None = Field(default=None, gt=0.0, lt=1.0)

    def as_settings(self) -> CacheSettings:
        return CacheSettings(
            mode=self.mode, ttl_s=self.ttl_s, similarity_threshold=self.similarity_threshold
        )


class CacheView(BaseModel):
    """One tenant's cache policy, the values actually in force, and what is stored."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    tenant_name: str
    mode: str
    #: ``None`` when the tenant follows the default. Reported beside the effective value
    #: rather than instead of it, so an operator can tell a pin from a default at a
    #: glance — the distinction that decides whether changing the default moves them.
    ttl_s: int | None
    similarity_threshold: float | None
    effective_ttl_s: int
    effective_similarity_threshold: float
    #: Which vector space this deployment's entries live in. Part of every semantic
    #: query, so an operator who changes it needs to see that it changed.
    embedding_model: str
    entries: int
    semantic_entries: int
    body_bytes: int


async def _tenant(gateway: Gateway, tenant_id: str) -> Tenant:
    tenant = await gateway.store.get_tenant(tenant_id)
    if tenant is None:
        raise AdminError(status.HTTP_404_NOT_FOUND, "tenant_not_found", f"no tenant {tenant_id!r}")
    return tenant


async def _view(gateway: Gateway, tenant: Tenant) -> CacheView:
    stats = await gateway.cache.store.stats(tenant.id)
    settings = tenant.cache
    return CacheView(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        mode=settings.mode,
        ttl_s=settings.ttl_s,
        similarity_threshold=settings.similarity_threshold,
        effective_ttl_s=settings.ttl,
        effective_similarity_threshold=settings.threshold,
        embedding_model=gateway.cache.embedder.model_id,
        entries=stats.entries,
        semantic_entries=stats.semantic_entries,
        body_bytes=stats.body_bytes,
    )


@router.get("", response_model=list[CacheView], dependencies=[AdminAuth])
async def list_cache(gateway: GatewayDep) -> list[CacheView]:
    """Every tenant with caching switched on, in creation order.

    Disabled tenants do not appear, matching ``/admin/limits``: the listing answers "who
    is caching", and every tenant not in it is the default answer.
    """
    return [
        await _view(gateway, tenant)
        for tenant in await gateway.store.list_tenants()
        if tenant.cache.enabled
    ]


@router.get("/{tenant_id}", response_model=CacheView, dependencies=[AdminAuth])
async def get_cache(tenant_id: str, gateway: GatewayDep) -> CacheView:
    """One tenant's policy and entry counts.

    A disabled tenant is a 200 with ``mode: "disabled"``, not a 404 — the same
    distinction ``/admin/limits`` draws against ``/admin/budgets``: a budget is a record
    that exists or does not, while a cache policy is a property every tenant has and most
    tenants have switched off.
    """
    return await _view(gateway, await _tenant(gateway, tenant_id))


@router.put("/{tenant_id}", response_model=CacheView, dependencies=[AdminAuth])
async def set_cache(
    tenant_id: str, body: Annotated[CacheSet, ...], gateway: GatewayDep
) -> CacheView:
    """Replace a tenant's cache policy. Existing entries are kept.

    Kept deliberately: every stored entry was eligible when it was written and is still a
    complete answer to the request that keyed it. Lowering a threshold makes more of them
    reachable, raising it makes fewer, and neither changes what any single one of them
    *says*. Narrowing ``semantic`` to ``exact`` likewise only narrows what can match. The
    one change that must not merely narrow is switching off, which is ``DELETE``.
    """
    if body.mode not in CACHE_MODES:
        # The literal, not `status.HTTP_422_*` — the convention `headroom/api/budgets.py`
        # set: Starlette 1.6 deprecates the old spelling, the replacement is not in every
        # version this runs against, and a new deprecation warning in the suite is a real
        # cost (Phase 0's gate counts them).
        raise AdminError(
            422,
            "unknown_cache_mode",
            f"mode must be one of {', '.join(CACHE_MODES)}, got {body.mode!r}",
        )
    tenant = await _tenant(gateway, tenant_id)
    if body.mode == CACHE_SEMANTIC:
        _require_embedder(gateway)
    updated = await gateway.store.set_cache_settings(tenant.id, body.as_settings())
    assert updated is not None  # the tenant was resolved one line above
    # The policy rides the Principal (H-037's placement), so the process that made the
    # change has to drop its cached copy or the very next request here would still be
    # using the old mode — the same invalidation, and the same argument, as a scope or a
    # limit change.
    gateway.authenticator.cache.invalidate_tenant(tenant.id)
    return await _view(gateway, updated)


@router.delete("/{tenant_id}", response_model=CacheView, dependencies=[AdminAuth])
async def clear_cache(tenant_id: str, gateway: GatewayDep) -> CacheView:
    """Disable caching for a tenant **and** delete everything it has stored.

    The incident-response route, and the only operation here that has to be more than a
    configuration change. See the module docstring for why the purge comes second.
    """
    tenant = await _tenant(gateway, tenant_id)
    updated = await gateway.store.set_cache_settings(tenant.id, CacheSettings(mode=CACHE_DISABLED))
    assert updated is not None  # resolved one line above
    gateway.authenticator.cache.invalidate_tenant(tenant.id)
    await gateway.cache.store.purge_tenant(tenant.id)
    return await _view(gateway, updated)


def _require_embedder(gateway: Gateway) -> None:
    """Load the model now, so a broken one is the operator's 503 and not a tenant's.

    ``ConfigurationError`` already names the missing extra (H-009's rule: a 500 that says
    which knob is wrong is not the generic 500 the plan forbids); this turns it into a
    503, because the gateway is not misconfigured for anything *else* it is doing and the
    condition clears the moment the model is available.
    """
    try:
        gateway.cache.embedder.resolve()
    except ConfigurationError as exc:
        raise AdminError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "embedder_unavailable", exc.message
        ) from exc


#: Re-exported so the README and the tests quote one source for the shipped defaults.
DEFAULTS = {"ttl_s": DEFAULT_TTL_S, "similarity_threshold": DEFAULT_SIMILARITY_THRESHOLD}
