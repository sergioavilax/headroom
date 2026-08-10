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

**Phase 6 added one thing a kind must declare: which dialects it speaks.** BUILD_PLAN L4
puts cross-dialect translation permanently out of scope, and a failover chain is the one
place a config file could quietly violate that — ``fallbacks: [anthropic]`` under an
``openai:`` route would send an OpenAI-dialect body to the Messages API. Routing being
per-dialect makes a chain same-dialect *structurally*; this declaration makes it
*checked*, at startup, by ``headroom/api/gateway.py``. It is a required keyword rather
than an optional one on purpose: a kind that has not said which wire format it speaks
cannot be wired safely, and a permissive default is how a rule ends up unenforced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from headroom.providers.base import Provider

__all__ = [
    "ProviderRegistry",
    "kind_dialects",
    "provider_kinds",
    "register_kind",
]

#: kind name -> factory. A factory takes the instance name plus its resolved settings.
ProviderFactory = Callable[..., Provider]

_KINDS: dict[str, ProviderFactory] = {}
_DIALECTS: dict[str, frozenset[str]] = {}


def register_kind(kind: str, factory: ProviderFactory, *, dialects: frozenset[str]) -> None:
    """Make an implementation available to configuration under ``kind``.

    ``dialects`` is the set of wire formats this kind can be handed — ``{"anthropic"}``
    for the Messages API, ``{"openai"}`` for anything OpenAI-compatible, both for the
    MockProvider, which speaks each of them. It is what turns BUILD_PLAN L4 from a rule
    a reviewer has to remember into a rule the gateway refuses to boot without.
    """
    _KINDS[kind] = factory
    _DIALECTS[kind] = dialects


def provider_kinds() -> dict[str, ProviderFactory]:
    """The registered implementations. A copy — callers cannot mutate the table."""
    return dict(_KINDS)


def kind_dialects(kind: str) -> frozenset[str]:
    """Which dialects a kind speaks. Empty for a kind nobody registered."""
    return _DIALECTS.get(kind, frozenset())


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
