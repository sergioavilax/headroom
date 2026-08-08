"""The ``Gateway``: configuration, providers, and routing, assembled once at startup.

One object rather than three module-level globals, because the test suite builds a
mock-only gateway per test and the Phase 9/10 deployments will build one per process
with different config. Nothing here is a singleton, and nothing reads configuration at
request time.
"""

from __future__ import annotations

from dataclasses import dataclass

from headroom.core.config import GatewayConfig, load_config
from headroom.core.errors import ConfigurationError
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
    """Everything one running gateway needs to serve a request."""

    config: GatewayConfig
    registry: ProviderRegistry
    routing: RoutingTable

    def provider_for(self, dialect: str, model: str) -> Provider:
        """Resolve a request to the provider that will serve it.

        Phase 6 widens this to return a chain; every caller already goes through it,
        so the failover phase changes this method and not the proxy.
        """
        return self.registry.get(self.routing.resolve(dialect, model))

    async def aclose(self) -> None:
        await self.registry.aclose()


def build_gateway(config: GatewayConfig | None = None) -> Gateway:
    """Construct a gateway from config (loaded from disk when not supplied)."""
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
    return Gateway(config=resolved, registry=registry, routing=resolved.routing_table())
