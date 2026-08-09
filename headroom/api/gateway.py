"""The ``Gateway``: configuration, providers, and routing, assembled once at startup.

One object rather than three module-level globals, because the test suite builds a
mock-only gateway per test and the Phase 9/10 deployments will build one per process
with different config. Nothing here is a singleton, and nothing reads configuration at
request time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from headroom.cache.embedding import load_embedder
from headroom.cache.gate import ResponseCache
from headroom.core.budgets import BudgetStore
from headroom.core.cache import ResponseCacheStore
from headroom.core.config import ADMIN_TOKEN_ENV, GatewayConfig, load_config
from headroom.core.errors import ConfigurationError
from headroom.core.ledger import LedgerStore
from headroom.core.limits import RateLimitStore
from headroom.core.storage import TenantStore
from headroom.db.buckets import DynamoRateLimitStore
from headroom.db.budgets import DynamoBudgetStore
from headroom.db.cache import PostgresResponseCacheStore
from headroom.db.ledger import PostgresLedgerStore
from headroom.db.tenants import PostgresTenantStore
from headroom.metering.meter import Meter
from headroom.metering.prices import PriceBook, load_price_book
from headroom.metering.writer import LedgerWriter
from headroom.policy.auth import Authenticator
from headroom.policy.budgets import BudgetGate
from headroom.policy.failover import Failover
from headroom.policy.health import HealthTracker
from headroom.policy.limits import RateLimiter
from headroom.policy.routing import RoutingTable

# Imported for their registration side effects: each module calls `register_kind` at
# import time, and a kind that is never imported is a kind config cannot name. Doing it
# here — at the one place that builds a gateway — keeps `headroom.providers.__init__`
# free of imports and the dependency explicit.
from headroom.providers import anthropic as _anthropic  # noqa: F401
from headroom.providers import mock as _mock  # noqa: F401
from headroom.providers import openai_compat as _openai_compat  # noqa: F401
from headroom.providers.base import Provider
from headroom.providers.registry import ProviderRegistry, kind_dialects, provider_kinds

__all__ = ["Gateway", "build_gateway"]


@dataclass(slots=True)
class Gateway:
    """Everything one running gateway needs to serve a request.

    Phase 2 adds the control plane — the store, the authenticator that reads it, and
    the root admin token — as fields rather than as globals, for the same reason the
    registry is one: the test suite builds a complete gateway per test, and Phase 9
    builds one per process against different backing services.

    ``admin_token`` is ``None`` when ``HEADROOM_ADMIN_TOKEN`` is unset, and that is not
    a synonym for "no authentication required": the admin API refuses every request
    with 503 in that state (H-019).
    """

    config: GatewayConfig
    registry: ProviderRegistry
    routing: RoutingTable
    store: TenantStore
    authenticator: Authenticator
    #: Phase 3. The ledger is where the meter's rows land and what ``/admin/usage``
    #: reads; the meter holds the price book and the writer. Fields, not globals, for
    #: the same reason everything else here is: one gateway per test, one per process.
    ledger: LedgerStore
    meter: Meter
    #: Phase 4. The reservation-based budget gate: admission before the upstream is
    #: opened, settlement wherever the request ends. Beside the meter rather than
    #: inside it, because they answer different questions — the meter says what a
    #: request cost, the gate says whether it was allowed to.
    budgets: BudgetGate
    #: Phase 4b. Token buckets, per key and per tenant, on the same conditional-write
    #: discipline. Ahead of the budget gate on the request path rather than beside it:
    #: it is the load shedder, and what it sheds is exactly the burst the budget gate
    #: serialises on (docs/DECISIONS.md H-039).
    limits: RateLimiter
    #: Phase 5. The response cache, consulted between the rate limiter and the budget
    #: gate (docs/DECISIONS.md H-046). Constructed always and used only by tenants that
    #: switched it on: a tenant in the default ``disabled`` mode never reaches the store
    #: and never builds the embedder, so the feature costs a deployment nothing until
    #: somebody asks for it.
    cache: ResponseCache
    #: Phase 6. What this process has been able to reach, and the breaker built on it.
    #: In memory and per process on purpose — a breaker is not a fact about the world,
    #: it is a record of what *this* task can talk to (docs/DECISIONS.md H-052).
    health: HealthTracker
    #: Phase 6. The only object in the codebase allowed to call a provider twice for one
    #: request. It replaces exactly one line of the proxy — the ``provider.open`` call —
    #: and everything around it is unchanged and unaware.
    failover: Failover
    admin_token: str | None = None

    def provider_for(self, dialect: str, model: str) -> Provider:
        """Resolve a request to the provider a model is routed to — its chain's primary.

        Kept as it was (H-013 predicted Phase 6 would widen it; what actually widened is
        the *rule*, which is better — the name a model resolves to still means the same
        thing). ``routing.resolve_route`` is the whole decision, chain included, and it
        is what ``headroom/api/proxy.py`` calls.
        """
        return self.registry.get(self.routing.resolve(dialect, model))

    async def aclose(self) -> None:
        # The budget gate first: it drains settlements that were left running on a
        # disconnect, and a hold that is not settled here is one the sweeper has to
        # find later. Money before bookkeeping.
        await self.budgets.aclose()
        # The rate limiter has nothing to drain — a bucket consumption never settles —
        # so this only releases its client's thread pool.
        await self.limits.aclose()
        # Nor has the cache: a store is awaited on the request's own path, so nothing is
        # ever in flight at shutdown. This releases the pool it was given.
        await self.cache.aclose()
        # The writer next, and deliberately: it drains its queue into the ledger
        # store, so closing the store out from under it would throw away exactly the
        # rows a graceful shutdown exists to save (docs/DECISIONS.md H-027).
        if self.meter.writer is not None:
            await self.meter.writer.aclose()
        await self.registry.aclose()
        await self.store.aclose()
        await self.ledger.aclose()


def build_gateway(
    config: GatewayConfig | None = None,
    *,
    store: TenantStore | None = None,
    ledger: LedgerStore | None = None,
    budgets: BudgetStore | None = None,
    limits: RateLimitStore | None = None,
    cache: ResponseCacheStore | None = None,
) -> Gateway:
    """Construct a gateway from config (loaded from disk when not supplied).

    The stores default to Postgres and DynamoDB and open nothing until first use
    (``headroom/db/pool.py``, ``headroom/db/dynamo.py``), so building a gateway — and
    therefore starting the process — never requires a reachable backing service. They
    are injectable for tests; nothing in configuration can select a non-durable one.

    Prices are read here, once, from ``config/models.yaml``. A missing or malformed
    price file fails at startup rather than at the first billed request: a gateway that
    booted without prices would serve traffic and write a ledger full of NULL costs,
    and nobody finds out until an invoice arrives. The budget gate shares that same
    price book, so an estimate and the cost it is eventually compared against can never
    come from two different files.
    """
    resolved = config if config is not None else load_config()
    kinds = provider_kinds()
    registry = ProviderRegistry()
    for name, spec in resolved.providers.items():
        factory = kinds.get(spec.kind)
        if factory is None:
            known = ", ".join(sorted(kinds)) or "none"
            raise ConfigurationError(
                f"provider {name!r} has unknown kind {spec.kind!r} (registered: {known})"
            )
        registry.add(factory(name, **spec.settings()))
    _require_same_dialect_routes(resolved)
    health = HealthTracker()
    for provider in registry:
        health.track(provider.name, provider.kind)
    tenant_store = store if store is not None else PostgresTenantStore()
    ledger_store = ledger if ledger is not None else PostgresLedgerStore()
    budget_store = budgets if budgets is not None else DynamoBudgetStore()
    # A second DynamoDB store, and deliberately its own client: the two tables have
    # different access patterns and different retention rules, and sharing one thread
    # pool would let a slow budget item queue behind a burst of bucket writes on exactly
    # the path where the limiter exists to keep latency bounded.
    bucket_store = limits if limits is not None else DynamoRateLimitStore()
    # Postgres again, and the same pool discipline: lazy, so building a gateway opens
    # nothing. The embedder is lazier still — `load_embedder` records a *name*, and the
    # model is not imported, let alone loaded, until a tenant with semantic caching
    # actually sends a request. That is what keeps CI's image job (no `embed` extra
    # installed) able to build this container and smoke `/healthz`.
    cache_store = cache if cache is not None else PostgresResponseCacheStore()
    prices: PriceBook = load_price_book()
    return Gateway(
        config=resolved,
        registry=registry,
        routing=resolved.routing_table(),
        store=tenant_store,
        authenticator=Authenticator(tenant_store),
        ledger=ledger_store,
        meter=Meter(prices=prices, writer=LedgerWriter(ledger_store)),
        budgets=BudgetGate(store=budget_store, prices=prices),
        limits=RateLimiter(store=bucket_store),
        cache=ResponseCache(store=cache_store, embedder=load_embedder()),
        health=health,
        failover=Failover(registry=registry, health=health),
        admin_token=os.environ.get(ADMIN_TOKEN_ENV) or None,
    )


def _require_same_dialect_routes(config: GatewayConfig) -> None:
    """BUILD_PLAN L4, checked rather than trusted: no route may cross a dialect.

    Routing being per dialect already makes a failover chain same-dialect *structurally*
    — a chain lives inside one dialect's rule list. What it does not stop is an operator
    writing ``fallbacks: [anthropic]`` under an ``openai:`` route, which would hand an
    OpenAI-dialect body to the Messages API on exactly the day the primary went down.
    That is the cross-dialect translation L4 puts permanently out of scope, arriving
    through a config file instead of through a translation layer.

    Checked here rather than at config load because this is the module that imports the
    provider kinds (for their registration side effects), and therefore the only one
    where the kind table is guaranteed populated. Primaries are checked too, not just
    fallbacks: an asymmetry there would be a rule that only applies to the new feature.
    """
    for dialect, rules in config.routes.items():
        for rule in rules:
            for name in (rule.provider, *rule.fallbacks):
                kind = config.providers[name].kind
                spoken = kind_dialects(kind)
                if spoken and dialect not in spoken:
                    raise ConfigurationError(
                        f"route {dialect}:{rule.prefix!r} names provider {name!r} of kind "
                        f"{kind!r}, which speaks {', '.join(sorted(spoken))} and not "
                        f"{dialect!r}. BUILD_PLAN L4 puts cross-dialect translation out "
                        f"of scope, so a chain must be same-dialect end to end."
                    )
