"""The ``Gateway``: configuration, providers, and routing, assembled once at startup.

One object rather than three module-level globals, because the test suite builds a
mock-only gateway per test and the Phase 9/10 deployments will build one per process
with different config. Nothing here is a singleton, and nothing reads configuration at
request time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from headroom.core.budgets import BudgetStore
from headroom.core.config import ADMIN_TOKEN_ENV, GatewayConfig, load_config
from headroom.core.errors import ConfigurationError
from headroom.core.ledger import LedgerStore
from headroom.core.limits import RateLimitStore
from headroom.core.storage import TenantStore
from headroom.db.buckets import DynamoRateLimitStore
from headroom.db.budgets import DynamoBudgetStore
from headroom.db.ledger import PostgresLedgerStore
from headroom.db.tenants import PostgresTenantStore
from headroom.metering.meter import Meter
from headroom.metering.prices import PriceBook, load_price_book
from headroom.metering.writer import LedgerWriter
from headroom.policy.auth import Authenticator
from headroom.policy.budgets import BudgetGate
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
from headroom.providers.registry import ProviderRegistry, provider_kinds

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
    admin_token: str | None = None

    def provider_for(self, dialect: str, model: str) -> Provider:
        """Resolve a request to the provider that will serve it.

        Phase 6 widens this to return a chain; every caller already goes through it,
        so the failover phase changes this method and not the proxy.
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
    tenant_store = store if store is not None else PostgresTenantStore()
    ledger_store = ledger if ledger is not None else PostgresLedgerStore()
    budget_store = budgets if budgets is not None else DynamoBudgetStore()
    # A second DynamoDB store, and deliberately its own client: the two tables have
    # different access patterns and different retention rules, and sharing one thread
    # pool would let a slow budget item queue behind a burst of bucket writes on exactly
    # the path where the limiter exists to keep latency bounded.
    bucket_store = limits if limits is not None else DynamoRateLimitStore()
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
        admin_token=os.environ.get(ADMIN_TOKEN_ENV) or None,
    )
