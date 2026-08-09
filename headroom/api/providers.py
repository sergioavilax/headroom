"""``/admin/providers`` — what this process can reach, and what its breakers think.

Three routes behind the same root admin token as the rest of ``/admin`` (H-019), shaped
like ``/admin/limits`` on purpose: one place that reports *the configuration and the
live state together*, because "why is my traffic coming from the fallback" is answered by
neither on its own. Phase 7's provider-health tiles read this; the Phase 6 kill demo is
watched through it.

**Everything here is per process, and the listing says so.** A breaker is not a fact
about the world — it is a record of what *this* task has been able to reach — so a
Fargate deployment with four tasks has four independent opinions, deliberately
(docs/DECISIONS.md H-052). An operator reading this page is reading one gateway's
experience, which is the only thing any single gateway can honestly report.

**The chain is reported beside the health**, so the two questions an operator actually
has — *is vllm_a healthy* and *where does traffic go when it is not* — are one GET rather
than a GET plus a memory of the YAML file.

**``DELETE`` closes a breaker and forgets the window.** The incident-response route, and
the direct analogue of ``DELETE /admin/limits`` (H-037): an operator who has just fixed a
provider should not have to wait out a cooldown to prove it, and a breaker that only the
passage of time can close is a breaker nobody trusts at 3 a.m. Lifetime counters survive,
because they are the record of what happened.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict

from headroom.api.admin import AdminAuth, AdminError
from headroom.api.deps import GatewayDep
from headroom.api.gateway import Gateway
from headroom.policy.health import HealthSnapshot

__all__ = ["router"]

router = APIRouter(prefix="/admin/providers", tags=["admin", "providers"])


class RouteView(BaseModel):
    """One routing rule that can reach this provider, and the chain behind it."""

    model_config = ConfigDict(extra="forbid")

    dialect: str
    prefix: str
    #: Primary first, then fallbacks, in the order the executor will try them.
    chain: list[str]
    #: The attempt sequence the chain implies — it wraps when ``max_attempts`` exceeds
    #: the chain, which is how a single-provider route asks to be retried.
    attempts: list[str]


class ProviderView(BaseModel):
    """One provider: what it is, what it has been doing, and what the breaker decided."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    #: ``closed`` | ``open`` | ``half_open``.
    state: str
    samples: int
    failures: int
    failure_ratio: float
    consecutive_failures: int
    total_successes: int
    total_failures: int
    #: ``None`` until something completes. A provider that has only ever failed has no
    #: latency, and reporting ``0`` for that would be H-025's mistake in a new column.
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    last_error: str | None
    #: Seconds until an open breaker allows a probe. ``None`` unless open.
    reopen_in_s: float | None
    #: Every route that can send traffic here, with its chain. Empty means the provider
    #: is configured but unreachable by any rule — worth seeing, and easy to miss.
    routes: list[RouteView]

    @classmethod
    def of(cls, snapshot: HealthSnapshot, routes: list[RouteView]) -> ProviderView:
        return cls(
            name=snapshot.provider,
            kind=snapshot.kind,
            state=snapshot.state,
            samples=snapshot.samples,
            failures=snapshot.failures,
            failure_ratio=snapshot.failure_ratio,
            consecutive_failures=snapshot.consecutive_failures,
            total_successes=snapshot.total_successes,
            total_failures=snapshot.total_failures,
            p50_latency_ms=snapshot.p50_latency_ms,
            p95_latency_ms=snapshot.p95_latency_ms,
            last_error=snapshot.last_error,
            reopen_in_s=snapshot.reopen_in_s,
            routes=routes,
        )


def _routes_for(gateway: Gateway, provider: str) -> list[RouteView]:
    """Every rule whose chain contains ``provider``, in dialect then prefix order."""
    views: list[RouteView] = []
    for dialect in gateway.routing.dialects():
        for rule in gateway.routing.rules_for(dialect):
            if provider not in rule.chain:
                continue
            views.append(
                RouteView(
                    dialect=dialect,
                    prefix=rule.prefix,
                    chain=list(rule.chain),
                    attempts=list(rule.attempts()),
                )
            )
    return views


def _require_known(gateway: Gateway, name: str) -> None:
    if name not in gateway.registry:
        known = ", ".join(gateway.registry.names()) or "none"
        raise AdminError(
            status.HTTP_404_NOT_FOUND,
            "provider_not_found",
            f"no provider {name!r} is configured (configured: {known})",
        )


@router.get("", response_model=list[ProviderView], dependencies=[AdminAuth])
async def list_providers(gateway: GatewayDep) -> list[ProviderView]:
    """Every configured provider, by name, with its health and its routes.

    Includes providers that have never served a request — they appear ``closed`` with
    zero samples, because "configured and idle" and "not configured" are different facts
    and only one of them is a problem.
    """
    return [
        ProviderView.of(gateway.health.snapshot(name), _routes_for(gateway, name))
        for name in gateway.registry.names()
    ]


@router.get("/{name}", response_model=ProviderView, dependencies=[AdminAuth])
async def get_provider(name: str, gateway: GatewayDep) -> ProviderView:
    _require_known(gateway, name)
    return ProviderView.of(gateway.health.snapshot(name), _routes_for(gateway, name))


@router.delete("/{name}/health", response_model=ProviderView, dependencies=[AdminAuth])
async def clear_health(name: str, gateway: GatewayDep) -> ProviderView:
    """Close this provider's breaker and forget its window. Incident response.

    Spelled ``/{name}/health`` rather than ``/{name}`` deliberately: a ``DELETE`` on the
    provider itself would read as *remove this provider*, which this emphatically is not
    — providers come from ``config/routing.yaml`` and a running gateway never grows or
    loses one. What is being deleted is the observation history, which is exactly what an
    operator means by "I fixed it, stop skipping it".
    """
    _require_known(gateway, name)
    gateway.health.clear(name)
    return ProviderView.of(gateway.health.snapshot(name), _routes_for(gateway, name))
