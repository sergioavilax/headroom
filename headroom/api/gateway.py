"""The ``Gateway``: configuration, providers, and routing, assembled once at startup.

One object rather than three module-level globals, because the test suite builds a
mock-only gateway per test and the Phase 9/10 deployments will build one per process
with different config. Nothing here is a singleton, and nothing reads configuration at
request time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from headroom.core.config import ADMIN_TOKEN_ENV, GatewayConfig, load_config
from headroom.core.errors import ConfigurationError
from headroom.core.storage import TenantStore
from headroom.db.tenants import PostgresTenantStore
from headroom.policy.auth import Authenticator
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
    admin_token: str | None = None

    def provider_for(self, dialect: str, model: str) -> Provider:
        """Resolve a request to the provider that will serve it.

        Phase 6 widens this to return a chain; every caller already goes through it,
        so the failover phase changes this method and not the proxy.
        """
        return self.registry.get(self.routing.resolve(dialect, model))

    async def aclose(self) -> None:
        await self.registry.aclose()
        await self.store.aclose()


def build_gateway(
    config: GatewayConfig | None = None, *, store: TenantStore | None = None
) -> Gateway:
    """Construct a gateway from config (loaded from disk when not supplied).

    The store defaults to Postgres and its pool is lazy (``headroom/db/pool.py``), so
    building a gateway — and therefore starting the process — never requires a
    reachable database. ``store`` is injectable for tests; nothing in configuration can
    select a non-durable one.
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
    return Gateway(
        config=resolved,
        registry=registry,
        routing=resolved.routing_table(),
        store=tenant_store,
        authenticator=Authenticator(tenant_store),
        admin_token=os.environ.get(ADMIN_TOKEN_ENV) or None,
    )
