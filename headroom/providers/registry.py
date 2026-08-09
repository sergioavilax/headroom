"""The provider registry — the seam Phase 6's failover chains extend.

Two levels, on purpose:

* **Kinds** are implementations (``anthropic``, ``openai_compat``, ``mock``). New ones
  register a factory here and become available to config without the config loader,
  the router, or the proxy learning anything about them.
* **Instances** are configured providers with names (``anthropic``, ``vllm_a``,
  ``vllm_b``). The routing table resolves a model to an instance *name*; the registry
  turns that name into an object.

The indirection is what makes BUILD_PLAN's Phase 6 additive rather than a rewrite: the
operator's two vLLM boxes are two instances of one kind, a failover chain is a list of
instance names, and the kill demo works because the routing layer never needed to know
which GPU it was talking to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from headroom.providers.base import Provider

__all__ = ["ProviderRegistry", "provider_kinds", "register_kind"]

#: kind name -> factory. A factory takes the instance name plus its resolved settings.
ProviderFactory = Callable[..., Provider]

_KINDS: dict[str, ProviderFactory] = {}


def register_kind(kind: str, factory: ProviderFactory) -> None:
    """Make an implementation available to configuration under ``kind``."""
    _KINDS[kind] = factory


def provider_kinds() -> dict[str, ProviderFactory]:
    """The registered implementations. A copy — callers cannot mutate the table."""
    return dict(_KINDS)


class ProviderRegistry:
    """The configured providers of one running gateway, by name."""

    __slots__ = ("_providers",)

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def add(self, provider: Provider) -> None:
        """Register an instance. A duplicate name is a configuration bug, so it raises."""
        if provider.name in self._providers:
            raise ValueError(f"provider {provider.name!r} is already registered")
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        """Look up an instance by name."""
        try:
            return self._providers[name]
        except KeyError:
            known = ", ".join(sorted(self._providers)) or "none"
            raise KeyError(f"unknown provider {name!r} (registered: {known})") from None

    def names(self) -> list[str]:
        return sorted(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def __iter__(self) -> Iterator[Provider]:
        return iter(self._providers.values())

    async def aclose(self) -> None:
        """Shut every instance down. Called once, from the application's lifespan."""
        for provider in self._providers.values():
            await provider.aclose()
